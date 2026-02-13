import requests

class LineSamplerClient:
    def __init__(self, base_url="http://127.0.0.1:8000"):
        self.base_url = base_url
    
    def load(self, filepath: str) -> dict:
        response = requests.post(f"{self.base_url}/load", json={"filepath": filepath})
        response.raise_for_status()
        return response.json()
    
    def sample(self, n: int) -> list:
        response = requests.post(f"{self.base_url}/sample", json={"n": n})
        response.raise_for_status()
        return response.json()["lines"]