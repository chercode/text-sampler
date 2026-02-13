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
    cache.clear()
    yield
    cache.clear()


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


# tests/test_server.py - start with a few tests
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

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])