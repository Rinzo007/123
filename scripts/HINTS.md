# Подсказки по файлам движка Takt

Папка содержит снимок (сниппеты) активов playtakt.app: минифицированные
JS-бандлы и JSON-данные городов. Идентификация файлов выполнена по
содержимому и по манифестам городов (`files {имя: размер}`) в JSON-файлах
`549ac5bf…`, `63eb8db0…`, `b3abbdea…`.

> **Внимание.** Классических `bc1e6aad22337e5eeaa8.js` /
> `64b3e62b167b2f7dcfa2.js` (на них ссылается `passenger_flow/base/takt.py`)
> здесь НЕТ — движок переехал. Их функции рассредоточены в `085f71988…` и
> модульных чанках (`bf42f327…`, `fb018ec9…`, `4bc693…`).

## JS — бандлы движка

| файл | что это |
| --- | --- |
| `085f71988f12f8f584de.js` | **Главный бандл расчёта** (~119 КБ). Источник переноса в `takt.py`: `jo`~21239, `hs`/`Yt`~30764–30794, `Mn`, `Po`, `Ca`~11176, `Ra`~11230, `Ua` (crowding), `we` (стоимость ребра), MSA `Zc/Gs`, тариф, ожидание; константы `accessM`/`An`/`Za`/`Na`/`lr`/`jc`/`nt`/`mt` и модель режимов va/wa/ga/Pa → см. раздел «Константы движка…». Содержит список данных города. |
| `178f73c0bd39ccdfb4fc.js` | **MapLibre GL JS v5.24.0** (~1.9 МБ) — рендер карты (несёт `loadGlyphRange`, `setNow`, sprite/glyph, воркеры). К расчёту отношения не имеет. |
| `bf42f3279969668ecfba.js` | **Геометрия/м-на-градус** для спайсинга и доступности: `re=111e3` (м/градус широты), `G=68e3` → `111320·cos(lat)` (долгота), плоский гаверсинус `$e=hypot(dx·G, dy·re)`. Импортируется из `index-b3cUwI2f.js`. |
| `fb018ec901c599306396.js` | **Взвешенный выбор по весам** (`k(n,r)` — линейный поиск по кумулятивным весам, возвращает `.name`), плюс вспомогательные утилиты (выбор аттракторов/целей). |
| `4bc693412a4c56192dae.js` | **IndexedDB-слой сохранений**: ключи `takt_lines_v15` / `takt_saves_v15` (+ суффикс города, для Berlin без суффикса), версия `U=15`. |
| `4b44de9c21af19ce3f1e.js` | **Vite-tочка входа**: `__vite__mapDeps` → `assets/main-…, city-version-…, idb-…, main-….css`; код автораскрытия «интро». |
| `35e33803b3130f6d19a8.js`, `d21d1fd17da521ea4ced.js` | **Тумблер навигационного меню** (`.navburger`). Дубль — две копии на страницу. |
| `47a399a268638bef0f11.js` | **Сканер `<script src>`** всех DOM-корней (сбор инлайна для отчёта/дебага). |
| `2997099b0c2e9b59f987.js`, `c503dbce536b79c15478.js` | **Хуки-захваты**: `window.__capturedInlineScripts` / `__capturedEvalScripts` (используются снифером). |
| `20464c1ec525e0516ff3.js` | Прокрутка `window.scrollBy` (пошаговый автоскролл статьи). |
| `472ad7c3eb7728a68e74.js` | `window.scrollTo(0,0)` — сброс в начало страницы. |
| `5519fe1146a3ded17e69.js` | **Пагинация журнала изменений** (`#changes .chg`, по 3 записи/страница, кнопка «more»). |
| `6f41dcdfe8f208facbeb.js` | **Производительность**: список загруженных ресурсов-скриптов (`performance.getEntriesByType("resource")`). |

## Константы движка из `085f71988…`, перенесённые в takt.py

Аудит главного бандла (сентябрь 2026) уточнил часть идентификаторов и добавил
константы, которых не было при первом переносе. Все они закреплены тестом
`TestTaktConstants::test_reference_constants_match_source`.

| константа | значение | где в проекте |
| --- | --- | --- |
| `accessM` по типам | bus 500 м, tram 600 м, metro 800 м, rail 1500 м | `VehicleSpec.access_m` (takt.py); привязка зон фильтрует остановки — `_line_access_stops` (algorithm/assign.py) |
| `An`/`Za` — совмещение пересадок | 800 м | `_TAKT_INTERCHANGE_MATCH_M`; дефолт `flow_transfer_radius_m = 800` |
| `Na` — порог OD-строк для воркеров | 1200 | `_TAKT_PARALLEL_MIN_OD_ROWS` |
| `lr` / `jc` — подвыборка OD-строк в `Js(..., s)` | 9 / 3 | `_TAKT_ENGINE_INTERNALS["lr"]` / `["jc"]` |
| `nt` / `mt` — пиковый множитель загрузки `yt = min(mt, 1 + nt·max(0, N/T − 1))` | 0.6 / 1.8 | `_TAKT_ENGINE_INTERNALS["nt"]` / `["mt"]` |
| `va`/`wa`/`ga`/`Pa` — модель режимов «транзит / авто / пешком» | — | дефолты `ModeChoiceConfig` (модель включена по умолчанию) |

> `lr` — **грубая** подвыборка OD-строк (а не «запас на поворот»), `jc` — мелкая.

## JSON — манифесты городов

Структура: `{city, version, sha256, files: {имя_роли: размер_в_байтах}, total}`.
Служат для сверки целостности пачками данных.

| файл | город / версия |
| --- | --- |
| `549ac5bf5c9ab770ffe9.json` | Amsterdam **v8** |
| `63eb8db095825be91d6a.json` | Hong Kong **v6** |
| `b3abbdead487216b6ec9.json` | Berlin **v5** |

## JSON — параметры города (model.json)

Задаёт профили режимов (mobility/rest), авто, тариф/VoT и знаковую схему.
Читается напрямую (не .bin) — полный словарь города с источниками чисел.

| файл | город |
| --- | --- |
| `07465affd6ff9e3c06b8.json` | Amsterdam |
| `474ed0bcaad130fb8a88.json` | Berlin |
| `bce18b5b8c89beee4e52.json` | Hong Kong |

## JSON — данные спроса и улиц

| файл | роль |
| --- | --- |
| `09d689b8d407e78e70ed.json` | **demand.json (Berlin)**: `pts=[[lon,lat,pop,jobs],…]` — точки спроса |
| `0c359956bd513caf1326.json` | **demand.json (Amsterdam)** |
| `b9086b4df13fc00fac60.json` | **demand.json (Hong Kong)** (только его попал в снимок) |
| `230641f21604b331eb13.json` | **demand-streets.json (Hong Kong)**: FeatureCollection MultiLineString улиц со значением `d` (плотность/спрос) |
| `84c007db78dab1f041bb.json` | **areas.json (Berlin)**: административные районы с `residents/jobs`, границами |
| `44d59719ad65a78b5d95.json` | **areas.json (Amsterdam)**: то же, плюс `sources` с лицензиями |

## JSON — текущее расписание (baseline.json)

GTFS-подобное описание действующих линий: `lines[{id,name,mode,headways[5],
stops[],cumT[],trips}]`. Основа для «до»-сценария и калибровки `restMeasured`.

| файл | город |
| --- | --- |
| `9f6f14977f2733f681d9.json` | Berlin (VBB GTFS) |
| `8784de1f99d69184f8a0.json` | Amsterdam (OVapi/NDOV) (в снимке назван `baseline.json`) |
| `0fca2be5ce71a57de708.json` | Hong Kong (TD GTFS + MTR) |

## JSON — растры (bitmask) и кольца воды

Компактные растра покрытия: `{v,w,s,cellLon,cellLat,nx,ny,cov|bits}`.
Кодировка base64 (символы A–Z = 0..25 и т.д.), 1 бит на ячейку.

| файл | роль |
| --- | --- |
| `b94f819a0f2e5c0c3416.json` | **buildings.json (Berlin)** — растровая маска зданий (`cov`) |
| `7bd99f2ad081675a0cbd.json` | **buildings.json (Amsterdam)** |
| `14eeba3a019b6f7ab0f1.json` | **buildings.json (Hong Kong)** |
| `15f7294dbd3f9e2d2c24.json` | **water.json (Berlin)** — маска водоёмов (`bits`) |
| `9355828b940d8d6ac614.json` | **water.json (Amsterdam)** |
| `5c2aa5ba8be628bcf847.json` | **water.json (Hong Kong)** |
| `2bfdba17c58cd0acbcb6.json` | **water-rings.json (Berlin)**: полигоны воды в компактном виде `{v,n,off[],xy base64}` |
| `d6f7324d14a3735fcbb3.json` | **water-rings.json (Amsterdam)** |
| `ff02d2186873be76f301.json` | **water-rings.json (Hong Kong)** |

## JSON — районы, границы, POI, аттракторы

| файл | роль |
| --- | --- |
| `4313ba21d05083cab2c4.json` | **districts.json (Berlin)** — `places[]` городов/районов для выпадающего списка |
| `69cce8db17b5d78ef36d.json` | **districts.json (Amsterdam)** |
| `0822f24ea4993db74dea.json` | **districts.json (Hong Kong)** |
| `4dcfca691220f81ca3ae.json` | **extent.json (Berlin)** — MultiPolygon границы территории + `cellM/reachM/km2` |
| `352d5664882cf6291c98.json` | **extent.json (Amsterdam)** |
| `eb4257b96fc4e547044a.json` | **extent.json (Hong Kong)** |
| `8fef470edb88c678ae4b.json` | **pois.json (Berlin)** — точки-аттракторы по целям (`t,w,x,y,n,q,img`) |
| `be860121a3cc23a42018.json` | **pois.json (Amsterdam)** |
| `7733113cb2a0305b5629.json` | **pois.json (Hong Kong)** |
| `ad9ab812773b8abfda13.json` | **landmarks.json (Berlin)** — POI-метки `{n,k,w,x,y}` |
| `cda194fb819e04177dbf.json` | **landmarks.json (Amsterdam)** |
| `85f756df013fd2e5eb00.json` | **landmarks.json (Hong Kong)** |

## JSON — базовые OD-матрицы по целям (purposes.bin.json)

Готовые слои спроса (читаются в `od/demand/takt.py::load_takt_purposes`):
`layers[{t (edu/…), n, out[5], ret[5], od, baseT[5]}]` + корневой
`commuteBaseT[5]`; `od` — base64 float32-поток `[origin, dest, trips, sec]`
(строк × n, представление бандла `ft.od`), `baseT`/`commuteBaseT` — base64
float32-потоки по периодам (времена поездки, длина = n пар). Версия `v: 2`.

> **Пара файлов (Berlin):** `66e80bf4d55b4659d8c1.json` (purposes) относится к
> **`09d689b8d407e78e70ed.json`** (demand, 19 317 точек / 42 237 OD-пар), а НЕ к
> `0c359956bd513caf1326.json` (Amsterdam). Индексы целей (1..19 289) лежат в
> пределах точек demand. Это единственная «полная» пара в снимке: Amsterdam/Hong Kong
> purposes есть, их demand.json — только HK (`b9086b4d…`).

> **Не путать с fallback:** `_TAKT_BASELINE_OD_LAYERS` в `takt.py` — это
> двухслойный резерв (errands/leisure) для случая, когда purposes.json не
> загружен; purposes.bin.json содержит готовые слои города поверх OSM.

| файл | город |
| --- | --- |
| `66e80bf4d55b4659d8c1.json` | Berlin (demand `09d689b8d407e78e70ed.json`) |
| `94cdd435df4b24a3cfbc.json` | Amsterdam |
| `a73e56fb9803a24243ca.json` | Hong Kong (demand `b9086b4df13fc00fac60.json`) |

## JSON — картографические службы (не расчёт)

| файл | роль |
| --- | --- |
| `4ba4a1990dc5e1b72b38.json` | Стиль карты OpenFreeMap (`sources`/`sprite`/`layers`). |
| `cd7f8edb06b60130359a.json` | `tiles.json` OpenFreeMap (векторные тайлы planet, версия). |
| `73e75e58d8c7bb62cc25.json` | Спрайт-атлас (`aerialway`, `airfield`, … координаты иконок на атласе). |