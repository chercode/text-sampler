from fastapi import FastAPI
import uvicorn

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