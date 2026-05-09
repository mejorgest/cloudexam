"""
File Operations - State Persistence Tools for MCP Agent
Provides read, write, edit, and list operations for the workspace directory.
Enables agents to maintain state across operations.
"""
import os
import json
import csv
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pathlib import Path

# Setup logging para debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("file_operations")

# Historial de cambios para debugging
CHANGE_LOG: List[Dict] = []

def _log_change(operation: str, target: str, details: str = ""):
    """Log a change for debugging"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "operation": operation,
        "target": target,
        "details": details[:200] if details else ""
    }
    CHANGE_LOG.append(entry)
    # Mantener solo los últimos 50 cambios
    if len(CHANGE_LOG) > 50:
        CHANGE_LOG.pop(0)
    
    # Print para ver en logs de Docker
    logger.info(f"📝 [{operation}] {target} {('→ ' + details[:100]) if details else ''}")

def get_change_log() -> List[Dict]:
    """Obtener historial de cambios recientes"""
    return CHANGE_LOG.copy()

# Workspace base path - all operations are relative to this
WORKSPACE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "workspace")
SKILLS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")

def _ensure_workspace():
    """Ensure workspace directory exists"""
    os.makedirs(WORKSPACE_PATH, exist_ok=True)

def _safe_path(filename: str, base_path: str = WORKSPACE_PATH) -> str:
    """
    Ensure the path is within the allowed directory (prevent path traversal)
    """
    # Normalize and resolve the path
    base = Path(base_path).resolve()
    target = (base / filename).resolve()
    
    # Check if the target is within the base directory
    if not str(target).startswith(str(base)):
        raise ValueError(f"Access denied: Path '{filename}' is outside allowed directory")
    
    return str(target)


# ============== READ OPERATIONS ==============

def read_file(filename: str, encoding: str = "utf-8") -> str:
    """
    Read contents of a file from the workspace.
    
    Args:
        filename: Path relative to workspace (e.g., 'data/results.txt' or 'leads.csv')
        encoding: File encoding (default: utf-8)
    
    Returns:
        File contents as string
    
    Example:
        content = read_file('leads.csv')
        data = read_file('config/settings.json')
    """
    _ensure_workspace()
    filepath = _safe_path(filename)
    
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filename}")
    
    with open(filepath, 'r', encoding=encoding) as f:
        return f.read()


def read_json(filename: str) -> Dict[str, Any]:
    """
    Read and parse a JSON file from the workspace.
    
    Args:
        filename: Path relative to workspace (e.g., 'state.json')
    
    Returns:
        Parsed JSON as dictionary (empty dict if file is empty)
    
    Example:
        config = read_json('settings.json')
        state = read_json('agent_state.json')
    """
    content = read_file(filename)
    if not content or not content.strip():
        return {}  # Return empty dict for empty files
    return json.loads(content)


def read_csv(filename: str, has_header: bool = True) -> List[Dict[str, str]]:
    """
    Read and parse a CSV file from the workspace.
    
    Args:
        filename: Path relative to workspace
        has_header: Whether the CSV has a header row
    
    Returns:
        List of dictionaries (if has_header) or list of lists
    
    Example:
        leads = read_csv('leads.csv')
        for lead in leads:
            print(lead['email'])
    """
    _ensure_workspace()
    filepath = _safe_path(filename)
    
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filename}")
    
    with open(filepath, 'r', encoding='utf-8', newline='') as f:
        if has_header:
            reader = csv.DictReader(f)
            return list(reader)
        else:
            reader = csv.reader(f)
            return [row for row in reader]


def list_files(directory: str = "", pattern: str = "*") -> List[Dict[str, Any]]:
    """
    List files in a workspace directory.
    
    Args:
        directory: Subdirectory within workspace (empty for root)
        pattern: Glob pattern to filter files (e.g., '*.csv', '*.json')
    
    Returns:
        List of file info dictionaries with name, size, modified date
    
    Example:
        files = list_files()  # All files in workspace
        csvs = list_files('data', '*.csv')  # CSV files in data/
    """
    _ensure_workspace()
    search_path = Path(_safe_path(directory))
    
    if not search_path.exists():
        return []
    
    files = []
    for filepath in search_path.glob(pattern):
        if filepath.is_file():
            stat = filepath.stat()
            files.append({
                "name": str(filepath.relative_to(WORKSPACE_PATH)),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "type": filepath.suffix
            })
    
    return sorted(files, key=lambda x: x["name"])


def file_exists(filename: str) -> bool:
    """
    Check if a file exists in the workspace.
    
    Args:
        filename: Path relative to workspace
    
    Returns:
        True if file exists, False otherwise
    """
    _ensure_workspace()
    try:
        filepath = _safe_path(filename)
        return os.path.exists(filepath)
    except ValueError:
        return False


# ============== WRITE OPERATIONS ==============

def write_file(filename: str, content: str, encoding: str = "utf-8") -> str:
    """
    Write content to a file in the workspace. Creates directories if needed.
    
    Args:
        filename: Path relative to workspace (e.g., 'results/output.txt')
        content: Content to write
        encoding: File encoding (default: utf-8)
    
    Returns:
        Success message with file path
    
    Example:
        write_file('leads.csv', 'id,email\\n1,test@example.com')
        write_file('reports/summary.txt', report_text)
    """
    _ensure_workspace()
    filepath = _safe_path(filename)
    
    # Create parent directories if they don't exist
    os.makedirs(os.path.dirname(filepath), exist_ok=True) if os.path.dirname(filepath) else None
    
    with open(filepath, 'w', encoding=encoding) as f:
        f.write(content)
    
    _log_change("WRITE_FILE", filename, content[:100] if content else "")
    return f"✅ File written successfully: {filename} ({len(content)} bytes)"


def write_json(filename: str, data: Union[Dict, List], indent: int = 2) -> str:
    """
    Write data as JSON to a file in the workspace.
    
    Args:
        filename: Path relative to workspace
        data: Dictionary or list to serialize as JSON
        indent: JSON indentation (default: 2)
    
    Returns:
        Success message
    
    Example:
        write_json('state.json', {'progress': 50, 'last_id': 123})
        write_json('results.json', [{'id': 1}, {'id': 2}])
    """
    content = json.dumps(data, indent=indent, ensure_ascii=False, default=str)
    return write_file(filename, content)


def write_csv(filename: str, data: List[Dict[str, Any]], fieldnames: List[str] = None) -> str:
    """
    Write data as CSV to a file in the workspace.
    
    Args:
        filename: Path relative to workspace
        data: List of dictionaries to write
        fieldnames: Column names (auto-detected from first row if not provided)
    
    Returns:
        Success message
    
    Example:
        leads = [{'id': '1', 'email': 'a@b.com'}, {'id': '2', 'email': 'c@d.com'}]
        write_csv('leads.csv', leads)
    """
    _ensure_workspace()
    filepath = _safe_path(filename)
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True) if os.path.dirname(filepath) else None
    
    if not data:
        return write_file(filename, "")
    
    if fieldnames is None:
        fieldnames = list(data[0].keys())
    
    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)
    
    return f"✅ CSV written successfully: {filename} ({len(data)} rows)"


def append_file(filename: str, content: str, encoding: str = "utf-8") -> str:
    """
    Append content to a file in the workspace. Creates file if it doesn't exist.
    
    Args:
        filename: Path relative to workspace
        content: Content to append
        encoding: File encoding
    
    Returns:
        Success message
    
    Example:
        append_file('log.txt', f'{datetime.now()}: Task completed\\n')
    """
    _ensure_workspace()
    filepath = _safe_path(filename)
    
    os.makedirs(os.path.dirname(filepath), exist_ok=True) if os.path.dirname(filepath) else None
    
    with open(filepath, 'a', encoding=encoding) as f:
        f.write(content)
    
    return f"✅ Content appended to: {filename}"


# ============== EDIT OPERATIONS ==============

def edit_file(filename: str, old_text: str, new_text: str) -> str:
    """
    Replace text in a file (search and replace).
    
    Args:
        filename: Path relative to workspace
        old_text: Text to find
        new_text: Text to replace with
    
    Returns:
        Success message with count of replacements
    
    Example:
        edit_file('config.json', '"debug": false', '"debug": true')
    """
    content = read_file(filename)
    count = content.count(old_text)
    
    if count == 0:
        return f"⚠️ Text not found in {filename}"
    
    new_content = content.replace(old_text, new_text)
    write_file(filename, new_content)
    
    return f"✅ Replaced {count} occurrence(s) in {filename}"


def delete_file(filename: str) -> str:
    """
    Delete a file from the workspace.
    
    Args:
        filename: Path relative to workspace
    
    Returns:
        Success message
    """
    _ensure_workspace()
    filepath = _safe_path(filename)
    
    if not os.path.exists(filepath):
        return f"⚠️ File not found: {filename}"
    
    os.remove(filepath)
    return f"✅ File deleted: {filename}"


def create_directory(dirname: str) -> str:
    """
    Create a directory in the workspace.
    
    Args:
        dirname: Directory path relative to workspace
    
    Returns:
        Success message
    """
    _ensure_workspace()
    dirpath = _safe_path(dirname)
    os.makedirs(dirpath, exist_ok=True)
    return f"✅ Directory created: {dirname}"


# ============== STATE MANAGEMENT ==============

def save_state(key: str, value: Any, state_file: str = "agent_state.json") -> str:
    """
    Save a key-value pair to the agent's persistent state.
    
    Args:
        key: State key (trailing/leading spaces are stripped automatically)
        value: State value (must be JSON serializable)
        state_file: State file name (default: agent_state.json)
    
    Returns:
        Success message
    
    Example:
        save_state('last_processed_id', 123)
        save_state('progress', {'completed': 50, 'total': 100})
    """
    # Normalizar la clave - eliminar espacios al principio y final
    key = key.strip() if key else key
    
    try:
        state = read_json(state_file)
    except FileNotFoundError:
        state = {}
    
    state[key] = value
    state["_last_updated"] = datetime.now().isoformat()
    
    # Log detallado
    value_preview = str(value)[:100] if value else ""
    _log_change("SAVE_STATE", f"state['{key}']", value_preview)
    
    return write_json(state_file, state)


def load_state(key: str, default: Any = None, state_file: str = "agent_state.json") -> Any:
    """
    Load a value from the agent's persistent state.
    
    Args:
        key: State key to retrieve (trailing/leading spaces are stripped automatically)
        default: Default value if key doesn't exist
        state_file: State file name
    
    Returns:
        The stored value or default
    
    Example:
        last_id = load_state('last_processed_id', 0)
        progress = load_state('progress', {'completed': 0, 'total': 0})
    """
    # Normalizar la clave - eliminar espacios al principio y final
    key = key.strip() if key else key
    
    try:
        state = read_json(state_file)
        return state.get(key, default)
    except FileNotFoundError:
        return default


def get_full_state(state_file: str = "agent_state.json") -> Dict[str, Any]:
    """
    Get the entire agent state.
    
    Returns:
        Full state dictionary
    """
    try:
        return read_json(state_file)
    except FileNotFoundError:
        return {}


def clear_state(state_file: str = "agent_state.json") -> str:
    """
    Clear all agent state.
    
    Returns:
        Success message
    """
    return write_json(state_file, {"_cleared": datetime.now().isoformat()})


# Convenience exports
__all__ = [
    'read_file', 'read_json', 'read_csv', 'list_files', 'file_exists',
    'write_file', 'write_json', 'write_csv', 'append_file',
    'edit_file', 'delete_file', 'create_directory',
    'save_state', 'load_state', 'get_full_state', 'clear_state',
    'get_change_log', 'CHANGE_LOG',
    'WORKSPACE_PATH', 'SKILLS_PATH'
]

