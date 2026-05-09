"""
RAG Search Module
=================
Performs semantic search on PostgreSQL with pgvector and generates LLM responses.
Uses the same pgvector collection populated by PDF uploads.
"""

import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from langchain_postgres.vectorstores import PGVector
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import PromptTemplate

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PostgreSQL connection - same as pdf_processor
PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = os.environ.get("PG_PORT", "5432")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")
PG_DB = os.environ.get("PG_DB", "mibase")
COLLECTION_NAME = "pdf_documents"
CONNECTION_STRING = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


def get_vector_store() -> PGVector:
    """
    Gets the PGVector vector store with OpenAI embeddings.
    Uses the same collection as pdf_processor.
    """
    embeddings = OpenAIEmbeddings(
        model="text-embedding-ada-002",
        openai_api_key=OPENAI_API_KEY
    )
    
    vector_store = PGVector(
        embeddings=embeddings,
        collection_name=COLLECTION_NAME,
        connection=CONNECTION_STRING,
        use_jsonb=True
    )
    
    return vector_store


def search_vectorstore(query: str, k: int = 5) -> List[Dict[str, Any]]:
    """
    Performs similarity search on PostgreSQL with pgvector.
    
    Args:
        query: Search query text
        k: Number of results to return
        
    Returns:
        List of {content, metadata, score} dicts
    """
    try:
        vector_store = get_vector_store()
        results = vector_store.similarity_search_with_score(query, k=k)
        
        formatted = []
        for doc, score in results:
            formatted.append({
                "content": doc.page_content,
                "metadata": doc.metadata,
                "score": float(score)
            })
        
        logger.info(f"🔍 pgvector search: found {len(formatted)} results for '{query[:50]}...'")
        return formatted
        
    except Exception as e:
        logger.error(f"pgvector search error: {e}")
        return []


# Alias for backward compatibility
search_chroma = search_vectorstore


def rag_query(
    query: str,
    context: Optional[str] = None,
    k: int = 5
) -> Dict[str, Any]:
    """
    Performs RAG query: searches pgvector and generates LLM response.
    
    Args:
        query: User's question
        context: Optional additional context (e.g., selected text)
        k: Number of documents to retrieve
        
    Returns:
        Dict with response, sources, and metadata
    """
    # 1. Search pgvector
    search_results = search_vectorstore(query, k=k)
    
    if not search_results:
        return {
            "response": "No se encontraron documentos relevantes en la base de conocimientos.",
            "sources": [],
            "chunks_used": 0
        }
    
    # 2. Format retrieved documents as context
    retrieved_context = "\n\n".join([
        f"[Documento {i+1} - {r['metadata'].get('source', 'Desconocido')}]\n{r['content']}"
        for i, r in enumerate(search_results)
    ])
    
    # 3. Build prompt with optional user context
    if context:
        full_context = f"""CONTEXTO DEL USUARIO (texto seleccionado):
{context}

DOCUMENTOS RECUPERADOS DE LA BASE DE CONOCIMIENTOS:
{retrieved_context}"""
    else:
        full_context = f"""DOCUMENTOS RECUPERADOS DE LA BASE DE CONOCIMIENTOS:
{retrieved_context}"""
    
    # 4. Create LLM and generate response
    prompt = PromptTemplate(
        input_variables=["context", "question"],
        template="""Eres un asistente experto. Responde la pregunta usando el contexto proporcionado.
Si el contexto no contiene información relevante, indícalo claramente.

{context}

PREGUNTA: {question}

Respuesta detallada:"""
    )
    
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        openai_api_key=OPENAI_API_KEY
    )
    
    chain = prompt | llm
    
    try:
        response = chain.invoke({
            "context": full_context,
            "question": query
        })
        
        response_text = response.content if hasattr(response, 'content') else str(response)
        
    except Exception as e:
        logger.error(f"LLM error: {e}")
        response_text = f"Error generando respuesta: {str(e)}"
    
    # 5. Format sources
    sources = [
        {
            "source": r["metadata"].get("source", "Desconocido"),
            "chunk_index": r["metadata"].get("chunk_index", 0),
            "score": r["score"]
        }
        for r in search_results
    ]
    
    logger.info(f"✅ RAG query completed: {len(sources)} sources, {len(response_text)} chars response")
    
    return {
        "response": response_text,
        "sources": sources,
        "chunks_used": len(search_results),
        "query": query
    }


def format_rag_result_as_markdown(result: Dict[str, Any]) -> str:
    """
    Formats RAG result as markdown for saving to workspace.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    md = f"""# Búsqueda RAG - {timestamp}

## Consulta
{result.get('query', 'N/A')}

## Respuesta
{result.get('response', 'Sin respuesta')}

## Fuentes Utilizadas ({result.get('chunks_used', 0)} fragmentos)
"""
    
    for i, source in enumerate(result.get('sources', []), 1):
        md += f"- **{source.get('source', 'Desconocido')}** (chunk {source.get('chunk_index', '?')}, score: {source.get('score', 0):.4f})\n"
    
    return md


async def buscar_documentos(
    query: str,
    context: Optional[str] = None,
    target_file: Optional[str] = None
) -> str:
    """
    Tool function: Searches documents in pgvector and saves result.
    
    Args:
        query: Search query
        context: Optional context (selected text)
        target_file: State key or filename to append to (None = create new file)
        
    Returns:
        String response for the agent
    """
    from servers.filesystem_service.file_operations import (
        write_file, read_file, save_state, load_state, _log_change
    )
    import os
    
    # Perform RAG query
    result = rag_query(query, context=context, k=5)
    
    # Format as markdown
    md_content = format_rag_result_as_markdown(result)
    
    # Determine if target is a state or file
    # States don't have file extensions, files do
    if target_file:
        # Check if it has a file extension
        _, ext = os.path.splitext(target_file)
        is_file = bool(ext)  # Has extension = file, no extension = state
        
        if is_file:
            # It's a workspace file - use file operations
            try:
                existing = read_file(target_file)
            except:
                existing = ""
            combined = existing + "\n\n---\n\n" + md_content
            write_file(target_file, combined)
            _log_change("RAG_APPEND_FILE", target_file, f"Query: {query[:50]}...")
            saved_to = f"file:{target_file}"
        else:
            # It's a state key - use state operations
            existing = load_state(target_file)
            if existing:
                if isinstance(existing, str):
                    combined = existing + "\n\n---\n\n" + md_content
                else:
                    combined = str(existing) + "\n\n---\n\n" + md_content
            else:
                combined = md_content
            save_state(target_file, combined)
            _log_change("RAG_APPEND_STATE", f"state['{target_file}']", f"Query: {query[:50]}...")
            saved_to = f"state:{target_file}"
    else:
        # Create new file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_filename = f"rag_resultado_{timestamp}.md"
        write_file(new_filename, md_content)
        _log_change("RAG_NEW_FILE", new_filename, f"Query: {query[:50]}...")
        saved_to = f"file:{new_filename}"
    
    # Return summary for agent
    sources_summary = ", ".join([s.get('source', '?') for s in result.get('sources', [])[:3]])
    return f"""✅ Búsqueda completada.

**Respuesta:**
{result.get('response', 'Sin respuesta')}

**Fuentes consultadas:** {result.get('chunks_used', 0)} fragmentos de: {sources_summary}
**Guardado en:** {saved_to}"""
