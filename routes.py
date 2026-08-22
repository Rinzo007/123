import contextlib
import logging
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Protocol

import requests

from .cache import JsonCache
from .constants import API_HEADERS_EXTRA, BASE_URL, REQUEST_TIMEOUT
from .decoding import decode_obj
from .enums import RouteType, type_label
from .http_client import SessionProvider
from .models import RouteData, RouteTask
from .parsers import parse_route_payload
from .route_meta import extract_metadata

logger = logging.getLogger("wikiroutes.routes")


def extract_token(html: str) -> str | None:
    """Извлекает токен WikiRoutes из HTML страницы каталога маршрута."""
    match = re.search(r"tk:\s*['\"]([a-f0-9-]+)['\"]", html)
    return match.group(1) if match else None


def _cached_token(cache: JsonCache, cache_key: str) -> str | None:
    """Достаёт сохранённый токен маршрута (одно сетевое обращение меньше)."""
    entry = cache.get("route_token", cache_key)

    if isinstance(entry, dict):
        token = entry.get("token")

        if isinstance(token, str) and token:
            return token

    return None


def _route_base(task: RouteTask) -> dict[str, Any]:
    """Базовые поля RouteData из задачи."""
    return {
        "name": task.name,
        "route_type": task.route_type,
        "route_id": task.route_id,
        "url": f"{BASE_URL}/{task.city}?routes={task.route_id}",
    }


def _route_from_cached_payload(
    cache: JsonCache,
    task: RouteTask,
) -> RouteData | None:
    """Собирает RouteData из кэша; ``None``, если записи нет или она битая."""
    cached = cache.get("route", f"{task.city}:{task.route_id}")

    if cached is None:
        return None

    parsed = parse_route_payload(cached)

    if parsed is None:
        return None

    base = _route_base(task)

    if extract_metadata(cached).is_electrobus:
        base["route_type"] = RouteType.ELECTROBUS

    return RouteData(
        **base,
        directions=parsed.directions,
        cached=True,
        price=parsed.price,
        company=parsed.company,
        active=parsed.active,
        schedule=parsed.schedule,
        transport_class=parsed.transport_class,
    )


def fetch_route_network(
    city: str,
    route_type: str,
    name: str,
    route_id: int,
    sessions: SessionProvider,
    cache: JsonCache,
) -> RouteData:
    """Загружает маршрут из сети с кэшированием токена и payload.

    Ошибки HTTP, декодирования и разбора возвращаются в ``RouteData.error``.
    """
    task = RouteTask(city=city, route_type=RouteType(route_type), name=name, route_id=route_id)
    base = _route_base(task)
    url = base["url"]
    cache_key = f"{city}:{route_id}"

    session = sessions.get()

    page_text: str | None = None

    def fetch_with_token(token: str | None) -> Any:
        """Выполняет API-запрос; бросает ``HTTPError`` при неуспехе."""
        headers = API_HEADERS_EXTRA | {
            "X-WR-T": token,
            "Referer": url,
        }

        api_response = session.get(
            f"{BASE_URL}/api/wr/route/{route_id}",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        if api_response.status_code != 200:
            body = (api_response.text or "")[:150].replace("\n", " ")
            raise requests.HTTPError(f"API {api_response.status_code}: {body}")

        return decode_obj(api_response.json())

    def fetch_page_text() -> str:
        """Загружает страницу маршрута один раз на всех ветках."""
        page = session.get(url, timeout=REQUEST_TIMEOUT)
        page.raise_for_status()
        return page.text

    try:
        # Токен кэшируется: повторные запуски не делают запрос страницы.
        token = _cached_token(cache, cache_key)

        if not token:
            page_text = fetch_page_text()

            token = extract_token(page_text)

            if not token:
                return RouteData(**base, error="token not found")

            cache.put("route_token", cache_key, {"token": token})

        payload = fetch_with_token(token)

        if payload is None:
            # Пустой ответ (например, устаревший токен) — пробуем ещё раз
            # со свежей страницей и свежим токеном.
            page_text = fetch_page_text()

            token = extract_token(page_text)

            if not token:
                return RouteData(**base, error="token not found")

            cache.put("route_token", cache_key, {"token": token})
            payload = fetch_with_token(token)

        if not isinstance(payload, dict):
            return RouteData(**base, error="no geometry")

        meta = extract_metadata(payload, page_text)

        if not meta.transport_class and page_text is None:
            with contextlib.suppress(requests.RequestException, ValueError):
                meta = extract_metadata(payload, fetch_page_text())

        payload["transport_class"] = meta.transport_class
        payload["is_electrobus"] = meta.is_electrobus

        parsed = parse_route_payload(payload)

        if parsed is None:
            return RouteData(**base, error="no geometry")

        cache.put("route", cache_key, payload)

        if meta.is_electrobus:
            base["route_type"] = RouteType.ELECTROBUS

        return RouteData(
            **base,
            directions=parsed.directions,
            price=parsed.price,
            company=parsed.company,
            active=parsed.active,
            schedule=parsed.schedule,
            transport_class=parsed.transport_class,
        )

    except (requests.RequestException, ValueError) as exc:
        logger.warning("Route %s failed: %s", route_id, exc)
        return RouteData(**base, error=str(exc))


class RouteFetcher(Protocol):
    """Интерфейс загрузчика маршрута: кэш и/или сеть.

    ``is_cached`` позволяет вызывающему коду выбирать стратегию
    параллельности без сетевых запросов.
    """

    def is_cached(self, task: RouteTask) -> bool:
        """True, если маршрут доступен целиком из кэша."""
        ...

    def fetch(self, task: RouteTask) -> RouteData:
        """Возвращает ``RouteData`` по задаче, включая записи с ошибками."""
        ...


class CachedNetworkRouteFetcher:
    """Реализация ``RouteFetcher``: кэш → сеть."""

    def __init__(self, cache: JsonCache, sessions: SessionProvider) -> None:
        self._cache = cache
        self._sessions = sessions

    def is_cached(self, task: RouteTask) -> bool:
        """True, если маршрут доступен целиком из кэша."""
        return (
            self._cache.get("route", f"{task.city}:{task.route_id}") is not None
        )

    def fetch(self, task: RouteTask) -> RouteData:
        """Возвращает маршрут из кэша или загружает из сети."""
        cached = _route_from_cached_payload(self._cache, task)

        if cached is not None:
            return cached

        return fetch_route_network(
            task.city,
            task.route_type.value,
            task.name,
            task.route_id,
            self._sessions,
            self._cache,
        )


def fetch_route(
    city: str,
    route_type: str,
    name: str,
    route_id: int,
    sessions: SessionProvider,
    cache: JsonCache,
) -> RouteData:
    """Загружает и разбирает один маршрут WikiRoutes с использованием кэша.

    При наличии валидной записи кэша сетевой запрос к API маршрута не выполняется.
    Ошибки HTTP, декодирования и разбора возвращаются в ``RouteData.error``.
    """
    return CachedNetworkRouteFetcher(cache, sessions).fetch(
        RouteTask(city=city, route_type=RouteType(route_type), name=name, route_id=route_id)
    )


def download_routes(
    tasks: Sequence[RouteTask],
    workers: int,
    fetcher: RouteFetcher,
) -> list[RouteData]:
    """Параллельно загружает маршруты из набора задач через ``fetcher``.

    Возвращает один ``RouteData`` на каждый уникальный маршрут, включая записи
    с ошибками загрузки. Повторяющиеся задачи с тем же городом, типом и ID
    игнорируются. ``workers`` ограничивает число рабочих потоков.
    """
    routes: list[RouteData] = []

    if not tasks:
        return routes

    # Один маршрут может встретиться в каталоге несколько раз. Убираем
    # дубликаты до создания futures, чтобы не выполнять повторную загрузку
    # и не добавлять одинаковый RouteData в результат.
    unique_tasks: list[RouteTask] = []
    seen: set[tuple[str, object, int]] = set()

    for task in tasks:
        key = (task.city, task.route_type, int(task.route_id))
        if key in seen:
            continue
        seen.add(key)
        unique_tasks.append(task)

    # Если весь кэш уже прогрет, сетевой конкуренции нет — пул потоков
    # только добавляет оверхед на создание потоков.
    if all(fetcher.is_cached(task) for task in unique_tasks):
        for task in unique_tasks:
            route = fetcher.fetch(task)

            routes.append(route)

            status = route.error if route.error else f"{route.min_km:.1f} km"

            logger.info(
                "[%d/%d] %s %s: %s",
                len(routes),
                len(unique_tasks),
                type_label(task.route_type),
                task.name,
                status,
            )

        return routes

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(fetcher.fetch, task): task for task in unique_tasks
        }

        total = len(futures)

        for future in as_completed(futures):
            task = futures[future]

            try:
                route = future.result()
            except Exception as exc:
                logger.exception("Unexpected error while fetching %s", task.name)

                route = RouteData(
                    name=task.name,
                    route_type=task.route_type,
                    route_id=task.route_id,
                    url=f"{BASE_URL}/{task.city}?routes={task.route_id}",
                    error=str(exc),
                )

            routes.append(route)

            status = route.error if route.error else f"{route.min_km:.1f} km"

            logger.info(
                "[%d/%d] %s %s: %s",
                len(routes),
                total,
                type_label(task.route_type),
                task.name,
                status,
            )

    return routes
