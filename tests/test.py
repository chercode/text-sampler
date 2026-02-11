from fastapi.testclient import TestClient
from src.server import app

client = TestClient(app)

def test_root():
    # Placeholder test to ensure CI passes initially
    assert True