"""
Medical Keywords Extractor (LLM-based + Schema-Aware)
=====================================================
Uses LLM to intelligently extract medical keywords from exam questions/justifications.
The LLM receives the ACTUAL keywords available in the database to generate matches.

This is similar to the query_construction pattern used in protocolosaguas301.py,
where the LLM knows exactly what metadata fields and values exist.

Usage:
    keywords = await extract_medical_keywords_llm(question_text, justification_text)
    images = search_images_by_keywords(keywords)
"""

import asyncio
import json
import logging
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

# Threshold for using RAG vs full schema (if more than this many keywords, use RAG)
RAG_THRESHOLD = 50


# ============== RAG-based Keyword Discovery ==============

def get_relevant_keywords_via_rag(
    question_text: str,
    justification_text: str = "",
    top_k: int = 15
) -> Dict[str, Any]:
    """
    Use RAG to discover relevant keywords when the database is large.
    This is much more efficient than passing all keywords to the LLM.
    
    Args:
        question_text: The exam question
        justification_text: Optional justification
        top_k: Maximum relevant keywords to return
    
    Returns:
        Dict with keywords and categories
    """
    try:
        from servers.keyword_rag_service import (
            discover_keywords_progressively,
            initialize_keyword_rag
        )
        
        # Initialize RAG if needed
        initialize_keyword_rag()
        
        # Progressive discovery
        result = discover_keywords_progressively(
            question_text,
            justification_text,
            initial_top_k=top_k,
            expand_top_k=5
        )
        
        logger.info(f"🔍 RAG discovered {len(result['all_keywords'])} keywords "
                   f"(high: {result['confidence_distribution']['high']}, "
                   f"medium: {result['confidence_distribution']['medium']})")
        
        return {
            "_all_keywords": result["all_keywords"],
            "_categories": result["categories_found"],
            "_primary": result["primary_keywords"],
            "_confidence": result["confidence_distribution"]
        }
        
    except Exception as e:
        logger.warning(f"RAG discovery failed, falling back to full schema: {e}")
        return get_available_keywords_from_db()


# ============== Database Schema Functions ==============

def get_available_keywords_from_db() -> Dict[str, List[str]]:
    """
    Consulta la base de datos para obtener TODOS los keywords disponibles.
    Esto le permite al LLM saber exactamente qué términos puede usar.
    
    NOTE: Use get_relevant_keywords_via_rag() for large databases.
    
    Returns:
        Dict con categorías como keys y listas de keywords como values
    """
    try:
        from servers.db_pool import get_cursor
        
        with get_cursor(dict_cursor=True) as cur:
            # Obtener todos los keywords únicos agrupados por categoría
            cur.execute("""
                SELECT category, 
                       array_agg(DISTINCT unnested_keyword) as keywords
                FROM medical_images, 
                     LATERAL unnest(keywords) as unnested_keyword
                GROUP BY category
            """)
            results = cur.fetchall()
            
            schema = {}
            all_keywords = set()
            
            for row in results:
                category = row['category'] or 'general'
                keywords = row['keywords'] or []
                schema[category] = keywords
                all_keywords.update(keywords)
            
            # También incluir un listado plano de todos los keywords
            schema['_all_keywords'] = list(all_keywords)
            
            logger.info(f"📚 Loaded {len(all_keywords)} unique keywords from DB across {len(schema)-1} categories")
            return schema
            
    except Exception as e:
        logger.warning(f"Could not load keywords from DB: {e}")
        return {"_all_keywords": []}


def get_keyword_schema_smart(
    question_text: str = "",
    justification_text: str = ""
) -> Dict[str, Any]:
    """
    Smart schema loader: uses RAG for large databases, full schema for small ones.
    
    This is the recommended entry point for getting keywords.
    """
    try:
        from servers.db_pool import get_cursor
        
        # Count total keywords
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT COUNT(DISTINCT unnested) FROM medical_images, LATERAL unnest(keywords) as unnested")
            count = cur.fetchone()['count']
        
        if count > RAG_THRESHOLD:
            logger.info(f"📊 {count} keywords > {RAG_THRESHOLD}, using RAG for discovery")
            return get_relevant_keywords_via_rag(question_text, justification_text)
        else:
            logger.info(f"📊 {count} keywords <= {RAG_THRESHOLD}, using full schema")
            return get_available_keywords_from_db()
            
    except Exception as e:
        logger.warning(f"Smart schema failed: {e}")
        return get_available_keywords_from_db()


def get_images_metadata_schema() -> str:
    """
    Genera un string formateado con el esquema de imágenes disponibles,
    similar a METADATA_FIELD_INFO en protocolosaguas301.py
    """
    try:
        from servers.db_pool import get_cursor
        
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, title, category, 
                       array_to_string(keywords, ', ') as keywords_str
                FROM medical_images
                ORDER BY category, title
            """)
            images = cur.fetchall()
            
            if not images:
                return "No hay imágenes registradas en la base de datos."
            
            # Formatear como schema legible
            schema_lines = ["IMÁGENES MÉDICAS DISPONIBLES:"]
            schema_lines.append("=" * 50)
            
            current_category = None
            for img in images:
                if img['category'] != current_category:
                    current_category = img['category']
                    schema_lines.append(f"\n📁 Categoría: {current_category or 'general'}")
                    schema_lines.append("-" * 30)
                
                schema_lines.append(f"  • {img['title']}")
                schema_lines.append(f"    Keywords: [{img['keywords_str']}]")
            
            return "\n".join(schema_lines)
            
    except Exception as e:
        logger.warning(f"Could not generate schema: {e}")
        return "Error al cargar el esquema de imágenes."


# ============== Pydantic Schema for Structured Output ==============

class MedicalKeywords(BaseModel):
    """Schema for extracted medical keywords."""
    
    matched_keywords: List[str] = Field(
        default_factory=list,
        description="Keywords from the available schema that match the question/justification"
    )
    
    suggested_keywords: List[str] = Field(
        default_factory=list,
        description="Additional keywords not in schema but relevant to the topic"
    )
    
    primary_topic: str = Field(
        default="",
        description="Main medical topic (e.g., 'hematología', 'cardiología')"
    )
    
    matched_category: str = Field(
        default="",
        description="Category from the schema that best matches the question"
    )


# ============== System Prompt for Keyword Extraction ==============

def build_schema_aware_prompt(images_schema: str, available_keywords: List[str]) -> str:
    """
    Construye el prompt incluyendo el schema de imágenes disponibles.
    Similar a get_query_constructor_prompt() en query_construction.py
    """
    return f"""Eres un experto en terminología médica. Tu tarea es seleccionar palabras clave 
de una LISTA CONOCIDA para buscar imágenes médicas relevantes.

IMPORTANTE: Debes PRIORIZAR las palabras clave que YA EXISTEN en la base de datos.

{images_schema}

KEYWORDS DISPONIBLES (pre-filtrados por relevancia):
{', '.join(available_keywords) if available_keywords else 'No hay keywords registrados'}

INSTRUCCIONES:
1. Analiza la pregunta y justificación del examen
2. Selecciona keywords de la lista "KEYWORDS DISPONIBLES" que sean relevantes
3. Si ningún keyword existente es relevante, sugiere nuevos keywords
4. Prioriza SIEMPRE los keywords que ya existen en la base de datos
5. Los keywords sugeridos deben ser en español y en minúsculas

REGLA CLAVE: Si un keyword de la lista disponible coincide con el tema de la pregunta, 
DEBES incluirlo en matched_keywords."""


# ============== LLM-based Keyword Extractor ==============

async def extract_medical_keywords_llm(
    question_text: str,
    justification_text: str = "",
    model: str = "gpt-4o-mini"
) -> MedicalKeywords:
    """
    Use LLM to intelligently extract medical keywords from exam content.
    
    Now SMART: automatically uses RAG for large databases to avoid context overflow.
    - If < 50 keywords: loads full schema into LLM context
    - If >= 50 keywords: uses vector search to find relevant keywords first
    
    Args:
        question_text: The exam question
        justification_text: Optional justification/explanation text
        model: OpenAI model to use
    
    Returns:
        MedicalKeywords with extracted terms
    """
    try:
        # 1. Obtener schema INTELIGENTEMENTE (RAG si hay muchos keywords)
        images_schema = get_images_metadata_schema()
        keyword_schema = get_keyword_schema_smart(question_text, justification_text)
        available_keywords = keyword_schema.get('_all_keywords', [])
        
        logger.info(f"📚 Schema loaded: {len(available_keywords)} keywords available")
        
        # 2. Construir prompt schema-aware
        system_prompt = build_schema_aware_prompt(images_schema, available_keywords)
        
        llm = ChatOpenAI(model=model, temperature=0)
        
        # Create prompt
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=system_prompt),
            ("human", """PREGUNTA DE EXAMEN:
{question}

JUSTIFICACIÓN (si existe):
{justification}

Selecciona los keywords relevantes de la lista disponible:""")
        ])
        
        # Use structured output
        structured_llm = llm.with_structured_output(MedicalKeywords)
        
        # Run extraction
        chain = prompt | structured_llm
        result = await chain.ainvoke({
            "question": question_text,
            "justification": justification_text or "(sin justificación)"
        })
        
        logger.info(f"✅ LLM matched keywords: {result.matched_keywords}")
        logger.info(f"✅ LLM suggested keywords: {result.suggested_keywords}")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error extracting keywords with LLM: {e}")
        # Return empty result on error
        return MedicalKeywords()


def extract_medical_keywords_llm_sync(
    question_text: str,
    justification_text: str = "",
    model: str = "gpt-4o-mini"
) -> MedicalKeywords:
    """Synchronous wrapper for keyword extraction."""
    return asyncio.run(extract_medical_keywords_llm(question_text, justification_text, model))


# ============== Combined Enrichment with Parallel LLM ==============

async def enrich_with_images_parallel(
    question_text: str,
    justification_text: str,
    main_agent_task: Optional[asyncio.Task] = None
) -> dict:
    """
    Run keyword extraction in parallel with main agent response.
    
    This function:
    1. Extracts keywords using LLM (fast, ~1s)
    2. Searches database for matching images
    3. Returns A2UI components for rendering
    
    Can run concurrently with the main agent using asyncio.gather()
    
    Args:
        question_text: Exam question
        justification_text: Justification content
        main_agent_task: Optional task running main agent (for true parallel execution)
    
    Returns:
        Dict with a2ui_components and metadata
    """
    from servers.medical_images_service import search_images_by_keywords
    
    # Extract keywords using LLM (now schema-aware)
    keywords_result = await extract_medical_keywords_llm(question_text, justification_text)
    
    # Priorizar matched_keywords (que existen en la DB), luego suggested
    all_keywords = list(set(
        keywords_result.matched_keywords +  # Prioridad: keywords que existen
        keywords_result.suggested_keywords   # Secundario: sugerencias nuevas
    ))
    
    if not all_keywords:
        return {
            "success": False,
            "a2ui_components": [],
            "keywords_detected": [],
            "primary_topic": ""
        }
    
    # Search for images
    images = search_images_by_keywords(all_keywords, limit=5)
    
    # Convert to A2UI components
    a2ui_components = []
    for img in images:
        image_url = f"/api/medical-images/{img['id']}"
        a2ui_components.append({
            "type": "Image",
            "id": f"med_img_{img['id']}",
            "properties": {
                "url": image_url,
                "alt": img.get('title') or img.get('filename', ''),
                "caption": img.get('description') or img.get('title', ''),
                "category": img.get('category', 'general')
            }
        })
    
    return {
        "success": True,
        "a2ui_components": a2ui_components,
        "keywords_detected": all_keywords,
        "primary_topic": keywords_result.primary_topic,
        "images_found": len(images)
    }


# ============== Example Usage ==============

if __name__ == "__main__":
    import asyncio
    
    async def test():
        question = "¿Cuál es la forma característica de los eritrocitos maduros y cuál es la principal ventaja funcional de esta morfología?"
        justification = "Los eritrocitos tienen forma de disco bicóncavo que aumenta la relación superficie/volumen."
        
        result = await extract_medical_keywords_llm(question, justification)
        print("Anatomical:", result.anatomical_terms)
        print("Physiological:", result.physiological_terms)
        print("Search Keywords:", result.image_search_keywords)
        print("Primary Topic:", result.primary_topic)
    
    asyncio.run(test())
