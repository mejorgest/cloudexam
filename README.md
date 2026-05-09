# tsagentexam

Aplicación web de exámenes médicos asistida por agente: subir PDFs de exámenes,
extraer preguntas y opciones, y analizar cada pregunta con un agente
ReAct (LangGraph) que puede buscar en Google y consultar imágenes/PDFs locales
indexados en pgvector.

Stack: **FastAPI + LangGraph (backend)**, **React + Vite (frontend)**,
**PostgreSQL + pgvector (RAG)**, **OpenAI / Gemini (LLM)**.

## Estructura

```
host_and_client_react_agent.py   # entry point FastAPI (servido por uvicorn)
config_manager.py                # configuración runtime (data/secrets.json + .env)
Dockerfile                       # build multi-stage frontend + backend
requirements1.txt                # dependencias Python

servers/                         # módulos importados por el agente
├── react_tools/tools_loader.py  # registro de tools de LangGraph
├── filesystem_service/          # operaciones sobre el state del agente
├── frontend_tools/              # operaciones inteligentes de edición
├── smart_tools/                 # smart_edit / smart_enrich con LLM
├── advanced_tools/              # google_search
├── pdf_processor.py             # ingesta de PDFs (pgvector)
├── pdf_registry.py              # tabla pdf_files
├── medical_images_service.py    # imágenes médicas con embeddings
├── medical_keywords_extractor.py
├── keyword_rag_service.py
├── entity_*.py                  # extracción de entidades médicas
├── rag_search.py
├── db_pool.py
└── versioning_service/          # checkpoints de workspace en git

frontend/                        # SPA React (TypeScript + Vite)
skills/                          # skills cargados dinámicamente
static/, templates/              # assets servidos por FastAPI
```

## Configuración

Copia `.env.example` a `.env` y completa los valores, o configúralos por la UI
de "Configuración" en la app (se persisten en `data/secrets.json`).

```
OPENAI_API_KEY=...
GEMINI_API_KEY=...
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PWD=...
DB_NAME=mibase
```

La base de datos requiere la extensión `pgvector`.

## Ejecutar localmente

```bash
# Backend
pip install -r requirements1.txt
uvicorn host_and_client_react_agent:app --host 0.0.0.0 --port 8000

# Frontend (en otro terminal, durante desarrollo)
cd frontend
npm install
npm run dev
```

La SPA de Vite proxy-ea `/api/*` al backend en `localhost:8000`.

## Ejecutar con Docker

```bash
docker build -t tsagentexam .
docker run --rm -p 8000:8000 --env-file .env tsagentexam
```

La imagen construye el frontend con Vite y lo sirve estáticamente desde
FastAPI en `/`.

## Healthcheck

```
GET /api/workspace/state
```
