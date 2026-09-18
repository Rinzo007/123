"""Сетевой слой Overture: HTTP/S3, STAC-резолвинг, докачка и чтение частей.

Скачивание данных Overture обходит нестабильный pyarrow-S3 транспортом
напрямую по HTTP (Range-чанки, переживающие TLS-обрывы), затем парт-файлы
читаются с локального диска через geopandas/pyarrow.
"""

import contextlib
import datetime as datetime_module
import email.utils
import hashlib
import io
import json
import logging
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("wikiroutes.gis.overture")


# Официальные endpoint'ы Overture. Azure добавлен как резервное зеркало:
# документация Overture публикует основной каталог и на S3, и на Azure.
# Второй Azure endpoint через dfs полезен там, где blob endpoint режется
# сетевым прокси/фильтрацией.
_OVERTURE_HTTP_HOSTS: tuple[str, ...] = (
    "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com",
    "https://overturemaps-us-west-2.s3.amazonaws.com",
    "https://s3.us-west-2.amazonaws.com/overturemaps-us-west-2",
    "https://overturemapswestus2.blob.core.windows.net",
    "https://overturemapswestus2.dfs.core.windows.net",
)


# Порция данных на один HTTP-запрос для докачки. Обрывы TLS (EOF) на больших
# файлах S3 случаются тем чаще, чем длиннее единичный GET; лимит чанка делает
# запрос коротким, а resume — по уже записанным байтам.
_OVERTURE_CHUNK_BYTES = 4 * 1024 * 1024

# Пауза между повторами одного чанка после TLS-обрыва (INVALID_SESSION_ID):
# даёт S3 время сбросить повреждённую сессию до полного handshake.
_CHUNK_RETRY_DELAY_S = 2.0

# Таймаут одного STAC-запроса. Каталог небольшой, но на Windows/прокси
# чтение может подвисать заметно дольше обычного HTTP GET.
_STAC_TIMEOUT_S = 120.0
_STAC_CHUNK_BYTES = 1024 * 1024


def _http_get_url(
    url: str, timeout: float, context: ssl.SSLContext | None = None
) -> bytes:
    req = urllib.request.Request(url, method="GET")
    # Не удерживаем потенциально зависшее keep-alive-соединение между
    # попытками: STAC-попытка должна начинаться с чистого HTTP/TLS-сеанса.
    req.add_header("Connection", "close")
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        return resp.read()


def _http_get_range(
    url: str, start: int, timeout: float, chunk: int, context: ssl.SSLContext | None
) -> tuple[int, bytes, int | None]:
    """GET ``bytes=start-`` на URL; возвращает (статус, байты).

    При поддержке Range (206) отдаёт до ``chunk`` байт от смещения ``start``.
    Если сервер игнорирует Range (200), читает всё тело целиком — докачка для
    такого хоста невозможна, вызывающий обязан сбросить смещение на 0.
    ``context`` — свежий TLS-контекст: S3 после обрыва отвечает
    ``INVALID_SESSION_ID`` на предложение старой сессии, полный handshake
    на новом контексте исключает повтор этой ошибки.
    """
    req = urllib.request.Request(url, method="GET")
    # Точный диапазон позволяет отличать конец файла от укороченного ответа.
    end = start + max(1, chunk) - 1
    req.add_header("Range", f"bytes={start}-{end}")
    req.add_header("Connection", "close")
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        status = getattr(resp, "status", 200)
        data = resp.read(chunk if status == 206 else None)
        total_size: int | None = None
        if status == 206:
            content_range = resp.headers.get("Content-Range")
            if content_range:
                import re
                match = re.match(r"^bytes\s+(\d+)-(\d+)/(\d+)$", content_range.strip())
                if match:
                    range_start, range_end, total = map(int, match.groups())
                    if range_start != start or range_end < range_start:
                        raise IOError(
                            f"Некорректный Content-Range: {content_range!r}, ожидался offset {start}"
                        )
                    total_size = total
        else:
            content_length = resp.headers.get("Content-Length")
            if content_length:
                try:
                    total_size = int(content_length)
                except ValueError:
                    total_size = None
    return status, data, total_size


def _overture_host_url(host: str, bucket: str, bucket_key: str, obj_path: str) -> str:
    """Строит HTTP URL для S3/Azure endpoint'а из общего object key."""
    if host.endswith((".blob.core.windows.net", ".dfs.core.windows.net")):
        return f"{host}/{obj_path}"
    if "s3.us-west-2.amazonaws.com/" in host and not host.endswith(bucket):
        return f"{host}/{obj_path}"
    return f"{host}/{obj_path}" if bucket in host else f"{host}/{bucket_key}"


def _fetch_chunk(
    urls: str | list[str],
    start: int,
    timeout: float,
    chunk: int,
    retries: int,
    retry_delay: float = 2.0,
) -> tuple[int, bytes, int | None]:
    """Один Range-GET на URL с повторами; при исчерпании — последняя ошибка.

    ``urls`` может быть одним URL или списком хост-вариантов одного объекта:
    попытки идут по кругу через все хосты, поэтому транзитный отказ одного S3
    хоста (DPI-сброс с ``INVALID_SESSION_ID``) не сжигает весь бюджет — чанк
    уходит через следующий живой хост. Каждая попытка — свежий TLS-контекст
    плюс пауза между повторами.
    """
    url_list = [urls] if isinstance(urls, str) else list(urls)
    attempts = max(1, retries + 1) * len(url_list)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(retry_delay)
        url = url_list[attempt % len(url_list)]
        try:
            context = ssl.create_default_context()
            return _http_get_range(url, start, timeout, chunk, context)
        except Exception as exc:  # noqa: BLE001 — внешняя сетевая граница
            last_exc = exc
            logger.warning(
                "Overture: Range-чанк %s (offset %d) failed: %s",
                url,
                start,
                exc,
            )
    raise last_exc if last_exc is not None else RuntimeError(
        f"Overture: Range-загрузка {url_list[0]} не удалась"
    )


def _append_chunk(part: Path, start: int, data: bytes, chunk: int) -> int:
    """Записывает чанк в part (wb/ab по смещению) и возвращает новое смещение."""
    mode = "ab" if start > 0 else "wb"
    with part.open(mode) as fh:
        fh.seek(start) if mode == "wb" else None
        fh.write(data)
    return start + len(data)


class _PartProgress:
    """Замер скорости докачки парт-файла по HTTP (байты и время).

    Логирует ход каждые ``log_interval_s`` секунд; ``summary()`` отдаёт
    итоговую строку со средней скоростью за всю загрузку части (вместе с
    паузами между повторами — видна реальная эффективность).
    """

    _MB = 1024 * 1024

    def __init__(self, name: str, log_interval_s: float = 10.0) -> None:
        self._name = name
        self._log_interval_s = log_interval_s or float("inf")
        self._t0 = time.monotonic()
        self._last_log = self._t0
        self._bytes = 0

    def update(self, data: bytes) -> None:
        """Принимает очередной скачанный кусок, изредка логирует прогресс."""
        self._bytes += len(data)
        now = time.monotonic()
        if now - self._last_log >= self._log_interval_s:
            logger.info(
                "Overture: %s — скачано %.1f МБ, скорость %.2f МБ/с",
                self._name,
                self._bytes / self._MB,
                self._megabytes_per_sec(now),
            )
            self._last_log = now

    def _megabytes_per_sec(self, now: float) -> float:
        elapsed = now - self._t0
        if elapsed <= 0:
            return 0.0
        return (self._bytes / self._MB) / elapsed

    def summary(self) -> str:
        """Итоговая строка: имя, объём, время, средняя скорость."""
        now = time.monotonic()
        return (
            f"{self._name}: скачано {self._bytes / self._MB:.1f} МБ "
            f"за {now - self._t0:.1f} с "
            f"({self._megabytes_per_sec(now):.2f} МБ/с)"
        )


def _sha256_file(path: Path) -> str:
    """Считает SHA-256 файла потоково."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _part_manifest_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def _write_part_manifest(path: Path, *, key: str, size: int, sha256: str) -> None:
    """Атомарно сохраняет метаданные завершённой части."""
    target = _part_manifest_path(path)
    tmp = target.with_name(target.name + ".tmp")
    payload = {
        "version": 1,
        "key": key,
        "size": int(size),
        "sha256": sha256,
    }
    try:
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp.replace(target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def _read_part_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(_part_manifest_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_valid_cached_part(
    path: Path,
    *,
    key: str | None = None,
) -> bool:
    """Проверяет parquet-footer, размер и checksum сохранённого part-файла."""
    try:
        from pyarrow.parquet import ParquetFile

        if path.stat().st_size <= 0:
            return False
        manifest = _read_part_manifest(path)
        if manifest is None or manifest.get("version") != 1:
            return False
        if key is not None and manifest.get("key") != key:
            return False
        expected_size = int(manifest.get("size", -1))
        if expected_size != path.stat().st_size:
            return False
        expected_hash = str(manifest.get("sha256", "")).strip().lower()
        if not expected_hash:
            return False
        if _sha256_file(path) != expected_hash:
            return False
        ParquetFile(path)
        return True
    except Exception:
        return False


def _download_part_from_host(
    urls: str | list[str],
    part: Path,
    timeout: float,
    *,
    chunk: int = _OVERTURE_CHUNK_BYTES,
    per_chunk_retries: int = 3,
    retry_delay: float = _CHUNK_RETRY_DELAY_S,
) -> int:
    """Докачивает объект в part чанками и возвращает полный размер."""
    list_urls = [urls] if isinstance(urls, str) else list(urls)
    if not list_urls:
        raise ValueError("Overture: не переданы HTTP-хосты")
    progress = _PartProgress(part.name)
    start = part.stat().st_size if part.exists() else 0
    total_size: int | None = None
    while True:
        status, data, chunk_total = _fetch_chunk(
            list_urls, start, timeout, chunk, per_chunk_retries, retry_delay
        )
        if chunk_total is not None:
            total_size = chunk_total
            if total_size < start:
                raise IOError(
                    f"Некорректный размер Overture part: {total_size} < {start}"
                )

        if status != 206:
            if total_size is not None and len(data) != total_size:
                raise IOError(
                    f"Неполный HTTP 200 для {part.name}: {len(data)} из {total_size} байт"
                )
            with part.open("wb") as fh:
                fh.write(data)
            start = len(data)
        else:
            if total_size is not None and start + len(data) > total_size:
                raise IOError(
                    f"Range-ответ выходит за конец Overture part {part.name}"
                )
            start = _append_chunk(part, start, data, chunk)

        progress.update(data)

        complete = (
            status != 206
            or (total_size is not None and start == total_size)
            or (total_size is None and len(data) < chunk)
        )
        if complete:
            if total_size is not None and start != total_size:
                raise IOError(
                    f"Неполная Overture part: {start} из {total_size} байт"
                )
            logger.info("Overture: %s", progress.summary())
            return start


def _download_part_once(key: str, cache_dir: str | Path) -> None:
    """Скачивает одну часть атомарно и валидирует локальный cache-entry."""
    dest = _part_local_path(key, cache_dir)
    if dest.exists() and _is_valid_cached_part(dest, key=key):
        return
    if dest.exists():
        logger.warning("Overture: невалидный part-кэш, перекачиваем: %s", dest)
        with contextlib.suppress(OSError):
            dest.unlink()
        with contextlib.suppress(OSError):
            _part_manifest_path(dest).unlink()

    bucket, _, obj_path = key.partition("/")
    part = dest.with_name(dest.name + ".part")

    # Если процесс завершился после полной записи .part, но до rename,
    # распознаём готовый parquet и завершаем публикацию без Range-запроса.
    if part.exists():
        try:
            from pyarrow.parquet import ParquetFile

            if part.stat().st_size > 0:
                ParquetFile(part)
                size = part.stat().st_size
                sha256 = _sha256_file(part)
                part.replace(dest)
                _write_part_manifest(dest, key=key, size=size, sha256=sha256)
                return
        except (OSError, ValueError, TypeError, RuntimeError):
            pass

    urls = [
        _overture_host_url(host, bucket, key, obj_path)
        for host in _OVERTURE_HTTP_HOSTS
    ]
    _download_part_from_host(urls, part, timeout=120.0)
    if not _is_valid_cached_part(part):
        # part ещё не имеет manifest, поэтому валидируем его хотя бы как parquet
        # перед публикацией; checksum записывается ниже.
        try:
            from pyarrow.parquet import ParquetFile

            if part.stat().st_size <= 0:
                raise IOError("Пустой Overture part")
            ParquetFile(part)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            with contextlib.suppress(OSError):
                part.unlink()
            raise IOError(f"Невалидный скачанный Overture part {key!r}") from exc

    size = part.stat().st_size
    if size <= 0:
        raise IOError(f"Пустой Overture part после скачивания: {key!r}")
    sha256 = _sha256_file(part)
    part.replace(dest)
    _write_part_manifest(dest, key=key, size=size, sha256=sha256)

def _retry_after_value(retry_after: str) -> float | None:
    """Парсит ``Retry-After`` как число секунд; None при нечисловом значении."""
    try:
        return float(retry_after)
    except (TypeError, ValueError):
        return None


def _retry_after_date(retry_after: str) -> float | None:
    """Парсит ``Retry-After`` как HTTP-дату (RFC 7231); None при не-дата."""
    try:
        when = email.utils.parsedate_to_datetime(retry_after)
        return (when - datetime_module.datetime.now(datetime_module.UTC)).total_seconds()
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(retry_after: str | None, fallback: float) -> float:
    """Возвращает паузу в секундах из заголовка ``Retry-After`` (или fallback).

    Overture STAC отвечает на 429 как числом секунд, так и HTTP-датой
    (RFC 7231). Минимум 30 секунд: лимит обычно исчезает дольше, чем
    стандартный экспоненциальный backoff.
    """
    if not retry_after:
        return max(fallback, 30.0)
    seconds = _retry_after_value(retry_after)
    if seconds is not None:
        return max(seconds, 30.0)
    seconds = _retry_after_date(retry_after)
    if seconds is not None:
        return max(seconds, 30.0)
    return max(fallback, 30.0)


def _stac_retry_delay(exc: Exception, attempt: int, retry_delay: float) -> float:
    """Пауза перед повтором STAC-запроса.

    Для HTTP 429 — ``Retry-After`` из ответа (минимум 30 с по RFC 7231),
    иначе экспоненциальный backoff от ``retry_delay``.
    """
    import urllib.error

    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        return _retry_after_seconds(retry_after, retry_delay * (2**attempt))
    return retry_delay * (2**attempt)


def _log_stac_retry(
    url: str, attempt: int, retries: int, delay: float, exc: Exception, rate_limited: bool
) -> None:
    label = " (429)" if rate_limited else ""
    logger.warning(
        "Overture: STAC %s попытка %d/%d — повтор через %.1fs%s: %s",
        url,
        attempt + 1,
        retries + 1,
        delay,
        label,
        exc,
    )


def _http_get_stac(
    url: str, timeout: float = _STAC_TIMEOUT_S, retries: int = 0, retry_delay: float = 2.0
) -> bytes:
    """Скачивает STAC ``collections.parquet`` короткими HTTP Range-запросами.

    Полный GET оказался чувствителен к зависанию чтения тела на Windows и
    сетевых прокси: timeout возникал внутри ``resp.read()`` даже после успешного
    TLS-handshake. Здесь каждый диапазон ограничен ``_STAC_CHUNK_BYTES`` и
    получает новый TLS-сеанс; уже полученные диапазоны не теряются.
    """
    chunks: list[bytes] = []
    start = 0
    total_size: int | None = None

    while True:
        status, data, reported_total = _fetch_chunk(
            url,
            start=start,
            timeout=timeout,
            chunk=_STAC_CHUNK_BYTES,
            retries=retries,
            retry_delay=retry_delay,
        )
        if status != 206:
            if start == 0:
                return data
            raise IOError(
                "Overture STAC перестал поддерживать Range после частичного чтения "
                f"({status} для offset {start})"
            )
        if not data:
            raise IOError(f"Пустой Range-ответ STAC для offset {start}")

        chunks.append(data)
        total_size = reported_total or total_size
        start += len(data)

        if total_size is not None:
            if start >= total_size:
                if start != total_size:
                    raise IOError(
                        f"STAC Range превысил размер файла: {start} из {total_size} байт"
                    )
                return b"".join(chunks)
        elif len(data) < _STAC_CHUNK_BYTES:
            return b"".join(chunks)

def _stac_item_hrefs(collection: dict[str, Any], bbox: tuple[float, float, float, float]) -> list[str]:
    """Возвращает href'ы STAC Item, чьи bbox пересекают заданный bbox."""
    import urllib.parse

    min_lat, min_lon, max_lat, max_lon = bbox
    item_links = [
        link.get("href")
        for link in collection.get("links", [])
        if link.get("rel") == "item" and link.get("href")
    ]
    extent_boxes = (
        collection.get("extent", {})
        .get("spatial", {})
        .get("bbox", [])
    )

    # Overture's published collections currently carry one union bbox followed
    # by one bbox per Item, in the same order as the item links. Use that
    # spatial index to avoid fetching every Item JSON.
    candidate_links: list[str] = []
    if len(extent_boxes) == len(item_links) + 1:
        extent_boxes = extent_boxes[1:]
        for href, item_bbox in zip(item_links, extent_boxes):
            if len(item_bbox) != 4:
                continue
            xmin, ymin, xmax, ymax = map(float, item_bbox)
            if xmin < max_lon and xmax > min_lon and ymin < max_lat and ymax > min_lat:
                candidate_links.append(urllib.parse.urljoin(
                    "https://stac.overturemaps.org/", href
                ))
        return candidate_links

    # Be conservative if a future STAC writer changes the extent layout.
    return [
        urllib.parse.urljoin("https://stac.overturemaps.org/", href)
        for href in item_links
    ]


def _s3_key_from_stac_item(item: dict[str, Any]) -> str | None:
    """Извлекает канонический S3 object key из STAC Item assets."""
    assets = item.get("assets", {})
    aws = assets.get("aws", {})
    alternate = aws.get("alternate", {})
    s3 = alternate.get("s3", {})
    href = s3.get("href")
    if not href:
        href = aws.get("href")
    if not isinstance(href, str):
        return None
    if href.startswith("s3://"):
        return href[5:]
    return None


def _http_resolve_stac_part_files_via_collection(
    release: str,
    theme: str,
    overture_type: str,
    bbox: tuple[float, float, float, float],
    retries: int,
    retry_delay: float,
) -> list[str]:
    """Резервный STAC-резолвер через collection.json и Item JSON."""
    import concurrent.futures
    import json as json_module

    import urllib.parse

    url = f"https://stac.overturemaps.org/{release}/{theme}/{overture_type}/collection.json"
    collection_data = _http_get_stac(
        url,
        timeout=_STAC_TIMEOUT_S,
        retries=retries,
        retry_delay=retry_delay,
    )
    collection = json_module.loads(collection_data)
    item_hrefs = _stac_item_hrefs(collection, bbox)
    if not item_hrefs:
        return []

    def fetch_item(item_url: str) -> str | None:
        item_data = _http_get_stac(
            urllib.parse.urljoin(item_url, ""),
            timeout=_STAC_TIMEOUT_S,
            retries=retries,
            retry_delay=retry_delay,
        )
        return _s3_key_from_stac_item(json_module.loads(item_data))

    max_workers = min(8, len(item_hrefs))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        return [key for key in executor.map(fetch_item, item_hrefs) if key]



def _http_resolve_stac_part_files(
    release: str,
    theme: str,
    overture_type: str,
    bbox: tuple[float, float, float, float],
    retries: int = 0,
    retry_delay: float = 2.0,
) -> list[str]:
    """Возвращает список ключей S3 частей, пересекающих bbox (через STAC по HTTP)."""
    import pyarrow.compute as pc
    from pyarrow import parquet as pq

    stac_url = f"https://stac.overturemaps.org/{release}/collections.parquet"
    try:
        data = _http_get_stac(
            stac_url,
            timeout=_STAC_TIMEOUT_S,
            retries=retries,
            retry_delay=retry_delay,
        )
        import pyarrow.compute as pc
        from pyarrow import parquet as pq

        table = pq.read_table(io.BytesIO(data))

        feature_type_filter = (pc.field("collection") == overture_type) & (
            pc.field("type") == "Feature"
        )
        min_lat, min_lon, max_lat, max_lon = bbox
        bbox_filter = (
            (pc.field("bbox", "xmin") < max_lon)
            & (pc.field("bbox", "xmax") > min_lon)
            & (pc.field("bbox", "ymin") < max_lat)
            & (pc.field("bbox", "ymax") > min_lat)
        )
        table = table.filter(feature_type_filter & bbox_filter)
        keys: list[str] = []
        for path in table.column("assets").to_pylist():
            href = path["aws"]["alternate"]["s3"]["href"]
            if href.startswith("s3://"):
                keys.append(href[len("s3://") :])
        return keys
    except Exception as exc:  # noqa: BLE001 — сетевой/форматный fallback
        logger.warning(
            "Overture: STAC collections.parquet недоступен (%s); "
            "переключаемся на collection.json",
            exc,
        )
        return _http_resolve_stac_part_files_via_collection(
            release,
            f"{'addresses' if theme == 'addresses' else 'places' if theme == 'places' else theme}",
            overture_type,
            bbox,
            retries,
            retry_delay,
        )

    feature_type_filter = (pc.field("collection") == overture_type) & (
        pc.field("type") == "Feature"
    )
    min_lat, min_lon, max_lat, max_lon = bbox
    bbox_filter = (
        (pc.field("bbox", "xmin") < max_lon)
        & (pc.field("bbox", "xmax") > min_lon)
        & (pc.field("bbox", "ymin") < max_lat)
        & (pc.field("bbox", "ymax") > min_lat)
    )
    table = table.filter(feature_type_filter & bbox_filter)
    keys: list[str] = []
    for path in table.column("assets").to_pylist():
        href = path["aws"]["alternate"]["s3"]["href"]
        if href.startswith("s3://"):
            keys.append(href[len("s3://") :])
    return keys


def _part_local_path(key: str, cache_dir: str | Path) -> Path:
    safe = key.replace("/", "__").replace("=", "_")
    return Path(cache_dir) / "parts" / safe


def _download_part_once(key: str, cache_dir: str | Path) -> None:
    """Скачивает одну часть атомарно (tmp + replace), пропуская готовые.

    Крупные парт-файлы тянутся с докачкой по HTTP Range (переживает TLS-обрывы):
    частичный файл ``<name>.part`` продолжается на повторных запусках, а после
    полного скачивания атомарно переименовывается в целевой.
    """
    dest = _part_local_path(key, cache_dir)
    if dest.exists() and _is_valid_cached_part(dest):
        return
    if dest.exists():
        logger.warning("Overture: повреждённый part-кэш, перекачиваем: %s", dest)
        with contextlib.suppress(OSError):
            dest.unlink()

    bucket, _, obj_path = key.partition("/")
    part = dest.with_name(dest.name + ".part")
    urls = [_overture_host_url(host, bucket, key, obj_path) for host in _OVERTURE_HTTP_HOSTS]
    _download_part_from_host(urls, part, timeout=120.0)
    part.replace(dest)


def _download_part_round(keys: list[str], cache_dir: str | Path) -> None:
    """Одна попытка скачать все части; первая ошибка прерывает раунд."""
    for key in keys:
        _download_part_once(key, cache_dir)


def _download_overture_parts(
    keys: list[str], cache_dir: str | Path, retries: int, retry_delay: float
) -> None:
    """Качает отсутствующие части по HTTP, повторяя неудавшиеся попытки."""
    parts_dir = Path(cache_dir) / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _download_part_round(keys, cache_dir)
            return
        except Exception as exc:  # noqa: BLE001 — внешняя сетевая граница
            last_exc = exc
        if attempt < retries:
            delay = retry_delay * (2**attempt)
            logger.warning(
                "Overture: скачивание частей попытка %d/%d — повтор через %.1fs: %s",
                attempt + 1,
                retries + 1,
                delay,
                last_exc,
            )
            time.sleep(delay)
    raise last_exc if last_exc is not None else RuntimeError(
        "Overture: скачивание частей не удалось"
    )


def _read_part_frames(
    local_files: list[str], bbox_filter: tuple, gpd: Any
) -> list[Any]:
    """Читает локальные parquet-части с bbox-фильтром (cx-fallback на битые файлы)."""
    frames = []
    for path in local_files:
        try:
            frame = gpd.read_parquet(path, bbox=bbox_filter)
        except (ValueError, TypeError):
            frame = gpd.read_parquet(path)
            frame = frame.cx[
                bbox_filter[0] : bbox_filter[2],
                bbox_filter[1] : bbox_filter[3],
            ]
        frames.append(frame)
    return frames


def _concat_part_frames(frames: list[Any], gpd: Any, *, to_epsg4326: bool = True) -> Any:
    """Склеивает кадры частей, приводя CRS к EPSG:4326 при необходимости."""
    if not frames:
        return gpd.pd.DataFrame()
    gdf = gpd.pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if gdf.crs is None:
        return gdf.set_crs("EPSG:4326")
    if to_epsg4326 and str(gdf.crs).upper() != "EPSG:4326":
        return gdf.to_crs("EPSG:4326")
    return gdf


def _read_overture_parts(local_files: list[str], bbox_filter: tuple, gpd: Any) -> Any | None:
    frames = _read_part_frames(local_files, bbox_filter, gpd)
    if not frames:
        return None
    gdf = _concat_part_frames(frames, gpd)
    return gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]


__all__ = [
    "_CHUNK_RETRY_DELAY_S",
    "_STAC_TIMEOUT_S",
    "_STAC_CHUNK_BYTES",
    "_OVERTURE_CHUNK_BYTES",
    "_OVERTURE_HTTP_HOSTS",
    "_PartProgress",
    "_append_chunk",
    "_concat_part_frames",
    "_download_overture_parts",
    "_download_part_from_host",
    "_download_part_once",
    "_is_valid_cached_part",
    "_download_part_round",
    "_fetch_chunk",
    "_http_get_range",
    "_http_get_stac",
    "_http_get_url",
    "_http_resolve_stac_part_files",
    "_log_stac_retry",
    "_overture_host_url",
    "_part_local_path",
    "_read_overture_parts",
    "_read_part_frames",
    "_retry_after_date",
    "_retry_after_seconds",
    "_retry_after_value",
    "_stac_retry_delay",
]