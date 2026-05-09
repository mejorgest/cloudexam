"""
PDF Processor Module
====================
Extracts text from PDFs, chunks it, generates embeddings, and stores in PostgreSQL with pgvector.
Uses LangChain for chunking and embedding, PGVector for vector storage.
"""

import os
import asyncio
import logging
from typing import Optional, Tuple, List

from io import BytesIO

# PDF extraction
try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None

# LangChain components
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_postgres.vectorstores import PGVector

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PostgreSQL connection parameters for pgvector
PG_HOST = os.environ.get("DB_HOST", os.environ.get("PG_HOST", "localhost"))
PG_PORT = os.environ.get("DB_PORT", os.environ.get("PG_PORT", "5432"))
PG_USER = os.environ.get("DB_USER", os.environ.get("PG_USER", "postgres"))
PG_PASSWORD = os.environ.get("DB_PWD", os.environ.get("PG_PASSWORD", ""))
PG_DB = os.environ.get("DB_NAME", os.environ.get("PG_DB", "mibase"))
COLLECTION_NAME = "pdf_documents"

# Connection string for LangChain PGVector (SQLAlchemy format)
CONNECTION_STRING = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"

# OpenAI API key
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


def init_pgvector():
    """
    Initialize pgvector extension in PostgreSQL.
    This should be run once when setting up the database.
    """
    import psycopg
    
    conn_string = f"host={PG_HOST} port={PG_PORT} user={PG_USER} password={PG_PASSWORD} dbname={PG_DB}"
    
    try:
        with psycopg.connect(conn_string) as conn:
            conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
            conn.commit()
        logger.info("✅ pgvector extension initialized")
        return True
    except Exception as e:
        logger.error(f"Error initializing pgvector extension: {e}")
        return False


def extract_text_from_pdf(file_bytes: bytes) -> Tuple[str, int]:
    """
    Extracts text from a PDF file using PyPDF2.
    
    Args:
        file_bytes: Raw bytes of the PDF file
        
    Returns:
        Tuple of (extracted_text, page_count)
    """
    if PdfReader is None:
        raise ImportError("PyPDF2 not installed. Run: pip install pypdf2")
    
    try:
        pdf_file = BytesIO(file_bytes)
        reader = PdfReader(pdf_file)
        
        text_parts = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"--- Página {i + 1} ---\n{page_text}")
        
        full_text = "\n\n".join(text_parts)
        page_count = len(reader.pages)
        
        logger.info(f"📄 PDF extracted: {page_count} pages, {len(full_text)} chars")
        return full_text, page_count
        
    except Exception as e:
        logger.error(f"Error extracting PDF text: {e}")
        raise


def text_to_markdown(text: str, filename: str = "documento") -> str:
    """
    Formats extracted PDF text as markdown.
    
    Args:
        text: Raw extracted text
        filename: Original filename for the header
        
    Returns:
        Markdown formatted text
    """
    lines = text.split("\n")
    md_lines = []
    
    # Add header
    md_lines.append(f"# {filename}\n")
    md_lines.append("*Documento extraído de PDF*\n")
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Convert page headers
        if line.startswith("--- Página"):
            page_num = line.replace("---", "").strip()
            md_lines.append(f"\n## {page_num}\n")
        else:
            md_lines.append(line)
    
    return "\n".join(md_lines)


def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[str]:
    """
    Splits text into chunks using LangChain's RecursiveCharacterTextSplitter.
    
    Args:
        text: Text to split
        chunk_size: Maximum characters per chunk (~250 tokens for English)
        chunk_overlap: Overlap between chunks
        
    Returns:
        List of text chunks
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        add_start_index=True,
        separators=["\n\n", "\n", ".", " ", ""]
    )
    
    chunks = text_splitter.split_text(text)
    logger.info(f"📊 Text split into {len(chunks)} chunks (size={chunk_size}, overlap={chunk_overlap})")
    
    return chunks


# Singleton instances for vector store
_pdf_vector_store = None
_pdf_embeddings = None


def invalidate_vector_store_singleton():
    """
    Invalidates the vector store singleton, forcing a fresh connection on next use.
    Call this when connection errors occur.
    """
    global _pdf_vector_store
    _pdf_vector_store = None
    logger.info("🔄 Vector store singleton invalidated - will reconnect on next use")


def get_vector_store(collection_name: str = COLLECTION_NAME, force_fresh: bool = False) -> PGVector:
    """
    Gets or creates a PGVector vector store with OpenAI embeddings (singleton).
    
    Args:
        collection_name: Name of the collection
        force_fresh: If True, creates a fresh connection even if singleton exists
        
    Returns:
        PGVector vector store instance
    """
    global _pdf_vector_store, _pdf_embeddings
    
    # Force fresh connection if requested
    if force_fresh:
        _pdf_vector_store = None
    
    # Only use singleton for default collection
    if collection_name == COLLECTION_NAME and _pdf_vector_store is not None:
        return _pdf_vector_store
    
    if _pdf_embeddings is None:
        _pdf_embeddings = OpenAIEmbeddings(
            model="text-embedding-ada-002",
            openai_api_key=OPENAI_API_KEY
        )
        logger.info("✅ PDF Embeddings client initialized (singleton)")
    
    vector_store = PGVector(
        embeddings=_pdf_embeddings,
        collection_name=collection_name,
        connection=CONNECTION_STRING,
        use_jsonb=True
    )
    
    if collection_name == COLLECTION_NAME:
        _pdf_vector_store = vector_store
        logger.info(f"✅ PDF PGVector store initialized (singleton): {collection_name}")
    else:
        logger.info(f"📦 PGVector initialized: collection='{collection_name}'")
    
    return vector_store


async def process_pdf_to_vectors(
    text: str,
    filename: str,
    metadata: Optional[dict] = None,
    chunk_size: int = 1000,
    chunk_overlap: int = 200
) -> Tuple[int, List[str]]:
    """
    Async function to chunk text and store embeddings in PostgreSQL with pgvector.
    
    Args:
        text: Text to process
        filename: Source filename for metadata
        metadata: Additional metadata to store
        chunk_size: Characters per chunk
        chunk_overlap: Overlap between chunks
        
    Returns:
        Tuple of (chunk_count, chunk_ids) for traceability
    """
    max_retries = 2
    
    for attempt in range(max_retries):
        try:
            # Run chunking in thread pool (CPU bound)
            loop = asyncio.get_event_loop()
            chunks = await loop.run_in_executor(
                None, 
                lambda: chunk_text(text, chunk_size, chunk_overlap)
            )
            
            if not chunks:
                logger.warning("No chunks generated from text")
                return 0, []
            
            # Prepare metadata for each chunk
            metadatas = []
            for i, chunk in enumerate(chunks):
                chunk_meta = {
                    "source": filename,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    **(metadata or {})
                }
                metadatas.append(chunk_meta)
            
            # Get vector store (force fresh on retry)
            force_fresh = attempt > 0
            vector_store = get_vector_store(force_fresh=force_fresh)
            
            # Add texts with embeddings (this calls OpenAI API)
            document_ids = await loop.run_in_executor(
                None,
                lambda: vector_store.add_texts(chunks, metadatas=metadatas)
            )
            
            logger.info(f"✅ Stored {len(document_ids)} chunks in pgvector for '{filename}'")
            return len(document_ids), list(document_ids)
            
        except Exception as e:
            error_msg = str(e).lower()
            is_connection_error = any(keyword in error_msg for keyword in [
                'connection', 'closed', 'terminated', 'operational', 'timeout'
            ])
            
            if is_connection_error and attempt < max_retries - 1:
                logger.warning(f"⚠️ Connection error on attempt {attempt + 1}, retrying with fresh connection...")
                invalidate_vector_store_singleton()
                continue
            else:
                logger.error(f"Error processing PDF to vectors: {e}", exc_info=True)
                raise


def search_vectors(query: str, k: int = 5, collection_name: str = COLLECTION_NAME) -> List[dict]:
    """
    Searches the vector store for similar documents.
    
    Args:
        query: Search query
        k: Number of results to return
        collection_name: Collection to search
        
    Returns:
        List of results with content and metadata
    """
    max_retries = 2
    
    for attempt in range(max_retries):
        try:
            force_fresh = attempt > 0
            vector_store = get_vector_store(collection_name, force_fresh=force_fresh)
            results = vector_store.similarity_search(query, k=k)
            
            return [
                {
                    "content": doc.page_content,
                    "metadata": doc.metadata
                }
                for doc in results
            ]
        except Exception as e:
            error_msg = str(e).lower()
            is_connection_error = any(keyword in error_msg for keyword in [
                'connection', 'closed', 'terminated', 'operational', 'timeout'
            ])
            
            if is_connection_error and attempt < max_retries - 1:
                logger.warning(f"⚠️ Search connection error, retrying with fresh connection...")
                invalidate_vector_store_singleton()
                continue
            else:
                logger.error(f"Error searching vectors: {e}")
                raise


async def process_pdf_upload(
    pdf_bytes: bytes,
    filename: str,
    target_state: Optional[str] = None,
    target_file: Optional[str] = None
) -> dict:
    """
    Complete PDF upload processing flow:
    1. Extract text from PDF
    2. Convert to Markdown
    3. Save to state or file
    4. Register in database
    5. Process vectors in background
    
    Args:
        pdf_bytes: Raw PDF file bytes
        filename: Original filename
        target_state: State key to append to (optional)
        target_file: File to append to (optional)
        
    Returns:
        Dict with success status, filename, pages, saved_to, etc.
    """
    from servers.filesystem_service.file_operations import save_state, load_state, write_file, read_file
    from servers.pdf_registry import register_pdf, update_pdf_chunks
    import os
    
    try:
        # 1. Extract text from PDF
        raw_text, page_count = extract_text_from_pdf(pdf_bytes)
        
        # 2. Get base name and convert to markdown
        base_name = os.path.splitext(filename)[0]
        md_text = text_to_markdown(raw_text, base_name)
        
        # 3. Decide where to save
        saved_to = ""
        
        if target_state:
            # Append to existing state
            existing = load_state(target_state) or ""
            combined = existing + "\n\n---\n\n" + md_text if existing else md_text
            save_state(target_state, combined)
            saved_to = f"state:{target_state}"
            logger.info(f"📄 PDF saved to state: {target_state}")
            
        elif target_file:
            # Append to existing file
            try:
                existing = read_file(target_file)
            except:
                existing = ""
            combined = existing + "\n\n---\n\n" + md_text if existing else md_text
            write_file(target_file, combined)
            saved_to = f"file:{target_file}"
            logger.info(f"📄 PDF saved to file: {target_file}")
            
        else:
            # Create new file
            new_filename = f"{base_name}.md"
            write_file(new_filename, md_text)
            saved_to = f"file:{new_filename}"
            logger.info(f"📄 PDF saved to new file: {new_filename}")
        
        # 4. Register in database
        pdf_doc_id = register_pdf(
            filename=base_name,
            original_name=filename,
            pages=page_count,
            text_length=len(md_text),
            saved_to=saved_to
        )
        
        # 5. Process vectors in background
        async def process_vectors_background():
            try:
                # Use original filename (with .pdf) as source for entity extraction matching
                chunk_count, chunk_ids = await process_pdf_to_vectors(
                    text=md_text,
                    filename=filename,  # Use original_name with .pdf extension
                    metadata={"pages": page_count, "type": "pdf"}
                )
                
                if pdf_doc_id and chunk_ids:
                    update_pdf_chunks(pdf_doc_id, chunk_ids)
                    logger.info(f"✅ PDF '{filename}' vectorized: {chunk_count} chunks")
                    
                    # Set entity status to DISABLED to prevent frontend polling
                    try:
                        from servers.pdf_registry import update_entity_status
                        update_entity_status(pdf_doc_id, "disabled")
                        logger.info(f"🧬 Entity extraction explicitly disabled for {filename} (status='disabled')")
                    except Exception as e:
                        logger.warning(f"Could not updated entity status: {e}")

                    # Trigger entity extraction automatically (DISABLED)
                    # try:
                    #     from servers.entity_extraction_service import extract_entities_for_pdf_async
                    #     logger.info(f"🔬 Starting entity extraction for PDF {pdf_doc_id}...")
                    #     entity_result = await extract_entities_for_pdf_async(pdf_doc_id)
                    #     if entity_result.get("success"):
                    #         logger.info(f"✅ Entity extraction complete: {entity_result.get('total_entities', 0)} entities")
                    #     else:
                    #         logger.warning(f"⚠️ Entity extraction issue: {entity_result.get('error', 'unknown')}")
                    # except Exception as e:
                    #     logger.error(f"Error in entity extraction for '{filename}': {e}")
                    
            except Exception as e:
                logger.error(f"Error processing vectors for '{filename}': {e}")
        
        # Start background task
        asyncio.create_task(process_vectors_background())
        
        return {
            "success": True,
            "filename": base_name,
            "original_name": filename,
            "pages": page_count,
            "text_length": len(md_text),
            "saved_to": saved_to,
            "doc_id": pdf_doc_id,
            "message": f"PDF '{filename}' procesado exitosamente ({page_count} páginas)"
        }
        
    except Exception as e:
        logger.error(f"Error in process_pdf_upload: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "filename": filename
        }
