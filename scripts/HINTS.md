# Подсказки по файлам движка Takt

Папка содержит снимок (сниппеты) активов playtakt.app: минифицированные
JS-бандлы и JSON-данные городов. Идентификация файлов выполнена по
содержимому и по манифестам городов (`files {имя: размер}`) в JSON-файлах
`549ac5bf…`, `63eb8db0…`, `b3abbdea…`.

> **Внимание.** Классических `bc1e6aad22337e5eeaa8.js` /
> `64b3e62b167b2f7dcfa2.js` (на них ссылается `passenger_flow/base/takt.py`)
> здесь НЕТ — движок переехал. Их функции рассредоточены в `085f71988…` и
> модульных чанках (`bf42f327…`, `fb018ec9…`, `4bc693…`).

## JS — текущие бандлы приложения и движка

| файл | что это |
| --- | --- |
| `a8b4bba01e11002c4b03.js` | **Главный бандл приложения** (~909 КБ): интерфейс Takt, карта, редактор сети, линии, остановки, пути, анализ загрузки, графики и городские экраны. |
| `bd956ff0a1875604740f.js` | **Основной расчётный бандл** (~119 КБ): OD-спрос, маршрутизация, доступ, ожидание, пересадки, загрузка, стоимость, доход, парк и ограничения пропускной способности. |
| `716a2780445dd582d2f6.js` | **Модуль версий и сохранений** (~21 КБ): проверка игровых файлов, SHA-256-отпечатки, gzip, сериализация геометрии и проверка версии набора данных. |
| `15d114e402fdc70d23b3.js` | **Vite-точка загрузки** (~20 КБ): предзагрузка JS/CSS, загрузка городских ресурсов, обработка ошибок загрузки и отчётов производительности. |
| `6e443f984adafac6b27e.js` | **Слой хранения** (~3.9 КБ): IndexedDB `takt` / `kv`, ключи `takt_lines_v15`, `takt_saves_v15`, `takt_save_index_v15`; резервный `localStorage`. |
| `178f73c0bd39ccdfb4fc.js` | **MapLibre GL JS v5.24.0** (~1.9 МБ): отрисовка карты и WebGL; к транспортной модели не относится. |
| `35e33803b3130f6d19a8.js`, `d21d1fd17da521ea4ced.js` | Тумблер мобильного навигационного меню. |
| `5519fe1146a3ded17e69.js` | Пагинация журнала изменений: по 3 записи за страницу. |
| `20464c1ec525e0516ff3.js` | Пошаговая прокрутка через `window.scrollBy`. |
| `472ad7c3eb7728a68e74.js` | Сброс прокрутки через `window.scrollTo(0,0)`. |
| `b228e8a90f842f9ed336.js` | Список загруженных ресурсов-скриптов через Performance API. |
| `faf847e270ef28201c6b.js` | Сканер DOM: собирает `<script>` из document, template и shadow DOM для диагностики. |
| `2997099b0c2e9b59f987.js` | Возвращает `window.__capturedInlineScripts`. |
| `c503dbce536b79c15478.js` | Возвращает `window.__capturedEvalScripts`. |
| `e09b669a77a766c6fe80.js` | Пустой файл в текущем снимке. |

> **Обновление имён.** Более ранняя версия этой памятки ссылалась на `085f719…`, `bf42f327…`, `4bc693…`, `4b44de…` и `47a399…`. В текущем снимке этих имён нет; соответствующие роли распределены между перечисленными выше актуальными бандлами.

## Clickport — отдельный слой веб-аналитики

| файл | что это |
| --- | --- |
| `fb018ec901c599306396.js` | **Clickport**: A/B-эксперименты и поведенческая аналитика. Отслеживает просмотры страниц, клики, отправку форм, повторные/«rage» клики, «мёртвые» клики, уход из незаполненной формы, ошибки JavaScript, печать и копирование текста. События передаются на `/api/event`. |

В коде также встречаются UTM-метки, referrer, размер окна, число языков браузера, часовой пояс и WebGL renderer.
Обработчик `copy` передаёт до 200 символов выделенного текста как событие аналитики; для элементов форм имеются фильтры, исключающие чувствительные имена вроде password/email/card/token.

> **Разграничение:** Clickport не участвует в расчёте пассажиропотока; это отдельный клиентский слой аналитики.

## Архитектура расчётного движка

Упрощённый поток:

```text
городские данные
    ↓
население + рабочие места + дороги + действующий транспорт
    ↓
OD-спрос (origin → destination)
    ↓
созданные/изменённые линии
    ↓
доступ пешком + ожидание + поездка + пересадки
    ↓
выбор маршрута / распределение спроса
    ↓
пассажиропоток по периодам и сегментам
    ↓
загрузка и перегруженность
    ↓
доход + эксплуатационные расходы + парк + капитал
    ↓
результаты сети
```

Для больших матриц остановок используется `Web Worker` и параллельный расчёт.
При недоступности воркеров остаётся локальный поиск кратчайших путей на графе.

## Параметры режимов транспорта

Источник параметров: `716a278…` / `bd956ff…`.

| режим | вместимость | скорость/режимы | доступ пешком |
| --- | ---: | --- | ---: |
| bus | 90 | 18 км/ч mixed; 23 км/ч reserved | 500 м |
| tram | 250 | 19 / 25 / 33 км/ч | 600 м |
| metro | 750 | 70 км/ч | 800 м |
| rail | 1000 | 58 / 78 км/ч | 1500 м |

Дополнительно задаются время остановки, время посадки пассажиров, разброс времени,
разворот, стоимость вагоно-км, стоимость подвижного состава, пропускная способность
пути, минимальный интервал, длина платформы и стоимость строительства.

Типы инфраструктуры: `mixed`, `reserved`, `elevated`, `grade`.

## Интервалы и ожидание

В движке используются 5 периодов:

| ключ | время | часы |
| --- | --- | ---: |
| `early` | 04–06 | 2 |
| `am` | 06–09 | 3 |
| `mid` | 09–15 | 6 |
| `pm` | 15–19 | 4 |
| `eve` | 19–24 | 5 |

Среднее ожидание зависит от интервала и положения отправления относительно расписаний.
Для совместного движения двух линий рассчитываются также удержания/ожидание на пересадке.

## Загрузка и перегруженность

В `bd956ff…::Ua` рассчитывается загрузка по каждому сегменту и периоду.

```text
load factor ≈ пассажиры на участке / доступная вместимость
```

Отдельно считаются пассажиро-километры, перегруженные пассажиро-километры, сильная и экстремальная перегрузка.

## Диагностические и внешние запросы

В текущих бандлах встречаются обращения:

```text
/api/feedback/load-error
/api/feedback/performance
/api/share
/api/share/{id}/card
/api/event
```

`15d114…` отправляет отчёты об ошибках загрузки и производительности.
Кроме того, карта использует внешние OpenFreeMap-ресурсы; отдельные слои содержат ссылки на ArcGIS и Wikimedia.
## Константы движка, перенесённые в takt.py

Аудит текущего расчётного бандла уточняет часть идентификаторов и добавляет
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

## P0 — выравнивание `passenger_flow` с текущим Takt

После аудита `scripts/bd956ff0a1875604740f.js` Python-слой `passenger_flow` получил первый пакет parity-правок:

| область | что теперь делает Python |
| --- | --- |
| ожидание | По умолчанию использует Takt `Po(headway)`: `h/2` до 12 мин, затем `6 + 0.1h`. |
| route timing | При наличии `cumT` использует накопленное время маршрута; для `closed` поддерживается формат `cumT` длиной `n+1`. Без `cumT` используется геометрия + скорость типа транспорта. |
| per-line headway | Прямые поездки получают ожидание из headway конкретного маршрута, включая `headway_by_route`. |
| mode choice | Дефолты приведены к Takt: `noCar=0.35`, множитель `0.78`, `VOT=360`, ходьба `1.33 м/с` и `×1.25`. |
| density adjustment | `run_passenger_flow(..., population=...)` позволяет применять плотностную поправку `noCar` по origin-зонам, как в JS. |
| rider bias | Добавлен `rider_bias_s` для постоянного сдвига стоимости транзита `Wo`; знак значения не ограничивается. |
| defaults | Критичные parity-константы вынесены в `passenger_flow/base/defaults.py`; `models.py` и `takt.py` используют единый источник. |

Golden/regression-набор:

```text
tests/fixtures/takt_parity.json
tests/test_passenger_flow_takt_parity.py
```

Пока это **P0**, а не полный порт движка. Механизмы, отсутствовавшие в P0 (`baseT/rest`, поиск до 4 ножек, альтернативы маршрутов, сегментный crowding-feedback и экономический блок), перенесены в P1/P2 ниже.

> Тесты P0 добавлены в репозиторий. В текущей среде их фактический запуск не выполнялся; перед релизом нужен `pytest`/CI-прогон.

## P1 — parity `passenger_flow` с текущим расчётным бандлом Takt

Выполнен второй слой переноса математических ядер:

| область | P1-изменение |
| --- | --- |
| пересадки | Ограниченный поиск 0–4 ножек, до 3 пересадок и до 3 альтернатив с отсечением по detour-окну. |
| `baseT` / `rest` | `run_passenger_flow(..., base_time_s=...)` поддерживает NxN и [период, NxN]; `FlowResult` хранит `rest_trips`. |
| `riderBiasS` | `ModeChoiceConfig.rider_bias_s` участвует в транзитной полезности. |
| crowding | Feedback перенесён на сегменты и остановки: `lf`, множитель времени поездки, множитель ожидания и dwell-нагрузка. |
| MSA | Состояние сглаживает направленные сегменты и остановки; остановка по относительному разрыву `msa_gap` сохранена. |
| кольца | Закрытые маршруты используют последний элемент `cumT` как полный цикл, включая замыкающий сегмент. |
| экономика | OPEX включает `fleet × vehCostDay`; добавлен `capital_cost_eur`, а `capex_day` остаётся амортизированной величиной. |

Регрессионное покрытие P1 расширено в `tests/test_passenger_flow_takt_parity.py`: четыре ножки, `baseT/rest`, направленный crowding, OPEX и закрытый маршрут.

> CI-статусы текущих изменений отдельно не подтверждены: существующий workflow `Overture tests` относится к Overture-тестам, а не к новому passenger-flow parity набору.

## P2 — текущий первый узел parity

Первый P2-патч переносит периодную дорожную поправку Takt в Python:

- yt = min(1.8, 1 + 0.6 × max(0, N/T − 1)) считается для каждого периода;
- время движения автомобиля берётся из baseT при наличии;
- без baseT сохраняется геометрический fallback;
- парковка и денежная стоимость не умножаются на yt.

Следующий крупный P2-узел — выравнивание поиска маршрутов с графовым поиском Takt, затем shared-infrastructure capacity.

## P2 — shared infrastructure parity

Добавлен первый слой ``Ga/Ya`` parity: общие физические stop-to-stop секции
одного вида транспорта используют общий ``track_tph`` budget; для каждой
линии вычисляется residual capacity после остальных линий и из неё —
минимально допустимый ``headway``.

Ограничение намеренно использует stop-to-stop секции; геометрическое
разбиение JS ``Ja/Xa`` по промежуточным пересечениям остаётся следующим
уровнем exact parity.

## P2 — crowd-aware shortest-path routing

Shortest-path теперь учитывает текущую directional crowding-нагрузку уже на
этапе выбора пути: segment ride multiplier и stop dwell extra входят в edge cost.
После выбора варианта отдельно добавляется только crowding-множитель ожидания
и reliability extra, чтобы не учитывать ride/dwell feedback дважды.
## P2 — row-dependent infrastructure cost

Если маршрут явно содержит ``row/rows``, ``segCostMul``, ``fixedLegs`` или ``gaps``,
Python теперь считает CAPEX по физическим сегментам и значениям `TAKT_FLEET.rows`.
Для fixed leg поддерживаются `costM`/`rebuildCostM`; `gaps` дают нулевую стоимость.
Без явной инфраструктурной разметки сохраняется старый type-level CAPEX fallback.
При явном `row` скорость ряда также используется при построении fallback `cumT`.
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

P1 cleanup: `mode_choice.py` оставляет только единую Takt-модель выбора режима; исторический простой логит удалён.

## P2 — API, round-trip и финальная стабилизация

| область | P2-изменение |
| --- | --- |
| journey API | `network/routes.py` возвращает `JourneyAlternative` с именованными `total_time_min` и `legs`; объект сохраняет tuple-совместимость для старого кода. |
| повторные расчёты | `prepare_passenger_flow(routes, zones)` предвычисляет последовательности маршрутов, KD-tree и привязку зон; `run_passenger_flow(..., prepared=...)` переиспользует их без перестроения. |
| OD round-trip | `write_takt_purposes_bundle()` сохраняет `baseT`/`commuteBaseT`; `PurposeOd` также сохраняет вычисленный `baseT` для Takt-экспорта. |
| CAPEX | `capital_cost_eur` отделён от суточной амортизации `capex_day`; эксплуатационный `veh_cost_day` остаётся в OPEX. |
| режимы | `mode_choice.py` содержит единственную иерархическую модель Takt; старый общий логит удалён. |
| defaults | дополнительные механизмы `core` включены по умолчанию: 3 пересадки, 10 мин такт, периоды Takt, crowding, reliability и MSA. |

Регрессионное покрытие P2 находится в `tests/test_passenger_flow_takt_parity.py`: именованные альтернативы, round-trip `baseT`, переиспользуемый подготовленный контекст и дефолты `core`.

> Полный `pytest` в текущем окружении по-прежнему не подтверждён: репозиторий не удалось клонировать локально из-за сетевого DNS, а для текущего `HEAD` нет активного CI-прогона.
## Дедупликация функций

Проверен весь Python-код репозитория на повторяющиеся реализации. Убраны реальные дубли в Overture:
- одна реализация `_download_part_once` в `overture/http.py`;
- один общий `_sql_literal` в `overture/http.py`, используемый из `download.py`;
- одна реализация `resolve_poi_place_file` в `overture/poi.py`, а `download.py` и `load.py` делают только реэкспорт.
Добавлен регрессионный тест на отсутствие одинаковых функций верхнего уровня и на идентичность совместимых алиасов.
## P2 — shared infrastructure geometry and station headway

Shared infrastructure now uses atomic geometric sections: a segment is split
at intermediate stop vertices of same-mode lines, matching the effective
Ja/Xa overlap semantics for the stop-polyline representation. CAPEX reuse
and residual track capacity operate on these atomic sections.

Station minimum headway follows Takt Qa per period: the maximum stop
throughput is converted into the dwell constraint and combined with the
open-route turnback constraint. The scalar Python LineResult.min_headway
takes the maximum station/track constraint across supplied periods.

## P3 — differential / golden validation

Добавлен `scripts/takt_differential.py`: рекурсивное сравнение JSON snapshot
от reference Takt bundle и Python результата с абсолютной/относительной
tolerance и field-specific rules.

Golden reference для P3 хранится в `tests/fixtures/takt_reference_snapshot.json`
с provenance bundle `bd956ff0a1875604740f.js`. В `tests/test_passenger_flow_takt_parity.py`
Python-расчёты проходят через тот же comparator, поэтому изменение формулы
ломает golden test с указанием точного JSON path.

Для реального browser differential достаточно экспортировать JS snapshot и
Python snapshot в одинаковой JSON-схеме и запустить:
`python scripts/takt_differential.py reference.json actual.json`

## P4 — performance baseline

Добавлен `scripts/bench_passenger_flow.py` для воспроизводимых измерений двух
горячих путей: построение spatial transfer index и вычисление Takt C(...)
для большого числа stop-pairs.

Transfer spatial index создаётся один раз на `_AssignContext` и повторно
используется во всех OD-парах и MSA-итерациях. `_route_ride_time_min`
вычисляется O(1) через `cum_t_s/open_pre`, без прохода по промежуточным
сегментам.

## P5 — production hardening

Публичные числовые параметры `run_passenger_flow` проверяются на конечность
и допустимый диапазон до построения маршрутов. `NaN/Inf` больше не проходят
через сравнения вида `x <= 0`.

Подготовленный route graph дополнительно проверяет координаты остановок,
формат и монотонность `cum_t_s` и корректность `cycle_run_s`.
