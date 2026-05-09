"""
Smart Edit Tools - Edición inteligente de estados y archivos usando LLM con structured output.

Uso:
    from servers.smart_tools.smart_edit import smart_edit_state, smart_edit_file
    
    result = await smart_edit_state("protocolo_yuca", "cambia el pH de 6.5 a 7.0")
    result = await smart_edit_file("documento.txt", "corrige los errores ortográficos")
"""

import logging
import os
from typing import Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ============== STRUCTURED OUTPUT MODEL ==============

class TextEditCommand(BaseModel):
    """Comando estructurado para editar texto"""
    old_text: str = Field(description="El fragmento EXACTO del texto original que se debe reemplazar. Debe coincidir perfectamente con el contenido actual.")
    new_text: str = Field(description="El nuevo texto que reemplazará al fragmento anterior.")
    reasoning: str = Field(description="Explicación breve de por qué se hace este cambio.")


def _get_edit_llm():
    """Obtiene el LLM configurado para edición con structured output"""
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    return llm.with_structured_output(TextEditCommand)


async def smart_edit_state(key: str, instruction: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """
    Edita inteligentemente el contenido de un estado basándose en instrucciones en lenguaje natural.
    
    El LLM analiza el contenido actual y genera un comando de edición estructurado
    (old_text, new_text) que luego se aplica de forma precisa.
    
    Args:
        key: La clave del estado a editar
        instruction: Instrucción en lenguaje natural de qué cambiar (ej: "cambia el pH de 6.5 a 7.0")
        start_line: Línea inicial del fragmento a editar (opcional, 1-indexed)
        end_line: Línea final del fragmento a editar (opcional, 1-indexed)
    
    Returns:
        Mensaje de confirmación o error
    
    Example:
        result = await smart_edit_state("protocolo_yuca", "cambia el pH de 6.5 a 7.0")
        result = await smart_edit_state("protocolo_yuca", "traduce al español", start_line=9, end_line=12)
    """
    try:
        from servers.filesystem_service.file_operations import load_state, save_state
        
        # 1. Obtener contenido actual
        current_content = load_state(key)
        if not current_content:
            return f"❌ Error: No existe el estado '{key}'"
        
        # 2. Si hay rango de líneas, extraer solo ese fragmento para editar
        lines = current_content.split('\n')
        editing_fragment = False
        fragment_content = current_content
        
        if start_line is not None and end_line is not None:
            # Convertir a 0-indexed
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)
            fragment_content = '\n'.join(lines[start_idx:end_idx])
            editing_fragment = True
            logger.info(f"📍 Editando solo líneas {start_line}-{end_line} de '{key}'")
        
        # 3. Usar LLM con structured output para generar el comando de edición
        llm_structured = _get_edit_llm()
        
        if editing_fragment:
            prompt = f"""Eres un editor de texto preciso. Tu tarea es modificar SOLO el contenido entre las etiquetas <CONTENIDO_A_EDITAR>.

La instrucción del usuario es: "{instruction}"

<CONTENIDO_A_EDITAR>
{fragment_content}
</CONTENIDO_A_EDITAR>

REGLAS:
1. Si la instrucción pide TRANSFORMAR o CONVERTIR TODO, old_text debe ser TODO el contenido y new_text la versión completamente transformada.
2. Si la instrucción es un cambio específico, old_text solo incluye el fragmento que cambia.
3. El new_text debe tener la MISMA cantidad de contenido transformado que el old_text (no truncar).
4. ⚠️ TRADUCCIÓN: Si la instrucción es TRADUCIR, debes traducir LÍNEA POR LÍNEA preservando:
   - La MISMA cantidad de líneas
   - Los mismos encabezados (##, ###) traducidos
   - Los mismos separadores (---)
   - La misma estructura de párrafos
   - NO resumas ni condenses el contenido

Genera old_text (copia EXACTA del texto a cambiar) y new_text (versión modificada completa).
NO edites nada que no esté entre las etiquetas. NO incluyas las etiquetas en tu respuesta."""
        else:
            prompt = f"""Eres un editor de texto preciso. Tu tarea es modificar el contenido entre las etiquetas <DOCUMENTO>.

La instrucción del usuario es: "{instruction}"

<DOCUMENTO>
{current_content}
</DOCUMENTO>

REGLAS IMPORTANTES:
1. Si la instrucción pide TRANSFORMAR, CONVERTIR, o CAMBIAR TODO el documento (ej: "convierte a Python", "traduce todo", "reformatea"), entonces:
   - old_text debe ser TODO el contenido del documento (copia completa)
   - new_text debe ser la versión COMPLETAMENTE transformada

2. Si la instrucción pide un cambio ESPECÍFICO (ej: "cambia el pH de 6 a 7", "corrige el nombre"), entonces:
   - old_text debe ser SOLO el fragmento específico que cambia
   - new_text debe ser ese fragmento modificado

3. NUNCA dejes contenido sin transformar si el usuario pidió transformar TODO.
4. Si el documento tiene 35 líneas y el usuario pide transformarlo, new_text debe incluir las 35 líneas transformadas.

Genera old_text y new_text según estas reglas. NO incluyas las etiquetas en tu respuesta."""

        try:
            edit_command = llm_structured.invoke(prompt)
            
            # Validate response
            if not edit_command or not hasattr(edit_command, 'old_text') or not hasattr(edit_command, 'new_text'):
                return "❌ Error: El LLM no devolvió una respuesta válida. Intenta de nuevo."
            
            if not edit_command.old_text or not edit_command.new_text:
                return "❌ Error: El LLM devolvió respuesta vacía. Intenta con una instrucción más específica."
                
        except Exception as llm_error:
            error_msg = str(llm_error)
            if 'rate' in error_msg.lower() or 'limit' in error_msg.lower():
                return "❌ Error: Límite de API de OpenAI alcanzado. Espera un momento."
            elif 'Expecting value' in error_msg:
                return "❌ Error: Respuesta vacía de OpenAI. Puede ser problema temporal, intenta de nuevo."
            else:
                logger.error(f"LLM error in smart_edit: {llm_error}")
                return f"❌ Error del LLM: {error_msg[:150]}"
        
        logger.info(f"🧠 Smart Edit Command: {edit_command.reasoning}")
        logger.info(f"   Old: '{edit_command.old_text[:50]}...'")
        logger.info(f"   New: '{edit_command.new_text[:50]}...'")
        
        # 4. Aplicar la edición
        search_content = fragment_content if editing_fragment else current_content
        
        if edit_command.old_text not in search_content:
            # Intentar encontrar una coincidencia parcial
            logger.warning(f"⚠️ No se encontró coincidencia exacta. Buscando coincidencia parcial...")
            logger.info(f"   Buscando en: '{search_content[:100]}...'")
            logger.info(f"   old_text del LLM: '{edit_command.old_text[:100]}...'")
            
            search_lines = search_content.split('\n')
            old_lines = edit_command.old_text.split('\n')
            
            found_partial = False
            for i, line in enumerate(search_lines):
                if old_lines[0].strip() in line or line.strip() in old_lines[0].strip():
                    # Encontramos la primera línea, ahora reemplazamos TODO el fragmento
                    logger.info(f"   Coincidencia parcial encontrada en línea {i}: '{line[:50]}...'")
                    
                    if editing_fragment:
                        # Cuando editamos un fragmento y el LLM quiere borrarlo,
                        # reemplazamos TODO el fragmento con el new_text completo
                        # (no solo la primera línea)
                        new_content_text = edit_command.new_text.strip()
                        
                        # Si new_text está vacío o es solo espacios, el usuario quiere borrar
                        if not new_content_text:
                            # Borrar el fragmento completo - reemplazar con línea vacía o nada
                            new_lines = lines[:start_idx] + lines[end_idx:]
                        else:
                            # Reemplazar fragmento con nuevo contenido
                            new_lines = lines[:start_idx] + new_content_text.split('\n') + lines[end_idx:]
                        
                        new_content = '\n'.join(new_lines)
                    else:
                        # Sin fragmento específico, intentar reemplazar el old_text con new_text
                        new_content = current_content.replace(edit_command.old_text, edit_command.new_text, 1)
                        if new_content == current_content:
                            # Si no cambió, intentar reemplazar solo la línea encontrada
                            new_content = current_content.replace(line, edit_command.new_text.split('\n')[0], 1)
                    
                    save_state(key, new_content)
                    found_partial = True
                    return f"✅ Estado '{key}' editado (coincidencia parcial)\n📝 Cambio: {edit_command.reasoning}"
            
            if not found_partial:
                return f"❌ No se encontró el texto a reemplazar. El LLM generó:\nold_text: '{edit_command.old_text[:100]}...'"
        
        # 5. Realizar el reemplazo exacto
        if editing_fragment:
            # Reemplazar solo en el fragmento y reconstruir el documento completo
            new_fragment = fragment_content.replace(edit_command.old_text, edit_command.new_text, 1)
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)
            new_lines = lines[:start_idx] + new_fragment.split('\n') + lines[end_idx:]
            new_content = '\n'.join(new_lines)
        else:
            new_content = current_content.replace(edit_command.old_text, edit_command.new_text, 1)
        
        save_state(key, new_content)
        
        range_info = f" (líneas {start_line}-{end_line})" if editing_fragment else ""
        return f"✅ Estado '{key}'{range_info} editado exitosamente\n📝 Cambio: {edit_command.reasoning}"
        
    except Exception as e:
        logger.error(f"Error en smart_edit_state: {e}")
        return f"❌ Error editando estado: {str(e)}"


async def smart_edit_file(filename: str, instruction: str) -> str:
    """
    Carga un archivo del workspace a un estado temporal y lo edita.
    
    Flujo: Leer archivo → Crear estado temporal → Editar estado
    
    ⚠️ NO exporta automáticamente al archivo.
    El usuario debe usar export_state_to_file() para guardar los cambios
    de vuelta al archivo del workspace.
    
    Args:
        filename: Nombre del archivo a cargar y editar
        instruction: Instrucción en lenguaje natural de qué cambiar
    
    Returns:
        Mensaje de confirmación indicando que el estado fue creado/editado
    
    Example:
        result = await smart_edit_file("documento.txt", "corrige los errores ortográficos")
        # Luego: export_state_to_file("edit_documento") para guardar
    """
    try:
        from servers.filesystem_service.file_operations import read_file, save_state
        import os
        
        # 1. Obtener contenido del archivo
        try:
            file_content = read_file(filename)
        except FileNotFoundError:
            return f"❌ Error: Archivo '{filename}' no encontrado"
        
        # 2. Crear un state key basado en el nombre del archivo
        base_name = os.path.splitext(filename)[0]
        state_key = f"edit_{base_name}".replace(" ", "_").replace("-", "_").lower()
        
        # 3. Guardar el contenido del archivo como estado temporal
        save_state(state_key, file_content)
        logger.info(f"📂→🗄️ Archivo '{filename}' cargado a estado temporal '{state_key}'")
        
        # 4. Editar el estado usando smart_edit_state
        edit_result = await smart_edit_state(state_key, instruction)
        
        if "❌" in edit_result:
            return edit_result
        
        # 5. NO exportar automáticamente - solo informar del éxito
        return f"""✅ Archivo '{filename}' cargado a estado temporal '{state_key}' y editado.
{edit_result}
📋 El estado '{state_key}' contiene los cambios. El archivo original NO fue modificado."""
        
    except Exception as e:
        logger.error(f"Error en smart_edit_file: {e}")
        return f"❌ Error editando archivo: {str(e)}"


async def relocate_text(key: str, start_line: int, end_line: int, target_position: str = "inicio") -> str:
    """
    Mueve un bloque de texto de una posición a otra dentro de un estado.
    
    Esta es una operación DETERMINÍSTICA - no usa LLM, mueve exactamente
    las líneas especificadas a la posición indicada.
    
    Args:
        key: La clave del estado a modificar
        start_line: Línea inicial del texto a mover (1-indexed, inclusive)
        end_line: Línea final del texto a mover (1-indexed, inclusive)
        target_position: Dónde mover el texto:
            - "inicio": Al principio del documento
            - "final": Al final del documento
            - "linea:N": Después de la línea N (ej: "linea:10")
    
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


async def add_text(key: str, text: str, position: str = "final") -> str:
    """
    Añade texto a un estado/documento en una posición específica.
    
    Esta es una operación DETERMINÍSTICA - no usa LLM, añade exactamente
    el texto proporcionado en la posición indicada.
    
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


async def smart_resume(text: str, state_key: str = None, start_line: int = None, end_line: int = None, style: str = "conciso") -> str:
    """
    Resume texto y lo reemplaza directamente en el documento.
    
    Si se proporciona state_key y líneas, reemplaza el texto original con el resumen.
    Si no, solo retorna el resumen.
    
    Args:
        text: El texto a resumir
        state_key: Estado donde está el texto (para reemplazo in-place)
        start_line: Línea inicial del texto a reemplazar (1-indexed)
        end_line: Línea final del texto a reemplazar (1-indexed)
        style: Estilo: 'conciso', 'detallado', 'bullets'
    
    Returns:
        El resumen (y confirmación si se reemplazó)
    """
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.prompts import PromptTemplate
        
        if not text or len(text.strip()) < 20:
            return "❌ Error: Texto muy corto para resumir"
        
        # Prompts según estilo
        style_instructions = {
            "conciso": "Resume en 2-3 oraciones breves y directas.",
            "detallado": "Resume capturando los puntos principales con detalle.",
            "bullets": "Resume como lista de puntos clave usando guiones (-)."
        }
        
        instruction = style_instructions.get(style, style_instructions["conciso"])
        
        prompt = PromptTemplate(
            input_variables=["text", "instruction"],
            template="""Eres un experto en sintetizar información.

{instruction}

TEXTO:
{text}

RESUMEN:"""
        )
        
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        chain = prompt | llm
        
        response = chain.invoke({"text": text, "instruction": instruction})
        summary = response.content if hasattr(response, 'content') else str(response)
        
        logger.info(f"✅ Resumen: {len(text)} chars → {len(summary)} chars")
        logger.info(f"📊 SMART_RESUME DEBUG:")
        logger.info(f"   state_key={state_key}")
        logger.info(f"   start_line={start_line}, end_line={end_line}")
        logger.info(f"   style={style}")
        
        # Si se proporcionó state_key, guardar el resumen
        if state_key:
            from servers.filesystem_service.file_operations import load_state, save_state
            
            current = load_state(state_key)
            if not current:
                logger.error(f"❌ Estado '{state_key}' no existe")
                return f"❌ No existe el estado '{state_key}'"
            
            lines = current.split('\n')
            logger.info(f"   doc_total_lines={len(lines)}")
            
            # Si hay líneas específicas, reemplazar in-place
            if start_line and end_line:
                logger.info(f"   MODE: REEMPLAZAR líneas {start_line}-{end_line}")
                
                # Validar rangos
                if start_line < 1 or end_line > len(lines):
                    logger.error(f"❌ Rango inválido: {start_line}-{end_line} (doc tiene {len(lines)} líneas)")
                    return f"❌ Rango inválido: {start_line}-{end_line} (doc tiene {len(lines)} líneas)"
                
                # Reemplazar las líneas con el resumen
                start_idx = start_line - 1
                end_idx = end_line
                logger.info(f"   Reemplazando índices [{start_idx}:{end_idx}] con resumen de {len(summary.split(chr(10)))} líneas")
                new_lines = lines[:start_idx] + summary.split('\n') + lines[end_idx:]
                new_content = '\n'.join(new_lines)
                
                save_state(state_key, new_content)
                logger.info(f"   ✅ GUARDADO: {len(lines)} → {len(new_lines)} líneas")
                
                return f"""✅ Resumen aplicado en '{state_key}' (líneas {start_line}-{end_line} reemplazadas)

{summary}"""
            else:
                logger.info(f"   MODE: AÑADIR AL FINAL (no se recibieron líneas)")
                # Sin líneas específicas, añadir al final con separador
                new_content = current + '\n\n---\n\n## 📝 Resumen\n\n' + summary
                save_state(state_key, new_content)
                logger.info(f"   ✅ GUARDADO: añadido al final")
                
                return f"""✅ Resumen añadido al final de '{state_key}'

{summary}"""
        
        logger.info(f"   MODE: SOLO RETORNAR (sin state_key)")
        # Sin state_key, solo retornar el resumen
        return summary
        
    except Exception as e:
        logger.error(f"Error en smart_resume: {e}")
        return f"❌ Error: {str(e)}"


