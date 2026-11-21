# CloudDisk

**English** | [简体中文](README.zh-CN.md)

CloudDisk is a self-hosted document cloud drive for individuals and small teams. It stores files safely, keeps duplicate content to a single physical copy, and makes documents easy to find through filename search, full-text search, and natural-language semantic search.

The project is a Python microservice application:

- **Gateway** — FastAPI HTTP API, JWT authentication, file management, frontend static files, search orchestration.
- **VFS / Storage** — virtual file system, MD5 content-addressed local storage, reference counting, soft delete.
- **Search Service** — gRPC service using jieba + SQLite FTS5 + BM25.
- **Semantic Service** — gRPC service using sentence-transformers + FAISS, with an offline hashing fallback.

A plain HTML/CSS/JavaScript frontend is included and talks to the real backend API.

> This README explains what the project does and how to run and use it.
> For the full specification see [`PROJECT_REQUIREMENTS.md`](PROJECT_REQUIREMENTS.md);
> for code structure and complete call paths see [`docs/CODE_GUIDE.md`](docs/CODE_GUIDE.md).

---

## What it does

- Register / sign in with JWT authentication.
- Create folders, browse a folder tree, rename, move, and soft-delete files.
- Upload and download files with streaming I/O.
- Store physical content by MD5: identical content is written only once.
- Track logical files and physical objects separately with reference counts.
- Search by filename with Chinese/English tokenization.
- Search inside document contents (TXT, HTML, Markdown; optional PDF/DOCX/PPTX/XLSX).
- Semantic search over document embeddings.
- Find files similar to a selected file.
- View per-file keyword/semantic index status; retry failures; rebuild indexes.
- Degrade gracefully: keyword search works without the semantic service; file management works without either index service.

Supported text extraction out of the box: TXT, HTML, Markdown, and common text-like files. Binary or unsupported files are still searchable by filename.

---

## Architecture

```text
Browser
  │ HTTP + JWT
  ▼
Gateway Service (FastAPI, :8000)
  ├── VFS / Storage (same process, separate module)
  │     ├── SQLite (demo default) / MySQL 8 (optional)
  │     └── data/objects/ab/cd/<md5>
  ├── gRPC ──► Search Service (:50051)
  │              jieba + SQLite FTS5 + BM25
  └── gRPC ──► Semantic Service (:50052)
                 sentence-transformers + FAISS
```

Search and semantic services never access the business database. The Gateway asks them for candidate `file_id`s, then checks the database itself for ownership and active status before returning results.

---

## Requirements

- Linux / WSL (Ubuntu recommended)
- Miniconda or Anaconda
- Python 3.11 via the supplied Conda environment

No MySQL server and no Docker are required for the demo. The default configuration uses a local SQLite file. MySQL 8 is supported as an optional deployment target.

---

## Quick start

### 1. Create the Conda environment

```bash
cd CloudDisk
conda env create -f environment.yml
conda activate DSC5003
```

If Conda is not installed, the helper script can install Miniforge and create the environment without using `base`:

```bash
bash scripts/setup_conda_env.sh
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate DSC5003
```

### 2. Configure

```bash
cp .env.example .env
```

The default `.env` uses SQLite:

```ini
DATABASE_URL=sqlite:///data/clouddisk.db
AUTO_CREATE_TABLES=true
```

If a `.env` already exists in your checkout, keep it — do not overwrite it with the example.

Change `JWT_SECRET` before exposing the service to other people.

### 3. Initialize the database and demo account

```bash
python sql/seed.py --username demo --password demo123456
```

This creates the SQLite database, all tables, the demo user, and its root folder.

### 4. Start the services

```bash
python scripts/run_services.py
```

This starts:

| Service | Default address |
|---|---|
| Search gRPC | `127.0.0.1:50051` |
| Semantic gRPC | `127.0.0.1:50052` |
| Gateway + frontend | `http://127.0.0.1:8000/` |
| Swagger API docs | `http://127.0.0.1:8000/api/docs` |

Press `Ctrl+C` to stop everything.

You can also start the three services in separate terminals:

```bash
python -m services.search.server
python -m services.semantic.server
uvicorn services.gateway.main:app --host 127.0.0.1 --port 8000
```

### 5. Generate optional demo files

```bash
python scripts/generate_test_data.py --count 8
```

This uploads Chinese sample documents, including one duplicate-content file to demonstrate MD5 deduplication.

### 6. Open the app

Go to <http://127.0.0.1:8000/> and sign in with:

```text
username: demo
password: demo123456
```

---

## Using CloudDisk

- **Create folder** — use the “New folder” button in the file view.
- **Upload** — click “Upload file” or drag files onto the drop zone.
- **Download** — use the download action on a file row.
- **Rename / move / delete** — use the per-file actions; folders are deleted recursively.
- **Keyword search** — choose “All”, “Filename”, or “Content”, then search.
- **Semantic search** — choose “Semantic search” and describe what you are looking for in natural language.
- **Similar files** — click “Similar” on a file or search result.
- **Index status** — open “Index status” to inspect keyword/semantic state, retry a failed file, or rebuild indexes.
- **API docs** — browse interactive endpoints at `/api/docs`.

The interface is a single-page app backed by the real API; there is no mock data.

---

## Semantic model

The semantic service uses `sentence-transformers` with FAISS. To keep startup fast and avoid unexpected large downloads, `SEMANTIC_EMBEDDER=auto` uses **only an already-cached model** and otherwise falls back to an offline hashing embedder. The fallback is marked `degraded` in the API and UI.

To enable the neural model explicitly:

```bash
conda run -n DSC5003 python scripts/download_semantic_model.py
```

The default multilingual model is about 471 MB. A smaller Chinese model can be selected:

```bash
conda run -n DSC5003 python scripts/download_semantic_model.py \
    --model BAAI/bge-small-zh-v1.5
```

If you change the model, set `SEMANTIC_MODEL_NAME` in `.env` and rebuild the semantic index from the UI or with `scripts/rebuild_indexes.py`.

If Hugging Face is slow or resets connections, try:

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

Model weights live in `data/models/` and are never committed.

---

## Configuration

Copy `.env.example` to `.env`; all values can also be supplied as environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///data/clouddisk.db` | Demo database; MySQL URL supported |
| `JWT_SECRET` | placeholder | JWT signing key — change it |
| `HTTP_HOST` / `HTTP_PORT` | `127.0.0.1:8000` | Gateway bind address |
| `SEARCH_SERVICE_HOST/PORT` | `127.0.0.1:50051` | Internal search gRPC |
| `SEMANTIC_SERVICE_HOST/PORT` | `127.0.0.1:50052` | Internal semantic gRPC |
| `SEMANTIC_EMBEDDER` | `auto` | Cached neural model or offline hash |
| `MAX_UPLOAD_BYTES` | `536870912` | Maximum upload size (512 MiB) |

`data/` contains the SQLite database, MD5 objects, search index, FAISS index, and model cache. It is ignored by Git.

### Optional: MySQL 8

Switch by editing `.env`:

```ini
DATABASE_URL=mysql+pymysql://clouddisk:clouddisk@127.0.0.1:3306/clouddisk?charset=utf8mb4
```

Then initialize:

```bash
sudo mysql < sql/create_database.sql
mysql -u clouddisk -p clouddisk < sql/schema.sql
python sql/seed.py --username demo --password demo123456
```

The application code, search service, and semantic service do not change.

### Running on a server

For a quick HTTP demo:

1. Set `HTTP_HOST=0.0.0.0` and `HTTP_PORT=8000` in `.env`.
2. Allow TCP `8000` in the server firewall/security group.
3. Visit `http://SERVER_IP:8000/`.

For a public demo on ports 80/443, use Nginx as a reverse proxy to `127.0.0.1:8000` and keep `50051/50052` private. Do not expose the internal gRPC ports.

---

## Tests

```bash
conda activate DSC5003
pytest -q
```

The test suite boots real Search and Semantic gRPC servers on ephemeral ports and drives the real FastAPI app. It uses a temporary SQLite database and the offline hashing embedder, so no MySQL and no model download are required.

Coverage includes authentication, user isolation, VFS operations, MD5 deduplication and reference counting, keyword and semantic search, index status/retry/rebuild, database constraints, degradation, corrupted index recovery, and an end-to-end user journey.

---

## Project layout

| Path | Contents |
|---|---|
| `services/gateway/` | HTTP API, auth, routing, index orchestration, error handling |
| `services/vfs_storage/` | Virtual file system, MD5 object storage, text extraction |
| `services/search/` | jieba + SQLite FTS5 engine and gRPC service/client |
| `services/semantic/` | Chunking, embeddings, FAISS index and gRPC service/client |
| `shared/` | Settings, SQLAlchemy models, JWT/password helpers, shared types |
| `frontend/` | Plain HTML/CSS/JavaScript single-page app |
| `protos/` | `clouddisk.proto` |
| `sql/` | SQLite/MySQL setup, optional MySQL DDL, seed script |
| `scripts/` | Environment setup, service runner, demo data, index maintenance |
| `tests/` | pytest suite |
| `data/` | Runtime database, objects, indexes, model cache (ignored) |

For detailed module responsibilities and the full call chain, see [`docs/CODE_GUIDE.md`](docs/CODE_GUIDE.md). For the database ER diagram, see [`docs/ER.md`](docs/ER.md).

---

## Documentation

- [Code guide — directories, file responsibilities, microservice calls](docs/CODE_GUIDE.md)
- [Database ER diagram](docs/ER.md)
- [Project requirements](PROJECT_REQUIREMENTS.md)
- [Optional MySQL schema](sql/schema.sql)

---

## Notes

- The demo defaults to SQLite; MySQL 8 is optional.
- The Search and Semantic gRPC ports should stay on `127.0.0.1` unless you deliberately isolate the services on another network.
- All core settings come from `.env` / environment variables, not hard-coded paths.
