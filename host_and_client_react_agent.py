"""
MCP Host & Client con LangGraph React Agent
============================================
Versión que usa create_react_agent para un loop ReAct (Reason + Act)
en lugar de generación de código puro.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
try:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    _HAS_SQLITE_SAVER = True
except ImportError:
    AsyncSqliteSaver = None  # type: ignore
    _HAS_SQLITE_SAVER = False

# Trim conversation history to the last N messages before each LLM call,
# preserving tool-call/tool-response pairs so the model never sees an orphan ToolMessage.
MAX_HISTORY_MESSAGES = 10

def _trim_history_hook(state):
    msgs = state.get("messages", [])
    if len(msgs) <= MAX_HISTORY_MESSAGES:
        return {}
    trimmed = msgs[-MAX_HISTORY_MESSAGES:]
    while trimmed and isinstance(trimmed[0], ToolMessage):
        trimmed = trimmed[1:]
    return {"llm_input_messages": trimmed}

# Hydrate secrets BEFORE importing tools loader: tool modules read os.environ
# at import time. config_manager merges data/secrets.json into os.environ.
from config_manager import (
    load_into_environ as _load_secrets,
    get_status as _config_status,
    update_keys as _config_update,
)
_load_secrets()

# Import tools loader (StructuredTool sin @tool decorador)
from servers.react_tools.tools_loader import load_all_tools

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============== PYDANTIC MODELS ==============

class AskPayload(BaseModel):
    question: str
    thread_id: Optional[str] = "default"
    context_files: Optional[List[Dict[str, str]]] = None  # [{"name": "file.txt", "content": "..."}]

class SaveStatePayload(BaseModel):
    key: str
    value: Any

# ============== TOOLS LOADED DYNAMICALLY ==============
# Todas las herramientas se cargan desde servers/react_tools/tools_loader.py
# usando StructuredTool.from_function() - sin @tool decorators

# ============== SYSTEM PROMPT ==============

SYSTEM_PROMPT = """Eres un asistente para análisis de exámenes médicos. Tu rol es razonar
sobre preguntas de exámenes (microbiología, hematología, parasitología, etc.),
justificar respuestas correctas e incorrectas con evidencia, y ayudar al usuario
a editar y enriquecer sus documentos de estudio.

## 📁 ARQUITECTURA DE DOCUMENTOS

### 🗄️ Agent State (documentos DINÁMICOS):
- Documentos en memoria que puedes CREAR, EDITAR y MODIFICAR.
- Crear/guardar:  save_state("nombre", contenido)
- Editar:         smart_edit_state("nombre", "instrucción")
- Exportar:       export_state_to_file("nombre")  ← solo si el usuario lo pide
- Ejemplos: justificacion_pregunta_3, notas_microbiologia, resumen_examen_X

### 📂 Workspace Files (documentos ESTÁTICOS):
- Archivos permanentes en disco, SOLO LECTURA.
- Leer:           read_file("archivo.txt")
- NUNCA edites archivos directamente; trabaja siempre sobre un state y exporta
  cuando esté listo.

### 🔄 Flujo de trabajo
1. Leer archivos relevantes con read_file() para obtener contexto.
2. Trabajar sobre un STATE con save_state() / smart_edit_state().
3. Solo cuando el usuario diga "guarda" / "exporta", llamar export_state_to_file().

## 🛠️ TUS HERRAMIENTAS

### Edición de documentos
- smart_edit_state(key, instruction)        → editar un state con LLM
- smart_edit_file(filename, instruction)    → editar un archivo (carga a state)
- smart_enrich_document(key, instruction)   → enriquecer un state con otra tool
- smart_resume(text, state_key, lines, ...) → resumir y reemplazar/insertar
- add_text(key, text, position)             → añadir texto en posición
- delete_lines(key, start, end)             → borrar líneas (operación exacta)
- relocate_text(key, start, end, target)    → mover bloque de texto
- correct_text_in_state(key, old, new)      → reemplazo simple

### Estado y archivos
- save_state / load_state / get_full_state / search_state / create_new_state
- read_file / write_file / list_files
- export_state_to_file(state_key, filename, format)  ← solo bajo petición

### Búsqueda externa
- buscar_en_google(query, state_key, num_results)
  Úsalo SOLO cuando el usuario pida explícitamente "busca en internet / Google / web".
  Si hay un documento adjunto, pasa state_key='nombre' para agregar resultados ahí.

## 🔴 REGLAS

1. NO uses export_state_to_file() automáticamente — solo cuando el usuario lo pida
   explícitamente ("guarda", "exporta", "guarda como archivo").
2. Para editar contenido usa smart_edit_state, no escribas archivos directamente.
3. Cuando justifiques una respuesta de examen, indica brevemente:
   - cuál es la opción correcta y por qué,
   - por qué las otras opciones son incorrectas,
   - cita la fuente si la obtuviste de buscar_en_google.
4. Sé conciso y directo. No repitas información ya presente en el documento.
"""

# ============== LIFESPAN ==============

CHECKPOINT_DB_PATH = os.environ.get(
    "CHECKPOINT_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "checkpoints.sqlite"),
)

async def _init_agent(app: FastAPI):
    """Initialize the agent + checkpointer. Returns the memory close-coroutine
    or None. Called from lifespan and from /api/config/keys on first config."""
    model = ChatOpenAI(model="gpt-5-mini-2025-08-07", temperature=0)

    tools = load_all_tools()
    logger.info(f"📦 Loaded {len(tools)} tools: {[t.name for t in tools]}")

    memory_cm = None
    memory = None
    if _HAS_SQLITE_SAVER:
        try:
            os.makedirs(os.path.dirname(CHECKPOINT_DB_PATH), exist_ok=True)
            memory_cm = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB_PATH)
            memory = await memory_cm.__aenter__()
            await memory.setup()
            logger.info(f"💾 Persistent checkpointer at {CHECKPOINT_DB_PATH}")
        except Exception as exc:
            logger.warning(f"⚠️ SQLite checkpointer failed ({exc!r}) — falling back to MemorySaver")
            memory_cm = None
            memory = MemorySaver()
    else:
        logger.warning("⚠️ langgraph-checkpoint-sqlite not installed — using MemorySaver (volatile)")
        memory = MemorySaver()

    agent = create_react_agent(
        model=model,
        tools=tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=memory,
        pre_model_hook=_trim_history_hook,
    )

    app.state.agent = agent
    app.state.memory = memory
    app.state.memory_cm = memory_cm
    logger.info("✅ React Agent ready!")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting React Agent with LangGraph")

    # Defaults so config endpoints work even before agent init
    app.state.agent = None
    app.state.memory = None
    app.state.memory_cm = None

    # Only build the agent if the required keys are present. Otherwise the HTTP
    # server stays up so the frontend can show the configuration screen.
    if os.environ.get("OPENAI_API_KEY"):
        try:
            await _init_agent(app)
        except Exception as exc:
            logger.error(f"agent init failed: {exc!r}")
    else:
        logger.warning("⚠️ OPENAI_API_KEY missing — agent not initialised. Configure via UI.")

    try:
        yield
    finally:
        logger.info("👋 Shutting down React Agent")
        memory_cm = app.state.memory_cm
        if memory_cm is not None:
            try:
                await memory_cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning(f"checkpointer close failed: {exc!r}")

app = FastAPI(
    lifespan=lifespan, 
    title="React Agent with LangGraph",
    description="MCP Agent usando patrón ReAct (Reason + Act) con persistencia de estado"
)

# Detect if running in Docker (production) or local development
IS_DOCKER = os.environ.get("DOCKER_ENV", "false").lower() == "true"
STATIC_DIR = os.environ.get("STATIC_DIR", None)
FRONTEND_DIST = STATIC_DIR or os.path.join(os.path.dirname(__file__), "frontend", "dist")

# Mount static files based on environment
if IS_DOCKER and os.path.exists(FRONTEND_DIST):
    # Production: Serve Vite build from frontend/dist
    logger.info(f"🐳 Docker mode: Serving frontend from {FRONTEND_DIST}")
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")
    
    @app.get("/")
    def index():
        """Serve the Vite-built React app"""
        index_path = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"message": "React Agent API", "docs": "/docs"}
    
    @app.get("/subir-imagen.html")
    @app.get("/subir-imagen")
    def subir_imagen_page():
        """Serve the image upload interface"""
        upload_path = os.path.join(FRONTEND_DIST, "subir-imagen.html")
        if os.path.exists(upload_path):
            return FileResponse(upload_path, media_type="text/html")
        return {"error": "Upload page not found"}
    
    # Note: SPA catch-all must be registered AFTER all API routes
    # It's done at the bottom of this file
    _SPA_CATCH_ALL_NEEDED = True
else:
    # Development: Vite dev server handles frontend, just serve API
    logger.info("🔧 Development mode: Frontend served by Vite dev server")
    _SPA_CATCH_ALL_NEEDED = False
    try:
        app.mount("/static", StaticFiles(directory="static"), name="static")
    except Exception:
        pass
    
    @app.get("/")
    def index():
        try:
            return FileResponse("static/index_clean.html")
        except Exception:
            return {"message": "React Agent API - Frontend: http://localhost:5173", "docs": "/docs"}

@app.get("/api/tools")
async def get_tools():
    """Lista todas las herramientas disponibles"""
    tools = load_all_tools()
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "is_async": t.coroutine is not None  # StructuredTool usa coroutine para async
            }
            for t in tools
        ],
        "count": len(tools)
    }

@app.get("/api/workspace/state")
async def get_agent_state():
    """Obtiene el estado persistente del agente"""
    try:
        from servers.filesystem_service.file_operations import get_full_state
        state = get_full_state()
        return {"state": state}
    except Exception as e:
        logger.error(f"Error getting state: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/workspace/state")
async def save_agent_state(payload: SaveStatePayload):
    """Guarda un valor en el estado del agente"""
    try:
        from servers.filesystem_service.file_operations import save_state
        result = save_state(payload.key, payload.value)
        return {"success": True, "message": result}
    except Exception as e:
        logger.error(f"Error saving state: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/workspace/files")
async def list_workspace_files():
    """Lista archivos en el workspace"""
    try:
        from servers.filesystem_service.file_operations import list_files
        files_data = list_files()
        # Extract just filenames as strings (list_files returns dicts with name, size, etc)
        if isinstance(files_data, list):
            filenames = [f["name"] if isinstance(f, dict) else str(f) for f in files_data]
        else:
            filenames = []
        return {"files": filenames}
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        return {"files": []}

@app.post("/api/workspace/files/read")
async def read_workspace_file(payload: dict):
    """Lee un archivo del workspace"""
    try:
        from servers.filesystem_service.file_operations import read_file
        filename = payload.get("filename", "")
        content = read_file(filename)
        return {"content": content, "filename": filename}
    except Exception as e:
        logger.error(f"Error reading file: {e}")
        return {"content": "", "error": str(e)}

@app.get("/api/debug/changelog")
async def get_changelog():
    """Obtiene el historial de cambios recientes para debugging"""
    try:
        from servers.filesystem_service.file_operations import get_change_log
        changes = get_change_log()
        return {"changes": changes, "count": len(changes)}
    except Exception as e:
        logger.error(f"Error getting changelog: {e}")
        return {"changes": [], "error": str(e)}

# WebSocket endpoint for real-time updates (used by viewer)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time communication with frontend"""
    await websocket.accept()
    try:
        while True:
            # Keep connection alive, handle messages if needed
            data = await websocket.receive_text()
            # Echo back for now - can be extended for real-time features
            await websocket.send_json({"status": "received", "data": data})
    except Exception:
        pass  # Connection closed

@app.post("/api/workspace/state/delete")
async def delete_state_key(payload: dict):
    """Elimina una clave del estado del agente"""
    try:
        key = payload.get("key", "")
        if not key:
            return {"error": "No se especificó la clave a eliminar"}
        
        from servers.filesystem_service.file_operations import get_full_state, save_state, _log_change
        state = get_full_state()
        
        if key not in state:
            return {"error": f"Clave '{key}' no encontrada en el estado"}
        
        del state[key]
        
        # Guardar estado sin la clave
        from servers.filesystem_service.file_operations import write_json
        write_json("agent_state.json", state)
        _log_change("DELETE_STATE", f"state['{key}']", "")
        
        logger.info(f"🗑️ State key deleted: {key}")
        return {"success": True, "deleted": key}
    except Exception as e:
        logger.error(f"Error deleting state key: {e}")
        return {"error": str(e)}

@app.post("/api/workspace/files/write")
async def write_workspace_file(payload: dict):
    """Escribe contenido a un archivo del workspace"""
    try:
        filename = payload.get("filename", "")
        content = payload.get("content", "")
        
        if not filename:
            return {"error": "No se especificó el archivo"}
        
        from servers.filesystem_service.file_operations import write_file, _log_change
        result = write_file(filename, content)
        _log_change("WRITE_FILE", filename, content[:100] + "..." if len(content) > 100 else content)
        
        logger.info(f"💾 File written: {filename} ({len(content)} chars)")
        return {"success": True, "filename": filename, "message": result}
    except Exception as e:
        logger.error(f"Error writing file: {e}")
        return {"error": str(e)}

@app.post("/api/workspace/files/delete")
async def delete_workspace_file(payload: dict):
    """Elimina un archivo del workspace"""
    try:
        filename = payload.get("filename", "")
        if not filename:
            return {"error": "No se especificó el archivo a eliminar"}
        
        from servers.filesystem_service.file_operations import delete_file, _log_change
        result = delete_file(filename)
        _log_change("DELETE_FILE", filename, "")
        
        logger.info(f"🗑️ File deleted: {filename}")
        return {"success": True, "deleted": filename, "message": result}
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        return {"error": str(e)}
# ============== FILE IMPORT ENDPOINT ==============

@app.post("/api/import")
async def import_file(file: UploadFile = File(...), state_key: str = Form(None)):
    """
    Importa un archivo desde el navegador y lo convierte en un estado.
    
    Args:
        file: El archivo a importar
        state_key: Nombre opcional para el estado (si no se proporciona, usa el nombre del archivo)
    
    Tipos soportados: .txt, .md, .json, .csv, .html, .xml, .py, .js, .ts, .yaml, .yml
    """
    try:
        from servers.filesystem_service.file_operations import save_state, _log_change
        import os
        
        # Leer contenido del archivo
        content = await file.read()
        filename = file.filename or "imported_file"
        
        # Determinar el nombre del estado
        if not state_key:
            # Usar nombre del archivo sin extensión, reemplazando espacios con _
            base_name = os.path.splitext(filename)[0]
            state_key = base_name.replace(" ", "_").replace("-", "_").lower()
        
        # Detectar codificación y decodificar
        try:
            text_content = content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text_content = content.decode('latin-1')
            except:
                text_content = content.decode('utf-8', errors='ignore')
        
        # Detectar tipo de archivo y procesar si es necesario
        ext = os.path.splitext(filename)[1].lower()
        
        if ext == '.json':
            import json
            try:
                data = json.loads(text_content)
                text_content = json.dumps(data, indent=2, ensure_ascii=False)
            except:
                pass  # Si falla, mantener como texto
        
        elif ext == '.csv':
            # Convertir CSV a formato legible
            lines = text_content.strip().split('\n')
            if lines:
                header = f"📊 CSV importado: {filename}\n"
                header += f"📝 {len(lines)} filas\n\n"
                text_content = header + text_content
        
        # Guardar como estado
        save_state(state_key, text_content)
        _log_change("IMPORT_FILE", f"state['{state_key}']", f"Importado desde: {filename}")
        
        logger.info(f"📥 File imported: {filename} -> state['{state_key}']")
        
        return {
            "success": True,
            "state_key": state_key,
            "filename": filename,
            "size": len(text_content),
            "lines": text_content.count('\n') + 1
        }
        
    except Exception as e:
        logger.error(f"Error importing file: {e}")
        return {"error": str(e)}

# ============== PDF UPLOAD ENDPOINT ==============

@app.post("/api/upload/pdf")
async def upload_pdf(
    pdf: UploadFile = File(...),
    target_state: str = Form(None),
    target_file: str = Form(None)
):
    """
    Uploads a PDF, extracts text, and stores in ChromaDB vectors.
    
    If target_state is provided: appends text to existing state
    If target_file is provided: appends text to existing workspace file
    Otherwise: creates new workspace file with extracted text
    
    Async: chunks text and stores embeddings in ChromaDB
    """
    import asyncio
    import os
    
    try:
        from servers.filesystem_service.file_operations import (
            save_state, load_state, write_file, read_file, _log_change
        )
        from servers.pdf_processor import (
            extract_text_from_pdf, text_to_markdown, process_pdf_to_vectors
        )
        
        # Read PDF bytes
        pdf_bytes = await pdf.read()
        filename = pdf.filename or "documento.pdf"
        base_name = os.path.splitext(filename)[0]
        
        logger.info(f"📄 Processing PDF: {filename} ({len(pdf_bytes)} bytes)")
        
        # Extract text from PDF
        raw_text, page_count = extract_text_from_pdf(pdf_bytes)
        md_text = text_to_markdown(raw_text, base_name)
        
        result = {
            "success": True,
            "filename": filename,
            "pages": page_count,
            "text_length": len(md_text)
        }
        
        # Determine where to save
        if target_state:
            # Append to existing state
            existing = load_state(target_state) or ""
            if isinstance(existing, str):
                combined = existing + "\n\n---\n\n" + md_text
            else:
                combined = md_text
            save_state(target_state, combined)
            _log_change("PDF_APPEND_STATE", f"state['{target_state}']", f"PDF: {filename}")
            result["saved_to"] = f"state:{target_state}"
            result["state_key"] = target_state
            
        elif target_file:
            # Append to existing workspace file
            try:
                existing = read_file(target_file)
            except:
                existing = ""
            combined = existing + "\n\n---\n\n" + md_text
            write_file(target_file, combined)
            _log_change("PDF_APPEND_FILE", target_file, f"PDF: {filename}")
            result["saved_to"] = f"file:{target_file}"
            result["workspace_file"] = target_file
            
        else:
            # Create new workspace file
            new_filename = f"{base_name}.md"
            write_file(new_filename, md_text)
            _log_change("PDF_NEW_FILE", new_filename, f"PDF: {filename} ({page_count} pages)")
            result["saved_to"] = f"file:{new_filename}"
            result["workspace_file"] = new_filename
        
        # Register PDF in database
        pdf_doc_id = None
        try:
            from servers.pdf_registry import register_pdf as reg_pdf, update_pdf_chunks, init_pdf_table
            init_pdf_table()
            pdf_doc_id = reg_pdf(
                filename=base_name,
                original_name=filename,
                pages=page_count,
                text_length=len(md_text),
                saved_to=result.get("saved_to", "")
            )
            result["pdf_doc_id"] = pdf_doc_id
        except Exception as e:
            logger.warning(f"Could not register PDF in database: {e}")
        
        # Async: store in pgvector and extract entities (background task)
        async def store_vectors_and_extract_entities():
            try:
                chunk_count, chunk_ids = await process_pdf_to_vectors(
                    text=raw_text,
                    filename=filename,
                    metadata={"pages": page_count, "type": "pdf"}
                )
                logger.info(f"✅ pgvector: {chunk_count} chunks stored for {filename}")
                
                # Update PDF registry with chunk IDs
                if pdf_doc_id and chunk_ids:
                    try:
                        from servers.pdf_registry import update_pdf_chunks
                        update_pdf_chunks(pdf_doc_id, chunk_ids)
                    except Exception as e:
                        logger.warning(f"Could not update PDF chunks: {e}")
                
                # Automatic entity extraction (async, non-blocking)
                # DISABLED per user request (congelado por ahora)
                if pdf_doc_id:
                    try:
                        from servers.pdf_registry import update_entity_status
                        update_entity_status(pdf_doc_id, "disabled")
                        logger.info(f"🧬 Entity extraction disabled for {filename} (status set to 'disabled')")
                        
                        # from servers.entity_extraction_service import extract_entities_for_pdf
                        # 
                        # logger.info(f"🧬 Starting entity extraction for {filename}...")
                        # update_entity_status(pdf_doc_id, "processing")
                        # 
                        # # Run extraction in thread pool to avoid blocking
                        # import asyncio
                        # loop = asyncio.get_event_loop()
                        # result = await loop.run_in_executor(
                        #     None,
                        #     lambda: extract_entities_for_pdf(pdf_doc_id)
                        # )
                        # 
                        # if result.get("success"):
                        #     update_entity_status(pdf_doc_id, "completed")
                        #     logger.info(f"✅ Entities extracted: {result.get('total_entities', 0)} entities from {filename}")
                        # else:
                        #     update_entity_status(pdf_doc_id, "error")
                        #     logger.warning(f"⚠️ Entity extraction failed: {result.get('error')}")
                    except Exception as e:
                        logger.warning(f"Could not update entity status: {e}")
                        # try:
                        #     from servers.pdf_registry import update_entity_status
                        #     update_entity_status(pdf_doc_id, "error")
                        # except:
                        #     pass
                        
            except Exception as e:
                logger.error(f"❌ pgvector error: {e}")
                if pdf_doc_id:
                    try:
                        from servers.pdf_registry import update_pdf_status
                        update_pdf_status(pdf_doc_id, "error")
                    except:
                        pass
        
        # Fire and forget the async task
        asyncio.create_task(store_vectors_and_extract_entities())
        result["vector_status"] = "processing"
        result["entity_status"] = "pending"
        
        logger.info(f"✅ PDF processed: {filename} -> {result.get('saved_to')}")
        return result
        
    except Exception as e:
        logger.error(f"Error processing PDF: {e}", exc_info=True)
        return {"error": str(e)}


# ============== EXAM EXTRACTION FROM PDF ==============

@app.post("/api/exams/extract-from-pdf")
async def extract_exam_from_pdf(
    pdf: UploadFile = File(...),
    output_name: str = Form("examen_extraido")
):
    """
    Extracts exam questions from a PDF using Gemini AI Vision (textractor_robust.py).
    Saves the result as a JSON file in the workspace in the format recognized by ExamViewer.
    This is DIFFERENT from /api/upload/pdf which stores PDFs in the vector store.
    """
    import tempfile
    import json
    
    try:
        # Import textractor
        from textractor_robust import process_pdf, simplify_questions
        
        # Save PDF to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            pdf_content = await pdf.read()
            tmp.write(pdf_content)
            tmp_path = tmp.name
        
        logger.info(f"🤖 Extracting exam from PDF: {pdf.filename}")
        
        try:
            # Process PDF with textractor (Gemini AI Vision)
            result = process_pdf(
                pdf_path=tmp_path,
                output_path=None,  # Don't save intermediate files
                verbose=True,
                simplify=False  # We'll simplify ourselves
            )
            
            # Get simplified questions
            all_questions = result.get("preguntas", [])
            simplified = simplify_questions(all_questions)
            
            # Ensure output_name has "examen" in it for IDE recognition
            if not any(word in output_name.lower() for word in ['examen', 'pregunta', 'banco']):
                output_name = f"examen_{output_name}"
            
            # Save to workspace
            output_filename = f"{output_name}.json"
            workspace_path = "./workspace"
            os.makedirs(workspace_path, exist_ok=True)
            output_path = os.path.join(workspace_path, output_filename)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(simplified, f, ensure_ascii=False, indent=2)
            
            logger.info(f"✅ Exam extracted: {output_filename} ({len(simplified)} questions)")
            
            return {
                "success": True,
                "filename": output_filename,
                "total_questions": len(simplified),
                "pages_processed": result.get("total_paginas_procesadas", 0),
                "original_pdf": pdf.filename
            }
            
        finally:
            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        
    except ImportError as e:
        logger.error(f"Textractor import error: {e}")
        return {"success": False, "error": f"Dependencias faltantes: {str(e)}"}
    except Exception as e:
        logger.error(f"Error extracting exam: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

# ============== PDF MANAGEMENT ENDPOINTS ==============

@app.get("/api/pdf/documents")
async def list_pdf_documents():
    """List all registered PDF documents from the database."""
    try:
        from servers.pdf_registry import list_pdfs, init_pdf_table
        init_pdf_table()
        documents = list_pdfs()
        return {"success": True, "documents": documents, "count": len(documents)}
    except Exception as e:
        logger.error(f"Error listing PDF documents: {e}")
        return {"success": False, "error": str(e), "documents": []}


@app.delete("/api/pdf/documents/{doc_id}")
async def delete_pdf_document(doc_id: str):
    """Delete a PDF document and its ChromaDB chunks."""
    try:
        from servers.pdf_registry import delete_pdf
        result = delete_pdf(doc_id)
        return result
    except Exception as e:
        logger.error(f"Error deleting PDF document: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/pdf/documents/{doc_id}")
async def get_pdf_document(doc_id: str):
    """Get details of a specific PDF document."""
    try:
        from servers.pdf_registry import get_pdf
        doc = get_pdf(doc_id)
        if doc:
            return {"success": True, "document": doc}
        return {"success": False, "error": "Document not found"}
    except Exception as e:
        logger.error(f"Error getting PDF document: {e}")
        return {"success": False, "error": str(e)}


# ============== MEDICAL IMAGES ENDPOINTS (A2UI Support) ==============

class ImageUploadResponse(BaseModel):
    """Response for image upload endpoint."""
    success: bool
    id: Optional[int] = None
    title: str = ""
    keywords: List[str] = []
    category: str = ""
    http_url: str = ""
    embeddings_generated: bool = False
    error: Optional[str] = None


@app.post("/api/subir-imagen", response_model=ImageUploadResponse)
async def subir_imagen_medica(
    file: UploadFile = File(...),
    keywords: str = Form(...),  # Comma-separated: "leucemia,células en canasta,linfocito"
    title: str = Form(""),
    description: str = Form(""),
    category: str = Form("general"),
    base_url: str = Form("http://localhost:8001")  # URL base for generating HTTP links
):
    """
    📸 ENDPOINT COMPLETO para subir imágenes médicas.
    
    Este endpoint hace TODO automáticamente:
    1. ✅ Guarda la imagen en el directorio de imágenes
    2. ✅ Registra en PostgreSQL con keywords y metadata
    3. ✅ Genera embeddings para el sistema RAG
    4. ✅ Retorna la URL HTTP completa
    
    Uso con curl:
    ```bash
    curl -X POST http://localhost:8001/api/subir-imagen \
      -F "file=@imagen.png" \
      -F "title=Células en Canasta - Leucemia" \
      -F "keywords=leucemia,células en canasta,linfocito,hematología" \
      -F "category=hematología" \
      -F "description=Frotis mostrando células en canasta típicas de LLC"
    ```
    
    Args:
        file: Archivo de imagen (PNG, JPG, etc.)
        keywords: Keywords separados por coma (ej: "leucemia,células en canasta")
        title: Título descriptivo de la imagen
        description: Descripción detallada
        category: Categoría (hematología, cardiología, etc.)
        base_url: URL base del servidor para generar links
    
    Returns:
        JSON con id, http_url, y confirmación de embeddings generados
    """
    try:
        from servers.medical_images_service import add_medical_image, IMAGES_DIR
        import shutil
        
        # 1. Parse keywords
        keyword_list = [kw.strip().lower() for kw in keywords.split(",") if kw.strip()]
        if not keyword_list:
            return ImageUploadResponse(
                success=False,
                error="Se requiere al menos un keyword. Sepáralos por coma."
            )
        
        # 2. Auto-generate title if not provided
        if not title:
            title = file.filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
        
        # 3. Save uploaded file temporarily
        temp_path = IMAGES_DIR / f"temp_{file.filename}"
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # 4. Add to database (copies file and registers)
        image_id = add_medical_image(
            file_path=str(temp_path),
            keywords=keyword_list,
            title=title,
            description=description,
            category=category.lower()
        )
        
        # 5. Remove temp file (was copied by add_medical_image)
        if temp_path.exists():
            temp_path.unlink()
        
        if not image_id:
            return ImageUploadResponse(
                success=False,
                error="Error al guardar la imagen en la base de datos"
            )
        
        # 6. Generate embeddings for RAG system
        embeddings_generated = False
        try:
            from servers.keyword_rag_service import sync_keywords_to_vector_store
            synced = sync_keywords_to_vector_store()
            embeddings_generated = synced > 0
            logger.info(f"🔄 Synced {synced} new keywords to RAG vector store")
        except Exception as e:
            logger.warning(f"Could not sync keywords to RAG: {e}")
        
        # 7. Generate HTTP URL
        http_url = f"{base_url.rstrip('/')}/api/medical-images/{image_id}"
        
        logger.info(f"✅ Image uploaded: {title} (ID: {image_id})")
        logger.info(f"🔗 URL: {http_url}")
        logger.info(f"🏷️  Keywords: {keyword_list}")
        
        return ImageUploadResponse(
            success=True,
            id=image_id,
            title=title,
            keywords=keyword_list,
            category=category.lower(),
            http_url=http_url,
            embeddings_generated=embeddings_generated
        )
        
    except Exception as e:
        logger.error(f"Error uploading medical image: {e}")
        return ImageUploadResponse(
            success=False,
            error=str(e)
        )


@app.post("/api/medical-images/upload")
async def upload_medical_image(
    file: UploadFile = File(...),
    keywords: str = Form(...),  # Comma-separated keywords
    title: str = Form(""),
    description: str = Form(""),
    category: str = Form("general")
):
    """
    Upload a medical image with keywords for matching.
    (Legacy endpoint - use /api/subir-imagen for full features)
    
    Keywords should be comma-separated, e.g.: "pericardio,corazón,anatomía"
    Categories: anatomy, pathology, radiology, lab, general
    """
    try:
        from servers.medical_images_service import add_medical_image, IMAGES_DIR
        import shutil
        
        # Parse keywords
        keyword_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
        if not keyword_list:
            return {"success": False, "error": "At least one keyword is required"}
        
        # Save uploaded file temporarily
        temp_path = IMAGES_DIR / f"temp_{file.filename}"
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Add to database
        image_id = add_medical_image(
            file_path=str(temp_path),
            keywords=keyword_list,
            title=title,
            description=description,
            category=category
        )
        
        # Remove temp file (was copied by add_medical_image)
        if temp_path.exists():
            temp_path.unlink()
        
        if image_id:
            return {
                "success": True,
                "id": image_id,
                "filename": file.filename,
                "keywords": keyword_list,
                "http_url": f"/api/medical-images/{image_id}"
            }
        return {"success": False, "error": "Failed to save image"}
        
    except Exception as e:
        logger.error(f"Error uploading medical image: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/medical-images")
async def list_medical_images():
    """List all medical images in the database."""
    try:
        from servers.medical_images_service import list_all_images
        images = list_all_images()
        return {"success": True, "images": images, "count": len(images)}
    except Exception as e:
        logger.error(f"Error listing medical images: {e}")
        return {"success": False, "error": str(e), "images": []}


@app.get("/api/medical-images/search")
async def search_medical_images(keywords: str, limit: int = 5):
    """
    Search for medical images by keywords.
    Keywords should be comma-separated.
    """
    try:
        from servers.medical_images_service import search_images_by_keywords
        keyword_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
        images = search_images_by_keywords(keyword_list, limit=limit)
        return {
            "success": True,
            "images": images,
            "count": len(images),
            "searched_keywords": keyword_list
        }
    except Exception as e:
        logger.error(f"Error searching medical images: {e}")
        return {"success": False, "error": str(e), "images": []}


@app.get("/api/medical-images/{image_id}")
async def get_medical_image(image_id: int):
    """
    Serve a medical image file by ID.
    Returns the actual image file for embedding in justifications.
    """
    try:
        from servers.medical_images_service import get_image_by_id
        from fastapi.responses import FileResponse
        
        img = get_image_by_id(image_id)
        if not img:
            raise HTTPException(status_code=404, detail="Image not found")
        
        filepath = img.get("filepath")
        if not filepath or not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Image file not found")
        
        # Determine media type
        ext = os.path.splitext(filepath)[1].lower()
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml"
        }
        media_type = media_types.get(ext, "application/octet-stream")
        
        return FileResponse(
            filepath,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=3600"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving medical image: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/medical-images/{image_id}/info")
async def get_medical_image_info(image_id: int):
    """Get metadata for a medical image (without the file)."""
    try:
        from servers.medical_images_service import get_image_by_id
        img = get_image_by_id(image_id)
        if img:
            # Remove filepath from response (internal detail)
            img.pop("filepath", None)
            return {"success": True, "image": img}
        return {"success": False, "error": "Image not found"}
    except Exception as e:
        logger.error(f"Error getting medical image info: {e}")
        return {"success": False, "error": str(e)}


@app.delete("/api/medical-images/{image_id}")
async def delete_medical_image(image_id: int):
    """Delete a medical image by ID."""
    try:
        from servers.medical_images_service import delete_image
        success = delete_image(image_id)
        return {"success": success}
    except Exception as e:
        logger.error(f"Error deleting medical image: {e}")
        return {"success": False, "error": str(e)}


@app.put("/api/medical-images/{image_id}")
async def update_medical_image_endpoint(image_id: int, payload: dict):
    """
    Update a medical image's metadata (title, description, category, keywords).
    If keywords change, old embeddings are deleted and new ones generated automatically.
    """
    try:
        from servers.medical_images_service import update_medical_image
        
        result = update_medical_image(
            image_id=image_id,
            title=payload.get("title"),
            description=payload.get("description"),
            category=payload.get("category"),
            keywords=payload.get("keywords")  # Should be a list
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error updating medical image: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/medical-images/enrich")
async def enrich_with_images(payload: dict):
    """
    Enrich a justification text with relevant medical images.
    Uses LLM to intelligently extract medical keywords for better matching.
    
    Request body:
        {
            "justification_text": "El pericardio es una membrana...",
            "question_text": "¿Cuál es la función del pericardio?"
        }
    
    Returns A2UI-compatible components for rendering images.
    """
    try:
        justification = payload.get("justification_text", payload.get("justification", ""))
        question = payload.get("question_text", payload.get("question", ""))
        
        # Try LLM-based extraction first (more accurate)
        try:
            from servers.medical_keywords_extractor import enrich_with_images_parallel
            result = await enrich_with_images_parallel(question, justification)
            return {"success": True, **result}
        except Exception as llm_error:
            logger.warning(f"LLM extraction failed, falling back to vocabulary: {llm_error}")
            # Fallback to vocabulary-based extraction
            from servers.medical_images_service import enrich_justification_with_images
            result = enrich_justification_with_images(justification, question)
            return {"success": True, **result}
        
    except Exception as e:
        logger.error(f"Error enriching with images: {e}")
        return {"success": False, "error": str(e), "a2ui_components": [], "keywords_detected": []}


@app.post("/api/medical-images/refine")
async def refine_images(payload: dict):
    """
    Deep RAG-based image refinement with LLM re-ranking.
    
    Pipeline:
    1. Embeds question text → pgvector similarity search → candidate keywords
    2. Resolves keywords → candidate images with FULL metadata
    3. LLM re-ranks candidates using title, description, keywords, category
    4. Compares with currently displayed images, prioritizes new/better ones
    5. Returns refined A2UI components
    """
    try:
        question = payload.get("question_text", "")
        justification = payload.get("justification_text", "")
        current_ids = set(payload.get("current_image_ids", []))
        
        if not question and not justification:
            return {"success": False, "error": "No text provided", "a2ui_components": []}
        
        from servers.keyword_rag_service import search_relevant_keywords
        from servers.db_pool import get_cursor
        
        # 1. Direct vector similarity search against question + justification
        combined = f"{question}\n{justification}".strip()
        rag_results = search_relevant_keywords(
            combined, top_k=25, similarity_threshold=0.35, weight_by_specificity=True
        )
        
        if not rag_results:
            return {
                "success": True,
                "a2ui_components": [],
                "keywords_detected": [],
                "images_found": 0,
                "message": "No relevant keywords found via RAG"
            }
        
        # 2. Collect all image IDs from matched keywords
        image_scores: dict = {}
        image_keywords: dict = {}
        
        for r in rag_results:
            for img_id in (r.get('image_ids') or []):
                score = r.get('combined_score', r.get('similarity', 0))
                if img_id not in image_scores or score > image_scores[img_id]:
                    image_scores[img_id] = score
                if img_id not in image_keywords:
                    image_keywords[img_id] = []
                image_keywords[img_id].append(r['keyword'])
        
        if not image_scores:
            return {
                "success": True,
                "a2ui_components": [],
                "keywords_detected": [r['keyword'] for r in rag_results[:5]],
                "images_found": 0,
                "message": "Keywords found but no images linked"
            }
        
        # 3. Get FULL metadata for ALL candidate images
        all_image_ids = list(image_scores.keys())
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("""
                SELECT id, filename, filepath, keywords, category, title, description
                FROM medical_images
                WHERE id = ANY(%s)
            """, (all_image_ids,))
            images_data = {row['id']: dict(row) for row in cur.fetchall()}
        
        # 4. LLM Re-ranking using full metadata context
        scored_images = []
        try:
            from langchain_openai import ChatOpenAI
            import json as json_mod
            
            # Build image catalog for LLM
            image_catalog = []
            for img_id in all_image_ids:
                if img_id not in images_data:
                    continue
                img = images_data[img_id]
                image_catalog.append({
                    "id": img_id,
                    "title": img.get('title', ''),
                    "description": (img.get('description', '') or '')[:400],  # Truncate very long
                    "keywords": img.get('keywords', []),
                    "category": img.get('category', ''),
                    "rag_score": round(image_scores.get(img_id, 0), 3),
                    "is_currently_shown": img_id in current_ids,
                })
            
            rerank_prompt = f"""Eres un experto en imágenes médicas educativas. Tu tarea es seleccionar las imágenes MÁS RELEVANTES para ayudar a un estudiante a entender esta pregunta de examen médico.

PREGUNTA DEL EXAMEN:
{question}

JUSTIFICACIÓN/CONTEXTO:
{justification[:800] if justification else '(sin justificación aún)'}

IMÁGENES CANDIDATAS (con metadata completa):
{json_mod.dumps(image_catalog, ensure_ascii=False, indent=1)}

INSTRUCCIONES:
1. Analiza la pregunta y su contexto clínico
2. Para CADA imagen candidata, evalúa qué tan relevante es considerando:
   - ¿El título describe algo directamente relacionado con la pregunta?
   - ¿La descripción contiene información que ayuda a entender la respuesta?
   - ¿Los keywords coinciden con los conceptos clave de la pregunta?
   - ¿La imagen aporta valor educativo para esta pregunta específica?
3. Asigna un score de 0-10 (10 = perfectamente relevante)
4. Prioriza imágenes que NO se muestran actualmente (is_currently_shown=false) si son igualmente relevantes

Responde SOLO con un JSON array con los IDs seleccionados (máximo 8), ordenados por relevancia:
[{{"id": <image_id>, "score": <0-10>, "reason": "<breve razón>"}}]

Solo incluye imágenes con score >= 4. JSON puro sin markdown."""

            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=1000)
            response = await llm.ainvoke(rerank_prompt)
            
            # Parse LLM response
            llm_text = response.content.strip()
            # Clean markdown code fences if present
            if llm_text.startswith('```'):
                llm_text = llm_text.split('\n', 1)[1] if '\n' in llm_text else llm_text[3:]
            if llm_text.endswith('```'):
                llm_text = llm_text[:-3]
            llm_text = llm_text.strip()
            
            llm_rankings = json_mod.loads(llm_text)
            
            logger.info(f"🧠 LLM re-ranked {len(llm_rankings)} images from {len(image_catalog)} candidates")
            
            # Build scored images from LLM ranking
            for rank in llm_rankings:
                img_id = rank.get('id')
                llm_score = rank.get('score', 0) / 10.0  # Normalize to 0-1
                reason = rank.get('reason', '')
                
                if img_id not in images_data:
                    continue
                
                img = images_data[img_id]
                is_new = img_id not in current_ids
                rag_score = image_scores.get(img_id, 0)
                
                # Combined score: 60% LLM judgment + 40% RAG similarity
                final_score = (0.6 * llm_score) + (0.4 * rag_score)
                
                scored_images.append({
                    **img,
                    'rag_score': rag_score,
                    'llm_score': llm_score,
                    'final_score': final_score,
                    'is_new': is_new,
                    'matched_keywords': image_keywords.get(img_id, []),
                    'llm_reason': reason,
                })
            
        except Exception as llm_error:
            logger.warning(f"⚠️ LLM re-ranking failed, falling back to RAG-only: {llm_error}")
            # Fallback: use RAG scores only (original logic)
            for img_id, score in image_scores.items():
                if img_id not in images_data:
                    continue
                img = images_data[img_id]
                is_new = img_id not in current_ids
                final_score = score * 1.2 if is_new else score
                scored_images.append({
                    **img,
                    'rag_score': score,
                    'llm_score': 0,
                    'final_score': final_score,
                    'is_new': is_new,
                    'matched_keywords': image_keywords.get(img_id, []),
                    'llm_reason': '',
                })
        
        # Sort by final score
        scored_images.sort(key=lambda x: x['final_score'], reverse=True)
        top_images = scored_images[:8]
        
        # 5. Build A2UI components with rich metadata
        a2ui_components = []
        for img in top_images:
            image_url = f"/api/medical-images/{img['id']}"
            matched_kws = img.get('matched_keywords', [])
            llm_reason = img.get('llm_reason', '')
            
            # Build rich caption: title + LLM reason or keyword matches
            caption_parts = []
            if img.get('title'):
                caption_parts.append(img['title'])
            if llm_reason:
                caption_parts.append(llm_reason)
            elif matched_kws:
                caption_parts.append(f"[{', '.join(matched_kws[:3])}]")
            caption = ' — '.join(caption_parts) if caption_parts else img.get('filename', '')
            
            a2ui_components.append({
                "type": "Image",
                "id": f"refined_img_{img['id']}",
                "properties": {
                    "url": image_url,
                    "alt": img.get('title') or img.get('filename', ''),
                    "caption": caption,
                    "category": img.get('category', 'general'),
                },
                "metadata": {
                    "image_id": img['id'],
                    "rag_score": round(img.get('rag_score', 0), 3),
                    "llm_score": round(img.get('llm_score', 0), 3),
                    "final_score": round(img.get('final_score', 0), 3),
                    "is_new": img['is_new'],
                    "matched_keywords": matched_kws,
                    "all_keywords": img.get('keywords', []),
                    "description": (img.get('description', '') or '')[:200],
                    "llm_reason": llm_reason,
                }
            })
        
        all_detected_keywords = list(set(
            kw for r in rag_results[:10] for kw in [r['keyword']]
        ))
        
        new_count = sum(1 for c in a2ui_components if c['metadata']['is_new'])
        logger.info(f"🔬 Refined images: {len(a2ui_components)} results "
                     f"({new_count} new, LLM re-ranked)")
        
        return {
            "success": True,
            "a2ui_components": a2ui_components,
            "keywords_detected": all_detected_keywords,
            "images_found": len(a2ui_components),
            "new_images": new_count,
            "total_candidates": len(all_image_ids),
            "reranked_by_llm": len(scored_images) > 0 and scored_images[0].get('llm_score', 0) > 0,
        }
        
    except Exception as e:
        logger.error(f"Error refining images: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e), "a2ui_components": [], "keywords_detected": []}

@app.post("/api/export")
async def export_state_to_file(payload: dict):
    """
    Exporta un estado a un archivo del workspace.
    
    Args:
        state_key: Clave del estado a exportar
        filename: Nombre del archivo (opcional)
        format: Formato del archivo: txt, md, json (default: txt)
    """
    try:
        from servers.filesystem_service.file_operations import load_state, write_file, _log_change
        
        state_key = payload.get("state_key")
        filename = payload.get("filename")
        file_format = payload.get("format", "txt")
        
        if not state_key:
            return {"error": "Se requiere state_key"}
        
        # Cargar el estado
        content = load_state(state_key)
        if not content:
            return {"error": f"No existe el estado '{state_key}'"}
        
        # Convertir a string si es necesario
        if not isinstance(content, str):
            import json
            content = json.dumps(content, indent=2, ensure_ascii=False)
        
        # Determinar nombre del archivo
        if not filename:
            filename = f"{state_key}.{file_format}"
        elif not filename.endswith(f".{file_format}"):
            filename = f"{filename}.{file_format}"
        
        # Escribir archivo
        write_file(filename, content)
        _log_change("EXPORT_STATE", f"state['{state_key}'] -> file['{filename}']", f"Exportado ({len(content)} chars)")
        
        logger.info(f"📤 State exported: {state_key} -> {filename}")
        
        return {
            "success": True,
            "state_key": state_key,
            "filename": filename,
            "size": len(content),
            "lines": content.count('\n') + 1
        }
        
    except Exception as e:
        logger.error(f"Error exporting state: {e}")
        return {"error": str(e)}

# ============== CHECKPOINT/VERSIONING ENDPOINTS ==============

@app.get("/api/checkpoints")
async def get_checkpoints(limit: int = 50):
    """Lista los checkpoints (versiones) disponibles"""
    try:
        from servers.versioning_service.git_checkpoints import list_checkpoints, init_repo
        init_repo()  # Asegurar que el repo existe
        checkpoints = list_checkpoints(limit)
        return {"checkpoints": checkpoints, "count": len(checkpoints)}
    except Exception as e:
        logger.error(f"Error listing checkpoints: {e}")
        return {"checkpoints": [], "error": str(e)}

@app.post("/api/checkpoints/create")
async def create_checkpoint_endpoint(payload: dict):
    """Crea un checkpoint manual"""
    try:
        from servers.versioning_service.git_checkpoints import create_checkpoint
        message = payload.get("message", "Checkpoint manual")
        tool_used = payload.get("tool_used", None)
        
        checkpoint = create_checkpoint(message, tool_used)
        if checkpoint:
            return {"success": True, "checkpoint": checkpoint}
        return {"success": False, "message": "No hay cambios para guardar"}
    except Exception as e:
        logger.error(f"Error creating checkpoint: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/checkpoints/restore")
async def restore_checkpoint_endpoint(payload: dict):
    """Restaura el estado a un checkpoint anterior"""
    try:
        from servers.versioning_service.git_checkpoints import restore_checkpoint
        commit_hash = payload.get("hash", "")
        
        if not commit_hash:
            return {"success": False, "error": "No se especificó el checkpoint"}
        
        result = restore_checkpoint(commit_hash)
        if result.get("success"):
            logger.info(f"🔄 Restored to checkpoint: {commit_hash[:8]}")
        return result
    except Exception as e:
        logger.error(f"Error restoring checkpoint: {e}")
        return {"success": False, "error": str(e)}

@app.get("/api/checkpoints/{commit_hash}/state")
async def get_checkpoint_state(commit_hash: str):
    """Obtiene el estado en un checkpoint específico (preview sin restaurar)"""
    try:
        from servers.versioning_service.git_checkpoints import get_state_at_checkpoint
        state = get_state_at_checkpoint(commit_hash)
        if state:
            return {"success": True, "state": state}
        return {"success": False, "error": "No se pudo obtener el estado"}
    except Exception as e:
        logger.error(f"Error getting checkpoint state: {e}")
        return {"success": False, "error": str(e)}

@app.get("/api/diff/{state_key}")
async def get_state_diff(state_key: str):
    """
    Gets the diff between current state and previous version.
    Returns old content, new content, and computed diff for side-by-side view.
    """
    try:
        import difflib
        from servers.versioning_service.git_checkpoints import get_state_at_checkpoint, list_checkpoints
        from servers.filesystem_service.file_operations import load_state
        
        # Get current content
        current = load_state(state_key)
        if current is None:
            raise HTTPException(status_code=404, detail=f"Estado '{state_key}' no encontrado")
        
        current_str = current if isinstance(current, str) else str(current)
        
        # Get previous version from git history (most recent checkpoint that has this state)
        old_content = ""
        previous_hash = None
        checkpoints = list_checkpoints(limit=20)
        
        # Find the most recent checkpoint that contains this state AND is different from current
        for cp in checkpoints:
            old_state = get_state_at_checkpoint(cp['hash'])
            if old_state and state_key in old_state:
                old_val = old_state.get(state_key, "")
                candidate_content = old_val if isinstance(old_val, str) else str(old_val)
                
                # Only use this checkpoint if it's DIFFERENT from current
                if candidate_content != current_str:
                    old_content = candidate_content
                    previous_hash = cp['hash']
                    break
                # If same content, this checkpoint is current - keep looking for previous
                continue
        
        # Compute line-by-line diff
        old_lines = old_content.splitlines()
        new_lines = current_str.splitlines()
        
        # Use SequenceMatcher for better diff
        diff_result = []
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                for line in old_lines[i1:i2]:
                    diff_result.append({'type': 'same', 'old': line, 'new': line})
            elif tag == 'replace':
                # Lines changed - show both old and new
                old_chunk = old_lines[i1:i2]
                new_chunk = new_lines[j1:j2]
                max_len = max(len(old_chunk), len(new_chunk))
                for k in range(max_len):
                    diff_result.append({
                        'type': 'change',
                        'old': old_chunk[k] if k < len(old_chunk) else '',
                        'new': new_chunk[k] if k < len(new_chunk) else ''
                    })
            elif tag == 'delete':
                for line in old_lines[i1:i2]:
                    diff_result.append({'type': 'remove', 'old': line, 'new': ''})
            elif tag == 'insert':
                for line in new_lines[j1:j2]:
                    diff_result.append({'type': 'add', 'old': '', 'new': line})
        
        return {
            "success": True,
            "state_key": state_key,
            "old_content": old_content,
            "new_content": current_str,
            "diff": diff_result,
            "has_changes": old_content != current_str,
            "previous_hash": previous_hash
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting diff: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/ask")
async def ask_question(payload: AskPayload):
    """
    Procesa una pregunta usando el React Agent.
    El agente razona, decide qué herramientas usar, las ejecuta,
    y continúa hasta tener una respuesta final.
    """
    logger.info(f"📝 Question: {payload.question}")

    if app.state.agent is None:
        raise HTTPException(status_code=503, detail="agent_not_configured")

    try:
        agent = app.state.agent
        
        # Configure thread for conversation memory
        config = {"configurable": {"thread_id": payload.thread_id}}
        
        # Build message with context files if provided
        message_content = payload.question
        
        if payload.context_files:
            import re
            from servers.filesystem_service.file_operations import get_full_state
            
            logger.info("=" * 60)
            logger.info("📎 CONTEXT FILES RECEIVED FROM FRONTEND:")
            for i, cf in enumerate(payload.context_files):
                cf_name = cf.get("name", "unknown")
                cf_content = cf.get("content", "")
                cf_lines = cf_content.count('\n') + 1 if cf_content else 0
                logger.info(f"  [{i}] Name: {cf_name}")
                logger.info(f"      Content length: {len(cf_content)} chars, {cf_lines} lines")
                logger.info(f"      Content preview: {cf_content[:100]}...")
            logger.info("=" * 60)
            
            context_parts = []
            edit_instructions = []
            state_keys = []  # Track state keys for smart editing
            line_ranges = {}  # Track line ranges for snippets {key: (start, end)}
            
            # Load current state for auto-detection
            current_state = {}
            try:
                current_state = get_full_state() or {}
            except:
                pass
            
            for ctx_file in payload.context_files:
                file_name = ctx_file.get("name", "unknown")
                file_content = ctx_file.get("content", "")
                context_parts.append(f"📎 DOCUMENTO ADJUNTO: {file_name}\n```\n{file_content}\n```")
                
                # Parse the file reference - handle state:, file:, and snippet: prefixes
                key = None
                is_state = False
                start_line = None
                end_line = None
                
                if file_name.startswith("state:"):
                    key = file_name.replace("state:", "")
                    is_state = True
                elif file_name.startswith("file:"):
                    key = file_name.replace("file:", "")
                    is_state = False
                elif file_name.startswith("snippet:"):
                    # Extract key and line range from snippet:name[start-end] or snippet:name [start - end] format
                    # Allow optional spaces around brackets and dash
                    match = re.match(r'snippet:([^\[]+?)\s*\[\s*(\d+)\s*-\s*(\d+)\s*\]', file_name)
                    if match:
                        key = match.group(1).strip()  # Remove trailing space from name
                        start_line = int(match.group(2))
                        end_line = int(match.group(3))
                        line_ranges[key] = (start_line, end_line)
                        is_state = True  # Snippets from viewer are typically from state
                        logger.info(f"📍 SNIPPET PARSED: key='{key}', lines={start_line}-{end_line}")
                    else:
                        # Fallback for snippet without range
                        match = re.match(r'snippet:([^\[]+)', file_name)
                        if match:
                            key = match.group(1)
                            is_state = True
                else:
                    # AUTO-DETECT: Si el nombre no tiene prefijo, buscar el texto en estados existentes
                    if file_content and current_state and len(file_content.strip()) > 20:
                        # Normalizar texto para comparación (solo alfanuméricos y espacios)
                        def normalize(text):
                            import re as re_inner
                            # Remover markdown, puntuación extra, normalizar espacios
                            text = re_inner.sub(r'\*\*|\*|_|#', '', text)  # Quitar markdown
                            text = ' '.join(text.split()).lower()
                            return text
                        
                        # Usar un fragmento significativo del texto pegado para buscar
                        normalized_content = normalize(file_content)
                        search_fragment = normalized_content[:150]  # Primeros 150 chars
                        
                        best_match = None
                        best_lines = None
                        
                        logger.info(f"🔍 Buscando texto pegado en estados... (fragmento: '{search_fragment[:50]}...')")
                        
                        for state_key, state_value in current_state.items():
                            if state_key.startswith('_'):
                                continue  # Skip internal keys
                            if not isinstance(state_value, str):
                                continue
                            
                            normalized_state = normalize(state_value)
                            
                            # Verificar si el fragmento está contenido en el estado
                            if search_fragment in normalized_state:
                                best_match = state_key
                                
                                # Tratar de encontrar las líneas específicas
                                state_lines = state_value.split('\n')
                                pasted_lines = file_content.strip().split('\n')
                                first_pasted = normalize(pasted_lines[0])
                                
                                for i, state_line in enumerate(state_lines):
                                    if first_pasted[:30] in normalize(state_line):
                                        best_lines = (i + 1, i + len(pasted_lines))
                                        break
                                
                                break
                        
                        if best_match:
                            key = best_match
                            is_state = True
                            if best_lines:
                                start_line, end_line = best_lines
                                line_ranges[key] = (start_line, end_line)
                                logger.info(f"🔍 AUTO-DETECTADO: Texto pegado es de '{key}' líneas {start_line}-{end_line}")
                            else:
                                logger.info(f"🔍 AUTO-DETECTADO: Texto pegado pertenece al estado '{key}'")
                            file_name = f"texto_pegado_de:{key}"
                
                if key:
                    if is_state:
                        state_keys.append(key)
                        if key in line_ranges:
                            sl, el = line_ranges[key]
                            # Snippet con rango específico - instrucciones más precisas
                            edit_instructions.append(f"  ⚠️ FRAGMENTO SELECCIONADO: '{key}' líneas {sl}-{el}")
                            edit_instructions.append(f"  • BORRAR: delete_lines('{key}', start_line={sl}, end_line={el}) ← ¡Para eliminar!")
                            edit_instructions.append(f"  • EDITAR: smart_edit_state('{key}', 'instrucción', start_line={sl}, end_line={el})")
                            edit_instructions.append(f"  • RESUMIR: smart_resume(text='contenido del fragmento', state_key='{key}', start_line={sl}, end_line={el})")
                        else:
                            edit_instructions.append(f"  • EDICIÓN INTELIGENTE: smart_edit_state('{key}', 'instrucción de qué cambiar')")
                            edit_instructions.append(f"  • RESUMIR: smart_resume(text='texto a resumir', state_key='{key}')")
                        edit_instructions.append(f"  • EDICIÓN EXACTA: correct_text_in_state('{key}', 'texto_viejo', 'texto_nuevo')")
                    else:
                        edit_instructions.append(f"  • EDICIÓN INTELIGENTE: smart_edit_file('{key}', 'instrucción de qué cambiar')")
                        edit_instructions.append(f"  • EDICIÓN EXACTA: edit_document('{key}', 'texto_viejo', 'texto_nuevo')")
            
            context_str = "\n\n".join(context_parts)
            
            # Create clearer instructions
            state_keys_str = ", ".join([f"'{k}'" for k in state_keys]) if state_keys else "ninguno"
            
            # Advertencia especial si hay fragmento seleccionado
            fragment_warning = ""
            if line_ranges:
                for k, (sl, el) in line_ranges.items():
                    fragment_warning = f"""

🎯 ATENCIÓN CRÍTICA: El usuario seleccionó SOLO las líneas {sl}-{el} de '{k}'.
- Para BORRAR/ELIMINAR: delete_lines('{k}', start_line={sl}, end_line={el}) ← ¡Usa si dice 'borra', 'elimina', 'quita'!
- Para EDITAR: smart_edit_state('{k}', 'instrucción', start_line={sl}, end_line={el})
- Para RESUMIR: smart_resume(text='<el texto del fragmento>', state_key='{k}', start_line={sl}, end_line={el})
⚠️ DEBES pasar start_line={sl} y end_line={el} para que los cambios afecten SOLO ese fragmento."""
            
            message_content = f"""⚠️ DOCUMENTO(S) ADJUNTO(S) - El usuario ha seleccionado documento(s) para trabajar.

📋 ESTADOS/DOCUMENTOS ADJUNTOS: {state_keys_str}{fragment_warning}

📝 HERRAMIENTAS PARA EDITAR:
{chr(10).join(edit_instructions) if edit_instructions else "  (ninguna acción disponible)"}

⛔ REGLAS IMPORTANTES:
- SI hay un fragmento seleccionado (líneas específicas), USA start_line y end_line para editar SOLO ese fragmento
- USA smart_edit_state() para editar ESTADOS (keys en agent_state.json)
- USA smart_edit_file() para editar ARCHIVOS (files en workspace)
- El documento '{state_keys[0] if state_keys else "adjunto"}' es un ESTADO, NO un archivo
- NO uses save_state para crear nuevas claves, solo para actualizar existentes

---

{context_str}

---

🗣️ Instrucción del usuario: {payload.question}"""
            
            logger.info(f"📎 Context files attached for EDITING: {[f.get('name') for f in payload.context_files]}")
            logger.info(f"📋 Detected state keys: {state_keys}")
            logger.info(f"📍 Line ranges detected: {line_ranges}")
            if fragment_warning:
                logger.info(f"⚠️ Fragment warning: {fragment_warning[:200]}...")
        
        # Invoke agent with ReAct loop
        logger.info("🤖 Invoking React Agent...")
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=message_content)]},
            config=config
        )
        
        # Extract final response
        messages = result.get("messages", [])
        
        # Log ALL messages for debugging
        logger.info(f"📊 Total messages in conversation: {len(messages)}")
        
        tool_calls = []
        tool_results = []
        
        for i, msg in enumerate(messages):
            msg_type = type(msg).__name__
            
            if isinstance(msg, AIMessage):
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_name = tc.get("name", "unknown")
                        tool_args = tc.get("args", {})
                        tool_calls.append(tool_name)
                        logger.info(f"🔧 [{i}] TOOL CALL: {tool_name}")
                        logger.info(f"   📥 Arguments: {tool_args}")
                elif msg.content:
                    logger.info(f"💬 [{i}] AI Response: {msg.content[:200]}...")
            elif msg_type == "ToolMessage":
                # Log tool results
                tool_content = str(msg.content)[:300] if msg.content else "No content"
                tool_results.append(tool_content)
                logger.info(f"📤 [{i}] TOOL RESULT: {tool_content}...")
            elif isinstance(msg, HumanMessage):
                logger.info(f"👤 [{i}] Human: {str(msg.content)[:100]}...")
        
        # Get the last AI message as the answer
        answer = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                answer = msg.content
                break
        
        if tool_calls:
            logger.info(f"🔧 Total tools used: {tool_calls}")
        
        # Crear checkpoint automático si se usaron herramientas que modifican estado
        checkpoint_hash = None
        state_modifying_tools = {'save_state', 'write_file', 'smart_edit_state', 'smart_edit_file', 
                                  'smart_enrich_document', 'correct_text_in_state', 'edit_document'}
        if any(tool in tool_calls for tool in state_modifying_tools):
            try:
                from servers.versioning_service.git_checkpoints import create_checkpoint
                short_question = payload.question[:50] + "..." if len(payload.question) > 50 else payload.question
                tools_str = ", ".join(tool_calls[:3])
                checkpoint = create_checkpoint(
                    message=short_question,
                    tool_used=tools_str
                )
                if checkpoint:
                    checkpoint_hash = checkpoint.get("short_hash")
                    logger.info(f"📸 Checkpoint creado: {checkpoint_hash}")
            except Exception as e:
                logger.warning(f"No se pudo crear checkpoint: {e}")
        
        return {
            "answer": answer,
            "tools_used": tool_calls,
            "thread_id": payload.thread_id,
            "checkpoint": checkpoint_hash
        }
        
    except Exception as e:
        logger.error(f"Error processing question: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ask/stream")
async def ask_question_stream(payload: AskPayload):
    """
    Streaming version of the ask endpoint.
    Returns SSE (Server-Sent Events) for real-time updates.
    """
    import json

    if app.state.agent is None:
        raise HTTPException(status_code=503, detail="agent_not_configured")

    async def generate_stream():
        try:
            agent = app.state.agent
            config = {"configurable": {"thread_id": payload.thread_id}}
            
            # Build message content (simplified version)
            message_content = payload.question
            if payload.context_files:
                context_parts = []
                for ctx_file in payload.context_files:
                    file_name = ctx_file.get("name", "unknown")
                    file_content = ctx_file.get("content", "")
                    context_parts.append(f"📎 DOCUMENTO ADJUNTO: {file_name}\n```\n{file_content}\n```")
                message_content = "\n\n".join(context_parts) + f"\n\n❓ PREGUNTA: {payload.question}"
            
            # Start streaming
            yield f"data: {json.dumps({'type': 'start', 'content': ''})}\n\n"
            
            # Stream agent response
            full_answer = ""
            tool_calls = []
            
            async for event in agent.astream_events(
                {"messages": [HumanMessage(content=message_content)]},
                config=config,
                version="v2"
            ):
                event_type = event.get("event", "")
                
                if event_type == "on_chat_model_stream":
                    # Streaming token from LLM
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        content = chunk.content
                        full_answer += content
                        yield f"data: {json.dumps({'type': 'chunk', 'content': content})}\n\n"
                
                elif event_type == "on_tool_start":
                    # Tool started
                    tool_name = event.get("name", "unknown")
                    tool_calls.append(tool_name)
                    yield f"data: {json.dumps({'type': 'tool_start', 'content': f'🔧 Usando herramienta: {tool_name}'})}\n\n"
                
                elif event_type == "on_tool_end":
                    # Tool finished
                    yield f"data: {json.dumps({'type': 'tool_end', 'content': '✅ Herramienta completada'})}\n\n"
            
            # Send completion
            yield f"data: {json.dumps({'type': 'done', 'content': full_answer, 'tools_used': tool_calls, 'success': True})}\n\n"
            
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e), 'success': False})}\n\n"
    
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.get("/api/conversation/{thread_id}")
async def get_conversation(thread_id: str):
    """Obtiene el historial de conversación de un thread"""
    try:
        memory = app.state.memory
        # Get checkpoint for thread (prefer async API when available)
        config = {"configurable": {"thread_id": thread_id}}
        if hasattr(memory, "aget"):
            checkpoint = await memory.aget(config)
        else:
            checkpoint = memory.get(config)

        if not checkpoint:
            return {"messages": [], "thread_id": thread_id}

        messages = checkpoint.get("channel_values", {}).get("messages", [])
        
        return {
            "messages": [
                {
                    "role": "user" if isinstance(m, HumanMessage) else "assistant",
                    "content": m.content
                }
                for m in messages
                if isinstance(m, (HumanMessage, AIMessage)) and m.content
            ],
            "thread_id": thread_id
        }
    except Exception as e:
        logger.error(f"Error getting conversation: {e}")
        return {"messages": [], "thread_id": thread_id, "error": str(e)}


# ============== CONFIGURATION ENDPOINTS ==============

class ConfigUpdatePayload(BaseModel):
    keys: Dict[str, str]


@app.get("/api/config/status")
async def config_status():
    """Return which managed keys are configured. Never returns the actual values.
    Frontend uses this to decide whether to show the setup screen."""
    return _config_status()


@app.post("/api/config/keys")
async def config_update_keys(payload: ConfigUpdatePayload):
    """Persist key updates and (re)initialise the agent if needed."""
    result = _config_update(payload.keys or {})

    # If we now have OPENAI_API_KEY and the agent isn't running, build it.
    if os.environ.get("OPENAI_API_KEY") and app.state.agent is None:
        try:
            await _init_agent(app)
            result["agent"] = "initialised"
        except Exception as exc:
            logger.error(f"agent init after config update failed: {exc!r}")
            result["agent"] = f"error: {exc!r}"
    else:
        result["agent"] = "ready" if app.state.agent is not None else "missing required keys"

    return result


# ============== STATE VIEWER (from original) ==============

STATE_VIEWER_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🤖 React Agent IDE</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Inter:wght@400;500;600&display=swap');
        
        :root {
            --bg-dark: #0d1117;
            --bg-sidebar: #161b22;
            --bg-editor: #0d1117;
            --bg-hover: #21262d;
            --border: #30363d;
            --text: #c9d1d9;
            --text-muted: #8b949e;
            --accent: #58a6ff;
            --accent-green: #3fb950;
            --accent-yellow: #d29922;
            --accent-purple: #a371f7;
        }
        
        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg-dark);
            min-height: 100vh;
            color: var(--text);
        }
        
        .ide-container {
            display: grid;
            grid-template-columns: 240px 1fr 380px;
            height: 100vh;
        }
        
        /* Sidebar - File Explorer */
        .sidebar {
            background: var(--bg-sidebar);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
        }
        
        .sidebar-header {
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .file-tree {
            flex: 1;
            overflow-y: auto;
            padding: 8px 0;
        }
        
        .tree-section {
            margin-bottom: 8px;
        }
        
        .tree-section-header {
            padding: 6px 16px;
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
        }
        
        .tree-section-header:hover {
            background: var(--bg-hover);
        }
        
        .tree-item {
            padding: 6px 12px 6px 24px;
            font-size: 13px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text);
            border-left: 2px solid transparent;
            transition: all 0.15s;
        }
        
        .tree-item:hover {
            background: var(--bg-hover);
        }
        
        .tree-item.active {
            background: rgba(88, 166, 255, 0.1);
            border-left-color: var(--accent);
            color: var(--accent);
        }
        
        .tree-item .icon {
            font-size: 14px;
        }
        
        .tree-item .name {
            flex: 1;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .tree-item .badge {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 10px;
            background: var(--bg-hover);
            color: var(--text-muted);
        }
        
        /* Botón adjuntar - MUY VISIBLE */
        .btn-attach {
            background: #2563eb;
            color: white;
            border: none;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            margin-left: auto;
            white-space: nowrap;
        }
        
        .btn-attach:hover {
            background: #1d4ed8;
        }
        
        .btn-attach.attached {
            background: #16a34a;
        }
        
        .btn-attach.attached:hover {
            background: #dc2626;
        }
        
        /* Botón eliminar */
        .btn-delete {
            background: transparent;
            color: var(--text-muted);
            border: none;
            padding: 3px 6px;
            border-radius: 4px;
            font-size: 12px;
            cursor: pointer;
            opacity: 0.5;
            transition: all 0.15s;
        }
        
        .btn-delete:hover {
            background: #dc2626;
            color: white;
            opacity: 1;
        }
        
        /* Editor Panel */
        .editor-panel {
            display: flex;
            flex-direction: column;
            background: var(--bg-editor);
            overflow: hidden;
            min-height: 0;
        }
        
        .editor-tabs {
            display: flex;
            background: var(--bg-sidebar);
            border-bottom: 1px solid var(--border);
            overflow-x: auto;
        }
        
        .editor-tab {
            padding: 10px 16px;
            font-size: 13px;
            border-right: 1px solid var(--border);
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text-muted);
            white-space: nowrap;
        }
        
        .editor-tab.active {
            background: var(--bg-editor);
            color: var(--text);
            border-bottom: 2px solid var(--accent);
            margin-bottom: -1px;
        }
        
        .editor-content {
            flex: 1;
            overflow: auto;
            padding: 0;
            min-height: 0;
        }
        
        .code-view {
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            line-height: 1.6;
            padding: 16px;
        }
        
        .code-line {
            display: flex;
            min-height: 21px;
        }
        
        .line-number {
            width: 50px;
            text-align: right;
            padding-right: 16px;
            color: var(--text-muted);
            user-select: none;
            flex-shrink: 0;
        }
        
        .line-content {
            flex: 1;
            white-space: pre-wrap;
            word-break: break-word;
        }
        
        .empty-state {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: var(--text-muted);
            gap: 12px;
        }
        
        .empty-state .icon {
            font-size: 48px;
            opacity: 0.5;
        }
        
        /* Status Bar */
        .status-bar {
            background: var(--bg-sidebar);
            border-top: 1px solid var(--border);
            padding: 4px 16px;
            font-size: 12px;
            color: var(--text-muted);
            display: flex;
            justify-content: space-between;
        }
        
        .status-item {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent-green);
        }
        
        /* Chat Panel */
        .chat-panel {
            background: var(--bg-sidebar);
            border-left: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            height: 100vh;
            max-height: 100vh;
            overflow: hidden;
        }
        
        .chat-header {
            padding: 12px 16px;
            border-bottom: 1px solid var(--border);
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
            flex-shrink: 0;
        }
        
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            scroll-behavior: smooth;
            min-height: 0; /* Critical for flex overflow to work */
        }
        
        .message {
            padding: 10px 14px;
            border-radius: 12px;
            max-width: 95%;
            font-size: 13px;
            line-height: 1.5;
        }
        
        .message.user {
            background: var(--accent);
            align-self: flex-end;
            color: white;
            border-bottom-right-radius: 4px;
        }
        
        .message.assistant {
            background: var(--bg-hover);
            border: 1px solid var(--border);
            align-self: flex-start;
            border-bottom-left-radius: 4px;
        }
        
        .message.system {
            background: rgba(210, 153, 34, 0.1);
            border: 1px solid rgba(210, 153, 34, 0.3);
            font-size: 12px;
            text-align: center;
            align-self: center;
            color: var(--accent-yellow);
        }
        
        .tools-badge {
            font-size: 11px;
            color: var(--accent-green);
        }
        
        .message-footer {
            margin-top: 8px;
            padding-top: 8px;
            border-top: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        
        .checkpoint-btn {
            font-size: 10px;
            padding: 2px 8px;
            background: linear-gradient(135deg, #4a5568, #2d3748);
            color: var(--text-secondary);
            border: 1px solid var(--border);
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .checkpoint-btn:hover {
            background: linear-gradient(135deg, #667eea, #4a5568);
            color: var(--text-primary);
            border-color: var(--accent);
            transform: scale(1.02);
        }
        
        /* History panel styles */
        .history-panel {
            background: var(--bg-darker);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 12px;
            max-height: 200px;
            overflow-y: auto;
            display: none;
        }
        
        .history-panel.visible {
            display: block;
        }
        
        .history-panel h4 {
            margin: 0 0 10px 0;
            color: var(--accent);
            font-size: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .history-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 8px;
            margin: 4px 0;
            background: var(--bg-primary);
            border-radius: 4px;
            font-size: 11px;
            transition: background 0.2s;
        }
        
        .history-item:hover {
            background: var(--bg-secondary);
        }
        
        .history-item .hash {
            font-family: monospace;
            color: var(--accent);
            font-weight: bold;
        }
        
        .history-item .message {
            flex: 1;
            color: var(--text-secondary);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .history-item .time {
            color: var(--text-muted);
            font-size: 10px;
        }
        
        .history-item .restore-btn {
            padding: 2px 6px;
            font-size: 10px;
            background: var(--accent);
            color: #fff;
            border: none;
            border-radius: 3px;
            cursor: pointer;
        }
        
        .history-item .restore-btn:hover {
            background: var(--accent-hover);
        }
        
        .toggle-history-btn {
            font-size: 11px;
            padding: 4px 10px;
            background: var(--bg-secondary);
            color: var(--text-secondary);
            border: 1px solid var(--border);
            border-radius: 4px;
            cursor: pointer;
            margin-bottom: 8px;
        }
        
        .toggle-history-btn:hover {
            background: var(--bg-primary);
            color: var(--accent);
        }
        
        .quick-actions {
            padding: 8px 12px;
            border-top: 1px solid var(--border);
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }
        
        .quick-btn {
            padding: 5px 10px;
            border-radius: 6px;
            border: 1px solid var(--border);
            background: transparent;
            color: var(--text-muted);
            font-size: 11px;
            cursor: pointer;
            transition: all 0.15s;
        }
        
        .quick-btn:hover {
            background: var(--bg-hover);
            color: var(--text);
            border-color: var(--accent);
        }
        
        /* Attached files tags */
        .attached-files {
            padding: 8px 12px;
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            border-bottom: 1px solid var(--border);
            min-height: 0;
        }
        
        .attached-files:empty {
            display: none;
        }
        
        .file-tag {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            background: rgba(88, 166, 255, 0.15);
            border: 1px solid var(--accent);
            border-radius: 16px;
            font-size: 12px;
            color: var(--accent);
            animation: tagIn 0.2s ease;
        }
        
        @keyframes tagIn {
            from { transform: scale(0.8); opacity: 0; }
            to { transform: scale(1); opacity: 1; }
        }
        
        @keyframes slideIn {
            from { transform: translateX(20px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        
        @keyframes slideUp {
            from { transform: translateX(-50%) translateY(20px); opacity: 0; }
            to { transform: translateX(-50%) translateY(0); opacity: 1; }
        }
        
        @keyframes slideDown {
            from { transform: translateX(-50%) translateY(0); opacity: 1; }
            to { transform: translateX(-50%) translateY(20px); opacity: 0; }
        }
        
        .file-tag .tag-icon {
            font-size: 11px;
        }
        
        .file-tag .tag-name {
            max-width: 120px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .file-tag .tag-remove {
            cursor: pointer;
            opacity: 0.6;
            font-size: 14px;
            line-height: 1;
        }
        
        .file-tag .tag-remove:hover {
            opacity: 1;
        }
        
        /* Snippet reference tags */
        .snippet-tag {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            background: linear-gradient(135deg, #4a9eff20, #a855f720);
            border: 1px solid #4a9eff40;
            color: #8cb4ff;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-family: 'Consolas', 'Monaco', monospace;
            margin: 2px;
            animation: snippetIn 0.2s ease;
        }
        
        @keyframes snippetIn {
            from { transform: translateY(-5px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        
        .snippet-tag .snippet-icon {
            font-size: 10px;
        }
        
        .snippet-tag .snippet-lines {
            color: #a855f7;
            font-weight: 600;
        }
        
        .snippet-tag .snippet-remove {
            cursor: pointer;
            opacity: 0.6;
            margin-left: 4px;
        }
        
        .snippet-tag .snippet-remove:hover {
            opacity: 1;
            color: #ff6b6b;
        }
        
        .snippet-container {
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            margin-bottom: 4px;
        }
        
        .chat-input-area {
            padding: 12px;
            border-top: 1px solid var(--border);
            flex-shrink: 0; /* Don't shrink input area */
        }
        
        .chat-input-wrapper {
            display: flex;
            gap: 8px;
            background: var(--bg-dark);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 8px 12px;
            flex-wrap: wrap;
        }
        
        .chat-input {
            flex: 1;
            min-width: 150px;
            background: transparent;
            border: none;
            color: var(--text);
            font-size: 13px;
            outline: none;
        }
        
        .chat-input::placeholder {
            color: var(--text-muted);
        }
        
        .attach-hint {
            font-size: 11px;
            color: var(--text-muted);
            padding: 4px 0;
            width: 100%;
        }
        
        .send-btn {
            background: var(--accent);
            border: none;
            color: white;
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            transition: opacity 0.15s;
        }
        
        .send-btn:hover {
            opacity: 0.9;
        }
        
        .send-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        /* Add to context button on tree items */
        .tree-item .add-context {
            font-size: 14px;
            padding: 2px 8px;
            background: var(--accent);
            color: white;
            border-radius: 4px;
            cursor: pointer;
            margin-left: auto;
            flex-shrink: 0;
            opacity: 0.7;
            transition: all 0.15s;
        }
        
        .tree-item:hover .add-context {
            opacity: 1;
            transform: scale(1.1);
        }
        
        .tree-item .add-context.attached {
            background: var(--accent-green);
        }
        
        .loading-dots span {
            display: inline-block;
            width: 6px;
            height: 6px;
            background: white;
            border-radius: 50%;
            margin: 0 2px;
            animation: bounce 1.4s infinite ease-in-out both;
        }
        
        .loading-dots span:nth-child(1) { animation-delay: -0.32s; }
        .loading-dots span:nth-child(2) { animation-delay: -0.16s; }
        
        @keyframes bounce {
            0%, 80%, 100% { transform: scale(0); }
            40% { transform: scale(1); }
        }
        
        /* Scrollbar styling */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--border);
            border-radius: 4px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--text-muted);
        }
        
        /* Debug log items */
        .debug-item {
            padding: 4px 8px 4px 24px;
            font-size: 11px;
            border-bottom: 1px solid var(--border);
            font-family: 'JetBrains Mono', monospace;
        }
        
        .debug-item .debug-time {
            color: var(--text-muted);
            font-size: 10px;
        }
        
        .debug-item .debug-op {
            font-weight: 600;
            margin: 0 4px;
        }
        
        .debug-op-SAVE_STATE { color: #4ade80; }
        .debug-op-WRITE_FILE { color: #60a5fa; }
        .debug-op-DELETE { color: #f87171; }
        
        .debug-item .debug-target {
            color: #fbbf24;
        }
        
        .debug-item .debug-preview {
            color: var(--text-muted);
            font-size: 10px;
            display: block;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 200px;
        }
        
        /* ======= @ Mention Autocomplete Styles ======= */
        .chat-input-area {
            position: relative;
        }
        
        #mentionDropdown {
            display: none;
            position: absolute;
            bottom: 100%;
            left: 12px;
            right: 12px;
            max-height: 260px;
            overflow-y: auto;
            background: var(--bg-sidebar);
            border: 1px solid var(--border);
            border-radius: 8px;
            box-shadow: 0 -4px 20px rgba(0,0,0,0.4);
            z-index: 1000;
            margin-bottom: 8px;
        }
        
        #mentionDropdown.active {
            display: block;
        }
        
        .mention-header {
            padding: 8px 12px;
            font-size: 10px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            background: var(--bg-dark);
            border-bottom: 1px solid var(--border);
            position: sticky;
            top: 0;
        }
        
        .mention-item {
            display: flex;
            align-items: center;
            padding: 8px 12px;
            cursor: pointer;
            transition: background-color 0.1s;
            border-bottom: 1px solid var(--border);
        }
        
        .mention-item:last-child {
            border-bottom: none;
        }
        
        .mention-item:hover,
        .mention-item.selected {
            background: var(--bg-hover);
        }
        
        .mention-item.selected {
            background: rgba(88, 166, 255, 0.15);
            border-left: 2px solid var(--accent);
        }
        
        .mention-item .m-icon {
            width: 24px;
            height: 24px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-right: 10px;
            font-size: 12px;
        }
        
        .mention-item.state .m-icon {
            background: linear-gradient(135deg, #3fb950, #238636);
        }
        
        .mention-item.file .m-icon {
            background: linear-gradient(135deg, #58a6ff, #1f6feb);
        }
        
        .mention-item .m-name {
            flex: 1;
            font-size: 13px;
            color: var(--text);
        }
        
        .mention-item .m-type {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 8px;
            background: var(--bg-dark);
            color: var(--text-muted);
        }
        
        .mention-empty {
            padding: 16px;
            text-align: center;
            color: var(--text-muted);
            font-size: 12px;
        }
        
        .mention-hint {
            font-size: 10px;
            color: var(--text-muted);
            padding: 4px 12px 8px;
            border-top: 1px solid var(--border);
        }
        
        .mention-hint kbd {
            background: var(--bg-dark);
            padding: 1px 5px;
            border-radius: 3px;
            font-size: 10px;
            margin: 0 2px;
        }
    </style>
</head>
<body>
    <div class="ide-container">
        <!-- File Explorer Sidebar -->
        <div class="sidebar">
            <div class="sidebar-header">
                <span>📁</span> Explorer
            </div>
            <div class="file-tree" id="fileTree">
                <!-- Import Button -->
                <div style="padding: 8px 12px; border-bottom: 1px solid var(--border);">
                    <button onclick="triggerFileImport()" style="
                        width: 100%;
                        padding: 8px 12px;
                        background: linear-gradient(135deg, #238636 0%, #2ea043 100%);
                        border: none;
                        border-radius: 6px;
                        color: white;
                        cursor: pointer;
                        font-size: 12px;
                        font-weight: 500;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        gap: 6px;
                        transition: all 0.2s;
                    " onmouseover="this.style.transform='translateY(-1px)'" onmouseout="this.style.transform='translateY(0)'">
                        📥 Importar Archivo
                    </button>
                    <input type="file" id="fileImportInput" style="display: none;" 
                           accept=".txt,.md,.json,.csv,.html,.xml,.py,.js,.ts,.yaml,.yml,.sql,.log"
                           onchange="handleFileImport(event)">
                </div>
                <div class="tree-section">
                    <div class="tree-section-header">
                        <span>▼</span> 🗄️ AGENT STATE
                    </div>
                    <div id="stateItems"></div>
                </div>
                <div class="tree-section">
                    <div class="tree-section-header">
                        <span>▼</span> 📂 WORKSPACE FILES
                    </div>
                    <div id="fileItems"></div>
                </div>
                <div class="tree-section">
                    <div class="tree-section-header" onclick="toggleDebugPanel()" style="cursor:pointer">
                        <span id="debugArrow">▶</span> 🔧 DEBUG LOG
                    </div>
                    <div id="debugItems" style="display:none; max-height: 200px; overflow-y: auto;"></div>
                </div>
            </div>
        </div>
        
        <!-- Editor Panel -->
        <div class="editor-panel">
            <div class="editor-tabs" id="editorTabs">
                <div class="editor-tab active" data-key="welcome">
                    <span>👋</span> Welcome
                </div>
            </div>
            <div class="editor-content" id="editorContent">
                <div class="empty-state">
                    <div class="icon">🤖</div>
                    <div>React Agent IDE</div>
                    <div style="font-size: 12px;">Selecciona un archivo del explorador</div>
                </div>
            </div>
            <div class="status-bar">
                <div class="status-item">
                    <span class="status-dot"></span>
                    <span>React Agent Ready</span>
                </div>
                <div class="status-item">
                    <button id="editToggleBtn" onclick="toggleEditMode()" style="background:#4a9eff;border:none;color:white;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px;display:none;">✏️ Editar</button>
                    <span id="saveStatus" style="font-size:10px;margin-left:8px;"></span>
                </div>
                <div class="status-item" id="lastUpdated">
                    Actualizado: --
                </div>
            </div>
        </div>
        
        <!-- Chat Panel -->
        <div class="chat-panel">
            <div class="chat-header">
                <span>💬</span> Agent Chat
                <div style="margin-left:auto;display:flex;gap:6px;">
                    <button class="toggle-history-btn" onclick="toggleHistoryPanel()" title="Ver historial de versiones">
                        📚 Versiones
                    </button>
                    <button class="toggle-history-btn" onclick="createManualCheckpoint()" title="Crear checkpoint manual">
                        📸 +
                    </button>
                </div>
            </div>
            
            <!-- History Panel (hidden by default) -->
            <div class="history-panel" id="historyPanel">
                <h4>
                    📚 Historial de Versiones
                    <button onclick="toggleHistoryPanel()" style="background:none;border:none;color:var(--text-muted);cursor:pointer;">✕</button>
                </h4>
                <div id="checkpointsList"></div>
            </div>
            
            <div class="chat-messages" id="chatMessages">
                <div class="message system">
                    React Agent con LangGraph<br>
                    Haz clic en ➕ para adjuntar archivos como contexto<br>
                    <span style="font-size:10px;color:var(--text-muted);">📸 Cada cambio crea un checkpoint automático</span>
                </div>
            </div>
            <div class="quick-actions">
                <button class="quick-btn" onclick="sendQuick('muéstrame el estado')">📋 Estado</button>
                <button class="quick-btn" onclick="sendQuick('lista archivos')">📂 Archivos</button>
            </div>
            <div class="attached-files" id="attachedFiles"></div>
            <div class="snippet-container" id="snippetContainer" style="display:none;"></div>
            <div class="chat-input-area">
                <div class="chat-input-wrapper">
                    <input type="file" id="pdfInput" accept=".pdf" style="display:none" onchange="handlePdfUpload(event)">
                    <button class="pdf-btn" onclick="document.getElementById('pdfInput').click()" title="Subir PDF" style="background:linear-gradient(135deg,#e74c3c,#c0392b);border:none;color:white;padding:6px 10px;border-radius:6px;cursor:pointer;font-size:12px;margin-right:4px;">📄 PDF</button>
                    <input type="text" class="chat-input" id="chatInput" 
                           placeholder="Pregunta al agente... (@ para archivos, PDF para subir)"
                           onkeypress="if(event.key==='Enter') sendMessage()"
                           onpaste="handlePaste(event)">
                    <button class="send-btn" id="sendBtn" onclick="sendMessage()">Enviar</button>
                </div>
                <div id="mentionDropdown"></div>
            </div>
        </div>
    </div>

    <script>
        let state = {};
        let files = [];
        let selectedKey = null;
        let isLoading = false;
        
        // Icons for different types
        const typeIcons = {
            'string': '📄',
            'number': '🔢',
            'object': '📦',
            'array': '📚',
            'boolean': '✓'
        };
        
        function getIcon(value) {
            if (Array.isArray(value)) return '📚';
            return typeIcons[typeof value] || '📄';
        }
        
        // Attached files for context
        let attachedFiles = [];  // [{type: 'state'|'file', name: 'key', content: '...'}]
        
        // Snippet references (text selections pasted from state/files)
        let snippetRefs = [];  // [{source: 'state:key', startLine: 5, endLine: 12, preview: '...', content: '...'}]
        
        // ======= @ Mention Autocomplete =======
        let allMentionItems = [];
        let mentionState = { active: false, startPos: 0, items: [], selectedIndex: 0 };
        
        function loadMentionItems() {
            allMentionItems = [];
            // Add states
            for (const key of Object.keys(state)) {
                if (!key.startsWith('_')) {
                    const content = state[key];
                    allMentionItems.push({
                        name: key,
                        type: 'state',
                        content: typeof content === 'string' ? content : JSON.stringify(content)
                    });
                }
            }
            // Add files
            for (const filename of files) {
                allMentionItems.push({ name: filename, type: 'file', content: null });
            }
        }
        
        function filterMentionItems(query) {
            if (!query) return allMentionItems;
            const q = query.toLowerCase();
            return allMentionItems.filter(item => item.name.toLowerCase().includes(q));
        }
        
        function renderMentionDropdown(items) {
            const dropdown = document.getElementById('mentionDropdown');
            if (!items || items.length === 0) {
                dropdown.innerHTML = '<div class="mention-empty">No hay coincidencias</div>';
                return;
            }
            const states = items.filter(i => i.type === 'state');
            const filesFiltered = items.filter(i => i.type === 'file');
            let html = '';
            if (states.length > 0) {
                html += '<div class="mention-header">🗄️ Estados</div>';
                states.forEach((item, idx) => {
                    const globalIdx = items.indexOf(item);
                    html += `<div class="mention-item state ${globalIdx === mentionState.selectedIndex ? 'selected' : ''}" data-idx="${globalIdx}"><div class="m-icon">📦</div><span class="m-name">${escapeHtml(item.name)}</span><span class="m-type">state</span></div>`;
                });
            }
            if (filesFiltered.length > 0) {
                html += '<div class="mention-header">📂 Archivos</div>';
                filesFiltered.forEach((item, idx) => {
                    const globalIdx = items.indexOf(item);
                    html += `<div class="mention-item file ${globalIdx === mentionState.selectedIndex ? 'selected' : ''}" data-idx="${globalIdx}"><div class="m-icon">📄</div><span class="m-name">${escapeHtml(item.name)}</span><span class="m-type">file</span></div>`;
                });
            }
            html += '<div class="mention-hint"><kbd>↑</kbd><kbd>↓</kbd> navegar <kbd>Enter</kbd> seleccionar <kbd>Esc</kbd> cerrar</div>';
            dropdown.innerHTML = html;
            dropdown.querySelectorAll('.mention-item').forEach(el => {
                el.addEventListener('click', () => selectMentionItem(mentionState.items[parseInt(el.dataset.idx)]));
            });
        }
        
        function showMentionDropdown() {
            document.getElementById('mentionDropdown').classList.add('active');
            mentionState.active = true;
        }
        
        function hideMentionDropdown() {
            document.getElementById('mentionDropdown').classList.remove('active');
            mentionState.active = false;
            mentionState.selectedIndex = 0;
        }
        
        async function selectMentionItem(item) {
            if (!item) return;
            let content = item.content;
            if (item.type === 'file' && !content) {
                try {
                    const res = await fetch('/api/workspace/files/read', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename: item.name})
                    });
                    const data = await res.json();
                    content = data.content || '';
                } catch(e) { content = ''; }
            }
            if (!attachedFiles.some(f => f.name === item.name && f.type === item.type)) {
                attachedFiles.push({ name: item.name, type: item.type, content: content });
                renderAttachedFiles();
            }
            const input = document.getElementById('chatInput');
            const val = input.value;
            const before = val.substring(0, mentionState.startPos);
            const after = val.substring(input.selectionStart);
            input.value = before + after;
            input.focus();
            input.setSelectionRange(mentionState.startPos, mentionState.startPos);
            hideMentionDropdown();
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function onMentionInput(e) {
            const input = e.target;
            const val = input.value;
            const pos = input.selectionStart;
            let mentionStart = -1;
            for (let i = pos - 1; i >= 0; i--) {
                if (val[i] === '@') { mentionStart = i; break; }
                if (val[i] === ' ' || val[i] === String.fromCharCode(10)) break;
            }
            if (mentionStart >= 0) {
                loadMentionItems();
                const query = val.substring(mentionStart + 1, pos);
                mentionState.startPos = mentionStart;
                mentionState.items = filterMentionItems(query);
                mentionState.selectedIndex = 0;
                renderMentionDropdown(mentionState.items);
                showMentionDropdown();
            } else {
                hideMentionDropdown();
            }
        }
        
        function onMentionKeydown(e) {
            if (!mentionState.active) return;
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                mentionState.selectedIndex = Math.min(mentionState.selectedIndex + 1, mentionState.items.length - 1);
                renderMentionDropdown(mentionState.items);
                document.querySelector('.mention-item.selected')?.scrollIntoView({block:'nearest'});
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                mentionState.selectedIndex = Math.max(mentionState.selectedIndex - 1, 0);
                renderMentionDropdown(mentionState.items);
                document.querySelector('.mention-item.selected')?.scrollIntoView({block:'nearest'});
            } else if (e.key === 'Enter' || e.key === 'Tab') {
                if (mentionState.items.length > 0) {
                    e.preventDefault();
                    selectMentionItem(mentionState.items[mentionState.selectedIndex]);
                }
            } else if (e.key === 'Escape') {
                e.preventDefault();
                hideMentionDropdown();
            }
        }
        
        // ======= PDF Upload Handler =======
        async function handlePdfUpload(e) {
            const file = e.target.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append('pdf', file);
            
            // If a file/state is selected, append to it
            if (selectedKey) {
                // Check if it's a state (no prefix) or file (has 'file:' prefix)
                if (selectedKey.startsWith('file:')) {
                    // It's a workspace file - strip the prefix
                    const filename = selectedKey.replace('file:', '');
                    formData.append('target_file', filename);
                } else if (state[selectedKey] !== undefined) {
                    // It's a state
                    formData.append('target_state', selectedKey);
                }
            }
            
            // Show loading state - addMessage(text, type)
            addMessage('📄 Procesando PDF: ' + file.name + '...', 'system');
            
            try {
                const res = await fetch('/api/upload/pdf', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                
                if (data.success) {
                    addMessage('✅ PDF procesado: ' + data.pages + ' páginas extraídas.' +
                        (data.saved_to ? ' Guardado en: ' + data.saved_to : '') +
                        ' (Embeddings procesándose en background)', 'system');
                    
                    // Refresh file tree to show new file/state
                    await updateData();
                    
                    // If we created a new file, select it
                    if (data.workspace_file) {
                        selectItem('file', data.workspace_file);
                    } else if (data.state_key) {
                        selectItem('state', data.state_key);
                    }
                } else {
                    addMessage('❌ Error: ' + (data.error || 'Error desconocido'), 'error');
                }
            } catch (err) {
                addMessage('❌ Error subiendo PDF: ' + err.message, 'error');
            }
            
            // Reset file input
            e.target.value = '';
        }
        
        // Attach event listeners to chat input
        document.addEventListener('DOMContentLoaded', () => {
            const chatInput = document.getElementById('chatInput');
            if (chatInput) {
                chatInput.addEventListener('input', onMentionInput);
                chatInput.addEventListener('keydown', onMentionKeydown);
            }
            document.addEventListener('click', (e) => {
                if (!document.getElementById('mentionDropdown')?.contains(e.target) && e.target.id !== 'chatInput') {
                    hideMentionDropdown();
                }
            });
        });
        
        // Normalize text for comparison (handle different newline styles)
        function normalizeText(text) {
            if (!text) return '';
            // Replace Windows-style CRLF and standalone CR with LF
            return text.split(String.fromCharCode(13, 10)).join(String.fromCharCode(10))
                       .split(String.fromCharCode(13)).join(String.fromCharCode(10))
                       .trim();
        }
        
        // Find where pasted text comes from
        function findTextSource(pastedText) {
            const cleanText = normalizeText(pastedText);
            
            console.log('╔════════════════════════════════════════════════════════════════╗');
            console.log('║                    🔍 PASTE DEBUG START                        ║');
            console.log('╚════════════════════════════════════════════════════════════════╝');
            console.log('📋 RAW pasted text length:', pastedText ? pastedText.length : 0);
            console.log('📋 CLEAN text length:', cleanText ? cleanText.length : 0);
            
            // Count lines in pasted text
            const pastedLineCount = cleanText ? cleanText.split(String.fromCharCode(10)).length : 0;
            console.log('📋 LINES in pasted text:', pastedLineCount);
            console.log('📋 First 200 chars of pasted text:', cleanText ? cleanText.substring(0, 200) : 'null');
            console.log('📋 Last 100 chars of pasted text:', cleanText ? cleanText.substring(cleanText.length - 100) : 'null');
            
            if (!cleanText || cleanText.length < 10) {
                console.log('❌ Text too short, returning null');
                return null;
            }
            
            console.log('🗄️ Available state keys:', Object.keys(state));
            
            // Search in state
            for (const [key, value] of Object.entries(state)) {
                if (key.startsWith('_')) continue;
                const rawContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
                const content = normalizeText(rawContent);
                
                console.log(`Checking "${key}": length=${content.length}, first50="${content.substring(0,50)}"`);
                
                // Try exact match first
                let found = content.includes(cleanText);
                
                // If not found, try with trimmed lines (browser may add/remove spaces)
                if (!found) {
                    const cleanLines = cleanText.split(String.fromCharCode(10)).map(l => l.trim()).join(' ');
                    const contentLines = content.split(String.fromCharCode(10)).map(l => l.trim()).join(' ');
                    found = contentLines.includes(cleanLines);
                    if (found) console.log('Found with line-trim matching');
                }
                
                // If still not found, try substring match (first 50 chars)
                if (!found && cleanText.length > 50) {
                    const searchPart = cleanText.substring(0, 50);
                    found = content.includes(searchPart);
                    if (found) console.log('Found with partial match (first 50 chars)');
                }
                
                if (found) {
                    console.log('✅ MATCH FOUND in key:', key);
                    
                    // Find line numbers - split by LF (already normalized)
                    const lines = content.split(String.fromCharCode(10));
                    const pastedLines = cleanText.split(String.fromCharCode(10));
                    
                    console.log('📊 Document total lines:', lines.length);
                    console.log('📊 Pasted lines count:', pastedLines.length);
                    console.log('📊 First pasted line:', pastedLines[0]);
                    console.log('📊 Last pasted line:', pastedLines[pastedLines.length - 1]);
                    
                    let startLine = 1;
                    let endLine = pastedLines.length;
                    let foundByLineMatch = false;
                    
                    // Method 1: Try exact indexOf first
                    let textPos = content.indexOf(cleanText);
                    console.log('🔎 Method 1 - Exact indexOf result:', textPos);
                    
                    // Method 2: If not found, try finding first 50 chars
                    if (textPos === -1 && cleanText.length > 50) {
                        textPos = content.indexOf(cleanText.substring(0, 50));
                        console.log('🔎 Method 2 - First 50 chars indexOf result:', textPos);
                    }
                    
                    // Method 3: Line-by-line matching for better accuracy
                    if (textPos === -1) {
                        console.log('🔎 Method 3 - Trying line-by-line matching...');
                        const firstPastedLine = pastedLines[0].trim();
                        const lastPastedLine = pastedLines[pastedLines.length - 1].trim();
                        
                        console.log('  Looking for first line:', firstPastedLine.substring(0, 80));
                        
                        // Find first matching line
                        for (let i = 0; i < lines.length; i++) {
                            const lineContent = lines[i].trim();
                            if (lineContent.length > 5 && firstPastedLine.length > 5) {
                                // Check if lines match (allowing for some flexibility)
                                if (lineContent === firstPastedLine || 
                                    lineContent.includes(firstPastedLine) || 
                                    firstPastedLine.includes(lineContent)) {
                                    startLine = i + 1;
                                    endLine = startLine + pastedLines.length - 1;
                                    foundByLineMatch = true;
                                    console.log(`  ✅ Found by line match at document line ${i+1}:`, lineContent.substring(0, 50));
                                    break;
                                }
                            }
                        }
                        if (!foundByLineMatch) {
                            console.log('  ❌ No line match found');
                        }
                    }
                    
                    // Calculate start line from character position
                    if (textPos >= 0 && !foundByLineMatch) {
                        console.log('🔎 Calculating line from char position:', textPos);
                        let charCount = 0;
                        for (let i = 0; i < lines.length; i++) {
                            const lineLen = lines[i].length + 1; // +1 for newline
                            if (charCount + lineLen > textPos) {
                                startLine = i + 1;
                                console.log(`  Found at line ${startLine} (charCount=${charCount}, lineLen=${lineLen})`);
                                break;
                            }
                            charCount += lineLen;
                        }
                        endLine = startLine + pastedLines.length - 1;
                    }
                    
                    // Ensure endLine doesn't exceed total lines
                    endLine = Math.min(endLine, lines.length);
                    
                    console.log('╔════════════════════════════════════════════════════════════════╗');
                    console.log(`║ 📍 RESULT: Lines ${startLine}-${endLine} (textPos: ${textPos}, byLine: ${foundByLineMatch})`);
                    console.log('╚════════════════════════════════════════════════════════════════╝');
                    
                    return {
                        source: `state:${key}`,
                        type: 'state',
                        name: key,
                        startLine,
                        endLine,
                        preview: cleanText.substring(0, 50) + (cleanText.length > 50 ? '...' : ''),
                        content: cleanText
                    };
                }
            }
            
            return null;
        }
        
        function addSnippetRef(ref) {
            // Check if already exists
            const exists = snippetRefs.some(s => 
                s.source === ref.source && 
                s.startLine === ref.startLine && 
                s.endLine === ref.endLine
            );
            if (!exists) {
                snippetRefs.push(ref);
                renderSnippets();
            }
        }
        
        function removeSnippetRef(index) {
            snippetRefs.splice(index, 1);
            renderSnippets();
        }
        
        function renderSnippets() {
            const container = document.getElementById('snippetContainer');
            if (!container) return;
            
            if (snippetRefs.length === 0) {
                container.innerHTML = '';
                container.style.display = 'none';
                return;
            }
            
            container.style.display = 'flex';
            container.innerHTML = snippetRefs.map((ref, idx) => `
                <div class="snippet-tag">
                    <span class="snippet-icon">📎</span>
                    <span class="snippet-name">${ref.name}</span>
                    <span class="snippet-lines">[${ref.startLine}-${ref.endLine}]</span>
                    <span class="snippet-remove" onclick="removeSnippetRef(${idx})">×</span>
                </div>
            `).join('');
        }
        
        // Handle paste in chat input - convert to snippet reference
        function handlePaste(e) {
            const pastedText = e.clipboardData.getData('text');
            console.log('Paste detected:', pastedText.substring(0, 100));
            
            const source = findTextSource(pastedText);
            console.log('Source found:', source);
            
            if (source) {
                e.preventDefault();
                addSnippetRef(source);
                
                // Show notification
                const notification = document.createElement('div');
                notification.style.cssText = 'position:fixed;bottom:80px;right:20px;background:#4a9eff;color:white;padding:8px 16px;border-radius:8px;z-index:1000;font-size:12px;animation:fadeIn 0.3s;';
                notification.textContent = `📎 Referencia creada: ${source.name} [líneas ${source.startLine}-${source.endLine}]`;
                document.body.appendChild(notification);
                setTimeout(() => notification.remove(), 2500);
            }
        }
        
        function renderFileTree() {
            // Render state items
            const stateContainer = document.getElementById('stateItems');
            const keys = Object.keys(state).filter(k => !k.startsWith('_'));
            
            if (keys.length === 0) {
                stateContainer.innerHTML = '<div class="tree-item" style="color: var(--text-muted); font-style: italic;">Sin datos</div>';
            } else {
                stateContainer.innerHTML = keys.map(key => {
                    const value = state[key];
                    const isAttached = attachedFiles.some(f => f.type === 'state' && f.name === key);
                    return `
                        <div class="tree-item ${selectedKey === key ? 'active' : ''}" 
                             onclick="selectItem('state', '${key}')">
                            <span class="icon">${getIcon(value)}</span>
                            <span class="name">${key}</span>
                            <button class="btn-attach ${isAttached ? 'attached' : ''}" 
                                    onclick="event.stopPropagation(); attachToContext('state', '${key}')">
                                ${isAttached ? '✓ Adjunto' : '@ Adjuntar'}
                            </button>
                            <button class="btn-export" 
                                    onclick="event.stopPropagation(); exportState('${key}')"
                                    title="Exportar a archivo"
                                    style="background: transparent; border: none; cursor: pointer; padding: 4px; opacity: 0.6; transition: opacity 0.2s;"
                                    onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.6">
                                📤
                            </button>
                            <button class="btn-delete" 
                                    onclick="event.stopPropagation(); deleteItem('state', '${key}')"
                                    title="Eliminar estado">
                                🗑️
                            </button>
                        </div>
                    `;
                }).join('');
            }
            
            // Render file items
            const fileContainer = document.getElementById('fileItems');
            if (files.length === 0) {
                fileContainer.innerHTML = '<div class="tree-item" style="color: var(--text-muted); font-style: italic;">Sin archivos</div>';
            } else {
                fileContainer.innerHTML = files.map(file => {
                    const isAttached = attachedFiles.some(f => f.type === 'file' && f.name === file);
                    return `
                        <div class="tree-item ${selectedKey === 'file:'+file ? 'active' : ''}" 
                             onclick="selectItem('file', '${file}')">
                            <span class="icon">📄</span>
                            <span class="name">${file}</span>
                            <button class="btn-attach ${isAttached ? 'attached' : ''}" 
                                    onclick="event.stopPropagation(); attachToContext('file', '${file}')">
                                ${isAttached ? '✓ Adjunto' : '@ Adjuntar'}
                            </button>
                            <button class="btn-delete" 
                                    onclick="event.stopPropagation(); deleteItem('file', '${file}')"
                                    title="Eliminar archivo">
                                🗑️
                            </button>
                        </div>
                    `;
                }).join('');
            }
        }
        
        async function attachToContext(type, name) {
            // Check if already attached
            const existingIndex = attachedFiles.findIndex(f => f.type === type && f.name === name);
            if (existingIndex !== -1) {
                // Remove if already attached
                attachedFiles.splice(existingIndex, 1);
                renderAttachedFiles();
                renderFileTree();
                return;
            }
            
            // Get content
            let content = '';
            if (type === 'state') {
                content = typeof state[name] === 'string' ? state[name] : JSON.stringify(state[name], null, 2);
            } else {
                // Fetch file content
                try {
                    const res = await fetch('/api/workspace/files/read', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename: name})
                    });
                    const data = await res.json();
                    content = data.content || '';
                } catch (e) {
                    content = 'Error al cargar archivo';
                }
            }
            
            attachedFiles.push({ type, name, content });
            renderAttachedFiles();
            renderFileTree();
        }
        
        async function deleteItem(type, name) {
            const typeLabel = type === 'state' ? 'estado' : 'archivo';
            if (!confirm(`¿Eliminar ${typeLabel} "${name}"?`)) return;
            
            try {
                if (type === 'state') {
                    // Eliminar del estado
                    const res = await fetch('/api/workspace/state/delete', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({key: name})
                    });
                    const data = await res.json();
                    if (data.error) {
                        alert('Error: ' + data.error);
                        return;
                    }
                } else {
                    // Eliminar archivo
                    const res = await fetch('/api/workspace/files/delete', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename: name})
                    });
                    const data = await res.json();
                    if (data.error) {
                        alert('Error: ' + data.error);
                        return;
                    }
                }
                
                // Quitar de adjuntos si estaba
                const attachIdx = attachedFiles.findIndex(f => f.type === type && f.name === name);
                if (attachIdx !== -1) attachedFiles.splice(attachIdx, 1);
                
                // Limpiar selección si era este item
                if ((type === 'state' && selectedKey === name) || 
                    (type === 'file' && selectedKey === 'file:' + name)) {
                    selectedKey = null;
                    document.getElementById('editorContent').innerHTML = `
                        <div class="empty-state">
                            <div class="icon">🗑️</div>
                            <div>Elemento eliminado</div>
                        </div>
                    `;
                }
                
                // Actualizar vista
                await updateData();
                renderAttachedFiles();
                
            } catch (e) {
                alert('Error eliminando: ' + e.message);
            }
        }
        
        function removeAttachment(index) {
            attachedFiles.splice(index, 1);
            renderAttachedFiles();
            renderFileTree();
        }
        
        async function refreshAttachedContent() {
            // Actualizar el contenido de los archivos adjuntos (por si el agente los modificó)
            for (let i = 0; i < attachedFiles.length; i++) {
                const file = attachedFiles[i];
                if (file.type === 'state') {
                    // Recargar desde el estado actualizado
                    if (state[file.name]) {
                        attachedFiles[i].content = typeof state[file.name] === 'string' 
                            ? state[file.name] 
                            : JSON.stringify(state[file.name], null, 2);
                    }
                } else {
                    // Recargar archivo
                    try {
                        const res = await fetch('/api/workspace/files/read', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({filename: file.name})
                        });
                        const data = await res.json();
                        attachedFiles[i].content = data.content || '';
                    } catch (e) {
                        console.error('Error refreshing attachment:', e);
                    }
                }
            }
        }
        
        function renderAttachedFiles() {
            const container = document.getElementById('attachedFiles');
            if (attachedFiles.length === 0) {
                container.innerHTML = '';
                return;
            }
            
            const filesHtml = attachedFiles.map((file, index) => `
                <div class="file-tag">
                    <span class="tag-icon">${file.type === 'state' ? '🗄️' : '📄'}</span>
                    <span class="tag-name">@${file.type}:${file.name}</span>
                    <span class="tag-remove" onclick="removeAttachment(${index})">×</span>
                </div>
            `).join('');
            
            // Agregar botón para limpiar todo si hay más de 1
            const clearBtn = attachedFiles.length > 0 ? `
                <div class="file-tag" style="background: #3f3f46; cursor: pointer" onclick="clearAllAttachments()">
                    <span class="tag-icon">🗑️</span>
                    <span class="tag-name">Limpiar contexto</span>
                </div>
            ` : '';
            
            container.innerHTML = filesHtml + clearBtn;
        }
        
        function clearAllAttachments() {
            attachedFiles = [];
            renderAttachedFiles();
            renderFileTree();
        }
        
        function selectItem(type, key) {
            selectedKey = type === 'file' ? 'file:' + key : key;
            renderFileTree();
            
            // Update tabs
            const tabs = document.getElementById('editorTabs');
            tabs.innerHTML = `
                <div class="editor-tab active">
                    <span>${type === 'file' ? '📄' : getIcon(state[key])}</span>
                    ${key}
                </div>
            `;
            
            // Update content
            const content = document.getElementById('editorContent');
            let value = type === 'file' ? 'Cargando...' : state[key];
            
            if (type === 'file') {
                // Fetch file content
                fetch('/api/workspace/files/read', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({filename: key})
                })
                .then(r => r.json())
                .then(data => {
                    renderContent(data.content || 'Archivo vacío');
                })
                .catch(() => renderContent('Error al cargar archivo'));
            } else {
                renderContent(value);
            }
        }
        
        function renderContent(value) {
            const content = document.getElementById('editorContent');
            const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
            // Split by newlines (normalize CRLF to LF first)
            const normalizedText = text.split(String.fromCharCode(13, 10)).join(String.fromCharCode(10))
                                       .split(String.fromCharCode(13)).join(String.fromCharCode(10));
            const lines = normalizedText.split(String.fromCharCode(10));
            
            // Store original text as data attribute for copy operations
            content.dataset.originalText = text;
            
            // Show edit button if something is selected
            const editBtn = document.getElementById('editToggleBtn');
            if (selectedKey) {
                editBtn.style.display = 'inline-block';
            } else {
                editBtn.style.display = 'none';
            }
            
            // Add a hidden newline character at the end of each line so browsers copy it correctly
            content.innerHTML = `
                <div class="code-view" data-content="${encodeURIComponent(text)}">
                    ${lines.map((line, i) => `
                        <div class="code-line" data-line="${i + 1}">
                            <span class="line-number">${i + 1}</span>
                            <span class="line-content">${escapeHtml(line)}</span><br>
                        </div>
                    `).join('')}
                </div>
            `;
            
            // Reset edit mode
            isEditMode = false;
            document.getElementById('editToggleBtn').textContent = '✏️ Editar';
            document.getElementById('editToggleBtn').style.background = '#4a9eff';
        }
        
        // ======= Edit Mode =======
        let isEditMode = false;
        let saveTimeout = null;
        
        function toggleEditMode() {
            const content = document.getElementById('editorContent');
            const editBtn = document.getElementById('editToggleBtn');
            const saveStatus = document.getElementById('saveStatus');
            
            if (!selectedKey) return;
            
            if (!isEditMode) {
                // Enter edit mode
                const originalText = content.dataset.originalText || '';
                content.innerHTML = `
                    <textarea id="editTextarea" style="
                        width: 100%;
                        height: calc(100% - 10px);
                        background: #1a1a2e;
                        color: #e0e0e0;
                        border: 1px solid #4a9eff;
                        border-radius: 4px;
                        font-family: 'Fira Code', 'Consolas', monospace;
                        font-size: 13px;
                        line-height: 1.6;
                        padding: 12px;
                        resize: none;
                        outline: none;
                    " onkeyup="debouncedSave()">${escapeHtml(originalText)}</textarea>
                `;
                editBtn.textContent = '✅ Ver';
                editBtn.style.background = '#27ae60';
                isEditMode = true;
                saveStatus.textContent = '';
                document.getElementById('editTextarea').focus();
            } else {
                // Exit edit mode - save first
                const textarea = document.getElementById('editTextarea');
                if (textarea) {
                    const newContent = textarea.value;
                    saveContent(newContent);
                }
                // Reload content in view mode
                if (selectedKey.startsWith('file:')) {
                    const filename = selectedKey.replace('file:', '');
                    fetch('/api/workspace/files/read', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename: filename})
                    })
                    .then(r => r.json())
                    .then(data => renderContent(data.content || ''));
                } else {
                    renderContent(state[selectedKey] || '');
                }
            }
        }
        
        function debouncedSave() {
            const saveStatus = document.getElementById('saveStatus');
            saveStatus.textContent = '⏳ Guardando...';
            saveStatus.style.color = '#f39c12';
            
            if (saveTimeout) clearTimeout(saveTimeout);
            saveTimeout = setTimeout(() => {
                const textarea = document.getElementById('editTextarea');
                if (textarea) {
                    saveContent(textarea.value);
                }
            }, 1000); // 1 second debounce
        }
        
        async function saveContent(newContent) {
            const saveStatus = document.getElementById('saveStatus');
            
            try {
                if (selectedKey.startsWith('file:')) {
                    // Save to workspace file
                    const filename = selectedKey.replace('file:', '');
                    await fetch('/api/workspace/files/write', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename: filename, content: newContent})
                    });
                } else {
                    // Save to state
                    await fetch('/api/workspace/state', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({key: selectedKey, value: newContent})
                    });
                    // Update local state immediately
                    state[selectedKey] = newContent;
                }
                
                // Update dataset for copy operations
                document.getElementById('editorContent').dataset.originalText = newContent;
                
                saveStatus.textContent = '✅ Guardado';
                saveStatus.style.color = '#27ae60';
                setTimeout(() => { saveStatus.textContent = ''; }, 2000);
            } catch (e) {
                saveStatus.textContent = '❌ Error';
                saveStatus.style.color = '#e74c3c';
            }
        }
        
        
        let previousStateJson = '';
        let previousStateKeys = [];
        let previousFiles = [];
        
        async function updateData() {
            try {
                const statusEl = document.getElementById('lastUpdated');
                statusEl.textContent = '⟳ Actualizando...';
                
                // Fetch state
                const stateRes = await fetch('/api/workspace/state');
                const stateData = await stateRes.json();
                const newState = stateData.state || {};
                const newStateJson = JSON.stringify(newState);
                
                // Detectar si hubo cambios
                const hasChanges = newStateJson !== previousStateJson;
                
                // Detectar NUEVAS claves de estado (para auto-abrir)
                const newStateKeys = Object.keys(newState).filter(k => !k.startsWith('_'));
                const brandNewStateKeys = newStateKeys.filter(k => !previousStateKeys.includes(k));
                
                previousStateJson = newStateJson;
                previousStateKeys = newStateKeys;
                state = newState;
                
                // Fetch files
                const filesRes = await fetch('/api/workspace/files');
                const filesData = await filesRes.json();
                const newFiles = filesData.files || [];
                
                // Detectar NUEVOS archivos
                const brandNewFiles = newFiles.filter(f => !previousFiles.includes(f));
                previousFiles = newFiles;
                files = newFiles;
                
                renderFileTree();
                
                // AUTO-ABRIR: Si hay un nuevo estado, abrirlo automáticamente
                if (brandNewStateKeys.length > 0) {
                    const newKey = brandNewStateKeys[brandNewStateKeys.length - 1]; // Último creado
                    console.log('🆕 Nuevo estado detectado, abriendo:', newKey);
                    selectItem('state', newKey);
                    
                    // Notificación visual
                    showNotification(`📄 Nuevo: ${newKey}`, '#4ade80');
                }
                // Si hay un nuevo archivo, abrirlo automáticamente
                else if (brandNewFiles.length > 0) {
                    const newFile = brandNewFiles[brandNewFiles.length - 1];
                    console.log('🆕 Nuevo archivo detectado, abriendo:', newFile);
                    selectItem('file', newFile);
                    
                    showNotification(`📁 Nuevo archivo: ${newFile}`, '#4a9eff');
                }
                // Actualizar contenido si hay algo seleccionado y hubo cambios
                // PERO NO si estamos en modo edición
                else if (selectedKey && hasChanges && !isEditMode) {
                    if (!selectedKey.startsWith('file:') && state[selectedKey]) {
                        renderContent(state[selectedKey]);
                    }
                }
                
                const time = new Date().toLocaleTimeString();
                statusEl.textContent = hasChanges ? `✓ Cambios: ${time}` : `Actualizado: ${time}`;
                
                // Flash visual si hubo cambios
                if (hasChanges) {
                    statusEl.style.color = '#4ade80';
                    setTimeout(() => statusEl.style.color = '', 1000);
                }
            } catch (error) {
                console.error('Error:', error);
                document.getElementById('lastUpdated').textContent = '⚠️ Error';
            }
        }
        
        function showNotification(message, color) {
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                background: ${color || '#4a9eff'};
                color: white;
                padding: 12px 20px;
                border-radius: 8px;
                z-index: 1000;
                font-size: 13px;
                font-weight: 500;
                box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                animation: slideIn 0.3s ease;
            `;
            notification.textContent = message;
            document.body.appendChild(notification);
            setTimeout(() => {
                notification.style.opacity = '0';
                notification.style.transform = 'translateX(20px)';
                notification.style.transition = 'all 0.3s ease';
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }
        
        // ============== CHECKPOINT/VERSION CONTROL ==============
        
        let historyVisible = false;
        
        async function loadCheckpoints() {
            try {
                const response = await fetch('/api/checkpoints?limit=20');
                const data = await response.json();
                return data.checkpoints || [];
            } catch (error) {
                console.error('Error loading checkpoints:', error);
                return [];
            }
        }
        
        async function toggleHistoryPanel() {
            const panel = document.getElementById('historyPanel');
            historyVisible = !historyVisible;
            
            if (historyVisible) {
                panel.classList.add('visible');
                await renderCheckpoints();
            } else {
                panel.classList.remove('visible');
            }
        }
        
        async function renderCheckpoints() {
            const container = document.getElementById('checkpointsList');
            const checkpoints = await loadCheckpoints();
            
            if (checkpoints.length === 0) {
                container.innerHTML = '<div style="color:var(--text-muted);font-size:11px;text-align:center;">No hay checkpoints aún</div>';
                return;
            }
            
            container.innerHTML = checkpoints.map(cp => {
                const date = new Date(cp.timestamp);
                const timeStr = date.toLocaleTimeString('es', {hour: '2-digit', minute: '2-digit'});
                const toolBadge = cp.tool_used ? `<span style="color:var(--accent-green);">[${cp.tool_used}]</span>` : '';
                
                return `
                    <div class="history-item">
                        <span class="hash">${cp.short_hash}</span>
                        <span class="message">${toolBadge} ${cp.message}</span>
                        <span class="time">${timeStr}</span>
                        <button class="restore-btn" onclick="restoreCheckpoint('${cp.hash}')">↩️</button>
                    </div>
                `;
            }).join('');
        }
        
        async function restoreCheckpoint(hash) {
            if (!confirm('¿Restaurar el estado a este punto? Los cambios actuales se guardarán como backup.')) {
                return;
            }
            
            try {
                showNotification('🔄 Restaurando...', '#667eea');
                
                const response = await fetch('/api/checkpoints/restore', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hash: hash })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showNotification(`✅ ${data.message}`, '#48bb78');
                    // Actualizar datos del IDE
                    await updateData();
                    // Agregar mensaje al chat
                    addMessage(`🔄 Estado restaurado al checkpoint ${hash.substring(0, 8)}`, 'system');
                    // Refrescar historial si está visible
                    if (historyVisible) {
                        await renderCheckpoints();
                    }
                } else {
                    showNotification(`❌ Error: ${data.error}`, '#e53e3e');
                }
            } catch (error) {
                console.error('Error restoring checkpoint:', error);
                showNotification(`❌ Error: ${error.message}`, '#e53e3e');
            }
        }
        
        async function createManualCheckpoint() {
            const message = prompt('Descripción del checkpoint:', 'Checkpoint manual');
            if (!message) return;
            
            try {
                const response = await fetch('/api/checkpoints/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: message })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showNotification(`📸 Checkpoint creado: ${data.checkpoint.short_hash}`, '#48bb78');
                    if (historyVisible) {
                        await renderCheckpoints();
                    }
                } else {
                    showNotification(data.message || 'No hay cambios para guardar', '#f6ad55');
                }
            } catch (error) {
                console.error('Error creating checkpoint:', error);
                showNotification(`❌ Error: ${error.message}`, '#e53e3e');
            }
        }
        
        let debugOpen = false;
        
        function toggleDebugPanel() {
            debugOpen = !debugOpen;
            document.getElementById('debugItems').style.display = debugOpen ? 'block' : 'none';
            document.getElementById('debugArrow').textContent = debugOpen ? '▼' : '▶';
            if (debugOpen) updateDebugLog();
        }
        
        // ============== FILE IMPORT FUNCTIONS ==============
        
        function triggerFileImport() {
            document.getElementById('fileImportInput').click();
        }
        
        async function handleFileImport(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            // Show loading notification
            showNotification(`📥 Importando: ${file.name}...`, 'info');
            
            try {
                const formData = new FormData();
                formData.append('file', file);
                
                const response = await fetch('/api/import', {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                
                if (result.success) {
                    showNotification(`✅ Importado: ${result.state_key} (${result.lines} líneas)`, 'success');
                    // Clear the input so the same file can be imported again
                    event.target.value = '';
                    // Refresh the state list
                    updateData();
                } else {
                    showNotification(`❌ Error: ${result.error}`, 'error');
                }
            } catch (error) {
                console.error('Import error:', error);
                showNotification(`❌ Error importando archivo: ${error.message}`, 'error');
            }
        }
        
        function showNotification(message, type = 'info') {
            // Create notification element
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed;
                bottom: 20px;
                left: 50%;
                transform: translateX(-50%);
                padding: 12px 24px;
                border-radius: 8px;
                color: white;
                font-size: 14px;
                z-index: 9999;
                animation: slideUp 0.3s ease-out;
                ${type === 'success' ? 'background: linear-gradient(135deg, #238636 0%, #2ea043 100%);' : ''}
                ${type === 'error' ? 'background: linear-gradient(135deg, #da3633 0%, #f85149 100%);' : ''}
                ${type === 'info' ? 'background: linear-gradient(135deg, #1f6feb 0%, #388bfd 100%);' : ''}
            `;
            notification.textContent = message;
            document.body.appendChild(notification);
            
            // Remove after 3 seconds
            setTimeout(() => {
                notification.style.animation = 'slideDown 0.3s ease-out';
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }
        
        async function exportState(stateKey) {
            // Ask for format
            const format = prompt('¿Formato de archivo? (txt, md, json)', 'md');
            if (!format) return;
            
            // Ask for filename (optional)
            const filename = prompt('Nombre del archivo (dejar vacío para usar nombre del estado):', '');
            
            showNotification(`📤 Exportando: ${stateKey}...`, 'info');
            
            try {
                const response = await fetch('/api/export', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        state_key: stateKey,
                        filename: filename || null,
                        format: format.toLowerCase()
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    showNotification(`✅ Exportado: ${result.filename} (${result.lines} líneas)`, 'success');
                    // Refresh file list
                    updateData();
                } else {
                    showNotification(`❌ Error: ${result.error}`, 'error');
                }
            } catch (error) {
                console.error('Export error:', error);
                showNotification(`❌ Error exportando: ${error.message}`, 'error');
            }
        }
        
        async function updateDebugLog() {
            try {
                const res = await fetch('/api/debug/changelog');
                const data = await res.json();
                const changes = data.changes || [];
                
                const container = document.getElementById('debugItems');
                if (changes.length === 0) {
                    container.innerHTML = '<div class="debug-item" style="color: var(--text-muted);">Sin cambios recientes</div>';
                    return;
                }
                
                container.innerHTML = changes.reverse().map(c => {
                    const time = c.timestamp ? c.timestamp.split('T')[1].split('.')[0] : '';
                    return `
                        <div class="debug-item">
                            <span class="debug-time">${time}</span>
                            <span class="debug-op debug-op-${c.operation}">${c.operation}</span>
                            <span class="debug-target">${c.target}</span>
                            ${c.details ? `<span class="debug-preview">${escapeHtml(c.details)}</span>` : ''}
                        </div>
                    `;
                }).join('');
            } catch (e) {
                console.error('Debug log error:', e);
            }
        }
        
        // Auto-update debug log if open
        setInterval(() => { if (debugOpen) updateDebugLog(); }, 2000);
        
        function addMessage(text, type, toolsUsed = [], checkpointHash = null) {
            const container = document.getElementById('chatMessages');
            const msg = document.createElement('div');
            msg.className = `message ${type}`;
            
            let html = escapeHtml(text).replace(/\\n/g, '<br>');
            
            // Footer con tools y checkpoint
            let footer = '';
            if (toolsUsed.length > 0) {
                footer += `<span class="tools-badge">🔧 ${toolsUsed.join(', ')}</span>`;
            }
            if (checkpointHash && type === 'assistant') {
                footer += `<button class="checkpoint-btn" onclick="restoreCheckpoint('${checkpointHash}')" title="Restaurar a este punto">
                    📸 ${checkpointHash}
                </button>`;
            }
            if (footer) {
                html += `<div class="message-footer">${footer}</div>`;
            }
            
            msg.innerHTML = html;
            container.appendChild(msg);
            
            // Auto-scroll to bottom - multiple methods for reliability
            requestAnimationFrame(() => {
                msg.scrollIntoView({ behavior: 'smooth', block: 'end' });
                container.scrollTop = container.scrollHeight;
            });
        }
        
        async function sendMessage() {
            const input = document.getElementById('chatInput');
            const btn = document.getElementById('sendBtn');
            const message = input.value.trim();
            
            if (!message || isLoading) return;
            
            isLoading = true;
            btn.disabled = true;
            btn.innerHTML = '<span class="loading-dots"><span></span><span></span><span></span></span>';
            
            // Build display message with file tags and snippet refs
            let displayMsg = message;
            if (attachedFiles.length > 0) {
                const tags = attachedFiles.map(f => `@${f.type}:${f.name}`).join(' ');
                displayMsg = `${tags}\\n${message}`;
            }
            if (snippetRefs.length > 0) {
                const snippetTags = snippetRefs.map(s => `📎${s.name}[${s.startLine}-${s.endLine}]`).join(' ');
                displayMsg = `${snippetTags}\\n${displayMsg}`;
            }
            addMessage(displayMsg, 'user');
            input.value = '';
            
            try {
                // Build request with context files
                const requestBody = { question: message };
                
                // Combine attached files and snippet references as context
                let contextItems = [];
                
                if (attachedFiles.length > 0) {
                    contextItems = attachedFiles.map(f => ({
                        name: `${f.type}:${f.name}`,
                        content: f.content
                    }));
                }
                
                // Add snippets as focused context (with line info)
                if (snippetRefs.length > 0) {
                    for (const s of snippetRefs) {
                        contextItems.push({
                            name: `snippet:${s.name}[${s.startLine}-${s.endLine}]`,
                            content: `[FRAGMENTO SELECCIONADO de ${s.name}, líneas ${s.startLine}-${s.endLine}]:\\n${s.content}`
                        });
                    }
                }
                
                if (contextItems.length > 0) {
                    requestBody.context_files = contextItems;
                }
                
                const response = await fetch('/api/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });
                
                const data = await response.json();
                addMessage(data.answer, 'assistant', data.tools_used || [], data.checkpoint || null);
                
                // Mostrar notificación si se creó checkpoint
                if (data.checkpoint) {
                    showNotification(`📸 Checkpoint: ${data.checkpoint}`, 'info');
                }
                
                // Limpiar snippets después de cada mensaje (son referencias puntuales)
                snippetRefs = [];
                renderSnippets();
                
                // NO limpiar archivos adjuntos - mantener contexto entre ejecuciones
                // El usuario puede deseleccionar manualmente haciendo clic en ✓ Adjunto
                // Solo actualizar el contenido de los archivos adjuntos por si cambiaron
                await refreshAttachedContent();
                
                // Actualización agresiva después de respuesta del agente
                // (el agente puede haber modificado el estado)
                await updateData();
                setTimeout(updateData, 300);
                setTimeout(updateData, 800);
                setTimeout(updateData, 1500);
                
            } catch (error) {
                addMessage('Error: ' + error.message, 'system');
            } finally {
                isLoading = false;
                btn.disabled = false;
                btn.textContent = 'Enviar';
                
                // Scroll final al terminar
                const container = document.getElementById('chatMessages');
                setTimeout(() => {
                    container.scrollTop = container.scrollHeight;
                }, 100);
            }
        }
        
        function sendQuick(text) {
            document.getElementById('chatInput').value = text;
            sendMessage();
        }
        
        // Initialize
        updateData();
        setInterval(updateData, 1500); // Polling cada 1.5 segundos
    </script>
</body>
</html>
"""

@app.get("/viewer", response_class=HTMLResponse)
async def state_viewer():
    """Interactive React Agent viewer - loads from templates/viewer_ide.html"""
    import os
    template_path = os.path.join(os.path.dirname(__file__), "templates", "viewer_ide.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        # Fallback to embedded HTML if file not found
        return STATE_VIEWER_HTML

# ============== SPA CATCH-ALL (Must be registered LAST) ==============
# This must come after all API routes so they take precedence

if IS_DOCKER and os.path.exists(FRONTEND_DIST):
    @app.get("/{full_path:path}")
    def spa_catch_all(full_path: str):
        """Handle SPA routing - serve index.html for non-API routes"""
        # API routes are already handled by specific endpoints
        # This catches everything else for React Router
        index_path = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="Not found")
    logger.info("🔁 SPA catch-all route registered")

# ============== MAIN ==============

if __name__ == "__main__":
    import uvicorn
    logger.info("🚀 Starting React Agent Server")
    logger.info("📄 Viewer: http://localhost:8000/viewer")
    logger.info("📚 Docs: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

