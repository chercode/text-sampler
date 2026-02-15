import argparse
import json
from typing import Any, Dict, Optional

import requests


class LineSamplerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", *, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            r = requests.request(method, url, json=json_body, timeout=self.timeout)
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
        return self._request("POST", "/load", json_body={"filepath": filepath})

    def sample(self, n: int) -> Dict[str, Any]:
        if n < 0:
            raise SystemExit("n must be >= 0")
        return self._request("POST", "/sample", json_body={"n": n})

    def stats(self) -> Dict[str, Any]:
        return self._request("GET", "/stats", json_body=None)

    def clear(self) -> Dict[str, Any]:
        return self._request("POST", "/clear", json_body=None)

    def reset(self) -> Dict[str, Any]:
        return self._request("POST", "/reset", json_body=None)


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Client for the Text Sampler server")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout seconds")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_load = sub.add_parser("load", help="Load a text file into the server cache")
    p_load.add_argument("filepath")

    p_sample = sub.add_parser("sample", help="Sample N random lines (and invalidate them)")
    p_sample.add_argument("n", type=int)
    p_sample.add_argument("--output", help="Write sampled lines to a file (one per line)")

    sub.add_parser("stats", help="Show current cache size")
    sub.add_parser("clear", help="Clear the cache")
    sub.add_parser("reset", help="Clear cache and reset lifetime counters")

    args = parser.parse_args()
    client = LineSamplerClient(base_url=args.base_url, timeout=args.timeout)

    if args.cmd == "load":
        _print(client.load(args.filepath))

    elif args.cmd == "sample":
        res = client.sample(args.n)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                for line in res.get("lines", []):
                    f.write(line + "\n")
        _print(res)

    elif args.cmd == "stats":
        _print(client.stats())
    elif args.cmd == "clear":
        _print(client.clear())
    elif args.cmd == "reset":
        _print(client.reset())


if __name__ == "__main__":
    main()
