"""
Smart Edit — edita archivos del workspace con instrucciones en lenguaje natural.

Uso:
    from servers.smart_tools.smart_edit import smart_edit_file

    result = await smart_edit_file("examen.json", "corrige la pregunta 3")
"""

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TextEditCommand(BaseModel):
    """Comando estructurado para editar texto."""
    old_text: str = Field(
        description=(
            "Fragmento EXACTO del archivo original que debe reemplazarse. "
            "Tiene que coincidir literal con el contenido actual."
        )
    )
    new_text: str = Field(description="Texto nuevo que reemplaza al fragmento anterior.")
    reasoning: str = Field(description="Explicación breve del cambio.")


def _get_edit_llm():
    """LLM configurado para producir un TextEditCommand estructurado."""
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    return llm.with_structured_output(TextEditCommand)


async def smart_edit_file(filename: str, instruction: str) -> str:
    """
    Edita un archivo del workspace usando un LLM y reglas de reemplazo exacto.

    Lee el archivo, le pide al LLM que produzca un par (old_text, new_text) y aplica
    el reemplazo en el contenido. Si `old_text` no se encuentra, falla.

    Args:
        filename: Archivo del workspace a editar.
        instruction: Qué cambiar, en lenguaje natural.

    Returns:
        Mensaje de confirmación o error.
    """
    try:
        from servers.filesystem_service.file_operations import (
            read_file,
            write_file,
            _log_change,
        )

        try:
            content = read_file(filename)
        except FileNotFoundError:
            return f"❌ Error: archivo '{filename}' no encontrado"

        if not content:
            return f"❌ Error: archivo '{filename}' está vacío"

        llm = _get_edit_llm()
        prompt = (
            f"Tienes el contenido de un archivo del workspace y una instrucción del usuario.\n\n"
            f"=== INSTRUCCIÓN ===\n{instruction}\n\n"
            f"=== CONTENIDO ACTUAL ({len(content)} chars) ===\n{content}\n\n"
            f"Produce un comando de edición con old_text (fragmento EXACTO a reemplazar) "
            f"y new_text (texto de reemplazo). El old_text DEBE existir literal en el contenido."
        )

        try:
            command = llm.invoke(prompt)
        except Exception as e:
            logger.error(f"LLM falló generando edit command: {e}")
            return f"❌ Error consultando el LLM: {e}"

        if command.old_text not in content:
            return (
                f"❌ El LLM produjo un old_text que no existe en el archivo. "
                f"Reintenta con una instrucción más específica.\n"
                f"Razonamiento del LLM: {command.reasoning}"
            )

        new_content = content.replace(command.old_text, command.new_text, 1)
        write_file(filename, new_content)
        _log_change(
            "SMART_EDIT_FILE",
            filename,
            f"{command.reasoning} ({len(command.old_text)}→{len(command.new_text)} chars)",
        )

        return (
            f"✅ Archivo '{filename}' editado.\n"
            f"   • Razonamiento: {command.reasoning}\n"
            f"   • Cambio: {len(command.old_text)} → {len(command.new_text)} chars"
        )

    except Exception as e:
        logger.error(f"Error en smart_edit_file: {e}")
        return f"❌ Error editando archivo: {e}"
