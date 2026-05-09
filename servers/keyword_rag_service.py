"""
Keyword RAG Service
===================
Uses embeddings to progressively discover relevant keywords
instead of loading all keywords into LLM context.

This solves the scalability problem when you have 100s or 1000s of keywords.

Architecture:
1. Keywords are embedded and stored in PostgreSQL with pgvector
2. When a question arrives, we embed it and find similar keywords
3. Only the top-K relevant keywords are passed to the LLM

This is much more efficient than passing all keywords to the LLM.
"""

import os
import logging
import json
from typing import List, Dict, Any, Optional, Tuple

# OPENAI_API_KEY is provided by config_manager (data/secrets.json) at app startup.
# This module is imported indirectly through tool loaders, so by the time
# OpenAIEmbeddings() runs, the key is already in os.environ.

from langchain_openai import OpenAIEmbeddings

logger = logging.getLogger(__name__)

# Embedding model for keywords
_embeddings_model = None

def get_embeddings_model() -> OpenAIEmbeddings:
    """Get or create embeddings model (singleton)."""
    global _embeddings_model
    if _embeddings_model is None:
        _embeddings_model = OpenAIEmbeddings(model="text-embedding-ada-002")
    return _embeddings_model


# ============== Database Setup ==============

def init_keyword_embeddings_table():
    """
    Create the keyword_embeddings table with pgvector support.
    This stores embeddings for efficient similarity search.
    """
    try:
        from servers.db_pool import get_cursor
        
        with get_cursor() as cur:
            # Ensure pgvector extension exists
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            
            # Create keyword embeddings table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS keyword_embeddings (
                    id SERIAL PRIMARY KEY,
                    keyword VARCHAR(255) NOT NULL UNIQUE,
                    category VARCHAR(100),
                    image_ids INTEGER[],
                    embedding vector(1536),  -- ada-002 dimension
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            
            # Create index for fast similarity search
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_keyword_embedding 
                ON keyword_embeddings USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 10);
            """)
            
        logger.info("✅ keyword_embeddings table initialized with pgvector")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error creating keyword_embeddings table: {e}")
        return False


def sync_keywords_to_vector_store():
    """
    Synchronize keywords from medical_images to keyword_embeddings.
    Embeds all unique keywords and stores them for vector search.
    """
    try:
        from servers.db_pool import get_cursor
        
        # 1. Get all unique keywords from medical_images
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT DISTINCT unnested_keyword as keyword, 
                       category,
                       array_agg(id) as image_ids
                FROM medical_images, 
                     LATERAL unnest(keywords) as unnested_keyword
                GROUP BY unnested_keyword, category
            """)
            keywords_data = cur.fetchall()
        
        if not keywords_data:
            logger.info("No keywords to sync")
            return 0
            
        logger.info(f"📝 Syncing {len(keywords_data)} keywords to vector store...")
        
        # 2. Get existing keywords to avoid re-embedding
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT keyword FROM keyword_embeddings")
            existing = {row['keyword'] for row in cur.fetchall()}
        
        # 3. Filter new keywords
        new_keywords = [kw for kw in keywords_data if kw['keyword'] not in existing]
        
        if not new_keywords:
            logger.info("All keywords already embedded")
            return 0
            
        # 4. Generate embeddings for new keywords
        embeddings_model = get_embeddings_model()
        keyword_texts = [kw['keyword'] for kw in new_keywords]
        
        logger.info(f"🔄 Generating embeddings for {len(keyword_texts)} new keywords...")
        embeddings = embeddings_model.embed_documents(keyword_texts)
        
        # 5. Store in database
        with get_cursor() as cur:
            for i, kw_data in enumerate(new_keywords):
                embedding_str = "[" + ",".join(str(x) for x in embeddings[i]) + "]"
                cur.execute("""
                    INSERT INTO keyword_embeddings (keyword, category, image_ids, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    ON CONFLICT (keyword) DO UPDATE SET
                        category = EXCLUDED.category,
                        image_ids = EXCLUDED.image_ids,
                        embedding = EXCLUDED.embedding
                """, (
                    kw_data['keyword'],
                    kw_data['category'],
                    kw_data['image_ids'],
                    embedding_str
                ))
        
        logger.info(f"✅ Synced {len(new_keywords)} keywords to vector store")
        return len(new_keywords)
        
    except Exception as e:
        logger.error(f"❌ Error syncing keywords: {e}")
        raise


# ============== RAG Search ==============

def search_relevant_keywords(
    query_text: str,
    top_k: int = 15,
    similarity_threshold: float = 0.5,
    weight_by_specificity: bool = True
) -> List[Dict[str, Any]]:
    """
    Search for keywords most relevant to the query using vector similarity.
    
    Keywords are weighted by SPECIFICITY: keywords that appear in fewer images
    are considered more specific and get higher scores.
    
    Args:
        query_text: The question or justification text
        top_k: Maximum number of keywords to return
        similarity_threshold: Minimum similarity score (0-1)
        weight_by_specificity: If True, prefer specific keywords over generic ones
    
    Returns:
        List of relevant keywords with similarity scores
    """
    try:
        from servers.db_pool import get_cursor
        import math
        
        # 1. Embed the query
        embeddings_model = get_embeddings_model()
        query_embedding = embeddings_model.embed_query(query_text)
        query_vector_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        
        # 2. Vector search using pgvector - get more results to filter
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT keyword, category, image_ids,
                       1 - (embedding <=> %s::vector) as similarity,
                       array_length(image_ids, 1) as num_images
                FROM keyword_embeddings
                WHERE 1 - (embedding <=> %s::vector) > %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (query_vector_str, query_vector_str, similarity_threshold, query_vector_str, top_k * 2))
            
            results = cur.fetchall()
        
        if not results:
            return []
        
        # 3. Calculate specificity-weighted scores
        processed_results = []
        for r in results:
            result_dict = dict(r)
            num_images = result_dict.get('num_images', 1) or 1
            similarity = result_dict['similarity']
            
            if weight_by_specificity:
                # Specificity score: keywords in fewer images get higher scores
                # Using inverse log: 1/log(n+1) where n = number of images
                specificity = 1.0 / math.log(num_images + 1.5)
                
                # Combined score: balance similarity and specificity
                # 70% similarity, 30% specificity
                combined_score = (0.7 * similarity) + (0.3 * specificity)
                result_dict['specificity'] = specificity
                result_dict['combined_score'] = combined_score
            else:
                result_dict['combined_score'] = similarity
                result_dict['specificity'] = 1.0
            
            processed_results.append(result_dict)
        
        # 4. Sort by combined score and take top_k
        processed_results.sort(key=lambda x: x['combined_score'], reverse=True)
        final_results = processed_results[:top_k]
        
        logger.info(f"🔍 Found {len(final_results)} relevant keywords (specificity-weighted)")
        for r in final_results[:5]:
            logger.debug(f"  - {r['keyword']}: sim={r['similarity']:.2f}, spec={r['specificity']:.2f}, score={r['combined_score']:.2f}")
            
        return final_results
        
    except Exception as e:
        logger.error(f"❌ Error searching keywords: {e}")
        return []


def get_keywords_for_question(
    question_text: str,
    justification_text: str = "",
    top_k: int = 15
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Get relevant keywords for a medical question using RAG.
    
    This is the main entry point for the keyword RAG system.
    
    Args:
        question_text: The exam question
        justification_text: Optional justification
        top_k: Maximum keywords to retrieve
    
    Returns:
        Tuple of (keyword_list, full_results_with_metadata)
    """
    # Combine texts for better semantic matching
    combined_text = f"{question_text} {justification_text}".strip()
    
    # Search for relevant keywords
    results = search_relevant_keywords(combined_text, top_k=top_k)
    
    # Extract just the keywords
    keywords = [r['keyword'] for r in results]
    
    return keywords, results


# ============== Progressive Discovery ==============

def discover_keywords_progressively(
    question_text: str,
    justification_text: str = "",
    initial_top_k: int = 10,
    expand_top_k: int = 5
) -> Dict[str, Any]:
    """
    Progressive keyword discovery: start with most relevant,
    then expand if needed.
    
    This allows the system to "think" in stages:
    1. Find initial top-10 most relevant keywords
    2. If confidence is low, expand search
    3. Return categorized results
    
    Args:
        question_text: The exam question
        justification_text: Optional justification
        initial_top_k: First-pass keyword count
        expand_top_k: Additional keywords if needed
    
    Returns:
        Dict with primary_keywords, secondary_keywords, and metadata
    """
    combined_text = f"{question_text} {justification_text}".strip()
    
    # Stage 1: Get initial relevant keywords
    initial_results = search_relevant_keywords(
        combined_text, 
        top_k=initial_top_k, 
        similarity_threshold=0.6
    )
    
    # Analyze results
    if not initial_results:
        # No good matches - lower threshold and try again
        initial_results = search_relevant_keywords(
            combined_text,
            top_k=initial_top_k,
            similarity_threshold=0.3
        )
    
    # Categorize by similarity
    high_confidence = [r for r in initial_results if r['similarity'] > 0.7]
    medium_confidence = [r for r in initial_results if 0.5 < r['similarity'] <= 0.7]
    
    # Stage 2: If few high-confidence matches, expand search
    if len(high_confidence) < 3:
        secondary_results = search_relevant_keywords(
            combined_text,
            top_k=initial_top_k + expand_top_k,
            similarity_threshold=0.4
        )
        # Get additional keywords not in initial results
        initial_keywords = {r['keyword'] for r in initial_results}
        additional = [r for r in secondary_results if r['keyword'] not in initial_keywords]
    else:
        additional = []
    
    return {
        "primary_keywords": [r['keyword'] for r in high_confidence],
        "secondary_keywords": [r['keyword'] for r in medium_confidence],
        "expanded_keywords": [r['keyword'] for r in additional],
        "all_keywords": [r['keyword'] for r in initial_results] + [r['keyword'] for r in additional],
        "categories_found": list(set(r['category'] for r in initial_results if r['category'])),
        "confidence_distribution": {
            "high": len(high_confidence),
            "medium": len(medium_confidence),
            "expanded": len(additional)
        }
    }


# ============== Initialization ==============

def initialize_keyword_rag():
    """
    Initialize the keyword RAG system:
    1. Create tables
    2. Sync existing keywords
    """
    try:
        logger.info("🚀 Initializing Keyword RAG system...")
        
        # Create table
        init_keyword_embeddings_table()
        
        # Sync keywords
        synced = sync_keywords_to_vector_store()
        
        logger.info(f"✅ Keyword RAG initialized. {synced} new keywords embedded.")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize Keyword RAG: {e}")
        return False


# Auto-initialize on import (optional)
# initialize_keyword_rag()
