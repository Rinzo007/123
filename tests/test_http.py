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


def test_overture_http_includes_official_azure_mirrors():
    from overture.http import _OVERTURE_HTTP_HOSTS, _overture_host_url

    assert "https://overturemapswestus2.blob.core.windows.net" in _OVERTURE_HTTP_HOSTS
    assert "https://overturemapswestus2.dfs.core.windows.net" in _OVERTURE_HTTP_HOSTS

    key = "overturemaps-us-west-2/release/2026-01-21.0/theme=buildings/type=building/part.parquet"
    bucket, _, obj_path = key.partition("/")

    blob = _overture_host_url(
        "https://overturemapswestus2.blob.core.windows.net",
        bucket,
        key,
        obj_path,
    )
    dfs = _overture_host_url(
        "https://overturemapswestus2.dfs.core.windows.net",
        bucket,
        key,
        obj_path,
    )
    assert blob.endswith("/release/2026-01-21.0/theme=buildings/type=building/part.parquet")
    assert dfs.endswith("/release/2026-01-21.0/theme=buildings/type=building/part.parquet")


def test_stac_request_uses_fresh_tls_and_closes_connection(monkeypatch):
    import overture.http as http

    contexts = []
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"stac"

    def fake_context():
        ctx = object()
        contexts.append(ctx)
        return ctx

    def fake_urlopen(request, timeout, context):
        captured.append((request.headers.get("Connection"), timeout, context))
        return Response()

    monkeypatch.setattr(http.ssl, "create_default_context", fake_context)
    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)

    assert http._http_get_url("https://stac.overturemaps.org/x.parquet", 120.0, contexts[0] if contexts else None) == b"stac"
    assert captured == [("close", 120.0, None)]


def test_stac_retry_refreshes_tls_context(monkeypatch):
    import overture.http as http

    contexts = []
    calls = []

    def fake_context():
        ctx = object()
        contexts.append(ctx)
        return ctx

    def fake_get(url, timeout, context):
        calls.append((url, timeout, context))
        if len(calls) == 1:
            raise TimeoutError("read operation timed out")
        return b"stac"

    monkeypatch.setattr(http.ssl, "create_default_context", fake_context)
    monkeypatch.setattr(http, "_http_get_url", fake_get)

    assert http._http_get_stac(
        "https://stac.overturemaps.org/x.parquet",
        timeout=7,
        retries=1,
        retry_delay=0,
    ) == b"stac"
    assert len(contexts) == 2
    assert calls[0][2] is contexts[0]
    assert calls[1][2] is contexts[1]
