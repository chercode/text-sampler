# Text Sampler — Server/Client Pair for Random, Non‑Repeating Line Sampling

This project implements the **AI Engineer technical project(Feb 2026)**: a **server-client pair** that loads lines from large text files into a **global cache**, and provides **random samples** of those cached lines to clients. Sampled lines are **invalidated** (“used up”) so they cannot be returned to any other client.

The design is intentionally **simple, correct, and thread-safe**, and assumes the client and server run on the **same host** (localhost / Unix socket), per the assignment.

---

## What the assignment requires (and how this repo satisfies it)

### Required behavior
1. **`load()`**
   - Input: a **text file**
   - Behavior: split by newline and **append lines to a global cache**
   - Output: **number of lines read**
2. **`sample(N)`**
   - Input: integer **N**
   - Output: **N randomly sampled lines** from the global cache
   - Important: sampled lines are **invalidated** so **no other client** can receive them
3. **Concurrency**
   - Must be **thread-safe**
   - Must support **several clients concurrently**
4. **Deployment assumptions**
   - Client and server run on the **same host**
   - Localhost or Unix sockets are appropriate
5. **Testing**
   - Include tests as you see fit

This repository provides:
- A FastAPI server exposing `/load` and `/sample` endpoints backed by a **single in-memory global cache**
- A CLI client for interacting with the server
- Thread-safe cache logic using locks
- Tests including **concurrency tests** and **invalidation correctness**

---

## Project overview

### Components
- **Server**: FastAPI app exposing:
  - `POST /load` — load a file’s lines into the global cache
  - `POST /sample` — sample and invalidate N lines from the cache
  - (Optional utility) `/health` and `/stats` endpoints for sanity checking / debugging
- **Client**: simple CLI for:
  - loading a file by path
  - sampling N lines
  - optionally writing the result to a file
- **Tests**: pytest suite validating:
  - correct counts on load
  - sampling returns unique lines
  - sampled lines are removed from the cache (cannot be re-sampled)
  - concurrent client requests remain correct

---

## Implementation details (what I did and why)

### 1) Global cache (the core requirement)
The assignment asks for a **global cache** shared by all clients. In this implementation the cache is:
- **In-memory** (Python list of strings)
- **Process-local** (shared by all requests handled by the same server process)

This is the simplest way to satisfy the requirement while keeping performance excellent for typical text datasets that fit in RAM.

### 2) Thread safety (correctness under concurrency)
FastAPI can serve multiple requests concurrently (via threads / async execution). To prevent race conditions:
- All cache mutations (append during load, remove during sample) are protected by a **lock** (`threading.RLock`).

This ensures:
- Two clients cannot sample the same line
- A client cannot sample while another is partially updating the cache in a way that could corrupt state

### 3) Sampling + invalidation algorithm (why swap-pop)
A naive “remove sampled indices” approach can be slow because removing from the middle of a Python list shifts elements (O(n)).

To make sampling both **correct** and **fast**, the cache uses a **swap-with-last + pop** technique:

For each sampled line:
1. pick a random index `i`
2. swap `lines[i]` with `lines[-1]`
3. `pop()` the last element (which is now the sampled element)

This yields:
- **O(1)** removal per sampled item
- No duplicates within the same sample
- No duplicates across concurrent samples (because the whole operation is locked)

### 4) Streaming file loads (real-dataset friendliness)
Large files should be read **line-by-line**, not `read()` into memory at once.
This implementation loads files by iterating over the file handle, allowing large datasets to be handled without a single massive read.

### 5) Guardrails (practical safety)
This implementation includes configurable limits to avoid accidental overload:
- maximum file size (MB)
- maximum number of cached lines
- maximum sample size

These aren’t explicitly required, but they prevent:
- memory exhaustion
- enormous requests that stall the server

---

## Repository structure

Typical layout in this repo:
- `src/server.py` — FastAPI app + endpoints
- `src/client.py` — CLI client
- `tests/` — pytest test suite
- `requirements.txt` — Python dependencies
- `Dockerfile` / `docker-compose.yml` — containerized setups

(Exact paths may vary slightly depending on your local checkout, but the idea is the same.)

---

## How to run

### Option A — Local Python (recommended)

#### 1) Create a venv and install deps
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 2) Run the server
```bash
uvicorn src.server:app --host 127.0.0.1 --port 8000
```

#### 3) Use the CLI client

Load a file:
```bash
python -m src.client load /path/to/file.txt
```

Sample 10 lines:
```bash
python -m src.client sample 10
```

Write sample to an output file:
```bash
python -m src.client sample 100 --output sample.txt
```

#### 4) Open interactive docs (FastAPI)
```text
http://127.0.0.1:8000/docs
```

---

### Option B — Docker

Build image:
```bash
docker build -t text-sampler .
```

Run container:
```bash
docker run --rm -p 8000:8000 text-sampler
```

Then use the CLI on your host (or curl) against `http://127.0.0.1:8000`.

> Note: if your server runs inside Docker, file paths passed to `/load` must exist **inside the container**. A common pattern is to mount a host directory:
```bash
docker run --rm -p 8000:8000 -v "$PWD/data:/data" text-sampler
```
Then load:
```bash
python -m src.client load /data/file.txt
```

---

### Option C — Docker Compose

```bash
docker compose up --build
```


---

## API usage (direct)

### Load
Request:
```bash
curl -X POST http://127.0.0.1:8000/load \
  -H "Content-Type: application/json" \
  -d '{"filepath":"/path/to/file.txt"}'
```

### Sample
Request:
```bash
curl -X POST http://127.0.0.1:8000/sample \
  -H "Content-Type: application/json" \
  -d '{"n":10}'
```

---

## Testing

Run all tests:
```bash
pytest -q
```

What the tests cover (high level):
- Load returns correct line counts
- Sample returns correct number of lines
- Sample invalidates lines (they cannot be re-sampled)
- Concurrent loads/samples remain correct (thread safety)

---

## Limitations (important to know)

1. **Process-local global cache**
   - The “global cache” is global **within a single server process**.
   - If you run multiple worker processes (e.g., `uvicorn --workers 4`), each process will have its **own** cache.
   - For this assignment, run **one server process** to preserve a single global cache.

2. **In-memory storage**
   - Cache contents are lost if the server restarts.
   - Maximum dataset size is limited by available RAM.

3. **File path trust / security**
   - The server loads from file paths on the host.
   - The assignment assumes same-host usage; in a production environment you would restrict allowed directories and add auth.


---

## Next steps (beyond the scope, but “even better”)

If I wanted to expand this project beyond the requirements scope, here are the best upgrades I consider:

1. **Multi-process / distributed correctness**
   - Replace in-memory list with a shared store:
     - Redis (atomic pop + random selection strategies)
     - PostgreSQL with row-level locking or “SKIP LOCKED”
     - A message queue / log (if sampling semantics allow)
   - This enables horizontal scaling and multi-worker deployment.

2. **Authentication + authorization**
   - Add an API key (or OAuth) and restrict who can load/sample.
   - Restrict allowed load paths to a configured `DATA_DIR`.

3. **Performance enhancements for massive datasets**
   - Memory-map files (if you don’t need strict “append to cache” semantics)
   - Sharded cache / ring buffer
   - Better random sampling under very high contention


