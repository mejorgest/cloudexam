"""
Tools Loader - Carga herramientas para el React Agent usando StructuredTool.from_function()

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

class SmartEditFileInput(BaseModel):
    filename: str = Field(description="Nombre del archivo a editar")
    instruction: str = Field(description="Instrucción en lenguaje natural de qué cambiar")

class GoogleSearchInput(BaseModel):
    """Schema para búsqueda en Google."""
    query: str = Field(description="Términos de búsqueda")
    target_file: Optional[str] = Field(
        default=None,
        description="Si se proporciona, agrega los resultados a este archivo del workspace. Si no, los devuelve como texto.",
    )
    num_results: int = Field(default=5, description="Número de resultados (máximo 10)")


# ============== FUNCIONES DE HERRAMIENTAS ==============

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

async def smart_edit_file_func(filename: str, instruction: str) -> str:
    """Edita inteligentemente un archivo del workspace con instrucciones en lenguaje natural."""
    try:
        from servers.smart_tools.smart_edit import smart_edit_file as _smart_edit_file
        return await _smart_edit_file(filename, instruction)
    except Exception as e:
        logger.error(f"Error en smart_edit_file: {e}")
        return f"Error editando archivo: {str(e)}"

async def google_search_func(query: str, target_file: str = None, num_results: int = 5) -> str:
    """Busca información en Google y opcionalmente la agrega a un archivo del workspace."""
    try:
        from servers.advanced_tools.google_search import google_search
        return await google_search(query, state_key=target_file, num_results=num_results)
    except Exception as e:
        return f"Error en búsqueda de Google: {str(e)}"


# ============== CARGADOR DE HERRAMIENTAS ==============

def load_all_tools() -> List[StructuredTool]:
    """Carga todas las herramientas disponibles para el agente."""
    tools = [
        StructuredTool.from_function(
            func=read_file_func,
            name="read_file",
            description="Lee el contenido de un archivo del workspace. Útil para revisar exámenes, justificaciones u otros documentos guardados.",
            args_schema=FileInput,
        ),
        StructuredTool.from_function(
            func=write_file_func,
            name="write_file",
            description="Escribe contenido a un archivo del workspace. Crea el archivo si no existe; sobrescribe si ya existe.",
            args_schema=FileWriteInput,
        ),
        StructuredTool.from_function(
            func=list_files_func,
            name="list_files",
            description="Lista archivos y directorios en el workspace.",
            args_schema=DirectoryInput,
        ),
        StructuredTool.from_function(
            coroutine=smart_edit_file_func,
            name="smart_edit_file",
            description="Edita un archivo del workspace con una instrucción en lenguaje natural (usa LLM). Ejemplo: smart_edit_file('examen.json', 'corrige la pregunta 3').",
            args_schema=SmartEditFileInput,
        ),
        StructuredTool.from_function(
            coroutine=google_search_func,
            name="buscar_en_google",
            description=(
                "Busca información en INTERNET usando Google. Úsala cuando el usuario diga "
                "explícitamente 'busca en internet', 'busca en la web', 'busca en Google', "
                "o cuando necesites evidencia externa para justificar una pregunta de examen. "
                "Si hay un archivo adjunto, pasa target_file='nombre_archivo' para añadir los "
                "resultados a ese archivo en lugar de devolverlos como texto."
            ),
            args_schema=GoogleSearchInput,
        ),
    ]

    logger.info(f"📦 Loaded {len(tools)} tools via StructuredTool.from_function()")
    return tools
