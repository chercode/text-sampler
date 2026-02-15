from typing import List, Tuple
import random
import logging
import threading
from typing import List
from fastapi import FastAPI
import uvicorn
import random
from pydantic import BaseModel, Field, field_validator
from fastapi import HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LineCache:
    def __init__(self):
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

        appended = 0
        batch: list[str] = []

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                # Remove only newline characters; keep whitespace and blank lines intact.
                    line = raw.rstrip("\n").rstrip("\r")
                    batch.append(line)

                    if len(batch) >= chunk_size:
                        with self.lock:
                            self.lines.extend(batch)
                            self._total_loaded += len(batch)
                        appended += len(batch)
                        batch.clear()

                if batch:
                    with self.lock:
                        self.lines.extend(batch)
                        self._total_loaded += len(batch)
                    appended += len(batch)

        except FileNotFoundError:
            logger.error(f"File not found: {filepath}")
            raise
        except PermissionError:
            logger.error(f"Permission denied: {filepath}")
            raise

        logger.info(f"Loaded {appended} lines. Cache: {len(self.lines)}")
        return appended

    
    def sample(self, n: int) -> List[str]:

        if n < 0:
            raise ValueError("n must be >= 0")

        with self.lock:
            k = min(n, len(self.lines))
            out: List[str] = []

            for _ in range(k):
                i = random.randrange(len(self.lines))
                # swap-pop for O(1) removal
                self.lines[i], self.lines[-1] = self.lines[-1], self.lines[i]
                out.append(self.lines.pop())
            self._total_sampled += k

            logger.info(f"Sampled {k} lines. Remaining cache size: {len(self.lines)}")
            return out
        
    def clear(self):
        with self.lock:
            count = len(self.lines)
            self.lines.clear()
            logger.info(f"Cleared {count} lines from cache")
            return count
        
    def reset(self):
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
    version="1.0.0"

)

class LoadRequest(BaseModel):
    filepath: str = Field(..., description="Absolute path to the text file")

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
def load(request: LoadRequest):
    try:
        lines_read = cache.load(request.filepath)
        stats = cache.get_stats()
        return LoadResponse(
            lines_read=lines_read,
            total_lines_in_cache=stats["current_lines"]
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {request.filepath}")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {request.filepath}")
    
class SampleRequest(BaseModel):
    n: int = Field(..., ge=0)

class SampleResponse(BaseModel):
    lines: List[str]
    count: int
    remaining_in_cache: int

@app.post("/sample", response_model=SampleResponse)
def sample(request: SampleRequest):
    lines = cache.sample(request.n)
    stats = cache.get_stats()
    return SampleResponse(
        lines=lines,
        count=len(lines),
        remaining_in_cache=stats["current_lines"]
    )
@app.get("/stats")
def get_stats():
    return cache.get_stats()

@app.post("/clear")
def clear_cache():
    count = cache.clear()
    return {"cleared": count}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/reset")
def reset():
    cleared = cache.reset()
    return {"reset": True, "cleared": cleared}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")