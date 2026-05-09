"""
Entity Schema Module

Modelos Pydantic para representar entidades extraídas.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


EntityType = Literal[
    "bacteria", 
    "virus",
    "fungus",
    "parasite",
    "culture_medium", 
    "antibiotic",
    "disease", 
    "lab_technique",
    "virulence_factor",
    "clinical_sample",
    "scientist"
]


class Entity(BaseModel):
    """Una entidad individual extraída del texto."""
    
    name: str = Field(
        ...,
        description="Nombre de la entidad (e.g., 'Escherichia coli', 'Penicillin')."
    )
    entity_type: EntityType = Field(
        ...,
        description="Tipo de entidad: bacteria, culture_medium, disease, treatment, other."
    )
    context: str = Field(
        ...,
        description="Fragmento de texto donde aparece la entidad."
    )
    confidence: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="Nivel de confianza de la extracción (0.0 a 1.0)."
    )


class ChunkEntities(BaseModel):
    """Entidades extraídas de un chunk específico."""
    
    chunk_id: int = Field(
        ...,
        description="ID del chunk del que se extrajeron las entidades."
    )
    entities: list[Entity] = Field(
        default_factory=list,
        description="Lista de entidades extraídas del chunk."
    )


class ExtractionResult(BaseModel):
    """Resultado completo de la extracción de entidades."""
    
    source_file: Optional[str] = Field(
        default=None,
        description="Archivo fuente del texto procesado."
    )
    total_chunks: int = Field(
        ...,
        description="Número total de chunks procesados."
    )
    entities_by_chunk: list[ChunkEntities] = Field(
        default_factory=list,
        description="Entidades agrupadas por chunk."
    )
    
    @property
    def summary(self) -> dict[str, int]:
        """Genera un resumen con conteo por tipo de entidad."""
        counts = {
            "bacteria": 0,
            "virus": 0,
            "fungus": 0,
            "parasite": 0,
            "culture_medium": 0,
            "antibiotic": 0,
            "disease": 0,
            "lab_technique": 0,
            "virulence_factor": 0,
            "clinical_sample": 0,
            "scientist": 0
        }
        
        for chunk_entities in self.entities_by_chunk:
            for entity in chunk_entities.entities:
                if entity.entity_type in counts:
                    counts[entity.entity_type] += 1
        
        return counts
    
    @property
    def all_entities(self) -> list[Entity]:
        """Devuelve todas las entidades como lista plana."""
        entities = []
        for chunk_entities in self.entities_by_chunk:
            entities.extend(chunk_entities.entities)
        return entities
    
    @property
    def unique_entities(self) -> dict[str, list[str]]:
        """Devuelve entidades únicas agrupadas por tipo."""
        unique: dict[str, set[str]] = {
            "bacteria": set(),
            "virus": set(),
            "fungus": set(),
            "parasite": set(),
            "culture_medium": set(),
            "antibiotic": set(),
            "disease": set(),
            "lab_technique": set(),
            "virulence_factor": set(),
            "clinical_sample": set(),
            "scientist": set()
        }
        
        for entity in self.all_entities:
            unique[entity.entity_type].add(entity.name)
        
        return {k: sorted(list(v)) for k, v in unique.items()}


# JSON Schema para respuesta estructurada del LLM
ENTITY_LIST_JSON_SCHEMA = {
    "name": "entityList",
    "schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "entity_type": {
                            "type": "string",
                            "enum": ["bacteria", "virus", "fungus", "parasite", "culture_medium", "antibiotic", "disease", "lab_technique", "virulence_factor", "clinical_sample", "scientist"]
                        },
                        "context": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1}
                    },
                    "required": ["name", "entity_type", "context"]
                }
            }
        },
        "required": ["entities"]
    },
    "strict": True
}
