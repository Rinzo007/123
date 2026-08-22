import concurrent.futures
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .cache import JsonCache
from .constants import BASE_URL
from .enums import RouteType
from .http_client import SessionProvider
from .models import Direction, RouteData, Stop

logger = logging.getLogger("wikiroutes.ideas")

IDEA_GEO_CACHE_VER = "v2"


def parse_idea_rating(text: str | None) -> tuple[float | None, int | None]:
    text = (text or "").strip()

    if not text or text == "—":
        return None, None

    match = re.match(r"(-?\d+(?:[.,]\d+)?)\s*(?:\((\d+)\))?", text)

    if not match:
        return None, None

    rating_str = match.group(1).replace(",", ".")

    return float(rating_str), (int(match.group(2)) if match.group(2) else None)


def guess_route_type_from_idea(title: str, h1: str = "") -> RouteType:
    sources = [h1 or "", title or ""]

    for source in sources:
        text = source.lower()

        if not text:
            continue

        if any(
            kw in text
            for kw in (
                "троллейбус",
                "троллейбусы",
                "троллейбусов",
                "троллейбусн",
                "трол.",
            )
        ):
            return RouteType.TROLLEYBUS

        if any(
            kw in text
            for kw in (
                "автобус",
                "автобусы",
                "автобусов",
                "автобусн",
                "электробус",
                "электробусы",
                "электробусов",
                "авто.",
            )
        ):
            return RouteType.BUS

        if any(
            kw in text
            for kw in (
                "маршрутка",
                "маршрутки",
                "маршруток",
                "маршрутное такси",
                "марш.",
            )
        ):
            return RouteType.MINIBUS

        if any(
            kw in text
            for kw in (
                "трамвай",
                "трамваи",
                "трамваев",
                "трамвайн",
                "трам.",
            )
        ):
            return RouteType.TRAM

    return RouteType.BUS


def _extract_json_array(html: str, start: int, max_len: int = 400000) -> str | None:
    if start >= len(html) or html[start] != "[":
        return None

    depth = 0
    in_str = False
    esc = False

    last = min(len(html), start + max_len)

    for i in range(start, last):
        ch = html[i]

        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1

                if depth == 0:
                    return html[start : i + 1]

    return None


def _parse_lng_lat_pairs(arr_text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []

    for lng, lat in re.findall(
        r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]",
        arr_text,
    ):
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except ValueError:
            continue

        if -90 <= lat_f <= 90 and -180 <= lng_f <= 180:
            points.append((lat_f, lng_f))

    return points


def fetch_idea_geometry(
    idea_id: int,
    session: requests.Session,
    cache: JsonCache,
) -> tuple[list[tuple[float, float]], str, list[dict[str, Any]], bool]:
    cache_key = f"{IDEA_GEO_CACHE_VER}_{idea_id}"
    cached = cache.get("idea_geo", cache_key)

    if cached is not None:
        return (
            cached.get("coords", []),
            cached.get("h1", ""),
            cached.get("stops", []),
            True,
        )

    url = f"{BASE_URL}/idea/{idea_id}"

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()

        html = response.text

        h1_title = ""

        h1_match = re.search(r'<h1\s+class="wr-h1"[^>]*>(.*?)</h1>', html, re.DOTALL)

        if h1_match:
            h1_title = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip()

        coords: list[tuple[float, float]] = []

        for match in re.finditer(r'"coordinates"\s*:\s*\[', html):
            arr = _extract_json_array(html, match.end() - 1)

            if not arr:
                continue

            points = _parse_lng_lat_pairs(arr)

            if len(points) > len(coords):
                coords = points

        if len(coords) > 2 and coords[0] == coords[-1]:
            coords = coords[:-1]

        deduped: list[tuple[float, float]] = []

        for point in coords:
            if not deduped or deduped[-1] != point:
                deduped.append(point)

        coords = deduped

        stops: list[dict[str, Any]] = []

        stops_match = re.search(r'"stops"\s*:\s*\[', html)

        if stops_match:
            arr = _extract_json_array(html, stops_match.end() - 1)

            if arr:
                parsed = None

                try:
                    parsed = json.loads(arr)
                except requests.RequestException, ValueError, TypeError:
                    parsed = None

                if isinstance(parsed, list):
                    for obj in parsed:
                        if not isinstance(obj, dict):
                            continue

                        try:
                            raw_lat = obj.get("lat")
                            raw_lon = obj.get("lng")
                            if raw_lat is None or raw_lon is None:
                                continue
                            lat = float(raw_lat)
                            lon = float(raw_lon)
                        except requests.RequestException, ValueError, TypeError:
                            continue

                        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                            continue

                        stops.append(
                            {
                                "id": obj.get("id"),
                                "latitude": lat,
                                "longitude": lon,
                                "name": str(obj.get("name", "") or ""),
                            }
                        )

        cache.put(
            "idea_geo",
            cache_key,
            {
                "coords": coords,
                "h1": h1_title,
                "stops": stops,
            },
        )

    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("Не удалось загрузить геометрию для идеи %s: %s", idea_id, exc)
        return [], "", [], False
    else:
        return coords, h1_title, stops, False


def load_ideas(
    city_slug: str,
    cache: JsonCache,
    session_provider: SessionProvider,
    search: str | None = None,
    sort: str | None = None,
    max_pages: int | None = None,
    delay: float = 1.5,
) -> list[dict[str, Any]]:
    if not city_slug:
        logger.warning("Не указан slug города для загрузки идей")
        return []

    session = session_provider.get()

    terms = [term.strip() for term in str(search or "").split(",") if term.strip()]
    queries: list[str | None] = []
    queries.extend(terms or [None])

    all_ideas: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    def load_one(query: str | None) -> None:
        page = 1
        empty_count = 0
        cached_pages = 0
        stale_pages = 0

        while True:
            if max_pages and page > max_pages:
                logger.info("Достигнут лимит %d страниц", max_pages)
                break

            page_key = f"{city_slug}_s={query or ''}_o={sort or ''}_p={page}"

            page_ideas: list[dict[str, Any]] | None = None
            from_cache = False

            cached_page = cache.get("idea_list", page_key)

            if cached_page is not None:
                if (cached_page.get("search") or None) == (query or None) and (
                    cached_page.get("sort") or None
                ) == (sort or None):
                    page_ideas = cached_page.get("ideas", [])
                    from_cache = True
                    cached_pages += 1
                else:
                    stale_pages += 1

            if page_ideas is None:
                params = {"page": str(page)}

                if query:
                    params["search"] = query

                if sort:
                    params["sort"] = sort

                try:
                    response = session.get(
                        f"{BASE_URL}/{city_slug}/idea",
                        params=params,
                        timeout=40,
                    )

                    if response.status_code == 404:
                        break

                    response.raise_for_status()

                    soup = BeautifulSoup(response.text, "html.parser")
                    page_ideas = []

                    for table in soup.find_all("table"):
                        for tr in table.find_all("tr"):
                            tds = tr.find_all("td")

                            if len(tds) < 6:
                                continue

                            match = re.search(
                                r"(\d+)", tds[0].get_text(" ", strip=True)
                            )
                            idea_id = int(match.group(1)) if match else None

                            anchor = tds[1].find("a", href=True)
                            href = anchor["href"] if anchor else None

                            if isinstance(href, str) and not href.startswith("http"):
                                href = urljoin(BASE_URL, href)

                            rating, voters = parse_idea_rating(
                                tds[5].get_text(" ", strip=True)
                            )

                            page_ideas.append(
                                {
                                    "id": idea_id,
                                    "title": tds[1].get_text(" ", strip=True),
                                    "url": href
                                    or (
                                        f"{BASE_URL}/idea/{idea_id}"
                                        if idea_id
                                        else None
                                    ),
                                    "city": tds[2].get_text(" ", strip=True),
                                    "author": tds[3].get_text(" ", strip=True),
                                    "date": tds[4].get_text(" ", strip=True),
                                    "rating": rating,
                                    "voters": voters,
                                }
                            )

                    cache.put(
                        "idea_list",
                        page_key,
                        {
                            "search": query,
                            "sort": sort,
                            "ideas": page_ideas,
                        },
                    )

                except Exception as exc:
                    logger.warning("Ошибка на странице %d: %s", page, exc)

                    if page > 1:
                        time.sleep(delay * 2)
                        continue

                    page_ideas = []

            if not page_ideas:
                empty_count += 1

                if empty_count >= 3:
                    break
            else:
                empty_count = 0

            for idea in page_ideas:
                idea_id = idea.get("id")

                if idea_id is None:
                    continue

                if idea_id not in seen_ids:
                    seen_ids.add(idea_id)
                    all_ideas.append(idea)

            logger.info(
                "[%s] стр. %d: %d идей, всего: %d%s",
                query or "все",
                page,
                len(page_ideas),
                len(all_ideas),
                " (кэш)" if from_cache else "",
            )

            page += 1

            if not from_cache:
                time.sleep(delay)

        if stale_pages:
            logger.info("Сброшен кэш: %d стр. (изменился запрос/формат)", stale_pages)

        if cached_pages:
            logger.info("Страниц из кэша: %d", cached_pages)

    if len(queries) > 1:
        logger.info(
            "Поиск по %d словам: %s",
            len(queries),
            ", ".join(q for q in queries if q is not None),
        )

    for query in queries:
        logger.info(
            "Загрузка идей для %s%s...",
            city_slug,
            f" (поиск: {query})" if query else "",
        )

        load_one(query)

    if not all_ideas:
        logger.warning("Идеи не найдены")
    else:
        logger.info("Загружено %d идей (без дублей)", len(all_ideas))

    return all_ideas


def _fetch_idea_geometry_worker(
    idea: dict[str, Any],
    cache: JsonCache,
    session_provider: SessionProvider,
) -> (
    tuple[
        dict[str, Any], int, list[tuple[float, float]], str, list[dict[str, Any]], bool
    ]
    | None
):
    """Загружает геометрию одной идеи (выполняется в пуле потоков).

    ``SessionProvider`` создаёт отдельную ``requests.Session`` на поток,
    поэтому конкурентная загрузка безопасна.
    """
    idea_id = idea.get("id")

    if idea_id is None:
        return None

    session = session_provider.get()

    coords, h1_title, raw_stops, from_cache = fetch_idea_geometry(
        int(idea_id),
        session,
        cache,
    )

    return idea, int(idea_id), coords, h1_title, raw_stops, from_cache


def ideas_to_routes(
    ideas: list[dict[str, Any]],
    _city_slug: str,
    cache: JsonCache,
    session_provider: SessionProvider,
    _city_title: str = "",
) -> list[RouteData]:
    routes: list[RouteData] = []

    cached_count = 0

    logger.info("Загрузка геометрии для %d идей...", len(ideas))

    # Сеть — главный источник задержки; геометрия идей загружается в пуле
    # потоков, порядок результатов и нумерация в логах сохраняются.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for i, result in enumerate(
            pool.map(
                lambda idea: _fetch_idea_geometry_worker(
                    idea,
                    cache,
                    session_provider,
                ),
                ideas,
            ),
            start=1,
        ):
            if result is None:
                continue

            idea, idea_id, coords, h1_title, raw_stops, from_cache = result

            if from_cache:
                cached_count += 1

            route_type = guess_route_type_from_idea(
                str(idea.get("title", "")), h1_title
            )

            stops: list[Stop] = []

            for raw_stop in raw_stops:
                stop = Stop.from_api(raw_stop)

                if stop is not None:
                    stops.append(stop)

            directions: list[Direction] = []

            if coords:
                directions.append(
                    Direction(
                        coords=tuple(coords),
                        stops=tuple(stops),
                        name=str(idea.get("title", "")),
                    )
                )

            rating = idea.get("rating")
            voters = idea.get("voters")

            route = RouteData(
                name=str(idea.get("title", "")),
                route_type=route_type,
                route_id=idea_id,
                url=str(idea.get("url") or ""),
                directions=tuple(directions),
                error=None,
                active=True,
                is_idea=True,
                company=str(idea.get("author", "")),
                price=str(idea.get("city", "")),
                transport_class=f"{'' if rating is None else rating}({'' if voters is None else voters})",
            )

            routes.append(route)

            status = (
                f"✅ {len(coords)} точек, {len(stops)} ост."
                if coords
                else "⚠ нет геометрии"
            )

            if from_cache:
                status += " (кэш)"

            logger.info("[%d/%d] %s", i, len(ideas), status)

    geo_count = sum(1 for route in routes if route.directions)

    logger.info(
        "Загружено геометрии для %d/%d идей (из кэша: %d)",
        geo_count,
        len(routes),
        cached_count,
    )

    return routes
