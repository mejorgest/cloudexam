# cloudexam

Aplicación web app para análisis de exámenes médicos asistido por agente. Permite subir
PDFs de exámenes, extraer las preguntas y opciones, y analizar cada pregunta
con un agente ReAct (LangGraph) que razona sobre el contenido, busca en Google
y consulta una base local de PDFs e imágenes médicas indexada en pgvector.

## Características

- **Ingesta de exámenes**: sube PDFs/HTML de exámenes y la app extrae preguntas
  con opciones de respuesta usando Gemini Vision.
- **Análisis por pregunta**: cada pregunta tiene botones para analizar con el
  agente o buscar evidencia en Google.
- **Imágenes médicas**: galería A2UI con imágenes anotadas por keywords;
  el agente puede enriquecer respuestas con imágenes relevantes (requiere
  Postgres + pgvector).
- **State persistente**: el agente mantiene un workspace de archivos y un state
  por sesión; cambios se versionan con git checkpoints.
- **Smart edit**: edición de documentos con instrucciones en lenguaje natural
  (smart_edit, smart_resume, add_text, delete_lines, relocate_text).
- **Skills dinámicas**: módulos en `skills/` cargados en runtime
  (template incluido en `skills/TEMPLATE_SKILL.md`).

## Stack

| Capa | Tecnología |
|---|---|
| Backend | FastAPI + Uvicorn |
| Agente | LangChain + LangGraph (`create_react_agent`) |
| LLM | OpenAI (gpt-4o / gpt-4o-mini) o Google Gemini |
| Vector DB | PostgreSQL + pgvector (vía LangChain PGVector) |
| Frontend | React 19 + TypeScript + Vite |
| State client | Zustand |
| Empaquetado | Docker multi-stage (Node 20 + Python 3.11) |

## Estructura del repo

```
host_and_client_react_agent.py   # entry point FastAPI (servido por uvicorn)
config_manager.py                # carga config de data/secrets.json + .env
Dockerfile                       # build multi-stage (frontend + backend)
requirements1.txt                # dependencias Python
.env.example                     # plantilla de variables de entorno

servers/                         # módulos backend del agente
├── react_tools/tools_loader.py  # registro de tools de LangGraph (19 tools)
├── filesystem_service/          # operaciones sobre el state del agente
├── frontend_tools/              # operaciones de edición sobre state
├── smart_tools/                 # smart_edit / smart_enrich con LLM
├── advanced_tools/google_search.py
├── medical_images_service.py    # imágenes médicas con embeddings
├── medical_keywords_extractor.py
├── keyword_rag_service.py
├── db_pool.py                   # connection pool a Postgres
└── versioning_service/          # checkpoints del workspace en git

frontend/                        # SPA React (TypeScript + Vite)
├── src/components/
│   ├── ExamViewer/              # render de preguntas + botones de análisis
│   ├── Chat/                    # panel de chat con el agente (SSE/WS)
│   ├── Editor/                  # editor del state activo
│   ├── Sidebar/                 # navegación de archivos/states
│   ├── ConfigScreen/            # configuración runtime (API keys, DB)
│   ├── ImageUpload/             # subida y registro de imágenes médicas
│   ├── A2UIImageGallery/        # galería para enriquecer respuestas
│   ├── DiffModal/, Debug/
└── src/services/api.ts          # cliente HTTP/SSE/WS

skills/                          # skills cargadas dinámicamente
static/, templates/              # assets servidos por FastAPI
```

## Requisitos previos

- **Docker** 20.10+ (recomendado para correr todo)
- **API key de OpenAI** (obligatoria para que arranque el agente)
- **API key de Gemini** (obligatoria si vas a subir PDFs de exámenes)
- **PostgreSQL 14+ con la extensión `pgvector`** — *opcional*, solo si vas
  a ingestar PDFs o imágenes médicas. La app arranca y el chat funciona sin
  base de datos. Si la usas, crea la base antes:
  ```sql
  CREATE DATABASE mibase;
  \c mibase
  CREATE EXTENSION IF NOT EXISTS vector;
  ```

Para desarrollo local sin Docker:
- Python 3.11+
- Node.js 20+

## Configuración

Copia la plantilla de variables de entorno:

```bash
cp .env.example .env
```

Edita `.env` con tus valores:

```bash
# LLM principal del agente (OBLIGATORIO — sin esto el agente no arranca)
OPENAI_API_KEY=sk-...

# Extracción de preguntas de PDF con Gemini Vision (obligatorio para subir
# exámenes en PDF; el resto de la app funciona sin él)
GEMINI_API_KEY=...

# Postgres con pgvector (opcional — solo para ingestar PDFs e imágenes médicas)
DB_HOST=localhost     # o el host de tu Postgres
DB_PORT=5432
DB_USER=postgres
DB_PWD=tu_password
DB_NAME=mibase

# Búsqueda en Google (opcional — solo si quieres usar la tool buscar_en_google)
GOOGLE_SEARCH_API_KEY=...
GOOGLE_SEARCH_CX=...
```

### Cómo obtener `OPENAI_API_KEY`

La app usa OpenAI como LLM principal del agente (modelo configurable; por
defecto `gpt-5-mini`). Sin esta key el agente **no se inicializa** —
el backend arranca pero el chat no funciona.

1. Entra a <https://platform.openai.com/signup> y crea una cuenta (o haz login).
2. Ve a <https://platform.openai.com/api-keys> y haz clic en **Create new secret key**.
3. Dale un nombre (p. ej. `cloudexam`) y copia la key (empieza con `sk-...`).
   ⚠️ Solo se muestra una vez — guárdala en tu gestor de contraseñas.
4. OpenAI requiere **saldo prepagado**: ve a
   <https://platform.openai.com/account/billing> y carga al menos $5.

### Cómo obtener `GEMINI_API_KEY`

La app usa Gemini para extraer preguntas y opciones de los PDFs de exámenes
(`textractor_robust.py` con `gemini-2.0-flash`). Sin esta key, el endpoint
`/api/exams/extract-from-pdf` falla.

1. Entra a <https://aistudio.google.com/apikey> con tu cuenta de Google.
2. Haz clic en **Create API key** (selecciona o crea un proyecto de Google Cloud).
3. Copia la key (empieza con `AIza...`).
4. El uso de Gemini Flash tiene un **tier gratuito** generoso (15 req/min,
   1M tokens/día) — suficiente para uso personal sin tarjeta de crédito.

### Cómo obtener `GOOGLE_SEARCH_API_KEY` y `GOOGLE_SEARCH_CX`

La tool `buscar_en_google` usa la **Google Custom Search JSON API**, que
requiere DOS credenciales:

1. **`GOOGLE_SEARCH_API_KEY`** — API key de Google Cloud:
   - Entra a <https://console.cloud.google.com/> y selecciona/crea un proyecto.
   - Activa **Custom Search API** en
     <https://console.cloud.google.com/apis/library/customsearch.googleapis.com>.
   - Ve a **APIs & Services → Credentials → Create credentials → API key**.
   - Copia la key.

2. **`GOOGLE_SEARCH_CX`** — ID del Programmable Search Engine:
   - Entra a <https://programmablesearchengine.google.com/> y haz clic en **Add**.
   - Dale un nombre y, en *Sites to search*, activa **"Search the entire web"**.
   - Crea el engine. En el panel del CSE copia el **Search engine ID**
     (también llamado `cx`).

> El plan gratuito de Custom Search permite **100 búsquedas/día**. Si lo
> excedes, las llamadas devolverán un error 429.

Variables opcionales:

| Variable | Default | Descripción |
|---|---|---|
| `MEDICAL_IMAGES_DIR` | `<repo>/workspace/medical_images` | Dónde guardar imágenes subidas |
| `DOCKER_ENV` | `false` | Lo setea el `Dockerfile` automáticamente |
| `STATIC_DIR` | (ninguno; modo dev) | Path al build del frontend para servirlo desde FastAPI |

> Alternativa: la app también lee `data/secrets.json` (gestionado vía la
> pantalla "Configuración" en la UI). Si `data/secrets.json` existe, tiene
> prioridad sobre `.env`.

## Clonar y arrancar con Docker

### 1. Clonar

```bash
git clone https://github.com/mejorgest/cloudexam.git
cd cloudexam
```

### 2. Configurar variables

```bash
cp .env.example .env
$EDITOR .env  # rellena las claves
```

### 3. Levantar Postgres con pgvector (opcional, si no tienes uno)

```bash
docker run -d --name pgvector \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=mibase \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

Crea la extensión:

```bash
docker exec -it pgvector psql -U postgres -d mibase -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Y ajusta tu `.env` para apuntar al contenedor (desde el host, `DB_HOST=localhost`;
desde otro contenedor en la misma red, `DB_HOST=pgvector`).

### 4. Build de la imagen

```bash
docker build -t cloudexam .
```

El build hace dos stages: compila el frontend con `npm run build` y luego
copia el `dist/` resultante dentro de la imagen Python.

### 5. Run

Si tu Postgres está en el host:

```bash
docker run --rm -p 8000:8000 \
  --env-file .env \
  --add-host=host.docker.internal:host-gateway \
  -e DB_HOST=host.docker.internal \
  -v "$(pwd)/workspace:/app/workspace" \
  -v "$(pwd)/data:/app/data" \
  cloudexam
```

Si todo corre en Docker, comparte network:

```bash
docker network create cloudexam-net
docker network connect cloudexam-net pgvector

docker run --rm -p 8000:8000 \
  --env-file .env \
  --network cloudexam-net \
  -e DB_HOST=pgvector \
  -v "$(pwd)/workspace:/app/workspace" \
  -v "$(pwd)/data:/app/data" \
  cloudexam
```

Los volúmenes `workspace/` y `data/` persisten los exámenes procesados, las
imágenes subidas y la configuración runtime entre reinicios.

### 6. Abrir la app

```
http://localhost:8000
```

El frontend se sirve directamente desde FastAPI (no necesitas `npm run dev`
en producción).

## Ejecutar localmente sin Docker (desarrollo)

Backend:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements1.txt
uvicorn host_and_client_react_agent:app --reload --port 8000
```

Frontend (terminal aparte):

```bash
cd frontend
npm install
npm run dev
```

El frontend de Vite corre en `http://localhost:5173` y proxy-ea `/api/*` y
`/ws` a `http://localhost:8000`. Usa esa URL durante el desarrollo.

## Endpoints principales

| Método | Path | Uso |
|---|---|---|
| `GET`  | `/api/workspace/state` | Estado completo del agente (también es el healthcheck) |
| `POST` | `/api/workspace/state` | Guardar/actualizar un valor en el state |
| `GET`  | `/api/workspace/files` | Listar archivos del workspace |
| `POST` | `/api/workspace/files/read` | Leer contenido de un archivo |
| `POST` | `/api/workspace/files/write` | Escribir contenido |
| `POST` | `/api/exams/extract-from-pdf` | Extraer preguntas de un PDF de examen (Gemini Vision) |
| `POST` | `/api/medical-images/upload` | Subir imagen médica con keywords |
| `GET`  | `/api/medical-images/search` | Buscar imágenes por keywords |
| `POST` | `/api/medical-images/enrich` | Enriquecer respuesta con imágenes |
| `GET`  | `/api/checkpoints` | Listar checkpoints (git tags) del workspace |
| `POST` | `/api/checkpoints/create` | Crear checkpoint manual |
| `POST` | `/api/checkpoints/restore` | Restaurar a un checkpoint |
| `WS`   | `/ws` | Canal del chat con el agente |
| `GET`  | `/api/tools` | Lista de tools registradas para el agente |

## Tools disponibles para el agente

19 tools registradas en `servers/react_tools/tools_loader.py`:

- **Filesystem**: `read_file`, `write_file`, `list_files`
- **State**: `save_state`, `load_state`, `get_full_state`, `create_new_state`,
  `correct_text_in_state`, `search_state`, `edit_document`
- **Smart edit**: `smart_edit_state`, `smart_edit_file`, `smart_enrich_document`,
  `smart_resume`, `add_text`, `delete_lines`, `relocate_text`
- **Export**: `export_state_to_file`
- **Web**: `buscar_en_google`

## Healthcheck

```
GET /api/workspace/state    →    200 OK
```

El `Dockerfile` lo usa cada 30s (ver línea `HEALTHCHECK`).

## Troubleshooting

**`connection to server at "localhost" port 5432 failed`**
La app no encuentra Postgres. Verifica `DB_HOST`, `DB_PORT` en `.env`. Desde
un contenedor, `localhost` apunta al contenedor mismo, no al host — usa
`host.docker.internal` o conecta a la misma docker network que la DB.

**`extension "vector" is not available`**
Falta instalar pgvector en Postgres. Usa la imagen `pgvector/pgvector:pg16`
en lugar de `postgres:16`, o instala la extensión manualmente.

**`OPENAI_API_KEY not set` / `GEMINI_API_KEY not set`**
La key no se cargó. Confirma que `.env` esté en la raíz y que `--env-file .env`
esté en el comando de `docker run`. Alternativamente, configúralas desde la
pantalla "Configuración" de la UI (se persisten en `data/secrets.json`).

**El frontend muestra "Failed to fetch"**
El backend no está corriendo o el puerto no coincide. Verifica que
`http://localhost:8000/api/workspace/state` devuelva 200.

**Build de Docker lento**
El `npm ci` del frontend es lo más pesado. Asegúrate de que `frontend/node_modules`
esté en `.dockerignore` (ya lo está) para que no se copie al contexto.

## Licencia

Sin licencia declarada. Si planeas reutilizar el código, abre un issue.
