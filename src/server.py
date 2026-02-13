import logging
import threading
from typing import List
from fastapi import FastAPI
import uvicorn

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

cache = LineCache()

app = FastAPI(
    title="Text Line Sampler",
    description="Thread-safe server for loading and sampling text file lines",
    version="1.0.0"

)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")