import pytest

from overture.http import (
    _http_get_range,
    _is_valid_cached_part,
    _sha256_file,
    _write_part_manifest,
)


class _FakeResponse:
    def __init__(self, *, status, data, headers):
        self.status = status
        self._data = data
        self.headers = headers

    def read(self, _size=None):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_http_range_request_is_exact(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout, context):
        captured["range"] = request.headers["Range"]
        captured["timeout"] = timeout
        return _FakeResponse(
            status=206,
            data=b"efgh",
            headers={"Content-Range": "bytes 4-7/10"},
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    status, data, total = _http_get_range(
        "https://example.test/object",
        start=4,
        timeout=5,
        chunk=4,
        context=None,
    )
    assert captured["range"] == "bytes=4-7"
    assert status == 206
    assert data == b"efgh"
    assert total == 10


def test_invalid_content_range_is_rejected(monkeypatch):
    def fake_urlopen(request, timeout, context):
        return _FakeResponse(
            status=206,
            data=b"efgh",
            headers={"Content-Range": "bytes 5-8/10"},
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(OSError):
        _http_get_range(
            "https://example.test/object",
            start=4,
            timeout=5,
            chunk=4,
            context=None,
        )


def test_part_manifest_detects_tampering(tmp_path):
    pytest.importorskip("pyarrow")
    path = tmp_path / "part.parquet"
    import pyarrow.parquet as pq
    import pyarrow as pa

    pq.write_table(pa.table({"id": [1, 2]}), path)
    digest = _sha256_file(path)
    _write_part_manifest(path, key="bucket/object", size=path.stat().st_size, sha256=digest)

    assert _is_valid_cached_part(path, key="bucket/object")
    path.write_bytes(path.read_bytes()[:-1] + b"x")
    assert not _is_valid_cached_part(path, key="bucket/object")


def test_fetch_chunk_attempts_at_least_once_when_retries_zero(monkeypatch):
    calls = []

    def fake_range(url, start, timeout, chunk, context):
        calls.append((url, start))
        return 206, b"x", 1

    monkeypatch.setattr("overture.http._http_get_range", fake_range)
    from overture.http import _fetch_chunk

    result = _fetch_chunk(
        ["https://a.test/object", "https://b.test/object"],
        start=0,
        timeout=5,
        chunk=1,
        retries=0,
    )
    assert result == (206, b"x", 1)
    assert calls == [("https://a.test/object", 0)]
