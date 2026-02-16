# Text Sampler — Server/Client Pair for Random, Non-Repeating Line Sampling

This project implements the **AI Engineer technical test**: a **server-client pair** that loads lines from large text files into a **global cache**, and provides **random samples** of those cached lines to clients. Sampled lines are **invalidated** (“used up”) so they cannot be returned to any other client.

The implementation is intentionally **simple, correct, and thread-safe**, and assumes the client and server run on the **same host** (localhost / Unix sockets), per the assignment.

---

## Requirements coverage (what the assignment requires and how this repo satisfies it)

### `load()`
- Input: a **text file**
- Behavior: reads the file, splits by newline (i.e., reads line-by-line), and **appends** its lines to a **global cache**
- Output: returns the **number of lines read**
- Empty lines:
  - The spec doesn’t require dropping empty lines, so this implementation **keeps empty lines** if they exist in the file.
  - If you want to filter them out, it’s an easy change: skip lines that are `""` (after stripping `\n`).

### `sample(N)`
- Input: integer **N**
- Behavior: returns **randomly sampled** lines from the global cache, and **invalidates** them (removes them from the cache)
- If **N > lines currently in cache**:
  - the server returns **all remaining lines**
  - `count` will be `< N`
  - cache becomes empty

### Concurrency / thread safety
- The global cache is protected by a lock so multiple clients can call `/load` and `/sample` concurrently without duplicates or corrupted state.

### Same-host assumption
- Server and client are designed to run on the **same machine** (`127.0.0.1`).

---

## Project overview

### Server
FastAPI server exposing:
- `POST /load` — load a file’s lines into the global cache
- `POST /sample` — sample and invalidate N lines from the cache
- `GET /health` — basic health check
- `GET /stats` — current cache stats

> **Test utility endpoint:** `POST /reset` (or `POST /clear` if `/reset` is not present) is included only to help the test suite isolate server state between tests; it is not required by the assignment’s core `load()` / `sample()` specification.

### Client
A small CLI client to:
- load a file by path
- sample N lines
- optionally write sampled lines to an output file

### Tests
A `pytest` suite validates correctness, invalidation, and concurrency behavior.

---

## Implementation notes (how it works)

### Global cache
- Stored in-memory as a Python list of strings.
- “Global” means shared by all requests handled by the running server process.

### Invalidation & performance
Sampling uses an efficient remove strategy (swap with last + pop) under a lock to ensure:
- no duplicates within a response
- no duplicates across clients
- fast removal without costly list shifting

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

## Data / input format

### What file types are accepted?
`/load` expects a **plain-text file** where each **line** represents one entry in the cache.

- Recommended: `*.txt` (UTF-8 text).
- In practice: any file that is readable as text line-by-line will work (e.g., `.text`, `.log`), as long as it contains newline-separated text.
- Compressed archives (e.g., `.zip`, `.tar.gz`) and structured formats (e.g., `.jsonl`, `.csv`) are **not** accepted directly unless you first extract/convert them into a newline-separated text file.

### Why this matters for `nlp-datasets`
The `nlp-datasets` GitHub list is a directory of many datasets in many different packaging formats (plain text, zipped archives, XML, CSV/TSV, etc.). The workflow is typically:

1) **Download** the dataset  
2) **Extract** it (if it’s an archive)  
3) **Convert/reformat** into a newline-separated `.txt` file  
4) **Load** that `.txt` file with `/load`  
5) **Sample** with `/sample`

---

## Example: Cornell Movie-Dialogs (from `nlp-datasets`) → `cornell_dialogue.txt`

The Cornell Movie-Dialogs corpus includes a file called `movie_lines.txt` which is **metadata-rich**. Each row is delimited by:

```
 +++$+++ 
```

and the last field contains the **utterance text**. A common preparation step is to extract only that text into a one-utterance-per-line `.txt`.

### Step 1) Download + extract
Download the corpus from your preferred source (e.g., Kaggle / HuggingFace / original mirror), then extract it so you have:

- `movie_lines.txt`
- (other metadata files)

### Step 2) Convert `movie_lines.txt` to a clean `.txt` file
**Option A — Python (cross-platform):**
```bash
python - <<'PY'
import pathlib

src = pathlib.Path("movie_lines.txt")
out = pathlib.Path("cornell_dialogue.txt")

sep = " +++$+++ "
with src.open("r", encoding="utf-8", errors="replace") as f_in, out.open("w", encoding="utf-8") as f_out:
    for line in f_in:
        parts = line.rstrip("\n").split(sep)
        if len(parts) >= 5:
            utterance = parts[4]
            f_out.write(utterance + "\n")
print("Wrote", out)
PY
```

**Option B — Unix tools (macOS/Linux):**
```bash
awk -F' \+\+\+\$\+\+\+ ' '{print $5}' movie_lines.txt > cornell_dialogue.txt
```

> Note: This conversion keeps empty utterances if they exist. If you want to drop blank lines, filter them out during conversion or in the server load loop.

### Step 3) Load and sample
Now `cornell_dialogue.txt` is exactly what this project expects: one text entry per line.

---


## How to run

### 0) Clone the repo
```bash
git clone https://github.com/chercode/text-sampler.git
cd text-sampler
```

> If you’re running from a `.zip`, unzip it and `cd` into the extracted folder instead.

---

### Option A — Local Python (recommended)

#### 1) Create a virtual environment and install dependencies
```bash
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows (PowerShell)

pip install -r requirements.txt
```

#### 2) Start the server (Terminal 1)
Keep this terminal running:
```bash
uvicorn src.server:app --host 127.0.0.1 --port 8000
```

Stop the server anytime with **Ctrl+C**.

If you are actively editing code and want auto-reload:
```bash
uvicorn src.server:app --reload
```

#### 3) Use the client (Terminal 2)
Open a **new terminal** in the same repo folder and activate the venv again:
```bash
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows (PowerShell)
```

Now run:

Load a file:
```bash
python -m src.client load /path/to/file.txt
```

Sample 10 lines:
```bash
python -m src.client sample 10
```

Write sampled lines to a file:
```bash
python -m src.client sample 100 --output sample.txt
```

**What happens if N > remaining lines?**
- The server returns **all remaining lines**.
- The response `count` will be the number of remaining lines (so `< N`).
- The cache becomes empty.

#### 4) Use the interactive API docs (optional)
Open:
```text
http://127.0.0.1:8000/docs
```

You can also use `curl` directly:
```bash
curl -s -X POST http://127.0.0.1:8000/load \
  -H "Content-Type: application/json" \
  -d '{"filepath":"file.txt"}'

curl -s -X POST http://127.0.0.1:8000/sample \
  -H "Content-Type: application/json" \
  -d '{"n":5}'
```

---

### Option B — Docker (optional)

#### Dockerfile (simple run)

Build the image:

```bash
docker build -t text-sampler .
```

Run the server:

```bash
docker run --rm -p 8000:8000 text-sampler
```

Important: `POST /load` expects a **file path that exists where the server runs**.  
If the server runs in Docker, mount a host folder into the container and use the container path:

```bash
# Put your text file(s) under ./data on the host
docker run --rm -p 8000:8000 -v "$PWD/data:/data:ro" text-sampler
```

Then load using the container path (server reads from inside the container):

```bash
python -m src.client load /data/file.txt
```

---

#### Docker Compose

Your `docker-compose.yml` runs Uvicorn with `--reload` and bind-mounts:
- `./src` → `/app/src` (so edits hot-reload)
- `./data` → `/data:ro` (so files can be loaded via `/data/...` paths)

Start:

```bash
docker compose up --build
```

Now load files that are located in `./data` on your host by referencing them as `/data/...` paths:

```bash
python -m src.client load /data/file.txt
```

Stop:

```bash
docker compose down
```

---

## API summary

### `POST /load`
Request body:
```json
{"filepath": "/path/to/file.txt"}
```

Response:
- `lines_read`: number of lines appended
- `total_lines_in_cache`: cache size after the load

### `POST /sample`
Request body:
```json
{"n": 10}
```

Response:
- `lines`: sampled lines
- `count`: number of lines returned (may be `< n` if overshoot)
- `remaining_in_cache`: cache size after sampling

---

## Empty lines behavior 
This implementation **keeps empty lines** if they exist in the input file, because the assignment doesn’t specify removing them.

If you want to ignore blank lines, it’s a simple change in the server’s load loop:
- strip newline
- `if line == "": continue`

---

## Testing

This repo includes a `pytest` test suite that validates API correctness, invalidation semantics, and concurrency/thread-safety.

### Run the tests
From the repo root (after installing deps and activating your venv):

```bash
pytest -q
```

More detail:
```bash
pytest -vv
```

### What the tests cover (mapped to this suite)

**1) Basic server sanity**
- `GET /health` returns `{"status":"healthy"}`

**2) Load behavior (`POST /load`)**
- Successful load returns:
  - `lines_read` equal to the number of lines in the file
  - `total_lines_in_cache` increases accordingly
- Multiple `/load` calls **append** to the global cache (do not overwrite)
- Validation and errors:
  - Missing JSON body / missing required fields → `422`
  - Missing file path → `404`

**3) Sample behavior (`POST /sample`)**
- Normal sampling:
  - returns exactly `n` lines when enough are available
  - cache size decreases by `n`
  - response includes `count` and `remaining_in_cache` consistent with `/stats`
- Overshoot behavior:
  - if `n` is larger than remaining cache size, server returns **all remaining lines**
  - response `count` equals remaining lines, and cache becomes empty
- Edge cases:
  - `n = 0` is allowed and returns an empty list without changing the cache
  - `n < 0` or missing `n` → `422`
  - sampling from an empty cache returns `{count: 0, lines: []}`

**4) Invalidation (no line ever returned twice)**
- Sequential sampling responses do not overlap (previously-sampled lines never come back)

**5) Concurrency / thread safety**
These tests simulate multiple “clients” concurrently using `ThreadPoolExecutor` + separate `TestClient` instances:
- concurrent `/sample` requests return **no duplicate lines** across clients when the dataset has unique lines
- concurrent `/load` requests append correctly; final cache size and totals match expected values
- mixed concurrent `/load` + `/sample`:
  - sampled lines contain no duplicates
  - every sampled line is guaranteed to be from the known loaded inputs (nothing “invented”)

**6) End-to-end integration test (real server process)**
One test launches a real Uvicorn server on a free local port and uses `requests` to validate:
- `/load` works against a live server
- `/sample` returns unique lines
- overshoot drains the cache
- invalidation holds across multiple sample calls

### Notes about isolation/reset between tests
- The suite attempts to reset state between tests via `POST /reset` (or falls back to `POST /clear`).
- This keeps tests independent so results don’t leak between cases.


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


   ---

   Implemented by Shahrzad Mirzaei.



