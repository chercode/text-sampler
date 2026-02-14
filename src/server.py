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
    def load(self, filepath: str) -> int:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                new_lines = f.read().splitlines()
        except FileNotFoundError:
            logger.error(f"File not found: {filepath}")
            raise
        except PermissionError:
            logger.error(f"Permission denied: {filepath}")
            raise
    
        new_lines = [line for line in new_lines if line.strip()]
        with self.lock:
            self.lines.extend(new_lines)
            self._total_loaded += len(new_lines)
            logger.info(f"Loaded {len(new_lines)} lines. Cache: {len(self.lines)}")
            return len(new_lines)


    def sample(self, n: int) -> List[str]:
        with self.lock:
            if n > len(self.lines):
                n = len(self.lines)
        
            if n == 0:
                return []
        
            indices = random.sample(range(len(self.lines)), n)
            indices.sort(reverse=True)
        
            sampled = []
            for idx in indices:
                sampled.append(self.lines.pop(idx))
        
            self._total_sampled += n
            logger.info(f"Sampled {n} lines. Remaining: {len(self.lines)}")
            return sampled
        
    def clear(self):
        with self.lock:
            count = len(self.lines)
            self.lines.clear()
            logger.info(f"Cleared {count} lines from cache")
            return count
    

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
async def load(request: LoadRequest):
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
    n: int = Field(..., gt=0)

class SampleResponse(BaseModel):
    lines: List[str]
    count: int
    remaining_in_cache: int

@app.post("/sample", response_model=SampleResponse)
async def sample(request: SampleRequest):
    lines = cache.sample(request.n)
    stats = cache.get_stats()
    return SampleResponse(
        lines=lines,
        count=len(lines),
        remaining_in_cache=stats["current_lines"]
    )
@app.get("/stats")
async def get_stats():
    return cache.get_stats()

@app.post("/clear")
async def clear_cache():
    count = cache.clear()
    return {"cleared": count}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")