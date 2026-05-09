"""
Git Checkpoints Service - Sistema de versionamiento con Git para el IDE.

Crea commits automáticos cuando se modifica el estado y permite
restaurar a versiones anteriores (checkpoints).

Uso:
    from servers.versioning_service.git_checkpoints import (
        init_repo, create_checkpoint, list_checkpoints, restore_checkpoint
    )
    
    init_repo()  # Inicializar repo si no existe
    create_checkpoint("Agregada cotización para Juan Pérez")
    checkpoints = list_checkpoints()
    restore_checkpoint(checkpoints[0]['hash'])
"""

import os
import subprocess
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Directorio del workspace donde se guarda el estado
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "/app/workspace"))
STATE_FILE = WORKSPACE_DIR / "agent_state.json"


def _run_git(args: List[str], cwd: Optional[Path] = None) -> tuple[bool, str]:
    """Ejecuta un comando git y retorna (success, output)"""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd or WORKSPACE_DIR,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Timeout ejecutando git"
    except Exception as e:
        return False, str(e)


def init_repo() -> bool:
    """
    Inicializa el repositorio Git en el workspace si no existe.
    
    Returns:
        bool: True si el repo está listo
    """
    # Configurar safe.directory para evitar problemas de permisos con volúmenes montados
    _run_git(["config", "--global", "--add", "safe.directory", str(WORKSPACE_DIR)])
    
    git_dir = WORKSPACE_DIR / ".git"
    
    if git_dir.exists():
        logger.info("📚 Repositorio Git ya existe")
        return True
    
    # Crear directorio si no existe
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Inicializar repo
    success, msg = _run_git(["init"])
    if not success:
        logger.error(f"Error inicializando git: {msg}")
        return False
    
    # Configurar usuario para commits
    _run_git(["config", "user.email", "agent@localhost"])
    _run_git(["config", "user.name", "React Agent"])
    
    # Crear .gitignore
    gitignore = WORKSPACE_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*.pyc\n__pycache__/\n.env\n")
    
    # Commit inicial si hay archivos
    if STATE_FILE.exists():
        _run_git(["add", "-A"])
        _run_git(["commit", "-m", "🚀 Estado inicial"])
    
    logger.info("✅ Repositorio Git inicializado")
    return True


def create_checkpoint(message: str, tool_used: Optional[str] = None) -> Optional[Dict]:
    """
    Crea un checkpoint (commit) con el estado actual.
    
    Args:
        message: Descripción del cambio
        tool_used: Nombre de la herramienta que causó el cambio (opcional)
    
    Returns:
        Dict con info del checkpoint o None si falla
    """
    try:
        # Asegurar que el repo existe
        if not (WORKSPACE_DIR / ".git").exists():
            init_repo()
        
        # Verificar si hay cambios
        success, status = _run_git(["status", "--porcelain"])
        if not status.strip():
            logger.debug("No hay cambios para crear checkpoint")
            return None
        
        # Agregar todos los archivos
        _run_git(["add", "-A"])
        
        # Crear mensaje del commit con metadata
        timestamp = datetime.now().isoformat()
        emoji = "🔧" if tool_used else "📝"
        tool_info = f" [{tool_used}]" if tool_used else ""
        commit_message = f"{emoji}{tool_info} {message}"
        
        # Hacer commit
        success, result = _run_git(["commit", "-m", commit_message])
        if not success:
            logger.warning(f"No se pudo crear commit: {result}")
            return None
        
        # Obtener hash del commit
        success, commit_hash = _run_git(["rev-parse", "HEAD"])
        if not success:
            return None
        
        short_hash = commit_hash[:8]
        
        checkpoint = {
            "hash": commit_hash,
            "short_hash": short_hash,
            "message": message,
            "tool_used": tool_used,
            "timestamp": timestamp,
            "commit_message": commit_message
        }
        
        logger.info(f"✅ Checkpoint creado: {short_hash} - {message[:50]}...")
        return checkpoint
        
    except Exception as e:
        logger.error(f"Error creando checkpoint: {e}")
        return None


def list_checkpoints(limit: int = 50) -> List[Dict]:
    """
    Lista los checkpoints (commits) disponibles.
    
    Args:
        limit: Número máximo de checkpoints a retornar
    
    Returns:
        Lista de checkpoints ordenados del más reciente al más antiguo
    """
    try:
        if not (WORKSPACE_DIR / ".git").exists():
            return []
        
        # Obtener log de commits
        success, log_output = _run_git([
            "log",
            f"-{limit}",
            "--pretty=format:%H|%h|%s|%ai",
            "--"
        ])
        
        if not success or not log_output:
            return []
        
        checkpoints = []
        for line in log_output.split("\n"):
            if not line.strip():
                continue
            
            parts = line.split("|")
            if len(parts) >= 4:
                commit_msg = parts[2]
                
                # Extraer herramienta del mensaje si existe
                tool_used = None
                if "[" in commit_msg and "]" in commit_msg:
                    start = commit_msg.index("[") + 1
                    end = commit_msg.index("]")
                    tool_used = commit_msg[start:end]
                
                # Limpiar mensaje
                clean_msg = commit_msg
                for emoji in ["🔧", "📝", "🚀", "✅", "❌"]:
                    clean_msg = clean_msg.replace(emoji, "").strip()
                if tool_used:
                    clean_msg = clean_msg.replace(f"[{tool_used}]", "").strip()
                
                checkpoints.append({
                    "hash": parts[0],
                    "short_hash": parts[1],
                    "message": clean_msg,
                    "tool_used": tool_used,
                    "timestamp": parts[3],
                    "commit_message": commit_msg
                })
        
        return checkpoints
        
    except Exception as e:
        logger.error(f"Error listando checkpoints: {e}")
        return []


def restore_checkpoint(commit_hash: str) -> Dict:
    """
    Restaura el estado a un checkpoint anterior.
    
    Args:
        commit_hash: Hash del commit (completo o corto)
    
    Returns:
        Dict con resultado de la operación
    """
    try:
        if not (WORKSPACE_DIR / ".git").exists():
            return {"success": False, "error": "No hay repositorio Git"}
        
        # Verificar que el commit existe
        success, _ = _run_git(["cat-file", "-t", commit_hash])
        if not success:
            return {"success": False, "error": f"Commit '{commit_hash}' no encontrado"}
        
        # Guardar estado actual antes de restaurar (por si acaso)
        backup_msg = f"Backup antes de restaurar a {commit_hash[:8]}"
        create_checkpoint(backup_msg)
        
        # Restaurar archivos al estado del commit
        success, result = _run_git(["checkout", commit_hash, "--", "."])
        if not success:
            return {"success": False, "error": f"Error en checkout: {result}"}
        
        # Crear nuevo commit con el estado restaurado
        _run_git(["add", "-A"])
        restore_msg = f"🔄 Restaurado a checkpoint {commit_hash[:8]}"
        _run_git(["commit", "-m", restore_msg])
        
        # Obtener info del commit restaurado
        success, log = _run_git(["log", "-1", "--pretty=format:%s", commit_hash])
        original_msg = log if success else "Estado anterior"
        
        logger.info(f"✅ Estado restaurado a: {commit_hash[:8]}")
        
        return {
            "success": True,
            "restored_to": commit_hash,
            "message": f"Estado restaurado a: {original_msg}"
        }
        
    except Exception as e:
        logger.error(f"Error restaurando checkpoint: {e}")
        return {"success": False, "error": str(e)}


def get_checkpoint_diff(commit_hash: str) -> Optional[str]:
    """
    Obtiene el diff de un checkpoint específico.
    
    Args:
        commit_hash: Hash del commit
    
    Returns:
        String con el diff o None si falla
    """
    try:
        success, diff = _run_git(["show", "--stat", commit_hash])
        if success:
            return diff
        return None
    except Exception as e:
        logger.error(f"Error obteniendo diff: {e}")
        return None


def get_state_at_checkpoint(commit_hash: str) -> Optional[Dict]:
    """
    Obtiene el contenido del estado en un checkpoint específico sin restaurar.
    
    Args:
        commit_hash: Hash del commit
    
    Returns:
        Dict con el estado o None si falla
    """
    try:
        # Obtener contenido del archivo en ese commit
        success, content = _run_git(["show", f"{commit_hash}:agent_state.json"])
        if success and content:
            return json.loads(content)
        return None
    except Exception as e:
        logger.error(f"Error obteniendo estado: {e}")
        return None




def get_file_at_checkpoint(commit_hash: str, filename: str) -> Optional[str]:
    """
    Obtiene el contenido de un archivo en un checkpoint específico sin restaurar.
    
    Args:
        commit_hash: Hash del commit
        filename: Nombre del archivo (relativo al workspace)
    
    Returns:
        String con el contenido o None si falla/no existe
    """
    try:
        # Obtener contenido del archivo en ese commit
        # git show <commit>:<path>
        # Normalizar path para git (usar forward slashes)
        git_path = filename.replace("\\", "/")
        success, content = _run_git(["show", f"{commit_hash}:{git_path}"])
        if success:
            return content
        return None
    except Exception as e:
        logger.error(f"Error obteniendo archivo de checkpoint: {e}")
        return None
