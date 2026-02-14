import argparse
import json
from typing import Any, Dict, Optional

import requests


class LineSamplerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]],
        timeout: int
) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            r = requests.request(method, url, json=json_body, timeout=timeout)
        except requests.RequestException as e:
            raise SystemExit(f"Request failed: {e}")

        if not r.ok:
            msg = r.text.strip()
            raise SystemExit(f"{method} {path} failed ({r.status_code}): {msg}")

        try:
            return r.json()
        except ValueError:
            raise SystemExit(f"{method} {path} returned non-JSON response: {r.text[:200]}")

    def load(self, filepath: str) -> Dict[str, Any]:
        return self._request("POST", "/load", json_body={"filepath": filepath}, timeout=30)

    def sample(self, n: int) -> Dict[str, Any]:
        if n < 0:
            raise SystemExit("n must be >= 0")
        return self._request("POST", "/sample", json_body={"n": n}, timeout=30)

    def stats(self) -> Dict[str, Any]:
        return self._request("GET", "/stats", json_body=None, timeout=10)

    def clear(self) -> Dict[str, Any]:
        return self._request("POST", "/clear", json_body=None, timeout=10)


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Client for the Text Sampler server")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_load = sub.add_parser("load", help="Load a text file into the server cache")
    p_load.add_argument("filepath")

    p_sample = sub.add_parser("sample", help="Sample N random lines (and invalidate them)")
    p_sample.add_argument("n", type=int)

    sub.add_parser("stats", help="Show current cache size")
    sub.add_parser("clear", help="Clear the cache")

    args = parser.parse_args()
    client = LineSamplerClient(base_url=args.base_url)

    if args.cmd == "load":
        _print(client.load(args.filepath))
    elif args.cmd == "sample":
        _print(client.sample(args.n))
    elif args.cmd == "stats":
        _print(client.stats())
    elif args.cmd == "clear":
        _print(client.clear())


if __name__ == "__main__":
    main()
