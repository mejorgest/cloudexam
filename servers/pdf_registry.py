"""
PDF Registry Module
===================
Manages PDF document registry in PostgreSQL with JSONB for chunk_ids.
Tracks uploaded PDFs and their corresponding pgvector chunks.
"""

import os
import uuid
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional, Any

from psycopg2.extras import RealDictCursor, Json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import centralized connection pool
from servers.db_pool import get_connection

# Cache to avoid re-initializing table on every request
_table_initialized = False

# Cache for PDF list (reduces DB queries by ~90%)
_pdf_list_cache = None
_pdf_list_cache_time = 0
PDF_CACHE_TTL = 10  # Cache valid for 10 seconds


def invalidate_pdf_cache():
    """Invalidate the PDF list cache (call after modifications)."""
    global _pdf_list_cache, _pdf_list_cache_time
    _pdf_list_cache = None
    _pdf_list_cache_time = 0


def init_pdf_table():
    """Create the pdf_documents table if it doesn't exist (runs once per process)."""
    global _table_initialized
    
    if _table_initialized:
        return True  # Already initialized, skip
    
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS pdf_documents (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        filename TEXT NOT NULL,
        original_name TEXT,
        uploaded_at TIMESTAMP DEFAULT NOW(),
        pages INTEGER,
        text_length INTEGER,
        chunk_count INTEGER DEFAULT 0,
        chunk_ids JSONB DEFAULT '[]'::jsonb,
        saved_to TEXT,
        status TEXT DEFAULT 'pending',
        entity_status TEXT DEFAULT 'pending'
    );
    
    CREATE INDEX IF NOT EXISTS idx_pdf_status ON pdf_documents(status);
    CREATE INDEX IF NOT EXISTS idx_pdf_filename ON pdf_documents(filename);
    CREATE INDEX IF NOT EXISTS idx_pdf_entity_status ON pdf_documents(entity_status);
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(create_table_sql)
            conn.commit()
        _table_initialized = True
        logger.info("✅ PDF documents table initialized (first time)")
        return True
    except Exception as e:
        logger.error(f"Error initializing PDF table: {e}")
        return False


def register_pdf(
    filename: str,
    original_name: str,
    pages: int,
    text_length: int,
    saved_to: str
) -> Optional[str]:
    """
    Register a new PDF document.
    
    Args:
        filename: Base filename (without extension)
        original_name: Original uploaded filename
        pages: Number of pages extracted
        text_length: Total character count of extracted text
        saved_to: Where the markdown was saved (e.g., 'file:doc.md')
        
    Returns:
        Document ID (UUID string) or None if failed
    """
    doc_id = str(uuid.uuid4())
    
    insert_sql = """
    INSERT INTO pdf_documents (id, filename, original_name, pages, text_length, saved_to, status)
    VALUES (%s, %s, %s, %s, %s, %s, 'pending')
    RETURNING id;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(insert_sql, (doc_id, filename, original_name, pages, text_length, saved_to))
                result = cur.fetchone()
            conn.commit()
        
        logger.info(f"📄 Registered PDF: {filename} (ID: {doc_id})")
        invalidate_pdf_cache()  # Clear cache
        return str(result[0]) if result else doc_id
        
    except Exception as e:
        logger.error(f"Error registering PDF: {e}")
        return None


def update_pdf_chunks(doc_id: str, chunk_ids: List[str]) -> bool:
    """
    Update the chunk_ids JSONB array for a document.
    
    Args:
        doc_id: Document UUID
        chunk_ids: List of pgvector document IDs
        
    Returns:
        True if successful
    """
    update_sql = """
    UPDATE pdf_documents 
    SET chunk_ids = %s::jsonb,
        chunk_count = %s,
        status = 'indexed'
    WHERE id = %s;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(update_sql, (Json(chunk_ids), len(chunk_ids), doc_id))
            conn.commit()
        
        logger.info(f"✅ Updated PDF {doc_id} with {len(chunk_ids)} chunks")
        invalidate_pdf_cache()  # Clear cache
        return True
        
    except Exception as e:
        logger.error(f"Error updating PDF chunks: {e}")
        return False


def update_pdf_status(doc_id: str, status: str) -> bool:
    """Update the status of a PDF document."""
    update_sql = "UPDATE pdf_documents SET status = %s WHERE id = %s;"
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(update_sql, (status, doc_id))
            conn.commit()
        invalidate_pdf_cache()  # Clear cache
        return True
    except Exception as e:
        logger.error(f"Error updating PDF status: {e}")
        return False


def update_entity_status(doc_id: str, entity_status: str) -> bool:
    """
    Update the entity extraction status of a PDF document.
    
    Args:
        doc_id: Document UUID
        entity_status: Status value (pending, processing, completed, error)
        
    Returns:
        True if successful
    """
    update_sql = "UPDATE pdf_documents SET entity_status = %s WHERE id = %s;"
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(update_sql, (entity_status, doc_id))
            conn.commit()
        logger.info(f"📊 Updated entity_status for {doc_id}: {entity_status}")
        invalidate_pdf_cache()  # Clear cache
        return True
    except Exception as e:
        logger.error(f"Error updating entity status: {e}")
        return False


def list_pdfs_needing_entities() -> List[Dict[str, Any]]:
    """
    List PDFs that need entity extraction.
    
    Returns:
        List of PDF documents with entity_status='pending'
    """
    select_sql = """
    SELECT id, filename, original_name, chunk_count, entity_status
    FROM pdf_documents
    WHERE entity_status = 'pending' AND chunk_count > 0
    ORDER BY uploaded_at DESC;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(select_sql)
                results = cur.fetchall()
        
        docs = []
        for row in results:
            doc = dict(row)
            doc['id'] = str(doc['id'])
            docs.append(doc)
        
        return docs
        
    except Exception as e:
        logger.error(f"Error listing PDFs needing entities: {e}")
        return []


def list_pdfs() -> List[Dict[str, Any]]:
    """
    List all registered PDF documents (with caching).
    
    Returns:
        List of PDF document dictionaries
    """
    global _pdf_list_cache, _pdf_list_cache_time
    
    # Return cached result if still valid
    if _pdf_list_cache is not None and (time.time() - _pdf_list_cache_time) < PDF_CACHE_TTL:
        return _pdf_list_cache
    
    select_sql = """
    SELECT id, filename, original_name, uploaded_at, pages, text_length, 
           chunk_count, saved_to, status, entity_status
    FROM pdf_documents
    ORDER BY uploaded_at DESC;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(select_sql)
                results = cur.fetchall()
        
        # Convert to regular dicts and format dates
        docs = []
        for row in results:
            doc = dict(row)
            doc['id'] = str(doc['id'])
            if doc['uploaded_at']:
                doc['uploaded_at'] = doc['uploaded_at'].isoformat()
            docs.append(doc)
        
        # Update cache
        _pdf_list_cache = docs
        _pdf_list_cache_time = time.time()
        
        return docs
        
    except Exception as e:
        logger.error(f"Error listing PDFs: {e}")
        return []


def get_pdf(doc_id: str) -> Optional[Dict[str, Any]]:
    """Get a specific PDF document by ID."""
    select_sql = """
    SELECT id, filename, original_name, uploaded_at, pages, text_length, 
           chunk_count, chunk_ids, saved_to, status
    FROM pdf_documents
    WHERE id = %s;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(select_sql, (doc_id,))
                result = cur.fetchone()
        
        if result:
            doc = dict(result)
            doc['id'] = str(doc['id'])
            if doc['uploaded_at']:
                doc['uploaded_at'] = doc['uploaded_at'].isoformat()
            return doc
        return None
        
    except Exception as e:
        logger.error(f"Error getting PDF: {e}")
        return None


def delete_pdf(doc_id: str) -> Dict[str, Any]:
    """
    Delete a PDF document and its pgvector chunks.
    
    Args:
        doc_id: Document UUID
        
    Returns:
        Dict with success status and deleted chunk count
    """
    # First, get the chunk_ids to delete from pgvector
    doc = get_pdf(doc_id)
    if not doc:
        return {"success": False, "error": "Document not found"}
    
    chunk_ids = doc.get('chunk_ids', [])
    
    # Delete from pgvector
    chunks_deleted = 0
    if chunk_ids and len(chunk_ids) > 0:
        try:
            from servers.pdf_processor import get_vector_store
            vector_store = get_vector_store()
            
            # Delete chunks by ID
            vector_store.delete(ids=chunk_ids)
            chunks_deleted = len(chunk_ids)
            logger.info(f"🗑️ Deleted {chunks_deleted} chunks from pgvector")
            
        except Exception as e:
            logger.error(f"Error deleting from pgvector: {e}")
            # Continue with DB deletion even if pgvector fails
    
    # Delete from PostgreSQL
    delete_sql = "DELETE FROM pdf_documents WHERE id = %s;"
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(delete_sql, (doc_id,))
            conn.commit()
        
        logger.info(f"🗑️ Deleted PDF {doc_id} from registry")
        return {
            "success": True, 
            "chunks_deleted": chunks_deleted,
            "filename": doc.get('filename')
        }
        
    except Exception as e:
        logger.error(f"Error deleting PDF from DB: {e}")
        return {"success": False, "error": str(e)}


def get_pdf_by_filename(filename: str) -> Optional[Dict[str, Any]]:
    """Get a PDF document by filename."""
    select_sql = """
    SELECT id, filename, original_name, uploaded_at, pages, text_length, 
           chunk_count, chunk_ids, saved_to, status
    FROM pdf_documents
    WHERE filename = %s
    ORDER BY uploaded_at DESC
    LIMIT 1;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(select_sql, (filename,))
                result = cur.fetchone()
        
        if result:
            doc = dict(result)
            doc['id'] = str(doc['id'])
            return doc
        return None
        
    except Exception as e:
        logger.error(f"Error getting PDF by filename: {e}")
        return None
