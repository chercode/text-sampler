import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
from typing import List, Set

import pytest
from fastapi.testclient import TestClient

from src.server import app


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
