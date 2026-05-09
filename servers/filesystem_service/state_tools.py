"""
State Management Tools - Herramientas para manipular estado como un Content Agent
Similar al patrón de un coding agent pero para texto/estado en lugar de código.

Tools:
- read_state: Leer estado o clave específica
- edit_state: Editar/actualizar valores
- delete_state_key: Eliminar una clave
- list_state_keys: Listar claves disponibles
- search_state: Buscar en el estado
- read_document: Leer documento del workspace
- edit_document: Editar documento (buscar y reemplazar)
- append_to_document: Agregar contenido a documento
"""
import os
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

# Workspace path
WORKSPACE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "workspace")
STATE_FILE = os.path.join(WORKSPACE_PATH, "agent_state.json")

def _ensure_workspace():
    os.makedirs(WORKSPACE_PATH, exist_ok=True)

def _load_state() -> Dict[str, Any]:
    """Load current state from file"""
    _ensure_workspace()
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def _save_state(state: Dict[str, Any]) -> None:
    """Save state to file"""
    _ensure_workspace()
    state["_last_updated"] = datetime.now().isoformat()
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False, default=str)


# ============== STATE TOOLS ==============

def read_state(key: str = None) -> str:
    """
    Read the agent's persistent state.
    
    Args:
        key: Optional specific key to read. If None, returns all state.
    
    Returns:
        JSON formatted state or specific value
    
    Example:
        # Read all state
        all_state = read_state()
        
        # Read specific key
        last_result = read_state('last_protocol')
    """
    state = _load_state()
    
    if key is None:
        if not state:
            return "📭 Estado vacío. No hay datos guardados."
        return f"📦 Estado actual:\n```json\n{json.dumps(state, indent=2, ensure_ascii=False)}\n```"
    
    if key not in state:
        available = [k for k in state.keys() if not k.startswith('_')]
        return f"⚠️ Clave '{key}' no encontrada.\nClaves disponibles: {', '.join(available)}"
    
    value = state[key]
    if isinstance(value, (dict, list)):
        return f"📄 {key}:\n```json\n{json.dumps(value, indent=2, ensure_ascii=False)}\n```"
    return f"📄 {key}: {value}"


def edit_state(key: str, value: Any = None, operation: str = "set") -> str:
    """
    Edit the agent's persistent state.
    
    Args:
        key: Key to modify
        value: New value (for set/append operations)
        operation: "set" (replace), "append" (add to list/string), "increment" (add to number)
    
    Returns:
        Confirmation message
    
    Example:
        # Set a value
        edit_state('user_preference', 'dark_mode')
        
        # Append to a list
        edit_state('history', 'new_item', operation='append')
        
        # Increment a counter
        edit_state('query_count', 1, operation='increment')
    """
    state = _load_state()
    old_value = state.get(key, None)
    
    if operation == "set":
        state[key] = value
        _save_state(state)
        if old_value is not None:
            return f"✅ Actualizado '{key}':\n  Anterior: {_truncate(str(old_value))}\n  Nuevo: {_truncate(str(value))}"
        return f"✅ Creado '{key}': {_truncate(str(value))}"
    
    elif operation == "append":
        if key not in state:
            state[key] = [value] if not isinstance(value, list) else value
        elif isinstance(state[key], list):
            if isinstance(value, list):
                state[key].extend(value)
            else:
                state[key].append(value)
        elif isinstance(state[key], str):
            state[key] += str(value)
        else:
            return f"❌ No se puede hacer append a '{key}' (tipo: {type(state[key]).__name__})"
        _save_state(state)
        return f"✅ Agregado a '{key}'"
    
    elif operation == "increment":
        if key not in state:
            state[key] = value if isinstance(value, (int, float)) else 1
        elif isinstance(state[key], (int, float)):
            state[key] += value if isinstance(value, (int, float)) else 1
        else:
            return f"❌ No se puede incrementar '{key}' (tipo: {type(state[key]).__name__})"
        _save_state(state)
        return f"✅ Incrementado '{key}': {state[key]}"
    
    else:
        return f"❌ Operación desconocida: {operation}. Use 'set', 'append', o 'increment'"


def delete_state_key(key: str) -> str:
    """
    Delete a key from the agent's state.
    
    Args:
        key: Key to delete
    
    Returns:
        Confirmation message
    
    Example:
        delete_state_key('old_data')
    """
    state = _load_state()
    
    if key.startswith('_'):
        return f"❌ No se pueden eliminar claves del sistema (_{key})"
    
    if key not in state:
        return f"⚠️ Clave '{key}' no existe en el estado"
    
    old_value = state.pop(key)
    _save_state(state)
    return f"✅ Eliminado '{key}' (valor anterior: {_truncate(str(old_value))})"


def list_state_keys() -> str:
    """
    List all keys in the agent's state with their types and preview.
    
    Returns:
        Formatted list of keys
    
    Example:
        keys = list_state_keys()
    """
    state = _load_state()
    
    if not state:
        return "📭 Estado vacío"
    
    lines = ["📋 Claves en el estado:\n"]
    for key, value in state.items():
        if key.startswith('_'):
            continue
        
        type_name = type(value).__name__
        preview = _truncate(str(value), 50)
        
        if isinstance(value, list):
            lines.append(f"  • {key} (list[{len(value)}]): {preview}")
        elif isinstance(value, dict):
            lines.append(f"  • {key} (dict[{len(value)} keys]): {preview}")
        else:
            lines.append(f"  • {key} ({type_name}): {preview}")
    
    lines.append(f"\n📅 Última actualización: {state.get('_last_updated', 'N/A')}")
    return "\n".join(lines)


def search_state(query: str) -> str:
    """
    Search for a pattern in the state values.
    
    Args:
        query: Text to search for (case-insensitive)
    
    Returns:
        Matching keys and values
    
    Example:
        results = search_state('protocol')
    """
    state = _load_state()
    results = []
    query_lower = query.lower()
    
    for key, value in state.items():
        if key.startswith('_'):
            continue
        
        value_str = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        
        if query_lower in key.lower() or query_lower in value_str.lower():
            results.append({
                "key": key,
                "type": type(value).__name__,
                "preview": _truncate(value_str, 100)
            })
    
    if not results:
        return f"🔍 No se encontró '{query}' en el estado"
    
    lines = [f"🔍 Resultados para '{query}':\n"]
    for r in results:
        lines.append(f"  • {r['key']} ({r['type']}): {r['preview']}")
    
    return "\n".join(lines)


# ============== DOCUMENT TOOLS ==============

def read_document(filename: str, offset: int = None, limit: int = None) -> str:
    """
    Read a document from the workspace with optional pagination.
    
    Args:
        filename: Path relative to workspace
        offset: Line number to start from (1-based)
        limit: Maximum lines to read
    
    Returns:
        Document content with line numbers
    
    Example:
        # Read entire document
        content = read_document('results/protocolo.txt')
        
        # Read lines 10-20
        content = read_document('results/protocolo.txt', offset=10, limit=10)
    """
    _ensure_workspace()
    filepath = os.path.join(WORKSPACE_PATH, filename)
    
    if not os.path.exists(filepath):
        return f"❌ Archivo no encontrado: {filename}"
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        return f"❌ Error leyendo archivo: {e}"
    
    total = len(lines)
    start = (offset - 1) if offset else 0
    end = min(start + (limit or 500), total)
    
    # Add line numbers
    numbered_lines = []
    for i, line in enumerate(lines[start:end], start + 1):
        numbered_lines.append(f"{i:4} | {line.rstrip()}")
    
    result = f"📄 {filename} ({total} líneas total)\n"
    result += "-" * 50 + "\n"
    result += "\n".join(numbered_lines)
    
    if end < total:
        result += f"\n\n[Mostrando líneas {start+1}-{end} de {total}]"
        result += f"\nUsa read_document('{filename}', offset={end+1}) para ver más."
    
    return result


def edit_document(filename: str, old_text: str, new_text: str) -> str:
    """
    Edit a document by replacing text (search and replace).
    
    Args:
        filename: Path relative to workspace
        old_text: Text to find (must be unique)
        new_text: Text to replace with
    
    Returns:
        Confirmation with diff preview
    
    Example:
        edit_document('results/protocolo.txt', 
                     'texto incorrecto', 
                     'texto corregido')
    """
    _ensure_workspace()
    filepath = os.path.join(WORKSPACE_PATH, filename)
    
    if not os.path.exists(filepath):
        return f"❌ Archivo no encontrado: {filename}"
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return f"❌ Error leyendo archivo: {e}"
    
    # Check occurrences
    count = content.count(old_text)
    
    if count == 0:
        # Try case-insensitive search for helpful message
        if old_text.lower() in content.lower():
            return f"⚠️ Texto no encontrado exacto, pero existe con diferente capitalización.\nUsa el texto exacto del documento."
        return f"❌ Texto no encontrado en {filename}:\n'{_truncate(old_text, 100)}'"
    
    if count > 1:
        return f"⚠️ Se encontraron {count} ocurrencias. Incluye más contexto para identificar única la ubicación."
    
    # Perform replacement
    new_content = content.replace(old_text, new_text, 1)
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except Exception as e:
        return f"❌ Error escribiendo archivo: {e}"
    
    return f"""✅ Documento editado: {filename}

📝 Cambio realizado:
  - Anterior: {_truncate(old_text, 80)}
  + Nuevo: {_truncate(new_text, 80)}
"""


def append_to_document(filename: str, content: str, add_newline: bool = True) -> str:
    """
    Append content to the end of a document.
    
    Args:
        filename: Path relative to workspace
        content: Content to append
        add_newline: Whether to add newline before content
    
    Returns:
        Confirmation message
    
    Example:
        append_to_document('logs/history.txt', 'Nueva entrada')
    """
    _ensure_workspace()
    filepath = os.path.join(WORKSPACE_PATH, filename)
    
    # Create directories if needed
    os.makedirs(os.path.dirname(filepath), exist_ok=True) if os.path.dirname(filepath) else None
    
    try:
        prefix = "\n" if add_newline and os.path.exists(filepath) else ""
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(prefix + content)
    except Exception as e:
        return f"❌ Error escribiendo archivo: {e}"
    
    return f"✅ Contenido agregado a {filename}"


def correct_text_in_state(key: str, old_text: str, new_text: str) -> str:
    """
    Correct/edit text within a state value (like edit_document but for state).
    
    Args:
        key: State key containing the text
        old_text: Text to find and replace
        new_text: Corrected text
    
    Returns:
        Confirmation with preview
    
    Example:
        correct_text_in_state('last_protocol', 
                              'texto con error', 
                              'texto corregido')
    """
    state = _load_state()
    
    if key not in state:
        return f"❌ Clave '{key}' no existe en el estado"
    
    value = state[key]
    
    if not isinstance(value, str):
        return f"❌ La clave '{key}' no contiene texto (tipo: {type(value).__name__})"
    
    count = value.count(old_text)
    
    if count == 0:
        if old_text.lower() in value.lower():
            return f"⚠️ Texto no encontrado exacto, pero existe con diferente capitalización."
        return f"❌ Texto no encontrado en '{key}':\n'{_truncate(old_text, 100)}'"
    
    if count > 1:
        return f"⚠️ Se encontraron {count} ocurrencias. Incluye más contexto para identificar única."
    
    # Perform correction
    new_value = value.replace(old_text, new_text, 1)
    state[key] = new_value
    _save_state(state)
    
    return f"""✅ Texto corregido en '{key}'

📝 Cambio:
  - Anterior: {_truncate(old_text, 80)}
  + Nuevo: {_truncate(new_text, 80)}
"""


# ============== HELPER FUNCTIONS ==============

def _truncate(text: str, max_length: int = 100) -> str:
    """Truncate text with ellipsis"""
    text = str(text).replace('\n', ' ')
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."


# ============== TOOL SCHEMAS FOR LLM ==============

STATE_TOOLS_SCHEMA = [
    {
        "name": "read_state",
        "description": "Lee el estado persistente del agente. Sin argumentos devuelve todo el estado. Con 'key' devuelve un valor específico.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Clave específica a leer (opcional)"
                }
            }
        }
    },
    {
        "name": "edit_state",
        "description": "Edita el estado del agente. Operaciones: 'set' (reemplazar), 'append' (agregar a lista/string), 'increment' (sumar a número)",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Clave a modificar"},
                "value": {"description": "Nuevo valor"},
                "operation": {
                    "type": "string",
                    "enum": ["set", "append", "increment"],
                    "description": "Tipo de operación (default: set)"
                }
            },
            "required": ["key"]
        }
    },
    {
        "name": "delete_state_key",
        "description": "Elimina una clave del estado del agente",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Clave a eliminar"}
            },
            "required": ["key"]
        }
    },
    {
        "name": "list_state_keys",
        "description": "Lista todas las claves en el estado con tipos y vista previa",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "search_state",
        "description": "Busca un patrón en los valores del estado",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto a buscar"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_document",
        "description": "Lee un documento del workspace con números de línea. Soporta paginación.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Ruta del archivo"},
                "offset": {"type": "integer", "description": "Línea inicial (1-based)"},
                "limit": {"type": "integer", "description": "Máximo de líneas"}
            },
            "required": ["filename"]
        }
    },
    {
        "name": "edit_document",
        "description": "Edita un documento reemplazando texto. El texto a buscar debe ser único.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Ruta del archivo"},
                "old_text": {"type": "string", "description": "Texto a encontrar (debe ser único)"},
                "new_text": {"type": "string", "description": "Texto de reemplazo"}
            },
            "required": ["filename", "old_text", "new_text"]
        }
    },
    {
        "name": "correct_text_in_state",
        "description": "Corrige/edita texto dentro de un valor del estado (como edit_document pero para estado)",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Clave del estado"},
                "old_text": {"type": "string", "description": "Texto a corregir"},
                "new_text": {"type": "string", "description": "Texto corregido"}
            },
            "required": ["key", "old_text", "new_text"]
        }
    }
]


# Exports
__all__ = [
    'read_state', 'edit_state', 'delete_state_key', 'list_state_keys', 'search_state',
    'read_document', 'edit_document', 'append_to_document', 'correct_text_in_state',
    'STATE_TOOLS_SCHEMA'
]






