from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from fastapi.testclient import TestClient
from src.server import app
import pytest
from src.server import app, cache
import tempfile

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_cache():
    cache.reset()
    yield
    cache.reset()


@pytest.fixture
def sample_file():
    """Create a temporary file with sample content."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        for i in range(100):
            f.write(f"Line {i}\n")
        filepath = f.name
    
    yield filepath
    
    # Cleanup
    try:
        os.unlink(filepath)
    except:
        pass


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200

def test_load_file(client, sample_file):
    response = client.post("/load", json={"filepath": sample_file})
    assert response.status_code == 200

def test_sample_basic(client, sample_file):
    client.post("/load", json={"filepath": sample_file})
    response = client.post("/sample", json={"n": 10})
    assert response.status_code == 200


def test_no_duplicate_samples(client, sample_file):
    client.post("/load", json={"filepath": sample_file})
    
    response = client.post("/sample", json={"n": 100})
    lines = response.json()["lines"]

    assert len(lines) == len(set(lines)), "Found duplicate lines in sample"

def test_concurrent_loads(client):
    files = []
    for i in range(5):
        f = tempfile.NamedTemporaryFile(mode='w', delete=False)
        for j in range(20):
            f.write(f"File{i} Line{j}\n")
        f.close()
        files.append(f.name)
    
    try:
        # Load all files concurrently
        def load_file(filepath):
            return client.post("/load", json={"filepath": filepath})
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(load_file, f) for f in files]
            results = [f.result() for f in as_completed(futures)]
        
        # Verify all succeeded
        for result in results:
            assert result.status_code == 200
        
        # Verify total lines
        stats = client.get("/stats").json()
        assert stats["current_lines"] == 100 
        assert stats["total_loaded"] == 100
    finally:
        for f in files:
            os.unlink(f)


def test_concurrent_samples(client, sample_file):
    # Load file
    client.post("/load", json={"filepath": sample_file})
    
    # Sample concurrently from multiple "clients"
    def sample_lines():
        return client.post("/sample", json={"n": 10})
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(sample_lines) for _ in range(5)]
        results = [f.result() for f in as_completed(futures)]
    
    # Verify all succeeded
    for result in results:
        assert result.status_code == 200
    
    # Collect all sampled lines
    all_sampled = []
    for result in results:
        all_sampled.extend(result.json()["lines"])
    
    assert len(all_sampled) == len(set(all_sampled)), "Found duplicate lines across concurrent samples"
    
    assert len(all_sampled) == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])