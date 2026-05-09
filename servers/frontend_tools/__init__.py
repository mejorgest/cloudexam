"""
Frontend Tools - Herramientas para edición de estados desde el agente programático.

Estas herramientas pueden ser importadas y usadas desde código Python 
generado por el modelo:

    from servers.frontend_tools import delete_lines, smart_edit, add_text
    
    result = await delete_lines("mi_documento", 10, 20)
    result = await smart_edit("mi_documento", "traduce al español")
    result = await add_text("mi_documento", "Nuevo contenido", "final")

Todas las funciones son async para compatibilidad con el code_executor.
"""

from .state_operations import (
    delete_lines,
    add_text,
    relocate_text,
    get_state,
    save_state,
    list_states,
    create_state,
)

from .smart_operations import (
    smart_edit,
    smart_resume,
    smart_enrich,
    correct_text,
    translate_fragment,
    summarize_fragment,
)

__all__ = [
    # State operations (deterministic)
    "delete_lines",
    "add_text",
    "relocate_text",
    "get_state",
    "save_state",
    "list_states",
    "create_state",
    # Smart operations (LLM-powered)
    "smart_edit",
    "smart_resume",
    "smart_enrich",
    "correct_text",
    "translate_fragment",
    "summarize_fragment",
]
