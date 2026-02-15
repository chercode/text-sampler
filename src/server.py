from __future__ import annotations

import logging
import os
import random
import threading
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Safety limits (override via env vars)
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "500"))
MAX_CACHE_LINES = int(os.getenv("MAX_CACHE_LINES", "10000000"))  # 10M
MAX_SAMPLE_SIZE = int(os.getenv("MAX_SAMPLE_SIZE", "1000000"))   # 1M


class LineCache:
    def __init__(self) -> None:
        self.lines: List[str] = []
        self.lock = threading.RLock()
        self._total_loaded = 0
        self._total_sampled = 0

    def get_stats(self) -> dict:
        with self.lock:
            return {
                "current_lines": len(self.lines),
                "total_loaded": self._total_loaded,
                "total_sampled": self._total_sampled,
            }

    def load(self, filepath: str, *, chunk_size: int = 50_000) -> int:
        # File checks (size, existence) happen before reading
        st = os.stat(filepath)  # raises FileNotFoundError / PermissionError
        size_mb = st.st_size / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            raise ValueError(f"File too large ({size_mb:.1f}MB). Limit is {MAX_FILE_SIZE_MB}MB.")

        appended = 0
        batch: list[str] = []

        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.rstrip("\n").rstrip("\r")
                batch.append(line)

                if len(batch) >= chunk_size:
                    appended += self._flush_batch(batch)
                    batch.clear()

            if batch:
                appended += self._flush_batch(batch)

        logger.info("Loaded %s lines. Cache: %s", appended, len(self.lines))
        return appended

    def _flush_batch(self, batch: list[str]) -> int:
        with self.lock:
            remaining = MAX_CACHE_LINES - len(self.lines)
            if remaining <= 0:
                raise ValueError("Cache limit reached.")
            to_add = batch[:remaining]
            self.lines.extend(to_add)
            self._total_loaded += len(to_add)
            return len(to_add)

    def sample(self, n: int) -> List[str]:
        if n < 0:
            raise ValueError("n must be >= 0")
        if n > MAX_SAMPLE_SIZE:
            raise ValueError(f"n too large ({n}). Limit is {MAX_SAMPLE_SIZE}.")

        with self.lock:
            k = min(n, len(self.lines))
            out: List[str] = []
            for _ in range(k):
                i = random.randrange(len(self.lines))
                self.lines[i], self.lines[-1] = self.lines[-1], self.lines[i]
                out.append(self.lines.pop())
            self._total_sampled += k
            logger.info("Sampled %s lines. Remaining cache size: %s", k, len(self.lines))
            return out

    def clear(self) -> int:
        with self.lock:
            count = len(self.lines)
            self.lines.clear()
            logger.info("Cleared %s lines from cache", count)
            return count

    def reset(self) -> int:
        with self.lock:
            cleared = len(self.lines)
            self.lines.clear()
            self._total_loaded = 0
            self._total_sampled = 0
            return cleared


cache = LineCache()

app = FastAPI(
    title="Text Line Sampler",
    description="Thread-safe server for loading and sampling text file lines",
    version="1.0.0",
)


class LoadRequest(BaseModel):
    filepath: str = Field(..., description="Path to the text file on the server host")

    @field_validator("filepath")
    @classmethod
    def validate_filepath(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("filepath cannot be empty")
        return v


class LoadResponse(BaseModel):
    lines_read: int
    total_lines_in_cache: int


@app.post("/load", response_model=LoadResponse)
def load(request: LoadRequest) -> LoadResponse:
    try:
        lines_read = cache.load(request.filepath)
        stats = cache.get_stats()
        return LoadResponse(lines_read=lines_read, total_lines_in_cache=stats["current_lines"])
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {request.filepath}")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {request.filepath}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class SampleRequest(BaseModel):
    n: int = Field(..., ge=0)


class SampleResponse(BaseModel):
    lines: List[str]
    count: int
    remaining_in_cache: int


@app.post("/sample", response_model=SampleResponse)
def sample(request: SampleRequest) -> SampleResponse:
    try:
        lines = cache.sample(request.n)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    stats = cache.get_stats()
    return SampleResponse(lines=lines, count=len(lines), remaining_in_cache=stats["current_lines"])


@app.get("/stats")
def get_stats() -> dict:
    return cache.get_stats()


@app.post("/clear")
def clear_cache() -> dict:
    count = cache.clear()
    return {"cleared": count}


@app.get("/health")
def health_check() -> dict:
    return {"status": "healthy"}


@app.post("/reset")
def reset() -> dict:
    cleared = cache.reset()
    return {"reset": True, "cleared": cleared}
