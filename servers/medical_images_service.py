"""
Medical Images Service
======================
Stores and retrieves medical/anatomical images indexed by keywords.
Enables A2UI-style enrichment of exam justifications with relevant images.

Database Table:
    CREATE TABLE medical_images (
        id SERIAL PRIMARY KEY,
        filename VARCHAR(255) NOT NULL,
        filepath VARCHAR(500) NOT NULL,
        keywords TEXT[] NOT NULL,  -- Array of keywords for matching
        category VARCHAR(100),     -- e.g., 'anatomy', 'pathology', 'radiology'
        title VARCHAR(255),
        description TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE INDEX idx_keywords ON medical_images USING GIN(keywords);
"""

import os
import logging
import uuid
from typing import List, Optional, Dict, Any
from pathlib import Path

from servers.db_pool import get_cursor, get_connection

logger = logging.getLogger(__name__)

# Directory for storing uploaded images
# Detect if running in Docker or locally
_IS_DOCKER = os.environ.get("DOCKER_ENV", "false").lower() == "true"
_DEFAULT_PATH = "/app/workspace/medical_images" if _IS_DOCKER else "/home/mejorgest/tsagentexam/workspace/medical_images"
IMAGES_DIR = Path(os.environ.get("MEDICAL_IMAGES_DIR", _DEFAULT_PATH))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def init_medical_images_table():
    """Create the medical_images table if it doesn't exist."""
    try:
        with get_cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS medical_images (
                    id SERIAL PRIMARY KEY,
                    filename VARCHAR(255) NOT NULL,
                    filepath VARCHAR(500) NOT NULL,
                    keywords TEXT[] NOT NULL,
                    category VARCHAR(100),
                    title VARCHAR(255),
                    description TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            # Create GIN index for fast keyword searches
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_medical_images_keywords 
                ON medical_images USING GIN(keywords);
            """)
        logger.info("✅ medical_images table initialized")
        return True
    except Exception as e:
        logger.error(f"❌ Error creating medical_images table: {e}")
        return False


def add_medical_image(
    file_path: str,
    keywords: List[str],
    title: str = "",
    description: str = "",
    category: str = "general"
) -> Optional[int]:
    """
    Register a medical image in the database.
    
    Args:
        file_path: Path to the image file (will be copied to IMAGES_DIR)
        keywords: List of keywords for matching (e.g., ['pericardio', 'corazón', 'anatomía'])
        title: Image title
        description: Image description
        category: Category (anatomy, pathology, radiology, etc.)
    
    Returns:
        Image ID if successful, None otherwise
    """
    try:
        source_path = Path(file_path)
        if not source_path.exists():
            logger.error(f"Source file not found: {file_path}")
            return None
        
        # Generate unique filename
        ext = source_path.suffix
        unique_filename = f"{uuid.uuid4().hex[:8]}_{source_path.stem}{ext}"
        dest_path = IMAGES_DIR / unique_filename
        
        # Copy file
        import shutil
        shutil.copy2(source_path, dest_path)
        
        # Normalize keywords to lowercase
        normalized_keywords = [kw.lower().strip() for kw in keywords if kw.strip()]
        
        with get_cursor() as cur:
            cur.execute("""
                INSERT INTO medical_images (filename, filepath, keywords, category, title, description)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (unique_filename, str(dest_path), normalized_keywords, category, title, description))
            result = cur.fetchone()
            image_id = result[0] if result else None
        
        logger.info(f"✅ Added medical image: {unique_filename} with keywords: {normalized_keywords}")
        return image_id
    
    except Exception as e:
        logger.error(f"❌ Error adding medical image: {e}")
        return None


def search_images_by_keywords(keywords: List[str], limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search for medical images matching any of the given keywords.
    
    Args:
        keywords: List of keywords to search for
        limit: Maximum number of results
    
    Returns:
        List of matching images with metadata
    """
    try:
        normalized_keywords = [kw.lower().strip() for kw in keywords if kw.strip()]
        
        if not normalized_keywords:
            return []
        
        with get_cursor(dict_cursor=True) as cur:
            # Search for images where keywords array overlaps with search terms
            cur.execute("""
                SELECT id, filename, filepath, keywords, category, title, description,
                       (SELECT COUNT(*) FROM unnest(keywords) k WHERE k = ANY(%s)) as match_count
                FROM medical_images
                WHERE keywords && %s
                ORDER BY match_count DESC, created_at DESC
                LIMIT %s
            """, (normalized_keywords, normalized_keywords, limit))
            results = cur.fetchall()
        
        return [dict(r) for r in results] if results else []
    
    except Exception as e:
        logger.error(f"❌ Error searching medical images: {e}")
        return []


def extract_medical_keywords(text: str) -> List[str]:
    """
    Extract potential medical keywords from text using a predefined vocabulary.
    This is a simple implementation - could be enhanced with NLP/ML.
    
    Args:
        text: Text to extract keywords from (question, justification, etc.)
    
    Returns:
        List of detected medical keywords
    """
    # Medical keyword vocabulary (expandable)
    MEDICAL_TERMS = {
        # Anatomy
        'corazón', 'heart', 'pericardio', 'miocardio', 'endocardio', 'epicardio',
        'pulmón', 'pulmones', 'pulmonar', 'bronquio', 'alveolo', 'tráquea',
        'hígado', 'riñón', 'riñones', 'renal', 'bazo', 'páncreas', 'vesícula',
        'estómago', 'intestino', 'colon', 'duodeno', 'esófago', 'recto',
        'cerebro', 'cerebral', 'encéfalo', 'médula', 'nervio', 'neurona',
        'hueso', 'músculo', 'articulación', 'cartílago', 'tendón', 'ligamento',
        'arteria', 'vena', 'capilar', 'sangre', 'eritrocito', 'leucocito',
        'ojo', 'oído', 'nariz', 'lengua', 'piel', 'dermis', 'epidermis',
        'útero', 'ovario', 'testículo', 'próstata', 'vejiga', 'uretra',
        'tiroides', 'suprarrenal', 'hipófisis', 'hipotálamo', 'pineal',
        
        # Hematology (added)
        'eritrocitos', 'glóbulos rojos', 'hemoglobina', 'hematología', 'hematíes',
        'eritropoyesis', 'eritropoyetina', 'epo', 'médula ósea', 'reticulocito',
        'reticulocitos', 'proeritroblasto', 'normoblasto', 'anemia', 'policitemia',
        'hemólisis', 'hemostasia', 'coagulación', 'plaquetas', 'trombocitos',
        'plasma', 'suero', 'hematocrito', 'bicóncavo', 'biconcavo',
        'glóbulos blancos', 'leucocitos', 'neutrófilos', 'linfocitos', 'monocitos',
        'basófilos', 'eosinófilos', 'granulocitos', 'células sanguíneas',
        
        # Systems
        'cardiovascular', 'respiratorio', 'digestivo', 'nervioso', 'endocrino',
        'inmunológico', 'linfático', 'urinario', 'reproductor', 'musculoesquelético',
        
        # Pathology
        'infarto', 'isquemia', 'necrosis', 'inflamación', 'edema', 'tumor',
        'cáncer', 'metástasis', 'hemorragia', 'trombosis', 'embolia',
        'diabetes', 'hipertensión', 'arritmia', 'insuficiencia',
        'infección', 'bacteriano', 'viral', 'fúngico', 'parasitario',
        
        # Radiology
        'radiografía', 'tomografía', 'resonancia', 'ecografía', 'ultrasonido',
        'densitometría', 'angiografía', 'gammagrafía',
        
        # Lab/Procedures
        'biopsia', 'histología', 'citología', 'hemograma', 'electrocardiograma',
        'electroencefalograma', 'endoscopia', 'colonoscopia',
    }
    
    text_lower = text.lower()
    found_keywords = []
    
    for term in MEDICAL_TERMS:
        if term in text_lower:
            found_keywords.append(term)
    
    return found_keywords


def enrich_justification_with_images(
    justification_text: str,
    question_text: str = ""
) -> Dict[str, Any]:
    """
    Analyze justification/question text and find relevant medical images.
    Returns A2UI-compatible components for rendering.
    
    Args:
        justification_text: The justification content
        question_text: The exam question text
    
    Returns:
        Dict with original text and a2ui_components for images
    """
    # Combine texts for keyword extraction
    combined_text = f"{question_text} {justification_text}"
    keywords = extract_medical_keywords(combined_text)
    
    if not keywords:
        return {
            "text": justification_text,
            "a2ui_components": [],
            "keywords_detected": []
        }
    
    # Search for matching images
    images = search_images_by_keywords(keywords, limit=3)
    
    # Convert to A2UI-style components
    a2ui_components = []
    for img in images:
        # Use relative URL for the frontend
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
        "text": justification_text,
        "a2ui_components": a2ui_components,
        "keywords_detected": keywords,
        "images_found": len(images)
    }


def list_all_images() -> List[Dict[str, Any]]:
    """List all medical images in the database."""
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, filename, keywords, category, title, description, created_at
                FROM medical_images
                ORDER BY created_at DESC
            """)
            results = cur.fetchall()
        return [dict(r) for r in results] if results else []
    except Exception as e:
        logger.error(f"❌ Error listing medical images: {e}")
        return []


def get_image_by_id(image_id: int) -> Optional[Dict[str, Any]]:
    """Get a specific image by ID."""
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, filename, filepath, keywords, category, title, description
                FROM medical_images
                WHERE id = %s
            """, (image_id,))
            result = cur.fetchone()
        return dict(result) if result else None
    except Exception as e:
        logger.error(f"❌ Error getting image {image_id}: {e}")
        return None


def update_medical_image(
    image_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    category: Optional[str] = None,
    keywords: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Update a medical image's metadata.
    
    If keywords change, deletes old embeddings from keyword_embeddings 
    and regenerates them via sync.
    
    Args:
        image_id: The ID of the image to update
        title: New title (None = no change)
        description: New description (None = no change)
        category: New category (None = no change)
        keywords: New keywords list (None = no change)
    
    Returns:
        Dict with success, updated fields, and embeddings_regenerated flag
    """
    try:
        # Get current image data
        current = get_image_by_id(image_id)
        if not current:
            return {"success": False, "error": "Image not found"}
        
        # Build update query dynamically
        updates = []
        params = []
        keywords_changed = False
        old_keywords = current.get("keywords", [])
        
        if title is not None and title != current.get("title", ""):
            updates.append("title = %s")
            params.append(title)
        
        if description is not None and description != current.get("description", ""):
            updates.append("description = %s")
            params.append(description)
        
        if category is not None and category != current.get("category", ""):
            updates.append("category = %s")
            params.append(category)
        
        if keywords is not None:
            normalized = [kw.lower().strip() for kw in keywords if kw.strip()]
            if sorted(normalized) != sorted(old_keywords):
                updates.append("keywords = %s")
                params.append(normalized)
                keywords_changed = True
        
        if not updates:
            return {"success": True, "message": "No changes", "embeddings_regenerated": False}
        
        # Execute update
        params.append(image_id)
        with get_cursor() as cur:
            cur.execute(
                f"UPDATE medical_images SET {', '.join(updates)} WHERE id = %s",
                params
            )
        
        logger.info(f"✅ Updated medical image {image_id}: {', '.join(updates)}")
        
        # If keywords changed, regenerate embeddings
        embeddings_regenerated = False
        if keywords_changed:
            try:
                # Delete old keyword embeddings for keywords only used by this image
                with get_cursor() as cur:
                    for kw in old_keywords:
                        # Check if this keyword is used by other images
                        cur.execute("""
                            SELECT COUNT(*) FROM medical_images 
                            WHERE id != %s AND %s = ANY(keywords)
                        """, (image_id, kw))
                        count = cur.fetchone()[0]
                        if count == 0:
                            # Only this image used this keyword — delete its embedding
                            cur.execute(
                                "DELETE FROM keyword_embeddings WHERE keyword = %s", 
                                (kw,)
                            )
                            logger.info(f"🗑️ Deleted orphaned embedding for keyword: {kw}")
                    
                    # Also delete embeddings that reference this image
                    # and update image_ids arrays for shared keywords
                    for kw in old_keywords:
                        cur.execute("""
                            UPDATE keyword_embeddings 
                            SET image_ids = array_remove(image_ids, %s)
                            WHERE keyword = %s AND %s = ANY(image_ids)
                        """, (image_id, kw, image_id))
                
                # Resync to add new keyword embeddings
                from servers.keyword_rag_service import sync_keywords_to_vector_store
                synced = sync_keywords_to_vector_store()
                embeddings_regenerated = True
                logger.info(f"🔄 Regenerated embeddings after keyword update: {synced} new keywords synced")
                
            except Exception as e:
                logger.error(f"⚠️ Error regenerating embeddings: {e}")
                # Don't fail the whole update just because embeddings failed
        
        return {
            "success": True,
            "image_id": image_id,
            "keywords_changed": keywords_changed,
            "embeddings_regenerated": embeddings_regenerated
        }
        
    except Exception as e:
        logger.error(f"❌ Error updating medical image {image_id}: {e}")
        return {"success": False, "error": str(e)}


def delete_image(image_id: int) -> bool:
    """Delete a medical image by ID."""
    try:
        img = get_image_by_id(image_id)
        if not img:
            return False
        
        # Delete file
        filepath = Path(img['filepath'])
        if filepath.exists():
            filepath.unlink()
        
        # Delete from database
        with get_cursor() as cur:
            cur.execute("DELETE FROM medical_images WHERE id = %s", (image_id,))
        
        logger.info(f"🗑️ Deleted medical image {image_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Error deleting image {image_id}: {e}")
        return False


# Initialize table on module load
try:
    init_medical_images_table()
except Exception as e:
    logger.warning(f"Could not initialize medical_images table: {e}")
