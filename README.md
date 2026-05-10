# cloudexam

Aplicación web app para análisis de exámenes médicos asistido por agente. Permite subir
PDFs de exámenes, extraer las preguntas y opciones, y analizar cada pregunta
con un agente ReAct (LangGraph) que razona sobre el contenido y consulta una
base local de PDFs e imágenes médicas indexada en pgvector.

## Características

- **Ingesta de exámenes**: sube PDFs/HTML de exámenes y la app extrae preguntas
  con opciones de respuesta usando OpenAI Vision.
- **Análisis por pregunta**: cada pregunta tiene un botón para analizarla con
  el agente.
- **Workspace de archivos**: el agente trabaja sobre archivos guardados en
  `workspace/`; los cambios se versionan automáticamente con git checkpoints.
- **Smart edit por archivo**: edita un archivo con una instrucción en
  lenguaje natural (`smart_edit_file`).
- **Imágenes médicas**: galería A2UI con imágenes anotadas por keywords;
  el agente puede enriquecer respuestas con imágenes relevantes (requiere
  Postgres + pgvector).
- **Skills dinámicas**: módulos en `skills/` cargados en runtime
  (template incluido en `skills/TEMPLATE_SKILL.md`).

## Stack

| Capa | Tecnología |
|---|---|
| Backend | FastAPI + Uvicorn |
| Agente | LangChain + LangGraph (`create_react_agent`) |
| LLM | OpenAI (gpt-4o / gpt-4o-mini) |
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
├── react_tools/tools_loader.py  # registro de tools de LangGraph
├── filesystem_service/          # read/write/list de archivos del workspace
├── smart_tools/smart_edit.py    # edición de archivos con LLM
├── medical_images_service.py    # imágenes médicas con embeddings
├── medical_keywords_extractor.py
├── keyword_rag_service.py
├── db_pool.py                   # connection pool a Postgres
└── versioning_service/          # checkpoints del workspace en git

frontend/                        # SPA React (TypeScript + Vite)
├── src/components/
│   ├── ExamViewer/              # render de preguntas + botones de análisis
│   ├── Chat/                    # panel de chat con el agente (SSE/WS)
│   ├── Editor/                  # editor del archivo activo
│   ├── Sidebar/                 # navegación de archivos
│   ├── ConfigScreen/            # configuración runtime (API keys, DB)
│   ├── ImageUpload/             # subida y registro de imágenes médicas
│   ├── A2UIImageGallery/        # galería para enriquecer respuestas
│   └── Debug/
└── src/services/api.ts          # cliente HTTP/SSE/WS

skills/                          # skills cargadas dinámicamente
static/, templates/              # assets servidos por FastAPI
```

## Requisitos previos

- **Docker** 20.10+ (recomendado para correr todo)
- **API key de OpenAI** (obligatoria para que arranque el agente y para
  extraer preguntas de PDFs)
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
# LLM principal del agente y extracción de PDFs (OBLIGATORIO — sin esto el
# agente no arranca y el endpoint /api/exams/extract-from-pdf falla)
OPENAI_API_KEY=sk-...

# Postgres con pgvector (opcional — solo para ingestar PDFs e imágenes médicas)
DB_HOST=localhost     # o el host de tu Postgres
DB_PORT=5432
DB_USER=postgres
DB_PWD=tu_password
DB_NAME=mibase
```

### Cómo obtener `OPENAI_API_KEY`

La app usa OpenAI como LLM principal del agente (modelo configurable; por
defecto `gpt-5-mini`) y también para extraer preguntas de los PDFs de exámenes
(`textractor_robust.py` con `gpt-5.4-mini` vision). Sin esta key el agente
**no se inicializa** y el endpoint `/api/exams/extract-from-pdf` falla.

1. Entra a <https://platform.openai.com/signup> y crea una cuenta (o haz login).
2. Ve a <https://platform.openai.com/api-keys> y haz clic en **Create new secret key**.
3. Dale un nombre (p. ej. `cloudexam`) y copia la key (empieza con `sk-...`).
   ⚠️ Solo se muestra una vez — guárdala en tu gestor de contraseñas.
4. OpenAI requiere **saldo prepagado**: ve a
   <https://platform.openai.com/account/billing> y carga al menos $5.

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
| `GET`  | `/api/workspace/files` | Listar archivos del workspace (también es el healthcheck) |
| `POST` | `/api/workspace/files/read` | Leer contenido de un archivo |
| `POST` | `/api/workspace/files/write` | Escribir contenido |
| `POST` | `/api/workspace/files/delete` | Borrar un archivo |
| `POST` | `/api/workspace/files/export-pdf` | Renderizar un examen JSON como PDF |
| `POST` | `/api/exams/extract-from-pdf` | Extraer preguntas de un PDF de examen (OpenAI Vision) |
| `POST` | `/api/medical-images/upload` | Subir imagen médica con keywords |
| `GET`  | `/api/medical-images/search` | Buscar imágenes por keywords |
| `POST` | `/api/medical-images/enrich` | Enriquecer respuesta con imágenes |
| `GET`  | `/api/checkpoints` | Listar checkpoints (git tags) del workspace |
| `POST` | `/api/checkpoints/create` | Crear checkpoint manual |
| `POST` | `/api/checkpoints/restore` | Restaurar a un checkpoint |
| `POST` | `/api/ask` / `/api/ask/stream` | Enviar una pregunta al agente |
| `WS`   | `/ws` | Notificaciones de cambios en archivos |
| `GET`  | `/api/tools` | Lista de tools registradas para el agente |

## Tools disponibles para el agente

Tools registradas en `servers/react_tools/tools_loader.py`:

- `read_file(filename)` — leer un archivo del workspace.
- `write_file(filename, content)` — crear o sobrescribir un archivo.
- `list_files(directory=".")` — listar archivos del workspace.
- `smart_edit_file(filename, instruction)` — editar un archivo con LLM.

## Healthcheck

```
GET /api/workspace/files    →    200 OK
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

**`OPENAI_API_KEY not set`**
La key no se cargó. Confirma que `.env` esté en la raíz y que `--env-file .env`
esté en el comando de `docker run`. Alternativamente, configúrala desde la
pantalla "Configuración" de la UI (se persiste en `data/secrets.json`).

**El frontend muestra "Failed to fetch"**
El backend no está corriendo o el puerto no coincide. Verifica que
`http://localhost:8000/api/workspace/files` devuelva 200.

**Build de Docker lento**
El `npm ci` del frontend es lo más pesado. Asegúrate de que `frontend/node_modules`
esté en `.dockerignore` (ya lo está) para que no se copie al contexto.

## Licencia

[MIT](LICENSE) © 2026 mejorgest
