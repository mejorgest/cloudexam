"""
Entity Storage Module
====================
Operations for managing the `entities` JSONB column in langchain_pg_embedding.
Handles entity storage and chunk retrieval for entity extraction.
Uses psycopg2 for database operations (sync only for simplicity).
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from psycopg2.extras import RealDictCursor, Json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import centralized connection pool
from servers.db_pool import get_connection


def init_entities_column() -> bool:
    """
    Add the 'entities' JSONB column to langchain_pg_embedding table if it doesn't exist.
    
    Returns:
        True if successful
    """
    alter_sql = """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'langchain_pg_embedding' 
            AND column_name = 'entities'
        ) THEN
            ALTER TABLE langchain_pg_embedding ADD COLUMN entities JSONB;
            CREATE INDEX IF NOT EXISTS idx_embedding_entities_status 
                ON langchain_pg_embedding ((entities IS NOT NULL));
        END IF;
    END $$;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(alter_sql)
            conn.commit()
        logger.info("✅ Entities column initialized in langchain_pg_embedding")
        return True
    except Exception as e:
        logger.error(f"Error initializing entities column: {e}")
        return False


def update_chunk_entities(
    chunk_id: str,
    entities: List[Dict[str, Any]],
    model_id: str = "gpt-4o-mini"
) -> bool:
    """
    Update entities for a specific chunk.
    
    Args:
        chunk_id: The UUID of the chunk in langchain_pg_embedding
        entities: List of extracted entities
        model_id: The model used for extraction
        
    Returns:
        True if successful
    """
    entities_data = {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "model": model_id,
        "entity_count": len(entities),
        "entities": entities
    }
    
    update_sql = """
    UPDATE langchain_pg_embedding 
    SET entities = %s
    WHERE id = %s;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(update_sql, (Json(entities_data), chunk_id))
            conn.commit()
        logger.info(f"✅ Updated entities for chunk {chunk_id[:8]}... ({len(entities)} entities)")
        return True
    except Exception as e:
        logger.error(f"Error updating chunk entities: {e}")
        return False


# Async aliases (for compatibility - just call sync versions)
async def update_chunk_entities_async(
    chunk_id: str,
    entities: List[Dict[str, Any]],
    model_id: str = "gpt-4o-mini"
) -> bool:
    """Async wrapper - calls sync version."""
    return update_chunk_entities(chunk_id, entities, model_id)


def get_chunks_by_source(source: str) -> List[Dict[str, Any]]:
    """
    Fetch all chunks for a PDF by source filename.
    
    Args:
        source: The source filename stored in cmetadata
        
    Returns:
        List of chunk dicts with id, document, cmetadata, entities
    """
    select_sql = """
    SELECT id, document, cmetadata, entities
    FROM langchain_pg_embedding
    WHERE cmetadata->>'source' = %s
    ORDER BY (cmetadata->>'chunk_index')::int;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(select_sql, (source,))
                rows = cur.fetchall()
        
        chunks = []
        for row in rows:
            chunks.append({
                "id": str(row["id"]),
                "document": row["document"],
                "cmetadata": row["cmetadata"],
                "entities": row["entities"]
            })
        
        logger.info(f"📦 Fetched {len(chunks)} chunks for source '{source}'")
        return chunks
    except Exception as e:
        logger.error(f"Error fetching chunks by source: {e}")
        return []


async def get_chunks_by_source_async(source: str) -> List[Dict[str, Any]]:
    """Async wrapper - calls sync version."""
    return get_chunks_by_source(source)


def get_chunks_without_entities(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Fetch chunks that don't have entities extracted yet.
    
    Args:
        limit: Maximum number of chunks to return
        
    Returns:
        List of chunk dicts needing entity extraction
    """
    select_sql = """
    SELECT id, document, cmetadata
    FROM langchain_pg_embedding
    WHERE entities IS NULL
    LIMIT %s;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(select_sql, (limit,))
                rows = cur.fetchall()
        
        chunks = []
        for row in rows:
            chunks.append({
                "id": str(row["id"]),
                "document": row["document"],
                "cmetadata": row["cmetadata"]
            })
        
        logger.info(f"📦 Found {len(chunks)} chunks without entities")
        return chunks
    except Exception as e:
        logger.error(f"Error fetching chunks without entities: {e}")
        return []


async def get_chunks_without_entities_async(limit: int = 100) -> List[Dict[str, Any]]:
    """Async wrapper - calls sync version."""
    return get_chunks_without_entities(limit)


def get_entity_summary(source: Optional[str] = None) -> Dict[str, Any]:
    """
    Get summary statistics for extracted entities.
    
    Args:
        source: Optional filter by source filename
        
    Returns:
        Dict with entity counts and statistics
    """
    if source:
        where_clause = "WHERE cmetadata->>'source' = %s AND entities IS NOT NULL"
        params = (source,)
    else:
        where_clause = "WHERE entities IS NOT NULL"
        params = ()
    
    query = f"""
    SELECT 
        COUNT(*) as total_chunks,
        COALESCE(SUM((entities->>'entity_count')::int), 0) as total_entities,
        COUNT(CASE WHEN entities IS NOT NULL THEN 1 END) as processed_chunks
    FROM langchain_pg_embedding
    {where_clause};
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params if params else None)
                row = cur.fetchone()
        
        return {
            "total_chunks": row["total_chunks"] or 0,
            "total_entities": row["total_entities"] or 0,
            "processed_chunks": row["processed_chunks"] or 0
        }
    except Exception as e:
        logger.error(f"Error getting entity summary: {e}")
        return {"total_chunks": 0, "total_entities": 0, "processed_chunks": 0}


def get_all_entities_for_source(source: str) -> List[Dict[str, Any]]:
    """
    Get all extracted entities for a given PDF source.
    
    Args:
        source: The source filename
        
    Returns:
        List of all entities aggregated from all chunks
    """
    select_sql = """
    SELECT entities->'entities' as entities_list
    FROM langchain_pg_embedding
    WHERE cmetadata->>'source' = %s 
    AND entities IS NOT NULL;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(select_sql, (source,))
                rows = cur.fetchall()
        
        all_entities = []
        for row in rows:
            if row["entities_list"]:
                all_entities.extend(row["entities_list"])
        
        return all_entities
    except Exception as e:
        logger.error(f"Error getting entities for source: {e}")
        return []
