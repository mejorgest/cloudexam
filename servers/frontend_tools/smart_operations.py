"""
Smart Operations - Operaciones inteligentes usando LLM.

Estas funciones usan LLM para procesamiento de lenguaje natural.

Uso:
    from servers.frontend_tools.smart_operations import smart_edit, smart_resume
    
    # Editar con instrucciones naturales
    result = await smart_edit("mi_documento", "traduce al español")
    
    # Resumir texto
    result = await smart_resume("texto largo...", state_key="mi_doc", start_line=10, end_line=20)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def smart_edit(key: str, instruction: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """
    Edita inteligentemente un estado basándose en instrucciones en lenguaje natural.
    
    Usa LLM para interpretar la instrucción y generar los cambios apropiados.
    
    Args:
        key: La clave del estado a editar
        instruction: Instrucción en lenguaje natural (ej: "traduce al español")
        start_line: Línea inicial del fragmento a editar (opcional, 1-indexed)
        end_line: Línea final del fragmento a editar (opcional, 1-indexed)
    
    Returns:
        Mensaje de confirmación o error
    
    Example:
        # Editar todo el documento
        result = await smart_edit("protocolo_yuca", "cambia el pH de 6.5 a 7.0")
        
        # Editar solo un fragmento
        result = await smart_edit("protocolo_yuca", "traduce al español", start_line=9, end_line=12)
    """
    try:
        # Reutilizar la implementación existente de smart_tools
        from servers.smart_tools.smart_edit import smart_edit_state
        return await smart_edit_state(key, instruction, start_line=start_line, end_line=end_line)
        
    except Exception as e:
        logger.error(f"Error en smart_edit: {e}")
        return f"❌ Error editando estado: {str(e)}"


async def smart_resume(
    text: str, 
    state_key: Optional[str] = None, 
    start_line: Optional[int] = None, 
    end_line: Optional[int] = None, 
    style: str = "conciso"
) -> str:
    """
    Resume texto usando LLM.
    
    Si se proporciona state_key y líneas, reemplaza el texto original con el resumen.
    Si no, solo retorna el resumen.
    
    Args:
        text: El texto a resumir
        state_key: Estado donde está el texto (para reemplazo in-place)
        start_line: Línea inicial del texto a reemplazar (1-indexed)
        end_line: Línea final del texto a reemplazar (1-indexed)
        style: Estilo de resumen: 'conciso', 'detallado', 'bullets'
    
    Returns:
        El resumen (y confirmación si se reemplazó)
    
    Example:
        # Solo obtener resumen
        resumen = await smart_resume("texto muy largo...")
        
        # Reemplazar texto en documento con su resumen
        result = await smart_resume(
            text="texto a resumir...",
            state_key="mi_documento",
            start_line=50,
            end_line=100,
            style="bullets"
        )
    """
    try:
        # Reutilizar la implementación existente de smart_tools
        from servers.smart_tools.smart_edit import smart_resume as _smart_resume
        return await _smart_resume(
            text, 
            state_key=state_key, 
            start_line=start_line, 
            end_line=end_line, 
            style=style
        )
        
    except Exception as e:
        logger.error(f"Error en smart_resume: {e}")
        return f"❌ Error resumiendo texto: {str(e)}"


async def smart_enrich(key: str, instruction: str) -> str:
    """
    Enriquece un documento ejecutando herramientas e insertando resultados.
    
    Usa LLM para determinar qué herramientas llamar basándose en la instrucción.
    
    Args:
        key: La clave del estado a enriquecer
        instruction: Qué agregar al documento (ej: "agregar cotización para E-401")
    
    Returns:
        Mensaje de confirmación o error
    
    Example:
        result = await smart_enrich("propuesta_cliente", "agregar información del producto E-401")
    """
    try:
        # Reutilizar la implementación existente de smart_tools
        from servers.smart_tools.smart_enrich import smart_enrich_document
        return await smart_enrich_document(key, instruction)
        
    except Exception as e:
        logger.error(f"Error en smart_enrich: {e}")
        return f"❌ Error enriqueciendo documento: {str(e)}"


async def correct_text(key: str, old_text: str, new_text: str) -> str:
    """
    Corrige texto exacto en un estado.
    
    Esta es una operación DETERMINÍSTICA - busca y reemplaza texto exacto.
    
    Args:
        key: La clave del estado a editar
        old_text: Texto exacto a buscar y reemplazar
        new_text: Texto de reemplazo
    
    Returns:
        Mensaje de confirmación o error
    
    Example:
        result = await correct_text("mi_documento", "text incorrecto", "texto correcto")
    """
    try:
        from servers.filesystem_service.file_operations import load_state, save_state
        
        content = load_state(key)
        if not content:
            return f"❌ No existe el estado '{key}'"
        
        if old_text not in content:
            return f"❌ No se encontró el texto a reemplazar en '{key}'"
        
        # Contar ocurrencias
        count = content.count(old_text)
        
        # Reemplazar
        new_content = content.replace(old_text, new_text)
        save_state(key, new_content)
        
        logger.info(f"✏️ Texto corregido en '{key}': {count} ocurrencia(s)")
        
        return f"✅ Texto corregido en '{key}' ({count} ocurrencia{'s' if count > 1 else ''})"
        
    except Exception as e:
        logger.error(f"Error en correct_text: {e}")
        return f"❌ Error corrigiendo texto: {str(e)}"


async def translate_fragment(
    key: str, 
    start_line: int, 
    end_line: int, 
    target_language: str = "español"
) -> str:
    """
    Traduce un fragmento específico de un documento.
    
    Args:
        key: La clave del estado a editar
        start_line: Línea inicial del fragmento (1-indexed)
        end_line: Línea final del fragmento (1-indexed)
        target_language: Idioma destino (default: español)
    
    Returns:
        Mensaje de confirmación o error
    
    Example:
        result = await translate_fragment("mi_documento", 10, 25, "inglés")
    """
    instruction = f"Traduce este fragmento a {target_language}. Mantén la estructura y formato."
    return await smart_edit(key, instruction, start_line=start_line, end_line=end_line)


async def summarize_fragment(
    key: str,
    start_line: int,
    end_line: int,
    style: str = "conciso"
) -> str:
    """
    Resume un fragmento específico de un documento, reemplazándolo in-place.
    
    Args:
        key: La clave del estado
        start_line: Línea inicial del fragmento (1-indexed)
        end_line: Línea final del fragmento (1-indexed)
        style: Estilo de resumen: 'conciso', 'detallado', 'bullets'
    
    Returns:
        Mensaje de confirmación con el resumen
    
    Example:
        result = await summarize_fragment("mi_documento", 50, 100, "bullets")
    """
    try:
        from servers.filesystem_service.file_operations import load_state
        
        content = load_state(key)
        if not content:
            return f"❌ No existe el estado '{key}'"
        
        lines = content.split('\n')
        
        # Validar rangos
        if start_line < 1 or end_line > len(lines):
            return f"❌ Rango inválido: {start_line}-{end_line} (doc tiene {len(lines)} líneas)"
        
        # Extraer fragmento
        start_idx = start_line - 1
        end_idx = end_line
        fragment = '\n'.join(lines[start_idx:end_idx])
        
        # Resumir y reemplazar
        return await smart_resume(
            text=fragment,
            state_key=key,
            start_line=start_line,
            end_line=end_line,
            style=style
        )
        
    except Exception as e:
        logger.error(f"Error en summarize_fragment: {e}")
        return f"❌ Error resumiendo fragmento: {str(e)}"
