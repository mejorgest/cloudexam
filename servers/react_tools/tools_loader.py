"""
Tools Loader - Carga herramientas para el React Agent usando StructuredTool.from_function()

Este módulo elimina la necesidad de usar @tool en el archivo principal.
Las funciones están en servers/ y se cargan dinámicamente como herramientas.

Uso:
    from servers.react_tools.tools_loader import load_all_tools
    tools = load_all_tools()
    agent = create_react_agent(model=model, tools=tools, ...)
"""

import logging
from typing import List, Optional
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)

# ============== PYDANTIC SCHEMAS ==============

class FileInput(BaseModel):
    filename: str = Field(description="Nombre del archivo")

class FileWriteInput(BaseModel):
    filename: str = Field(description="Nombre del archivo")
    content: str = Field(description="Contenido a escribir")

class DirectoryInput(BaseModel):
    directory: str = Field(default=".", description="Directorio a listar")

class StateKeyInput(BaseModel):
    key: str = Field(description="Clave del estado")

class StateSaveInput(BaseModel):
    key: str = Field(description="Clave del estado")
    value: str = Field(description="Valor a guardar")

class TextCorrectionInput(BaseModel):
    key: str = Field(description="Clave del estado o nombre del archivo")
    old_text: str = Field(description="Texto a buscar (debe ser único)")
    new_text: str = Field(description="Texto de reemplazo")

class SearchInput(BaseModel):
    query: str = Field(description="Término de búsqueda")

class SmartEditInput(BaseModel):
    key: str = Field(description="Clave del estado a editar")
    instruction: str = Field(description="Instrucción en lenguaje natural de qué cambiar")
    start_line: Optional[int] = Field(default=None, description="Línea inicial del fragmento a editar (1-indexed, opcional)")
    end_line: Optional[int] = Field(default=None, description="Línea final del fragmento a editar (1-indexed, opcional)")

class SmartEditFileInput(BaseModel):
    filename: str = Field(description="Nombre del archivo a editar")
    instruction: str = Field(description="Instrucción en lenguaje natural de qué cambiar")

class SmartEnrichInput(BaseModel):
    key: str = Field(description="Clave del estado a enriquecer")
    instruction: str = Field(description="Qué agregar al documento")

class GoogleSearchInput(BaseModel):
    """Schema para búsqueda en Google"""
    query: str = Field(description="Términos de búsqueda")
    state_key: Optional[str] = Field(default=None, description="Si se proporciona, agrega los resultados a este estado existente. Si no, crea un nuevo estado.")
    num_results: int = Field(default=5, description="Número de resultados (máximo 10)")

class RelocateTextInput(BaseModel):
    """Schema para mover texto dentro de un documento"""
    key: str = Field(description="Clave del estado/documento a modificar")
    start_line: int = Field(description="Línea inicial del texto a mover (1-indexed)")
    end_line: int = Field(description="Línea final del texto a mover (1-indexed)")
    target_position: str = Field(default="inicio", description="Dónde mover: 'inicio', 'final', o 'linea:N'")

class AddTextInput(BaseModel):
    """Schema para añadir texto a un documento"""
    key: str = Field(description="Clave del estado/documento a modificar")
    text: str = Field(description="Texto a añadir")
    position: str = Field(default="final", description="Dónde añadir: 'inicio', 'final', o 'linea:N'")

class DeleteLinesInput(BaseModel):
    """Schema para eliminar líneas específicas de un documento"""
    key: str = Field(description="Clave del estado/documento a modificar")
    start_line: int = Field(description="Línea inicial a eliminar (1-indexed, inclusive)")
    end_line: int = Field(description="Línea final a eliminar (1-indexed, inclusive)")

class SmartResumeInput(BaseModel):
    """Schema para resumir texto con LLM"""
    text: str = Field(description="Texto a resumir")
    state_key: Optional[str] = Field(default=None, description="Estado donde reemplazar el texto (si se quiere reemplazo in-place)")
    start_line: Optional[int] = Field(default=None, description="Línea inicial del texto a reemplazar (1-indexed)")
    end_line: Optional[int] = Field(default=None, description="Línea final del texto a reemplazar (1-indexed)")
    style: str = Field(default="conciso", description="Estilo: 'conciso', 'detallado', o 'bullets'")

class CreateNewStateInput(BaseModel):
    """Schema para crear un nuevo estado/documento"""
    name: str = Field(description="Nombre del nuevo estado/documento (sin espacios, usar guiones_bajos)")
    title: Optional[str] = Field(default=None, description="Título del documento (se agrega como header)")
    initial_content: Optional[str] = Field(default=None, description="Contenido inicial opcional")
    template: Optional[str] = Field(default="blank", description="Plantilla: 'blank', 'report', 'notes', 'research'")

# ============== FUNCIONES DE HERRAMIENTAS ==============
# Todas importan desde servers/ - sin lógica duplicada

def read_file_func(filename: str) -> str:
    """Lee el contenido de un archivo del workspace."""
    try:
        from servers.filesystem_service.file_operations import read_file as fs_read
        return fs_read(filename)
    except FileNotFoundError:
        return f"Error: Archivo '{filename}' no encontrado"
    except Exception as e:
        return f"Error leyendo archivo: {str(e)}"

def write_file_func(filename: str, content: str) -> str:
    """Escribe contenido a un archivo en el workspace."""
    try:
        from servers.filesystem_service.file_operations import write_file as fs_write
        return fs_write(filename, content)
    except Exception as e:
        return f"Error escribiendo archivo: {str(e)}"

def list_files_func(directory: str = ".") -> str:
    """Lista archivos en el workspace."""
    try:
        from servers.filesystem_service.file_operations import list_files as fs_list
        files = fs_list(directory)
        if isinstance(files, list):
            return "\n".join(str(f) for f in files) if files else "Directorio vacío"
        return str(files)
    except Exception as e:
        return f"Error listando archivos: {str(e)}"

def save_state_func(key: str, value: str) -> str:
    """Guarda un valor en el estado persistente del agente."""
    try:
        from servers.filesystem_service.file_operations import save_state as fs_save
        return fs_save(key, value)
    except Exception as e:
        return f"Error guardando estado: {str(e)}"

def create_new_state_func(name: str, title: str = None, initial_content: str = None, template: str = "blank") -> str:
    """
    Crea un nuevo estado/documento vacío o con contenido inicial.
    
    Args:
        name: Nombre del estado (clave)
        title: Título opcional para el documento
        initial_content: Contenido inicial opcional
        template: Plantilla a usar: 'blank', 'report', 'notes', 'research'
    
    Returns:
        Mensaje de confirmación
    """
    from datetime import datetime
    try:
        from servers.filesystem_service.file_operations import save_state as fs_save, load_state as fs_load, _log_change
        
        # Verificar si ya existe
        existing = fs_load(name)
        if existing:
            return f"⚠️ El estado '{name}' ya existe. Usa save_state() para modificarlo o elige otro nombre."
        
        # Generar contenido según la plantilla
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        if template == "report":
            content = f"# {title or name.replace('_', ' ').title()}\n\n**Fecha:** {timestamp}\n\n## Resumen\n\n## Detalles\n\n## Conclusiones\n"
        elif template == "notes":
            content = f"# 📝 {title or name.replace('_', ' ').title()}\n\n**Creado:** {timestamp}\n\n---\n\n"
        elif template == "research":
            content = f"# 🔍 Investigación: {title or name.replace('_', ' ').title()}\n\n**Fecha:** {timestamp}\n\n## Objetivo\n\n## Fuentes\n\n## Hallazgos\n\n## Próximos pasos\n"
        else:  # blank
            if title:
                content = f"# {title}\n\n"
            else:
                content = ""
        
        # Agregar contenido inicial si se proporcionó
        if initial_content:
            content += initial_content
        
        # Guardar el nuevo estado
        fs_save(name, content)
        _log_change("CREATE_STATE", f"state['{name}']", f"template={template}, {len(content)} chars")
        
        return f"✅ Estado '{name}' creado exitosamente ({len(content)} caracteres)"
        
    except Exception as e:
        return f"❌ Error creando estado: {str(e)}"

def load_state_func(key: str) -> str:
    """Carga un valor del estado persistente del agente."""
    try:
        from servers.filesystem_service.file_operations import load_state as fs_load
        result = fs_load(key)
        return str(result) if result else f"No se encontró valor para '{key}'"
    except Exception as e:
        return f"Error cargando estado: {str(e)}"

def get_full_state_func() -> str:
    """Obtiene todo el estado persistente del agente."""
    try:
        from servers.filesystem_service.file_operations import get_full_state as fs_full
        import json
        state = fs_full()
        if not state:
            return "Estado vacío"
        return json.dumps(state, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error obteniendo estado: {str(e)}"

def correct_text_in_state_func(key: str, old_text: str, new_text: str) -> str:
    """Corrige texto en un valor del estado."""
    try:
        from servers.filesystem_service.state_tools import correct_text_in_state as st_correct
        return st_correct(key, old_text, new_text)
    except Exception as e:
        return f"Error corrigiendo texto: {str(e)}"

def search_state_func(query: str) -> str:
    """Busca texto en todo el estado del agente."""
    try:
        from servers.filesystem_service.state_tools import search_state as st_search
        return st_search(query)
    except Exception as e:
        return f"Error buscando: {str(e)}"

def edit_document_func(key: str, old_text: str, new_text: str) -> str:
    """Edita un documento reemplazando old_text con new_text."""
    try:
        from servers.filesystem_service.state_tools import edit_document as st_edit
        return st_edit(key, old_text, new_text)
    except Exception as e:
        return f"Error editando documento: {str(e)}"

async def smart_edit_state_func(key: str, instruction: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Edita inteligentemente un estado con instrucciones en lenguaje natural.
    Si se especifica start_line y end_line, solo edita ese fragmento."""
    try:
        from servers.smart_tools.smart_edit import smart_edit_state as _smart_edit
        return await _smart_edit(key, instruction, start_line=start_line, end_line=end_line)
    except Exception as e:
        logger.error(f"Error en smart_edit_state: {e}")
        return f"Error editando estado: {str(e)}"

async def relocate_text_func(key: str, start_line: int, end_line: int, target_position: str = "inicio") -> str:
    """Mueve un bloque de texto de una posición a otra dentro de un documento."""
    try:
        from servers.smart_tools.smart_edit import relocate_text as _relocate
        return await _relocate(key, start_line, end_line, target_position)
    except Exception as e:
        logger.error(f"Error en relocate_text: {e}")
        return f"Error moviendo texto: {str(e)}"

async def add_text_func(key: str, text: str, position: str = "final") -> str:
    """Añade texto a un documento en una posición específica."""
    try:
        from servers.smart_tools.smart_edit import add_text as _add_text
        return await _add_text(key, text, position)
    except Exception as e:
        logger.error(f"Error en add_text: {e}")
        return f"Error añadiendo texto: {str(e)}"

async def delete_lines_func(key: str, start_line: int, end_line: int) -> str:
    """Elimina líneas específicas de un documento. Operación determinística, sin LLM."""
    try:
        logger.info(f"🗑️ delete_lines_func: key={key}, lines={start_line}-{end_line}")
        from servers.smart_tools.smart_edit import delete_lines as _delete_lines
        return await _delete_lines(key, start_line, end_line)
    except Exception as e:
        logger.error(f"Error en delete_lines: {e}")
        return f"Error eliminando líneas: {str(e)}"

async def smart_resume_func(text: str, state_key: str = None, start_line: int = None, end_line: int = None, style: str = "conciso") -> str:
    """Resume texto usando LLM. Si se dan state_key y líneas, reemplaza in-place."""
    try:
        logger.info(f"🔧 smart_resume_func CALLED WITH:")
        logger.info(f"   text={text[:100]}...")
        logger.info(f"   state_key={state_key}")
        logger.info(f"   start_line={start_line}, end_line={end_line}")
        logger.info(f"   style={style}")
        
        from servers.smart_tools.smart_edit import smart_resume as _smart_resume
        return await _smart_resume(text, state_key=state_key, start_line=start_line, end_line=end_line, style=style)
    except Exception as e:
        logger.error(f"Error en smart_resume: {e}")
        return f"Error resumiendo texto: {str(e)}"

async def smart_edit_file_func(filename: str, instruction: str) -> str:
    """Edita inteligentemente un archivo con instrucciones en lenguaje natural."""
    try:
        from servers.smart_tools.smart_edit import smart_edit_file as _smart_edit_file
        return await _smart_edit_file(filename, instruction)
    except Exception as e:
        logger.error(f"Error en smart_edit_file: {e}")
        return f"Error editando archivo: {str(e)}"

async def smart_enrich_document_func(key: str, instruction: str) -> str:
    """Enriquece un documento llamando otras herramientas e insertando resultados."""
    try:
        from servers.smart_tools.smart_enrich import smart_enrich_document as _smart_enrich
        return await _smart_enrich(key, instruction)
    except Exception as e:
        logger.error(f"Error en smart_enrich_document: {e}")
        return f"Error enriqueciendo documento: {str(e)}"

# ============== GOOGLE SEARCH ==============

async def google_search_func(query: str, state_key: str = None, num_results: int = 5) -> str:
    """Busca información en Google y guarda los resultados."""
    try:
        from servers.advanced_tools.google_search import google_search
        return await google_search(query, state_key=state_key, num_results=num_results)
    except Exception as e:
        return f"Error en búsqueda de Google: {str(e)}"

# ============== EXPORT STATE TO FILE ==============

class ExportStateInput(BaseModel):
    """Schema para exportar un estado a archivo"""
    state_key: str = Field(description="Clave del estado a exportar")
    filename: str = Field(default=None, description="Nombre del archivo de destino (opcional, si no se proporciona usa el nombre del state)")
    format: str = Field(default="txt", description="Formato del archivo: txt, md, json")

async def export_state_to_file_func(state_key: str, filename: str = None, format: str = "txt") -> str:
    """Exporta un estado del agente a un archivo en el workspace."""
    try:
        from servers.filesystem_service.file_operations import load_state, write_file, _log_change
        
        # Cargar el estado
        content = load_state(state_key)
        if not content:
            return f"❌ Error: No existe el estado '{state_key}'"
        
        # Convertir a string si es necesario
        if not isinstance(content, str):
            import json
            content = json.dumps(content, indent=2, ensure_ascii=False)
        
        # Determinar nombre del archivo
        if not filename:
            # Usar el nombre del state con la extensión apropiada
            filename = f"{state_key}.{format}"
        elif not filename.endswith(f".{format}"):
            filename = f"{filename}.{format}"
        
        # Escribir archivo
        write_file(filename, content)
        _log_change("EXPORT_STATE", f"state['{state_key}'] -> file['{filename}']", f"Exportado ({len(content)} chars)")
        
        return f"✅ Estado '{state_key}' exportado a archivo '{filename}' ({len(content)} caracteres)"
        
    except Exception as e:
        return f"❌ Error exportando estado: {str(e)}"

# ============== CARGADOR DE HERRAMIENTAS ==============

def load_all_tools() -> List[StructuredTool]:
    """
    Carga todas las herramientas disponibles usando StructuredTool.from_function().
    
    Returns:
        Lista de herramientas listas para usar con create_react_agent()
    """
    tools = []
    
    # Filesystem tools
    tools.append(StructuredTool.from_function(
        func=read_file_func,
        name="read_file",
        description="Lee el contenido de un archivo del workspace. Útil para ver documentos guardados.",
        args_schema=FileInput
    ))
    
    tools.append(StructuredTool.from_function(
        func=write_file_func,
        name="write_file",
        description="Escribe contenido a un archivo en el workspace. Crea el archivo si no existe.",
        args_schema=FileWriteInput
    ))
    
    tools.append(StructuredTool.from_function(
        func=list_files_func,
        name="list_files",
        description="Lista archivos y directorios en el workspace.",
        args_schema=DirectoryInput
    ))
    
    # State tools
    tools.append(StructuredTool.from_function(
        func=save_state_func,
        name="save_state",
        description="Guarda un valor en el estado persistente del agente. Útil para recordar información importante.",
        args_schema=StateSaveInput
    ))
    
    tools.append(StructuredTool.from_function(
        func=load_state_func,
        name="load_state",
        description="Carga un valor del estado persistente del agente.",
        args_schema=StateKeyInput
    ))
    
    tools.append(StructuredTool.from_function(
        func=get_full_state_func,
        name="get_full_state",
        description="Obtiene todo el estado persistente del agente."
    ))
    
    tools.append(StructuredTool.from_function(
        func=create_new_state_func,
        name="create_new_state",
        description="Crea un NUEVO estado/documento. Usar cuando el usuario dice 'crea un documento', 'nuevo estado', 'inicia un reporte'. Plantillas: 'blank', 'report', 'notes', 'research'. ⚠️ NO sobrescribe estados existentes.",
        args_schema=CreateNewStateInput
    ))
    
    tools.append(StructuredTool.from_function(
        func=correct_text_in_state_func,
        name="correct_text_in_state",
        description="Corrige texto en un valor del estado. El old_text debe ser único.",
        args_schema=TextCorrectionInput
    ))
    
    tools.append(StructuredTool.from_function(
        func=search_state_func,
        name="search_state",
        description="Busca texto en todo el estado del agente.",
        args_schema=SearchInput
    ))
    
    tools.append(StructuredTool.from_function(
        func=edit_document_func,
        name="edit_document",
        description="Edita un documento reemplazando old_text con new_text. El old_text debe ser único.",
        args_schema=TextCorrectionInput
    ))
    
    # Smart edit tools (async)
    tools.append(StructuredTool.from_function(
        coroutine=smart_edit_state_func,
        name="smart_edit_state",
        description="Edita inteligentemente un estado con instrucciones en lenguaje natural. Puede editar todo el estado o solo un rango de líneas específico. Ejemplos: smart_edit_state('protocolo', 'cambia el pH') o smart_edit_state('protocolo', 'traduce al español', start_line=9, end_line=12)",
        args_schema=SmartEditInput
    ))
    
    tools.append(StructuredTool.from_function(
        coroutine=smart_edit_file_func,
        name="smart_edit_file",
        description="Carga un archivo del workspace a un estado temporal y lo edita. ⚠️ NO exporta automáticamente al archivo - el usuario debe usar export_state_to_file() para guardar los cambios.",
        args_schema=SmartEditFileInput
    ))
    
    tools.append(StructuredTool.from_function(
        coroutine=smart_enrich_document_func,
        name="smart_enrich_document",
        description="Enriquece un documento/estado existente ejecutando otra herramienta e insertando su resultado. Útil para agregar cotizaciones o protocolos a documentos existentes.",
        args_schema=SmartEnrichInput
    ))
    
    tools.append(StructuredTool.from_function(
        coroutine=relocate_text_func,
        name="relocate_text",
        description="Mueve un bloque de texto de una posición a otra dentro de un documento. Usa cuando el usuario diga 'mueve este texto al inicio/final', 'pasa estas líneas al principio'. Posiciones: 'inicio', 'final', 'linea:N'.",
        args_schema=RelocateTextInput
    ))
    
    tools.append(StructuredTool.from_function(
        coroutine=add_text_func,
        name="add_text",
        description="Añade texto a un documento/estado en una posición específica. Usa cuando el usuario diga 'añade esto', 'agrega este texto', 'pon esto al final/inicio'. Posiciones: 'inicio', 'final' (default), 'linea:N'.",
        args_schema=AddTextInput
    ))
    
    tools.append(StructuredTool.from_function(
        coroutine=delete_lines_func,
        name="delete_lines",
        description="""🗑️ ELIMINAR LÍNEAS: Usa esta herramienta para BORRAR/ELIMINAR líneas de un documento.
⚠️ IMPORTANTE: Cuando el usuario diga 'borra', 'elimina', 'quita', 'delete' un fragmento seleccionado, USA ESTA HERRAMIENTA.
Es una operación DETERMINÍSTICA - elimina exactamente las líneas especificadas sin usar LLM.
Ejemplo: delete_lines('documento', start_line=377, end_line=408)""",
        args_schema=DeleteLinesInput
    ))
    
    tools.append(StructuredTool.from_function(
        coroutine=smart_resume_func,
        name="smart_resume",
        description="""Resume texto con LLM. 
⚠️ IMPORTANTE: Si el usuario seleccionó un snippet con líneas (ej: 'snippet:documento [262 - 288]'), DEBES extraer esos números y pasarlos como start_line=262 y end_line=288 para que el resumen REEMPLACE esas líneas.
- Con state_key + start_line + end_line: REEMPLAZA las líneas seleccionadas con el resumen
- Con solo state_key: añade el resumen al final del documento
Estilos: 'conciso' (default), 'detallado', 'bullets'.""",
        args_schema=SmartResumeInput
    ))
    
    tools.append(StructuredTool.from_function(
        coroutine=export_state_to_file_func,
        name="export_state_to_file",
        description="Exporta/guarda un estado del agente como archivo en el workspace. Úsalo cuando el usuario diga 'guarda el state', 'exporta', 'guarda como archivo', etc. Formatos: txt, md, json.",
        args_schema=ExportStateInput
    ))

    tools.append(StructuredTool.from_function(
        coroutine=google_search_func,
        name="buscar_en_google",
        description="Busca información en INTERNET usando Google. SOLO usar cuando el usuario diga EXPLÍCITAMENTE: 'busca en internet', 'busca en la web', 'busca en Google'. ⚠️ IMPORTANTE: Si hay un documento adjunto/seleccionado, DEBES usar state_key='nombre_del_estado' para agregar los resultados a ese documento. Solo crea un nuevo estado si NO hay documento seleccionado.",
        args_schema=GoogleSearchInput
    ))

    logger.info(f"📦 Loaded {len(tools)} tools via StructuredTool.from_function()")
    return tools

