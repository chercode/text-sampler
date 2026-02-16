import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
from typing import List, Set

import pytest
from fastapi.testclient import TestClient

from src.server import app
import socket
import subprocess
import sys
import time

import requests



@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _post_reset_or_clear(c: TestClient) -> None:
    r = c.post("/reset")
    if r.status_code == 404:
        c.post("/clear")


@pytest.fixture(autouse=True)
def isolated_server_state(client: TestClient):
    _post_reset_or_clear(client)
    yield
    _post_reset_or_clear(client)


@pytest.fixture
def unique_lines_file() -> str:
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as f:
        for i in range(1000):
            f.write(f"Line-{i}\n")
        path = f.name

    yield path

    try:
        os.unlink(path)
    except OSError:
        pass


def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "healthy"


def test_load_returns_lines_read_and_appends(client: TestClient, unique_lines_file: str):
    r = client.post("/load", json={"filepath": unique_lines_file})
    assert r.status_code == 200
    body = r.json()
    assert body["lines_read"] == 1000
    assert body["total_lines_in_cache"] == 1000

    stats = client.get("/stats").json()
    assert stats["current_lines"] == 1000
    assert stats["total_loaded"] == 1000


def test_load_missing_file_returns_404(client: TestClient):
    r = client.post("/load", json={"filepath": "/no/such/file/definitely_missing.txt"})
    assert r.status_code == 404


def test_load_missing_body_returns_422(client: TestClient):
    r = client.post("/load", json={})
    assert r.status_code == 422


def test_sample_basic_count_and_cache_shrink(client: TestClient, unique_lines_file: str):
    client.post("/load", json={"filepath": unique_lines_file})

    before = client.get("/stats").json()["current_lines"]
    r = client.post("/sample", json={"n": 10})
    assert r.status_code == 200

    body = r.json()
    lines: List[str] = body["lines"]
    assert body["count"] == 10
    assert len(lines) == 10

    after = client.get("/stats").json()["current_lines"]
    assert before - after == 10
    assert body["remaining_in_cache"] == after


def test_sample_overshoot_returns_all_remaining(client: TestClient, unique_lines_file: str):
    client.post("/load", json={"filepath": unique_lines_file})

    # ask for more than available
    r = client.post("/sample", json={"n": 5000})
    assert r.status_code == 200

    body = r.json()
    assert body["count"] == 1000
    assert len(body["lines"]) == 1000
    assert body["remaining_in_cache"] == 0

    stats = client.get("/stats").json()
    assert stats["current_lines"] == 0
    assert stats["total_sampled"] == 1000


def test_sample_validation_negative_n_returns_422(client: TestClient):
    r = client.post("/sample", json={"n": -1})
    assert r.status_code == 422


def test_sample_validation_missing_n_returns_422(client: TestClient):
    r = client.post("/sample", json={})
    assert r.status_code == 422


def test_sample_allows_zero_and_returns_empty_list_without_changing_cache(
    client: TestClient, unique_lines_file: str
):
    # This is the cleanest API behavior: n=0 is a valid request that returns 0 lines.
    client.post("/load", json={"filepath": unique_lines_file})
    before = client.get("/stats").json()["current_lines"]

    r = client.post("/sample", json={"n": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["lines"] == []

    after = client.get("/stats").json()["current_lines"]
    assert after == before


def test_sequential_samples_do_not_overlap(client: TestClient, unique_lines_file: str):
    client.post("/load", json={"filepath": unique_lines_file})

    r1 = client.post("/sample", json={"n": 200})
    assert r1.status_code == 200
    a = set(r1.json()["lines"])
    assert len(a) == 200

    r2 = client.post("/sample", json={"n": 200})
    assert r2.status_code == 200
    b = set(r2.json()["lines"])
    assert len(b) == 200

    assert a.isdisjoint(b), "A previously sampled line was returned again"


def test_no_duplicates_across_concurrent_samples_on_unique_data(unique_lines_file: str):
    with TestClient(app) as c:
        c.post("/load", json={"filepath": unique_lines_file})

    def worker_sample(n: int):
        with TestClient(app) as c:
            r = c.post("/sample", json={"n": n})
            assert r.status_code == 200
            return r.json()["lines"]

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(worker_sample, 50) for _ in range(10)]
        results = []
        for f in as_completed(futs):
            results.extend(f.result())

    assert len(results) == 500
    assert len(results) == len(set(results)), "Duplicate lines returned across concurrent samples"


def test_concurrent_loads_append_all_lines():
    paths = []
    try:
        for i in range(5):
            f = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
            for j in range(200):
                f.write(f"F{i}-L{j}\n")
            f.close()
            paths.append(f.name)

        with TestClient(app) as c:
            # reset before test
            _post_reset_or_clear(c)

        def worker_load(p: str):
            with TestClient(app) as c:
                r = c.post("/load", json={"filepath": p})
                assert r.status_code == 200
                return r.json()["lines_read"]

        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = [ex.submit(worker_load, p) for p in paths]
            loaded_counts = [f.result() for f in as_completed(futs)]

        assert sum(loaded_counts) == 5 * 200

        with TestClient(app) as c:
            stats = c.get("/stats").json()
            assert stats["current_lines"] == 1000
            assert stats["total_loaded"] == 1000

    finally:
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass


def test_mixed_concurrent_load_and_sample_has_no_duplicates_and_only_returns_loaded_lines():
    # Create two files with disjoint known contents so we can validate sampled lines
    paths: List[str] = []
    expected: Set[str] = set()

    try:
        for i in range(2):
            f = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
            for j in range(500):
                line = f"M{i}-Line-{j}"
                f.write(line + "\n")
                expected.add(line)
            f.close()
            paths.append(f.name)

        # Reset server state
        with TestClient(app) as c:
            _post_reset_or_clear(c)

        start = Event()

        def worker_load(p: str) -> int:
            start.wait()
            with TestClient(app) as c:
                r = c.post("/load", json={"filepath": p})
                assert r.status_code == 200
                return r.json()["lines_read"]

        def worker_sample(n: int) -> List[str]:
            start.wait()
            with TestClient(app) as c:
                r = c.post("/sample", json={"n": n})
                assert r.status_code == 200
                return r.json()["lines"]

        # Mix loaders and samplers
        sampled: List[str] = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = []
            futs += [ex.submit(worker_load, p) for p in paths]         # 2 loaders
            futs += [ex.submit(worker_sample, 150) for _ in range(4)]  # 4 samplers

            # Start all at once
            start.set()

            # Collect results
            loaded_counts = 0
            for f in as_completed(futs):
                res = f.result()
                if isinstance(res, int):
                    loaded_counts += res
                else:
                    sampled.extend(res)

        # Sanity: loaders loaded what we expect (1000 total)
        assert loaded_counts == 1000

        # No duplicates among sampled lines (invalidation under concurrency)
        assert len(sampled) == len(set(sampled)), "Duplicate lines returned during mixed load/sample concurrency"

        # Every sampled line must come from the files we loaded
        assert set(sampled).issubset(expected), "Sample returned a line that was never loaded"

        # Finally, drain everything that's left and ensure overall sampled lines are still unique
        with TestClient(app) as c:
            r = c.post("/sample", json={"n": 5000})
            assert r.status_code == 200
            rest = r.json()["lines"]

        all_sampled = sampled + rest
        assert len(all_sampled) == len(set(all_sampled)), "Duplicate lines returned after draining the cache"
        assert set(all_sampled).issubset(expected)


    finally:
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass


def test_sample_from_empty_cache_returns_empty_list_and_zero_count(client: TestClient):
    stats_before = client.get("/stats").json()
    assert stats_before["current_lines"] == 0

    r = client.post("/sample", json={"n": 10})
    assert r.status_code == 200

    body = r.json()
    assert body["count"] == 0
    assert body["lines"] == []
    assert body["remaining_in_cache"] == 0

    stats_after = client.get("/stats").json()
    assert stats_after["current_lines"] == 0
    assert stats_after["total_sampled"] == 0


def test_multiple_load_calls_append_not_overwrite(client: TestClient):
    paths = []
    try:
        f1 = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
        for i in range(100):
            f1.write(f"A-{i}\n")
        f1.close()
        paths.append(f1.name)

        f2 = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
        for i in range(200):
            f2.write(f"B-{i}\n")
        f2.close()
        paths.append(f2.name)

        r1 = client.post("/load", json={"filepath": paths[0]})
        assert r1.status_code == 200
        assert r1.json()["lines_read"] == 100

        r2 = client.post("/load", json={"filepath": paths[1]})
        assert r2.status_code == 200
        assert r2.json()["lines_read"] == 200

        stats = client.get("/stats").json()
        assert stats["current_lines"] == 300
        assert stats["total_loaded"] == 300

        # Optional: sanity check you can sample everything and it’s all unique
        r = client.post("/sample", json={"n": 1000})
        assert r.status_code == 200
        lines = r.json()["lines"]
        assert len(lines) == 300
        assert len(lines) == len(set(lines))
    finally:
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass


def test_sample_rejects_n_over_max_sample_size_returns_400(client: TestClient, unique_lines_file: str):

    client.post("/load", json={"filepath": unique_lines_file})

    r = client.post("/sample", json={"n": 1_000_001})
    assert r.status_code == 400
    assert "Limit is" in r.json().get("detail", "")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(base_url: str, timeout_s: float = 8.0) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            r = requests.get(f"{base_url}/health", timeout=0.5)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.1)
    raise RuntimeError("Server did not start in time")


def test_integration_end_to_end_load_sample_invalidate():
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
    try:
        for i in range(50):
            tmp.write(f"Line-{i}\n")
        tmp.close()

        env = os.environ.copy()
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "src.server:app", "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        try:
            _wait_until_up(base_url)

            # 1) load
            r = requests.post(f"{base_url}/load", json={"filepath": tmp.name}, timeout=3)
            assert r.status_code == 200
            assert r.json()["lines_read"] == 50

            # 2) sample 10
            r = requests.post(f"{base_url}/sample", json={"n": 10}, timeout=3)
            assert r.status_code == 200
            body1 = r.json()
            assert body1["count"] == 10
            assert len(body1["lines"]) == 10
            assert len(set(body1["lines"])) == 10  # no dupes inside response

            # 3) sample remaining (overshoot on purpose)
            r = requests.post(f"{base_url}/sample", json={"n": 1000}, timeout=3)
            assert r.status_code == 200
            body2 = r.json()
            assert body2["count"] == 40
            assert len(body2["lines"]) == 40

            # 4) invalidation check: nothing left now
            r = requests.post(f"{base_url}/sample", json={"n": 1}, timeout=3)
            assert r.status_code == 200
            body3 = r.json()
            assert body3["count"] == 0
            assert body3["lines"] == []

            # 5) verify no overlap between first two samples
            assert set(body1["lines"]).isdisjoint(set(body2["lines"]))

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

            # If it failed, these logs help instantly diagnose import/port issues
            if proc.returncode not in (0, None):
                out = proc.stdout.read() if proc.stdout else ""
                err = proc.stderr.read() if proc.stderr else ""
                # Don’t fail test for logging; but useful when debugging locally
                _ = (out, err)

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


