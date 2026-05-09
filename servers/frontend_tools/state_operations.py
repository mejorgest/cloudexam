"""
State Operations - Operaciones determinísticas sobre estados.

Estas funciones NO usan LLM. Son operaciones precisas y deterministas
sobre el agent_state.json.

Uso:
    from servers.frontend_tools.state_operations import delete_lines, add_text
    
    # Eliminar líneas 10-20
    result = await delete_lines("mi_documento", 10, 20)
    
    # Añadir texto al final
    result = await add_text("mi_documento", "Nuevo párrafo", "final")
"""

import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


async def delete_lines(key: str, start_line: int, end_line: int) -> str:
    """
    Elimina líneas específicas de un estado/documento.
    
    Esta es una operación DETERMINÍSTICA - no usa LLM, elimina exactamente
    las líneas especificadas.
    
    Args:
        key: La clave del estado a modificar
        start_line: Línea inicial a eliminar (1-indexed, inclusive)
        end_line: Línea final a eliminar (1-indexed, inclusive)
    
    Returns:
        Mensaje de confirmación o error
    
    Example:
        # Eliminar líneas 10 a 20
        result = await delete_lines("mi_documento", 10, 20)
    """
    try:
        from servers.filesystem_service.file_operations import load_state, save_state
        
        # 1. Obtener contenido actual
        current_content = load_state(key)
        if not current_content:
            return f"❌ Error: No existe el estado '{key}'"
        
        # 2. Dividir en líneas
        lines = current_content.split('\n')
        total_lines = len(lines)
        
        # 3. Validar rangos
        if start_line < 1 or end_line < 1:
            return f"❌ Error: Las líneas deben ser >= 1"
        if start_line > end_line:
            return f"❌ Error: start_line ({start_line}) debe ser <= end_line ({end_line})"
        if start_line > total_lines:
            return f"❌ Error: start_line ({start_line}) excede el total de líneas ({total_lines})"
        
        # Ajustar end_line si excede el total
        end_line = min(end_line, total_lines)
        
        # Convertir a 0-indexed
        start_idx = start_line - 1
        end_idx = end_line  # exclusive para slicing
        
        # 4. Eliminar las líneas
        deleted_count = end_idx - start_idx
        new_lines = lines[:start_idx] + lines[end_idx:]
        new_content = '\n'.join(new_lines)
        
        # 5. Guardar
        save_state(key, new_content)
        
        logger.info(f"🗑️ Eliminadas {deleted_count} líneas de '{key}' ({start_line}-{end_line})")
        
        return f"""✅ Líneas {start_line}-{end_line} eliminadas de '{key}'

🗑️ {deleted_count} líneas eliminadas
📊 Documento ahora tiene {len(new_lines)} líneas"""
        
    except Exception as e:
        logger.error(f"Error en delete_lines: {e}")
        return f"❌ Error eliminando líneas: {str(e)}"


async def add_text(key: str, text: str, position: str = "final") -> str:
    """
    Añade texto a un estado/documento en una posición específica.
    
    Esta es una operación DETERMINÍSTICA - no usa LLM.
    
    Args:
        key: La clave del estado a modificar
        text: El texto a añadir
        position: Dónde añadir el texto:
            - "inicio": Al principio del documento
            - "final": Al final del documento (por defecto)
            - "linea:N": Después de la línea N (ej: "linea:10")
    
    Returns:
        Mensaje de confirmación o error
    
    Example:
        # Añadir texto al final
        result = await add_text("mi_documento", "Este es el nuevo texto")
        
        # Añadir al inicio
        result = await add_text("mi_documento", "# Título", "inicio")
        
        # Añadir después de la línea 5
        result = await add_text("mi_documento", "Nuevo párrafo", "linea:5")
    """
    try:
        from servers.filesystem_service.file_operations import load_state, save_state
        
        # 1. Obtener contenido actual (puede no existir)
        current_content = load_state(key)
        
        if not current_content:
            # Si no existe, crear nuevo con el texto proporcionado
            save_state(key, text)
            lines_added = len(text.split('\n'))
            logger.info(f"📝 Nuevo estado '{key}' creado con {lines_added} líneas")
            return f"""✅ Nuevo estado '{key}' creado

📝 {lines_added} líneas añadidas"""
        
        # 2. Dividir en líneas
        lines = current_content.split('\n')
        text_lines = text.split('\n')
        
        # 3. Insertar según posición
        if position == "inicio":
            new_lines = text_lines + ['', '---', ''] + lines
            insert_desc = "al inicio"
        elif position == "final":
            new_lines = lines + ['', '---', ''] + text_lines
            insert_desc = "al final"
        elif position.startswith("linea:"):
            try:
                target_line = int(position.split(":")[1])
                target_idx = max(0, min(target_line, len(lines)))
                new_lines = lines[:target_idx] + ['', '---', ''] + text_lines + ['', '---', ''] + lines[target_idx:]
                insert_desc = f"después de la línea {target_line}"
            except ValueError:
                return f"❌ Error: Formato inválido. Usa 'linea:N' donde N es un número"
        else:
            return f"❌ Error: position debe ser 'inicio', 'final', o 'linea:N'"
        
        # 4. Guardar el resultado
        new_content = '\n'.join(new_lines)
        save_state(key, new_content)
        
        lines_added = len(text_lines)
        logger.info(f"📝 Texto añadido en '{key}': {lines_added} líneas {insert_desc}")
        
        return f"""✅ Texto añadido exitosamente a '{key}'

📝 {lines_added} líneas añadidas {insert_desc}
📊 Documento ahora tiene {len(new_lines)} líneas"""
        
    except Exception as e:
        logger.error(f"Error en add_text: {e}")
        return f"❌ Error añadiendo texto: {str(e)}"


async def relocate_text(key: str, start_line: int, end_line: int, target_position: str = "inicio") -> str:
    """
    Mueve un bloque de texto de una posición a otra dentro de un estado.
    
    Esta es una operación DETERMINÍSTICA - no usa LLM.
    
    Args:
        key: La clave del estado a modificar
        start_line: Línea inicial del texto a mover (1-indexed, inclusive)
        end_line: Línea final del texto a mover (1-indexed, inclusive)
        target_position: Dónde mover el texto:
            - "inicio": Al principio del documento
            - "final": Al final del documento
            - "linea:N": Después de la línea N
    
    Returns:
        Mensaje de confirmación o error
    
    Example:
        # Mover líneas 44-71 al inicio
        result = await relocate_text("mi_documento", 44, 71, "inicio")
        
        # Mover líneas 10-20 después de la línea 5
        result = await relocate_text("mi_documento", 10, 20, "linea:5")
    """
    try:
        from servers.filesystem_service.file_operations import load_state, save_state
        
        # 1. Obtener contenido actual
        current_content = load_state(key)
        if not current_content:
            return f"❌ Error: No existe el estado '{key}'"
        
        # 2. Dividir en líneas
        lines = current_content.split('\n')
        total_lines = len(lines)
        
        # 3. Validar rangos
        if start_line < 1 or end_line < 1:
            return f"❌ Error: Las líneas deben ser >= 1"
        if start_line > end_line:
            return f"❌ Error: start_line ({start_line}) debe ser <= end_line ({end_line})"
        if end_line > total_lines:
            return f"❌ Error: end_line ({end_line}) excede el total de líneas ({total_lines})"
        
        # Convertir a 0-indexed
        start_idx = start_line - 1
        end_idx = end_line  # exclusive para slicing
        
        # 4. Extraer el bloque a mover
        block_to_move = lines[start_idx:end_idx]
        
        # 5. Remover el bloque de su posición original
        remaining_lines = lines[:start_idx] + lines[end_idx:]
        
        # 6. Insertar en la nueva posición
        if target_position == "inicio":
            new_lines = block_to_move + ['', '---', ''] + remaining_lines
            insert_desc = "al inicio"
        elif target_position == "final":
            new_lines = remaining_lines + ['', '---', ''] + block_to_move
            insert_desc = "al final"
        elif target_position.startswith("linea:"):
            try:
                target_line = int(target_position.split(":")[1])
                # Ajustar si el target está después del bloque que removimos
                if target_line > start_line:
                    target_line -= (end_line - start_line + 1)
                target_idx = max(0, min(target_line, len(remaining_lines)))
                new_lines = remaining_lines[:target_idx] + ['', '---', ''] + block_to_move + ['', '---', ''] + remaining_lines[target_idx:]
                insert_desc = f"después de la línea {target_position.split(':')[1]}"
            except ValueError:
                return f"❌ Error: Formato inválido. Usa 'linea:N' donde N es un número"
        else:
            return f"❌ Error: target_position debe ser 'inicio', 'final', o 'linea:N'"
        
        # 7. Guardar el resultado
        new_content = '\n'.join(new_lines)
        save_state(key, new_content)
        
        logger.info(f"📍 Texto reubicado en '{key}': líneas {start_line}-{end_line} movidas {insert_desc}")
        
        return f"""✅ Texto reubicado exitosamente en '{key}'

📍 Líneas {start_line}-{end_line} ({end_line - start_line + 1} líneas) movidas {insert_desc}
📊 Documento ahora tiene {len(new_lines)} líneas"""
        
    except Exception as e:
        logger.error(f"Error en relocate_text: {e}")
        return f"❌ Error reubicando texto: {str(e)}"


async def get_state(key: str) -> str:
    """
    Obtiene el contenido de un estado.
    
    Args:
        key: La clave del estado a obtener
    
    Returns:
        El contenido del estado o mensaje de error
    
    Example:
        content = await get_state("mi_documento")
        print(content)
    """
    try:
        from servers.filesystem_service.file_operations import load_state
        
        content = load_state(key)
        if content is None:
            return f"❌ No existe el estado '{key}'"
        
        return content if isinstance(content, str) else str(content)
        
    except Exception as e:
        logger.error(f"Error en get_state: {e}")
        return f"❌ Error obteniendo estado: {str(e)}"


async def save_state(key: str, value: str) -> str:
    """
    Guarda contenido en un estado.
    
    Args:
        key: La clave del estado
        value: El contenido a guardar
    
    Returns:
        Mensaje de confirmación
    
    Example:
        result = await save_state("mi_documento", "# Nuevo contenido\\n\\nTexto...")
    """
    try:
        from servers.filesystem_service.file_operations import save_state as fs_save
        
        result = fs_save(key, value)
        logger.info(f"💾 Estado '{key}' guardado ({len(value)} chars)")
        return result
        
    except Exception as e:
        logger.error(f"Error en save_state: {e}")
        return f"❌ Error guardando estado: {str(e)}"


async def list_states() -> Dict[str, Any]:
    """
    Lista todos los estados disponibles.
    
    Returns:
        Diccionario con los estados y sus metadatos
    
    Example:
        states = await list_states()
        for key, info in states.items():
            print(f"{key}: {info['lines']} líneas")
    """
    try:
        from servers.filesystem_service.file_operations import get_full_state
        
        full_state = get_full_state()
        if not full_state:
            return {}
        
        result = {}
        for key, value in full_state.items():
            if key.startswith('_'):
                continue
            
            if isinstance(value, str):
                result[key] = {
                    "type": "string",
                    "lines": value.count('\n') + 1,
                    "chars": len(value),
                    "preview": value[:100] + "..." if len(value) > 100 else value
                }
            else:
                result[key] = {
                    "type": type(value).__name__,
                    "preview": str(value)[:100]
                }
        
        return result
        
    except Exception as e:
        logger.error(f"Error en list_states: {e}")
        return {"error": str(e)}


async def create_state(name: str, title: str = None, content: str = "", template: str = "blank") -> str:
    """
    Crea un nuevo estado/documento.
    
    Args:
        name: Nombre del estado (clave)
        title: Título opcional para el documento
        content: Contenido inicial opcional
        template: Plantilla a usar: 'blank', 'report', 'notes', 'research'
    
    Returns:
        Mensaje de confirmación
    
    Example:
        result = await create_state("mi_reporte", title="Reporte Mensual", template="report")
    """
    from datetime import datetime
    try:
        from servers.filesystem_service.file_operations import save_state as fs_save, load_state as fs_load
        
        # Verificar si ya existe
        existing = fs_load(name)
        if existing:
            return f"⚠️ El estado '{name}' ya existe. Usa save_state() para modificarlo."
        
        # Generar contenido según la plantilla
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        if template == "report":
            doc_content = f"# {title or name.replace('_', ' ').title()}\n\n**Fecha:** {timestamp}\n\n## Resumen\n\n## Detalles\n\n## Conclusiones\n"
        elif template == "notes":
            doc_content = f"# 📝 {title or name.replace('_', ' ').title()}\n\n**Creado:** {timestamp}\n\n---\n\n"
        elif template == "research":
            doc_content = f"# 🔍 Investigación: {title or name.replace('_', ' ').title()}\n\n**Fecha:** {timestamp}\n\n## Objetivo\n\n## Fuentes\n\n## Hallazgos\n\n## Próximos pasos\n"
        else:  # blank
            if title:
                doc_content = f"# {title}\n\n"
            else:
                doc_content = ""
        
        # Agregar contenido inicial si se proporcionó
        if content:
            doc_content += content
        
        # Guardar el nuevo estado
        fs_save(name, doc_content)
        
        logger.info(f"✨ Estado '{name}' creado (template={template}, {len(doc_content)} chars)")
        
        return f"✅ Estado '{name}' creado exitosamente ({len(doc_content)} caracteres)"
        
    except Exception as e:
        logger.error(f"Error en create_state: {e}")
        return f"❌ Error creando estado: {str(e)}"
