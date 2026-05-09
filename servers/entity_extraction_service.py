"""
Entity Extraction Service
=========================
Async service that bridges the smolagents entity extraction pipeline with PostgreSQL.
Fetches chunks from the database, runs entity extraction, and stores results.
"""

import os
import sys
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from openai import OpenAI

# Import entity extraction components from local servers directory
from servers.entity_schema import Entity, ChunkEntities, ExtractionResult
from servers.entity_prompts import ENTITY_EXTRACTOR_SYSTEM_PROMPT, ENTITY_EXTRACTOR_USER_PROMPT_TEMPLATE

# Import storage module
from servers.entity_storage import (
    get_chunks_by_source,
    get_chunks_by_source_async,
    get_chunks_without_entities,
    get_chunks_without_entities_async,
    update_chunk_entities,
    update_chunk_entities_async,
    init_entities_column
)
from servers.pdf_registry import get_pdf, update_pdf_status, update_entity_status

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Model configuration
MODEL_ID = os.environ.get("ENTITY_MODEL_ID", "gpt-4o-mini")
API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")


def parse_entities_response(response: str) -> List[Dict[str, Any]]:
    """Parse the LLM response into a list of entity dicts."""
    try:
        data = json.loads(response)
        entities = []
        
        for e in data.get("entities", []):
            entities.append({
                "name": e.get("name", ""),
                "entity_type": e.get("entity_type", "other"),
                "context": e.get("context", ""),
                "confidence": e.get("confidence", 0.9)
            })
        
        return entities
    except json.JSONDecodeError:
        import re
        json_match = re.search(r'\{[\s\S]*"entities"[\s\S]*\}', response)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return data.get("entities", [])
            except:
                pass
        return []


def extract_entities_from_chunk_content(
    chunk_id: str,
    content: str,
    client: OpenAI,
    model_id: str = MODEL_ID
) -> List[Dict[str, Any]]:
    """
    Extract entities from a single chunk using OpenAI.
    
    Args:
        chunk_id: ID of the chunk for logging
        content: Text content to extract entities from
        client: OpenAI client
        model_id: Model to use
        
    Returns:
        List of extracted entity dicts
    """
    user_prompt = ENTITY_EXTRACTOR_USER_PROMPT_TEMPLATE.format(
        chunk_id=chunk_id,
        content=content
    )
    
    try:
        completion = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": ENTITY_EXTRACTOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        response_text = completion.choices[0].message.content
        entities = parse_entities_response(response_text)
        
        logger.info(f"  Chunk {chunk_id[:8]}...: {len(entities)} entities")
        return entities
        
    except Exception as e:
        logger.error(f"  Error processing chunk {chunk_id}: {e}")
        return []


async def extract_entities_for_pdf_async(
    pdf_id: str,
    model_id: str = MODEL_ID
) -> Dict[str, Any]:
    """
    Async extract entities for all chunks of a PDF.
    
    Args:
        pdf_id: UUID of the PDF in pdf_documents table
        model_id: Model to use for extraction
        
    Returns:
        Dict with extraction results
    """
    # Get PDF info
    pdf = get_pdf(pdf_id)
    if not pdf:
        return {"success": False, "error": "PDF not found"}
    # Use original_name (includes .pdf extension) to match chunks stored in DB
    source = pdf.get("original_name") or pdf.get("filename")
    if not source:
        return {"success": False, "error": "PDF has no filename"}
    
    # Ensure we have the .pdf extension for matching
    if not source.endswith(".pdf"):
        source = source + ".pdf"
    
    logger.info(f"\n{'='*60}")
    logger.info(f"ASYNC ENTITY EXTRACTION: {source}")
    logger.info(f"{'='*60}")
    
    # Update status
    update_entity_status(pdf_id, "processing")
    
    # Get chunks from database
    chunks = await get_chunks_by_source_async(source)
    if not chunks:
        return {"success": False, "error": f"No chunks found for source: {source}"}
    
    logger.info(f"Found {len(chunks)} chunks to process")
    
    # Initialize OpenAI client
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"success": False, "error": "OPENAI_API_KEY not set"}
    
    client = OpenAI(api_key=api_key, base_url=API_BASE)
    
    # Process each chunk (can be parallelized with asyncio.gather)
    results = {
        "pdf_id": pdf_id,
        "source": source,
        "total_chunks": len(chunks),
        "processed": 0,
        "total_entities": 0,
        "summary": {}
    }
    
    # Process chunks concurrently using asyncio
    async def process_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single chunk and update in database."""
        chunk_id = chunk["id"]
        content = chunk["document"]
        
        # Skip if already has entities
        if chunk.get("entities"):
            return {"chunk_id": chunk_id, "skipped": True, "entities": []}
        
        # Extract entities (CPU-bound, run in thread)
        loop = asyncio.get_event_loop()
        entities = await loop.run_in_executor(
            None,
            lambda: extract_entities_from_chunk_content(chunk_id, content, client, model_id)
        )
        
        # Store in database
        await update_chunk_entities_async(chunk_id, entities, model_id)
        
        return {"chunk_id": chunk_id, "entities": entities}
    
    # Run all chunks concurrently (with semaphore to limit concurrency)
    semaphore = asyncio.Semaphore(5)  # Max 5 concurrent API calls
    
    async def limited_process(chunk: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            return await process_chunk(chunk)
    
    chunk_results = await asyncio.gather(*[limited_process(c) for c in chunks])
    
    # Aggregate results
    entity_counts = {}
    for cr in chunk_results:
        if not cr.get("skipped"):
            results["processed"] += 1
            for entity in cr.get("entities", []):
                etype = entity.get("entity_type", "other")
                entity_counts[etype] = entity_counts.get(etype, 0) + 1
                results["total_entities"] += 1
    
    results["summary"] = entity_counts
    
    # Update PDF status
    update_entity_status(pdf_id, "completed")
    
    logger.info(f"\n✅ Extraction complete: {results['total_entities']} entities from {results['processed']} chunks")
    logger.info(f"Summary: {entity_counts}")
    
    return {"success": True, **results}


def extract_entities_for_pdf(
    pdf_id: str,
    model_id: str = MODEL_ID
) -> Dict[str, Any]:
    """
    Sync version: Extract entities for all chunks of a PDF.
    """
    pdf = get_pdf(pdf_id)
    if not pdf:
        return {"success": False, "error": "PDF not found"}
    
    # Use original_name (includes .pdf extension) to match chunks stored in DB
    source = pdf.get("original_name") or pdf.get("filename")
    if not source:
        return {"success": False, "error": "PDF has no filename"}
    
    # Ensure we have the .pdf extension for matching
    if not source.endswith(".pdf"):
        source = source + ".pdf"
    
    logger.info(f"\n{'='*60}")
    logger.info(f"ENTITY EXTRACTION: {source}")
    logger.info(f"{'='*60}")
    
    update_entity_status(pdf_id, "processing")
    
    chunks = get_chunks_by_source(source)
    if not chunks:
        return {"success": False, "error": f"No chunks found for source: {source}"}
    
    logger.info(f"Found {len(chunks)} chunks to process")
    
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"success": False, "error": "OPENAI_API_KEY not set"}
    
    client = OpenAI(api_key=api_key, base_url=API_BASE)
    
    results = {
        "pdf_id": pdf_id,
        "source": source,
        "total_chunks": len(chunks),
        "processed": 0,
        "total_entities": 0,
        "summary": {}
    }
    
    entity_counts = {}
    
    for chunk in chunks:
        chunk_id = chunk["id"]
        content = chunk["document"]
        
        if chunk.get("entities"):
            logger.info(f"  Chunk {chunk_id[:8]}...: already processed, skipping")
            continue
        
        entities = extract_entities_from_chunk_content(chunk_id, content, client, model_id)
        update_chunk_entities(chunk_id, entities, model_id)
        
        results["processed"] += 1
        for entity in entities:
            etype = entity.get("entity_type", "other")
            entity_counts[etype] = entity_counts.get(etype, 0) + 1
            results["total_entities"] += 1
    
    results["summary"] = entity_counts
    update_entity_status(pdf_id, "completed")
    
    logger.info(f"\n✅ Extraction complete: {results['total_entities']} entities from {results['processed']} chunks")
    
    return {"success": True, **results}


async def process_pending_entities_async(
    limit: int = 100,
    model_id: str = MODEL_ID
) -> Dict[str, Any]:
    """
    Async process all chunks that don't have entities yet.
    
    Args:
        limit: Maximum chunks to process
        model_id: Model to use
        
    Returns:
        Results dict
    """
    chunks = await get_chunks_without_entities_async(limit)
    if not chunks:
        return {"success": True, "message": "No chunks need processing", "processed": 0}
    
    logger.info(f"Processing {len(chunks)} chunks without entities...")
    
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"success": False, "error": "OPENAI_API_KEY not set"}
    
    client = OpenAI(api_key=api_key, base_url=API_BASE)
    
    processed = 0
    total_entities = 0
    
    semaphore = asyncio.Semaphore(5)
    
    async def process_chunk(chunk: Dict[str, Any]) -> int:
        nonlocal processed, total_entities
        async with semaphore:
            chunk_id = chunk["id"]
            content = chunk["document"]
            
            loop = asyncio.get_event_loop()
            entities = await loop.run_in_executor(
                None,
                lambda: extract_entities_from_chunk_content(chunk_id, content, client, model_id)
            )
            
            await update_chunk_entities_async(chunk_id, entities, model_id)
            return len(entities)
    
    results = await asyncio.gather(*[process_chunk(c) for c in chunks])
    
    processed = len(results)
    total_entities = sum(results)
    
    return {
        "success": True,
        "processed": processed,
        "total_entities": total_entities
    }


def process_pending_entities(
    limit: int = 100,
    model_id: str = MODEL_ID
) -> Dict[str, Any]:
    """
    Sync version: Process all chunks without entities.
    """
    chunks = get_chunks_without_entities(limit)
    if not chunks:
        return {"success": True, "message": "No chunks need processing", "processed": 0}
    
    logger.info(f"Processing {len(chunks)} chunks without entities...")
    
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"success": False, "error": "OPENAI_API_KEY not set"}
    
    client = OpenAI(api_key=api_key, base_url=API_BASE)
    
    processed = 0
    total_entities = 0
    
    for chunk in chunks:
        chunk_id = chunk["id"]
        content = chunk["document"]
        
        entities = extract_entities_from_chunk_content(chunk_id, content, client, model_id)
        update_chunk_entities(chunk_id, entities, model_id)
        
        processed += 1
        total_entities += len(entities)
    
    return {
        "success": True,
        "processed": processed,
        "total_entities": total_entities
    }


# Initialize entities column on module import
try:
    init_entities_column()
except Exception as e:
    logger.warning(f"Could not initialize entities column: {e}")
