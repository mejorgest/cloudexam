"""
Smart Enrich Tool - Enriquece documentos llamando otras herramientas e insertando resultados.

Uso:
    from servers.smart_tools.smart_enrich import smart_enrich_document
    
    result = await smart_enrich_document(
        "protocolo_tanques", 
        "agrega una cotización para Juan Perez por 1 galón de Environoc 301"
    )
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ============== STRUCTURED OUTPUT MODEL ==============

class EnrichDecision(BaseModel):
    """Decisión estructurada sobre cómo enriquecer un documento"""
    tool_name: str = Field(description="Nombre de la herramienta a usar: 'generar_cotizacion', 'consultar_protocolo_agricola', 'consultar_protocolo_aguas', 'custom_text'")
    tool_args: str = Field(description="Argumentos para la herramienta (ej: descripción de cotización)")
    insert_position: str = Field(description="Dónde insertar el resultado: 'inicio', 'final', 'despues_de:<texto>'")
    reasoning: str = Field(description="Explicación de por qué se eligió esta herramienta y posición")


def _get_decision_llm():
    """Obtiene el LLM configurado para tomar decisiones"""
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    return llm.with_structured_output(EnrichDecision)


async def smart_enrich_document(key: str, instruction: str) -> str:
    """
    Enriquece un documento/estado existente ejecutando otra herramienta e insertando su resultado.
    
    El LLM decide:
    1. Qué herramienta usar (cotización, protocolo agrícola, protocolo aguas, etc.)
    2. Qué argumentos pasarle
    3. Dónde insertar el resultado en el documento
    
    Args:
        key: La clave del estado/documento a enriquecer
        instruction: Instrucción de qué agregar (ej: "agrega cotización para Cliente X por producto Y")
    
    Returns:
        Mensaje de confirmación con detalles
    
    Example:
        result = await smart_enrich_document(
            "protocolo_tanques_septicos",
            "incluye una cotización para Juan Perez de Los Pollitos S.A. por 1 galón de Environoc 301"
        )
    """
    try:
        from servers.filesystem_service.file_operations import load_state, save_state
        
        # 1. Obtener contenido actual
        current_content = load_state(key)
        if not current_content:
            return f"❌ Error: No existe el estado '{key}'"
        
        logger.info(f"📄 Enriqueciendo documento '{key}' - Instrucción: {instruction}")
        
        # 2. Usar LLM para decidir qué herramienta usar
        decision_llm = _get_decision_llm()
        
        prompt = f"""Analiza la siguiente instrucción y decide cómo enriquecer el documento.

INSTRUCCIÓN DEL USUARIO: {instruction}

DOCUMENTO ACTUAL (primeras 500 chars):
{current_content[:500]}...

HERRAMIENTAS DISPONIBLES:
- custom_text: Para agregar texto personalizado

Decide qué herramienta usar y con qué argumentos."""

        decision = decision_llm.invoke(prompt)

        logger.info(f"🧠 Decisión: {decision.tool_name} con args: {decision.tool_args}")
        logger.info(f"📍 Posición de inserción: {decision.insert_position}")

        # 3. Ejecutar la herramienta seleccionada
        tool_result = ""

        if decision.tool_name == "custom_text":
            tool_result = decision.tool_args
            
        else:
            return f"❌ Herramienta no reconocida: {decision.tool_name}"
        
        if not tool_result:
            return f"❌ La herramienta {decision.tool_name} no devolvió resultados"
        
        logger.info(f"📤 Resultado de herramienta obtenido ({len(str(tool_result))} chars)")
        
        # 4. Insertar el resultado en la posición indicada
        separator = "\n\n---\n\n"
        
        if decision.insert_position == "inicio":
            new_content = f"{tool_result}{separator}{current_content}"
        elif decision.insert_position == "final":
            new_content = f"{current_content}{separator}{tool_result}"
        elif decision.insert_position.startswith("despues_de:"):
            marker = decision.insert_position.replace("despues_de:", "").strip()
            if marker in current_content:
                new_content = current_content.replace(marker, f"{marker}{separator}{tool_result}")
            else:
                # Si no encuentra el marcador, insertar al final
                new_content = f"{current_content}{separator}{tool_result}"
        else:
            new_content = f"{current_content}{separator}{tool_result}"
        
        # 5. Guardar el documento enriquecido
        save_state(key, new_content)
        
        return f"""✅ Documento '{key}' enriquecido exitosamente

📝 Razonamiento: {decision.reasoning}
🔧 Herramienta usada: {decision.tool_name}
📍 Posición: {decision.insert_position}
📊 Contenido agregado: {len(str(tool_result))} caracteres"""
        
    except Exception as e:
        logger.error(f"Error en smart_enrich_document: {e}", exc_info=True)
        return f"❌ Error enriqueciendo documento: {str(e)}"

