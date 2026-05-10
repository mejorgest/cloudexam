"""
Centralised secrets/config management.

Reads/writes a JSON file inside the persistent data/ volume so credentials
survive container restarts, image rebuilds, and host reboots. On startup
we hydrate os.environ from this file so the rest of the codebase keeps
reading os.environ.get(...) as before.

Migration: if secrets.json doesn't exist but the legacy .env (or current
os.environ) has values, we copy them in on first run.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Keys we manage. Adding/removing here automatically updates the UI.
MANAGED_KEYS: dict[str, dict[str, Any]] = {
    "OPENAI_API_KEY": {
        "label": "OpenAI API Key",
        "required": True,
        "secret": True,
        "help": "Used by the main agent (gpt-5-mini) and PDF exam extraction (gpt-5.4-mini vision).",
        "validate_prefix": "sk-",
    },
    "DB_HOST": {
        "label": "Postgres host",
        "required": False,
        "secret": False,
        "help": "Used by RAG/pgvector tools. Leave blank if you don't use them.",
    },
    "DB_PORT": {
        "label": "Postgres port",
        "required": False,
        "secret": False,
        "help": "",
    },
    "DB_USER": {
        "label": "Postgres user",
        "required": False,
        "secret": False,
        "help": "",
    },
    "DB_PWD": {
        "label": "Postgres password",
        "required": False,
        "secret": True,
        "help": "",
    },
    "DB_NAME": {
        "label": "Postgres database",
        "required": False,
        "secret": False,
        "help": "",
    },
}

_DEFAULT_PATH = Path(
    os.environ.get(
        "SECRETS_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "secrets.json"),
    )
)

_lock = threading.Lock()


def _read_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        logger.warning(f"secrets file unreadable ({exc!r}), starting empty")
        return {}


def _write_file(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _migrate_from_env(stored: dict[str, str]) -> dict[str, str]:
    """If secrets.json is missing values but os.environ has them, copy across."""
    changed = False
    for key in MANAGED_KEYS:
        if key in stored and stored[key]:
            continue
        env_val = os.environ.get(key, "").strip()
        if env_val:
            stored[key] = env_val
            changed = True
            logger.info(f"migrated {key} from environment into secrets store")
    return stored if changed else stored


def load_into_environ(path: Path = _DEFAULT_PATH) -> dict[str, str]:
    """Read secrets file, migrate from env if first run, hydrate os.environ."""
    with _lock:
        stored = _read_file(path)
        before = dict(stored)
        stored = _migrate_from_env(stored)
        if stored != before:
            _write_file(path, stored)
        for key, value in stored.items():
            if value:
                os.environ[key] = value
        return stored


def get_status(path: Path = _DEFAULT_PATH) -> dict[str, Any]:
    """Return per-key status without ever leaking the actual values."""
    with _lock:
        stored = _read_file(path)
    keys = []
    missing_required = []
    for key, meta in MANAGED_KEYS.items():
        value = stored.get(key) or os.environ.get(key, "")
        configured = bool(value)
        keys.append(
            {
                "name": key,
                "label": meta["label"],
                "required": meta["required"],
                "secret": meta["secret"],
                "help": meta["help"],
                "configured": configured,
                "preview": (value[:4] + "…" + value[-4:]) if configured and meta["secret"] and len(value) > 8 else ("set" if configured else ""),
            }
        )
        if meta["required"] and not configured:
            missing_required.append(key)
    return {
        "keys": keys,
        "needs_setup": bool(missing_required),
        "missing_required": missing_required,
    }


def update_keys(updates: dict[str, str], path: Path = _DEFAULT_PATH) -> dict[str, Any]:
    """Validate, persist, and apply key updates. Returns refreshed status."""
    with _lock:
        stored = _read_file(path)
        applied = []
        rejected: list[dict[str, str]] = []
        for key, raw in updates.items():
            if key not in MANAGED_KEYS:
                rejected.append({"key": key, "reason": "unknown key"})
                continue
            value = (raw or "").strip()
            meta = MANAGED_KEYS[key]
            prefix = meta.get("validate_prefix", "")
            if value and prefix and not value.startswith(prefix):
                rejected.append({"key": key, "reason": f"expected to start with '{prefix}'"})
                continue
            if value:
                stored[key] = value
                os.environ[key] = value
            else:
                # explicit empty = clear it
                stored.pop(key, None)
                os.environ.pop(key, None)
            applied.append(key)
        _write_file(path, stored)
    return {"applied": applied, "rejected": rejected, **get_status(path)}
