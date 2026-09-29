# Transit Planner

Самостоятельное ядро для проектирования и моделирования общественного транспорта.

Модуль расположен в transit_planner/ и не зависит от старого расчётного кода репозитория.

## Данные

Основной источник геоданных — Overture Maps.

- дорожные сегменты: transportation/segment;
- физические узлы: transportation/connector;
- остановки и станции: base/infrastructure, subtype=transit;
- реальные места: places/place, с taxonomy.primary и basic_category.

Топология дорожного графа строится по общим connector_id и линейной позиции at из Segment.connectors. Совпадающие координаты без общей ссылки на connector не считаются физическим соединением. Это соответствует модели Overture.

## Реализовано

- доменная модель города и транспортной сети;
- остановки, маршруты, сервисы и виды транспорта;
- OD-спрос;
- дорожный граф;
- топологический граф Overture segment + connector;
- кратчайший путь с учётом Overture connector topology;
- простые и via-ограничения поворотов из prohibited_transitions;
- пространственный индекс;
- Overture Transportation Segment/Connector-провайдеры;
- Overture Base Infrastructure transit-провайдер;
- сеточная генерация зон спроса;
- гравитационная генерация OD;
- привязка остановок к дорожному графу;
- мультимодальный маршрутизатор;
- логит-модель выбора вида транспорта;
- распределение OD и пассажиропотоки;
- загрузка и итерационный штраф перегруженности;
- аналитика остановок и участков;
- доступность с режимными радиусами доступа;
- пассажиро-километры;
- экономика с затратами на эксплуатацию, парк и доходами;
- настраиваемая каноническая модель городского спроса;
- сценарии и сравнение;
- FastAPI;
- Vanilla TypeScript + MapLibre GL JS веб-редактор;
- Network View с частотой по пяти периодам;
- слои Overture roads/stops/places/connectors;
- сохранение/загрузка проекта через IndexedDB;
- timetable с фактическими минутами отправления;
- четыре альтернативы mode choice: transit/car/walk/bike;
- тарифная чувствительность transit;
- диагностика причин потери спроса;
- калибровочный отчёт по наблюдаемой/расчётной перевозке;
- физическая инфраструктура surface/elevated/tunnel/trenched/ramp, узлы, сигнальные блоки и crossovers;
- визуализация Overture-коннекторов;
- автоматические тесты и CI.

## Построение маршрута по Overture

Для произвольных точек можно использовать `POST /api/v1/data/overture/route`. Сервер привязывает точки к Overture connector-based road graph, учитывает доступность направлений и запрещённые переходы, а в ответ возвращает GeoJSON-линейное представление маршрута, список рёбер, длину и время движения.

В веб-редакторе этот же расчёт доступен через кнопку «Построить по дорогам». Для отображения используются фактические геометрии дорожных рёбер, а не прямая линия между остановками.

## Каноническая модель спроса

Основная модель использует пять периодов работы:

- `early` — 04:00–06:00;
- `am` — 06:00–09:00;
- `mid` — 09:00–15:00;
- `pm` — 15:00–19:00;
- `eve` — 19:00–24:00.

Для рабочих поездок используется базовая гравитационная OD-матрица. Поверх неё модель добавляет слои спроса по образованию, медицине, покупкам, аэропорту и вечерним местам. Параметры слоёв включают интенсивность поездок на ресурс, характерную дальность притяжения и детерминированный набор направлений.

Overture Places агрегируются к ближайшим зонам спроса. Маппинг категорий поддерживает как канонические ключи модели (`edu`, `shop`, `air`), так и доменные названия (`education`, `shopping`, `airport`).

## Timetable и инфраструктура

Service хранит headway по каждому периоду и может дополнительно хранить departure offsets. Модуль timetable из этих параметров строит фактические отправления и умеет находить ближайшее отправление для заданного времени прибытия.

Модуль infrastructure отделяет физические TrackSection от маршрутов и поддерживает surface/elevated/tunnel/trenched/ramp, shared track, parallel track, узлы, направления, уклоны, радиусы кривых, crossovers и сигнальные блоки. Инфраструктура описывает эксплуатационные ограничения сети; жизненный цикл строительства и CAPEX в модели отсутствуют.


### Настройки городской модели


При выборе остановки глобальный предел доступа дополнительно ограничивается нормативом access_m соответствующего обслуживающего режима. Для экономики учитывается требуемое число транспортных средств по времени кругового оборота, интервалу и режимным значениям времени стоянки и оборота.


## Веб-редактор

Frontend построен на Vanilla TypeScript + Vite/Rollup + MapLibre GL JS 5.24.0. Состояние сети и проекта хранится непосредственно в runtime, проекты и кэш Overture хранятся в IndexedDB, настройки интерфейса — в localStorage.

Архитектура интерфейса ориентирована на предоставленный референс: один entry `src/main.ts` → `src/app.ts`, ручной DOM, MapLibre, собственный worker runtime (routing/demand/assignment) и бинарный TLC1-кэш дорожной геометрии. Манифесты city packs с SHA-256 и TKBL-контейнеры — цель Этапа 2; битые заглушки предыдущих попыток удалены на Этапе 1.


## Полноценное приложение

Transit Planner развивается как единое приложение для пространственного и эксплуатационного планирования транспортной сети:

```
Overture → Network → Topology → Routes → Timetable → Fleet → Demand → Assignment → Passenger Flow → Analytics → Economics
```

### Редактор сети

MapLibre-редактор поддерживает:

- выбор и добавление узлов;
- создание участков между узлами;
- перетаскивание узлов на карте;
- автоматический пересчёт длины, перепада высот и уклона;
- полноценную панель свойств выбранного узла или участка;
- редактирование типа пути, направления, скорости, количества путей, уклонов, радиусов кривых и переездов;
- удаление объектов с топологической защитой;
- Command History;
- Undo/Redo через кнопки и `Ctrl+Z` / `Ctrl+Shift+Z`;
- операции split/merge для участков сети.

Панель свойств является частью редактора, а не отдельной копией данных: изменения применяются к каноническому `NetworkPayload`.

### Маршруты

Route Editor отделяет транспортный маршрут от физической инфраструктуры. Маршрут может быть собран из существующих TrackSection с проверкой ссылочной целостности. Удаление маршрута также удаляет связанные service-записи.

### Расписание и парк

Планировщик timetable генерирует фактические отправления на основе периода, headway и departure offset.

Модуль fleet рассчитывает потребность в подвижном составе на основе оборотного времени и интервала движения. В интерфейсе отображаются длина линии, расчётный оборот, парк по периодам и количество отправлений.

### Спрос и пассажиропотоки

Demand и assignment используют единый поток расчёта. В приложении доступны:

- зоны спроса;
- OD-пары;
- выбор вида транспорта;
- assignment;
- пассажиропотоки по участкам;
- потоки по остановкам;
- transit share;
- максимальная загрузка;
- неназначенный спрос.

Расчётная часть переносится в браузер по мере готовности (Этапы 6, 7, 6.5); Python остаётся эталоном и источником паритет-тестов, а не вторым runtime-путём.

### Архитектура frontend

```
frontend/src/
├── core/
│   ├── geometry.ts
│   ├── history.ts
│   └── topology.ts
├── planning/
│   ├── route-editor.ts
│   ├── timetable.ts
│   └── fleet.ts
├── simulation/
│   └── preview.ts
├── workers/
│   ├── routing.ts
│   └── routing.worker.ts
├── journey.ts
├── projection.ts
├── spatial.ts
├── street-walk.ts
├── map-network-editor.ts
├── network-editor.ts
├── line-cache.ts
├── storage.ts
├── api.ts
└── app.ts
```

Дублирующие реализации удалены на Этапе 1; каждая функция имеет ровно один авторитетный модуль.
`app.ts` остаётся composition/bootstrap-слоем и постепенно разгружается по мере миграции UI.

### Что не входит в приложение

Transit Planner не моделирует жизненный цикл строительства:

- нет construction projects;
- нет строительного CAPEX;
- нет календаря строительства;
- нет demolition lifecycle;
- нет blueprint lifecycle.

Физическая инфраструктура моделируется только как существующая или эксплуатационно доступная сеть с ограничениями скорости, уклона, пропускной способности, сигнализации и топологии.

## Дорожная карта transit_planner

### Этап 0 — Архитектурная фиксация

Статус: ✅

- Overture — единственный источник геоданных;
- Reference/Takt — основной архитектурный эталон;
- Python — подготовка данных и эталонная/offline-модель;
- браузер — основной интерактивный вычислительный клиент;
- Vanilla TypeScript + Vite + MapLibre;
- Web Workers для всех тяжёлых расчётов;
- typed arrays вместо больших объектных структур;
- IndexedDB для city packs и проектов.

### Этап 1 — Полное удаление fallback

Статус: 🔄 финальная ревизия

Правило этапа: если обязательный источник или расчёт недоступен — выдаётся явная ошибка. Никакого молчаливого перехода на другую модель, synthetic data, пустой результат или упрощённую геометрию.

Уже устранены основные fallback-маршруты, synthetic demand, не-топологический road graph, скрытые дефолты скорости и стоимости, старые API-shim и дублирующие вычислительные реализации.

Остались к удалению именно как runtime-ветки: `journey.ts` grid-nearest резервный поиск, `toReferenceDemand()` synthetic-предрасчёт, скрывающие ошибки `catch` в обязательных слоях Overture/urban context и все fallback-операторы, которые подменяют обязательные данные.

Frontend:

- fallback маршрутизации: подмена Overture-маршрута прямой линией удалена (`routeGeoJSON` → `emptyRouteGeoJSON`), ошибка при недоступности расчёта;
- synthetic demand: стаб `toDemandInput` (плоские 1000 поездок) удалён, `runRuntimePreview` требует authoritative OD и бросает явную ошибку;
- fallback evaluation: `runClientPreview` без воркера переименован в честный `networkCounts`, reference-модель `/data/model.json` больше не деградирует в `{}`;
- fallback сборки: хеш-бандл `/assets/evaluation.worker-jRuvxHc_.js` удалён вместе с `evaluation-runtime.js`; reference-оценка в браузере выключена целиком (см. ниже);
- дублирующие TS-реализации: удалены 15 мёртвых модулей (~1000 строк), включая `rraptor.ts` с битым импортом, дубли валидаторов, редакторов и кэшей;
- worker-контракты: `routing` worker изолирован (`export {}`), устранены глобальные коллизии;
- storage: повреждённый или бесформенный JSON настроек даёт явную ошибку; реальная ошибка IndexedDB при восстановлении проекта выводится в статус;
- timetable/fleet/routing: отсутствующий headway = пустое расписание/нулевой парк, отсутствующий period = явная ошибка (не 20 мин и не 0–1440);
- mode cost: `DEFAULT_ROW_COST` индексируется `TransitMode` и компиляторно исчерпывающ, подмена `?? 1` и мёртвые ветки row-классификации удалены;
- резервные артефакты: удалены осиротевшие бандлы из `public/` и второй `model.json` (эталон в `reference/` сохранён).

Python:

- `build_road_graph()` удалён целиком (не-топологический путь); `_add_fallback_segment` удалён — сегмент с <2 connector refs бросает `ValueError`, а не деградирует в одно слитное ребро;
- `load_roads()` собирает сегменты без полной коннекторной топологии и падает явной ошибкой со списком id;
- `_effective_speed_kph`: параметр `fallback` переименован в обязательный `class_speed_kph`, без скрытого дефолта;
- `api.py` (star-shim над `api_strict.py`) свёрнут в один модуль — дублирующий API-слой удалён;
- мёртвый `coordinate_precision` убран.

Перенесено дальше по карте (не блокирует правило Этапа 1):

- `DEFAULT_RELEASE` фиксируется в manifest city pack на Этапе 2, вместо runtime-константы;
- `operating_cost_per_km or profile.opex_per_vehicle_km` в `analytics.py` — reference-evaluation semantics, меняется вместе с parity-тестами на Этапе 8;
- старые profiles/modes и дубли Python/TS — финальная ревизия на Этапе 17.


### Этап 2 — City Pack v1

Статус: ✅ завершён

Реализовано (Python):

- `city_pack.py`: манифест v1 строго по спеке (`city, version, sha256, files, totalBytes, schemaVersion, source, release`); `release` фиксирует Overture release источника; `source` зафиксирован как `overture`;
- состав pack закрыт: `pack_city_files` принимает ровно 8 обязательных файлов (манифест не входит), лишние файлы и недостающие — явная `CityPackError`, fallback-артефактов нет;
- атомарная запись: `write_city_pack` пишет в staging-каталог рядом с целью, вызывает `verify_city_pack` на staging и только потом подменяет цель (`Path.replace`); частично записанный pack под целевым путём не появляется;
- строгая загрузка: `read_city_pack` — отсутствие манифеста/поля, чужой `schemaVersion`, расходится набор файлов на диске, размер, SHA-256 файла или payload-хеш = `CityPackLoadError` (`partial=True` — недостаёт/лишние файлы, перепакуется; `partial=False` — повреждено содержимое);
- новые бинарные слои в `binary_pack.py`: TKSP `stops.bin` (id, lon/lat×1e6, is_station), TKZN `zones.bin` (центроиды m, population/jobs + purpose-аттракции), TKDM `demand.bin` (OD-строки float64 с purpose-таблицей), TKWR `water.bin` (дельта-полигоны как TKBL); все — little-endian magic+version+offsets, roundtrip-тесты;
- `build_city_zones`: адаптивная TAZ-сетка (≈24 ячеек, 200–2000 м) по places+stops в локальных метрах; population/jobs — place-importance proxy (work = jobs, остальные = residential), работа с точной калибровкой — Этап 6-7;
- `build_overture_city_pack` собирает все слои: streets (TKST, обратная проекция узлов в WGS84), stops/zones/demand/buildings/water (TK*), `streets.json` несёт origin проекции.

Не сделано (сознательно, дальше по карте):

- фронтенд-загрузка pack в IndexedDB под новый формат (Этап 3 — там же street graph worker);
- строгая провязка demand к reference-периодам вместо daily-матрицы (Этап 7).

Формат:

```
pack/
├── manifest.json   city, version, sha256, files, totalBytes, schemaVersion, source, release
├── model.json
├── streets.json
├── streets.bin
├── stops.bin
├── zones.bin
├── demand.bin
├── buildings.bin
└── water.bin
```

Реализовать: бинарный TKBL; typed-array геометрию; offsets; SHA-256 проверку; версионирование; атомарную загрузку pack; запрет частично загруженного pack; фиксацию Overture release в manifest; никаких fallback-файлов.

Заметка: удалённый на Этапе 1 `city-pack.ts` был битым скелетом; загрузка pack перестроится заново под этот формат.

### Этап 3 — Уличный граф

Статус: ✅ завершён

Overture streets → street graph → compressed binary graph → routing worker.

Сделано (Python):

- `road.py`: `RoadGraph.weakly_connected_components()`, `component_sizes()`, `are_connected()` — компоненты weakly-connected графа, а не «всё связано по умолчанию»;
- `route_points()` различает три исхода: точки в разных компонентах (явная ошибка со списком компонент), directed-пути нет, путь найден; прежний молчаливый отказ «маршрут не найден» на несвязанных сегментах устранён;
- `binary_pack.py`: TKST v2 — узлы (WGS84 int32 микроградусы) + component id на узел, `edgeA/edgeB/edgeLen`, `geomOffset`, `edgeClass/edgeSpeedKph/edgeDirection` (u8), дельты геометрии (dx,dy int32), JSON-словарь имён; `decode_streets_bin()` для чтения и тестов roundtrip;
- скорость вне `[0, 255]` кph — явная `ValueError` (было тихое усечение `min(255, …)`, то есть расхождение стоимости с Python);
- `city_pack.py`: `streets_bin_for_graph()` — сборка TKST из готового `RoadGraph` + WGS84-обратная проекция узлов;
- `api.py`: `GET /api/v1/data/overture/graph` отдаёт TKST v2 как `application/octet-stream` с `X-Streets-Version/Nodes/Edges/Components`; недоступный источник — 502, а не пустой граф.

Сделано (frontend):

- `street-graph.ts`: декодер TKST v2 (little-endian, полный mirror формата), CSR-строитель, `nearestGraphNode()` (grid-индекс `spatial.ts`, порог снаппинга 150 м), `assembleRouteCoordinates()`;
- CSR-семантика: `RoadEdge` в Python — уже направленная дуга, поэтому каждое ребро даёт ровно одну дугу `edgeA → edgeB`; `edgeDirection` — информативный признак, а не правило обхода (иначе терялись односторонние рёбра и появлялись фантомные обратные дуги);
- `speedKph === 0` — явная ошибка, а не подстановка 30 (Python `add_edge`/`travel_time_minutes` отбрасывают такие рёбра, паритет поведения);
- `workers/street.worker.ts`: Dijkstra на CSR, отмена по job-номеру, бинарная куча, `postMessage` с transfer ArrayBuffer; воркер отдаёт цепочку узлов, поэтому геометрия собирается с учётом проезда против направления ребра;
- `buildRoadRoute()` переведён на локальный граф: снаппинг остановок → пакет запросов в воркер → координаты; граф грузится один раз на bbox и кэшируется в IndexedDB как бинарник; серверный `/overture/route` фронтендом больше не используется и остаётся только как API;
- без fallback: `streetGraph === null` или точка дальше 150 м от графа — явная ошибка в статусе, тихого возврата к серверному маршруту нет.

Проверено: `python -m pytest tests -q` → 211 passed; `npx tsc --noEmit` → 0; `npx vite build` → собран `street.worker` (2.26 kB).

Не сделано (сознательно, дальше по карте):

- отображение street graph отдельным слоем карты (сейчас слой дорог остаётся на GeoJSON из `/overture/network`, граф используется для маршрутизации).

### Этап 4 — Matrix Worker

Статус: ✅ завершён

Матрица stop×stop в проекте **не нужна**, и это проверено по коду, а не по предположению:

- авторская модель (`routing.py::_build_walking_neighbors`) строит пешеходные пересадки по прямой линии между остановками с радиусом 500 м и скоростью 5 км/ч; walk access/egress в ней отсутствует — `origin`/`destination` это сами остановки. Frontend-пакер делает ровно то же (0.5 км / 5 км/ч = 6 мин), поэтому расхождения в фиделити нет;
- `assignment.py::_walk_minutes` для zone→stop использует прямую линию так же, поэтому street-distance walk не улучшает паритет с Python, а меняет метод;
- матричный путь был единственным в транспилированном `evaluation-runtime.js`: пул из `public/assets/reference-matrix.worker.js` (собственный CSR, кэш `routePools`/`warmLoads`, гейт `stops >= 1200` и whitelist hostname). Он не строил матрицу по Overture TKST, а вместе с воркером удалён, поэтому своего матричного пути в проекте больше нет;
- собственный `matrix.worker.ts` вместе с `solveMatrix`/`solveRoadMatrix` **не имел ни одного вызова**, то есть был мёртвым близнецом вендоренного воркера. Удалён.

Вместо несуществующей матрицы закрыта реальная дыра Этапа 3 — инвалидация кэша street graph:

- ключ кэша `graph.bin` теперь включает версию формата (`STREETS_VERSION`, экспортирована из декодера) и релиз Overture: смена релиза или формата автоматически инвалидирует запись, устаревший TKST больше не отдаётся вечно;
- `/api/v1/data/overture/graph` отдаёт `X-Overture-Release`, клиент требует этот заголовок и сверяет его с релизом, из которого загружена сеть — расхождение это явная ошибка, а не тихая подмена;
- повреждённая запись кэша декодируется с одной попыткой перезапроса у источника; вторая неудача остаётся явной ошибкой.

Проверено: `python -m pytest tests -q` → 211 passed; `npx tsc --noEmit` → 0; `npx vite build` → успешно.

Сделано позже:

- `demand-choice.worker.ts` / `solveDemand` / `reference-runtime.ts` — удалены. Воркер был faithful Wardman mode-share с frequency-based insertion, но не имел ни одного вызова (0 ссылок), а исходников для проверки покрытия mode choice нет. Вместе с ним удалён обёртка `solveDemandStrategy` и весь тип `DemandBatch`/`DemandOutput`. Выбор режима считает `choice.ts` по правилу игры (argmin по лестнице дохода), спрос — `demand.ts`.

### Этап 5 — Routing Worker

Статус: ✅ завершён

Ядро: `routing.worker.ts` — RAPTOR поверх packed typed arrays (route patterns, departures, transfers), transferable buffers. Time-dependent поиск был в worker изначально: перебор отправлений по окну.

Сделано на этом этапе:

- `journey.ts` — единственная точка входа планировщика A→B: door-to-door через `street-walk.ts` (граф обязателен, тихого отката к остановкам нет), legs с board/alight и walk-ногами, геометрия маршрута включая уличные сегменты, LRU-кэш на 32 записи на объект графа с ключом `projectRevision|period|from|to|параметры` и сброс при любой правке сети;
- `routing.ts` — отдаёт из worker не только путь, но и копии route patterns для геометрии; добавлены явные ошибки вместо молчаливых `continue`: неизвестный период, выход индексов за границы, совпадающие endpoints, service без интервала в периоде, отсутствие отправлений в окне периода, route/stop ID, не встречающиеся в services, повреждённая геометрия маршрута;
- UI — панель «Маршрут A→B» с выбором остановок, периода и времени отправления, слой геометрии на карте, текстовый itinerary;
- инвалидация результата централизована в `markDirty()`/`invalidateJourneyPlan()`: ранее перетаскивание узлов карты и undo/redo меняли сеть без `markDirty()`, `projectRevision` не рос и из кэша возвращался устаревший маршрут;
- `projection.ts` — общие `toLocalMeters()`/`fromLocalMeters()` для редактора и планировщика;
- паритет расписания с Python: формула первого отправления в JS использовала отрицательный остаток и давала отправление **до** начала периода, а `planning/timetable.ts` считала `start + offset` и расходилась с `timetable.py` при `offset > headway`. Обе TS-реализации переведены на общий `firstDepartureMinute()`, тест `tests/test_frontend_timetable_parity.py` собирает настоящий `planning/timetable.ts` и сверяет его с `timetable.py` (пропускается без node/typescript);
- `spatial.ts` — точный grid для `nearestGraphNode()` и `nearestStopIndex()`; вместо `Math.hypot` используется `sqrt(dx²+dy²)`, чтобы расстояния совпадали с `spatial.py` побитово. Индексы кэшируются через WeakMap. Тест `tests/test_spatial_parity.py` сверяет настоящий TS с Python и с полным перебором; радиус границ теперь включительный в обеих реализациях;
- `formatMinuteOfDay(1440)` показывает `24:00`, а не `00:00` — иначе конец периода «вечер» подписан полночью следующего дня.
- door-to-door по уличному графу: `TransitRouter.shortest_from_points()` принимает произвольные точки, считает пешеходный доступ/выход по графу (Dijkstra от снапнутых узлов, скорость 5 км/ч, кэтчмент 500 м — тот же `walk_transfer_radius_m`), ищет по всем остановкам кэтчмента и возвращает access/egress-ноги. Односторонние рёбра для пешеходов проходятся в обе стороны, motorway исключены. `street-walk.ts` — тот же алгоритм в TS (CSR, бинарная куча, порядок обхода outgoing-then-incoming как в `road.py`); `routing.worker.ts` стартует rRAPTOR со всех доступных остановок и выбирает лучший выход среди всех целевых; `journey.ts` добавляет walk-ноги и уличную геометрию в itinerary. Тест `tests/test_door_to_door_parity.py` сверяет настоящий TS (journey + worker) с эталоном; допуск 5e-3 мин покрывает квантование микроградусов TKST.
- пресеты обобщённой стоимости: `model.json` хранит Table 5 (walk 1.65 / wait 1.72) как стандарт; бандл Borough Studio цитирует Table 8 того же мета-анализа (walk 1.39 / wait 1.37) — разные таблицы, поэтому оба набора стали именованными пресетами (`journey_choice_table8`, `JOURNEY_CHOICE_TABLE5/TABLE8`), а не заменой. Веса инжектируются в worker, захардкоженных литералов там нет; `tests/test_choice_weights_parity.py` сверяет их с `model.json`.
- бюджет range-запроса: `RouterConfig.raptor_max_range_departures` (24) — отправления в окне берутся равномерно, а не каждую минуту; без лимита 30-минутное окно давало 31 прогон rRAPTOR на запрос.
- shared trunk'и: `trunks.detect_shared_trunks()` — пары маршрутов по длинному общему коридору (нормализованное перекрытие по длине, bearing-гейт 20°, минимум общих пар сегментов); только пары, без мега-мерджей. Упрощение относительно игры: вместо turf-буферов и RBush — точное сравнение пар остановок и длин сегментов.
- двусторонние TOD-множители: `build_temporal_demand_two_sided()` берёт `max(outbound, return)` по периоду вместо усреднения, сумма дневных поездок сохраняется.
- сглаживание парка: `fleet_required_at_minute()` (Python) и `fleetRequiredAt()` (TS) — в пределах ±cycle от границы периода линейная интерполяция между соседними периодами вместо ступеньки; паритет в `tests/test_fleet_blending.py`.
- strict-fit boarding: `SectionLoad.denied_boardings` и `AssignmentMetrics.denied_boardings` — избыток спроса над свободной вместимостью секции считается отдельно, а не теряется молча;
- Pareto-фильтр альтернатив: `pareto_filter_journeys()` оставляет только недоминируемые на (perceived time, transfers, duration); `shortest_alternatives` применяет его к выходу diversity-цикла. Идентичные метрические кортежи не доминируют друг друга.

Не сделано (сознательно):

- walk-only результаты: `shortest_from_points()` может вернуть чисто пешеходный маршрут без транзита; TS помечает такие `found=false`, потому что контракт worker не несёт provenance промежуточных transfer-ног и геометрию им построить не из чего. Узкое расхождение зафиксировано здесь, а не спрятано.

### Этап 6 — Demand Worker

Статус: 🔄 частично готов

Уже перенесено: commuter demand; purpose layers; Overture places; purpose generators; периоды; WGS84 точки; reference generator En(). Typed-array представление, передача OD через ArrayBuffer, полный reference demand pipeline в worker; mode choice (Этап 7); ядро и сетевой слой assignment (Этап 6.5).

Сделано в этой итерации:

- `app.ts` больше не ходит в `/api/v1/demand/reference`: матрица считается в `demand.worker.ts` из реальных WorldPop-зон и Overture-мест.
- Воркер отдаёт наружу и суточную gravity-матрицу, и temporal-разбивку по периодам (`buildTemporalDemand` перестал теряться).
- `calculateAssignment()` и `loadDemandStreets()` получают реальную матрицу по зонам населения и колоночные зоны, а не синтетическую пару `stops[0] → stops[last]` с `trips = previewTrips`.
- Паритет `gravityOd()` ↔ `gravity_od()` закреплён в `tests/test_demand_parity.py` (состав пар, поездки, базовое время, сохранение производств).
- Попутно исправлен баг: `pts` в `buildReferenceDemand()` брался из координат мест Overture по индексу зоны вместо центроидов зон.

Осталось: строгая провязка спроса к reference-периодам во всех сценариях, а не только в предпросмотре. Пассажиропотоки и экономика больше не ходят в Python — см. Этап 6.5.

Проверено: `python -m pytest tests -q` → 276 passed; `npx tsc --noEmit` → 0; `npx vite build` → успешно; `uvx vulture . --min-confidence 80` → чисто.

### Этап 7 — Mode Choice

Статус: 🔜

Перенести основной nested logit: car; transit; walk; bike / two-wheel.

Сделать: инкрементальный nested logit; per-mode generalized cost; mobility constraints; typed-array output; incremental recalculation.

Перенесено в TS (`frontend/src/choice.ts`): `ChoiceConfig` с полной валидацией; `transitGeneralizedMinutes` с нелинейной нагрузкой от пересадок; `utilities` для пяти режимов (car/transit/walk/bike/rest) с корректной обработкой `-Infinity`; `probabilities` (multinomial logit с долей households без автомобиля и ограничением доступности режимов); `alternativeProbabilities` — split транзитного спроса по обобщённой стоимости.

Паритет закреплён в `tests/test_choice_parity.py`: 6 сценариев (нулевые расстояния, недоступный транзит, вело без расстояния, пересадки, тариф, поездка дальше `bike_reach_m`) × 4 набора доступности; константы сверяются с `model.json`.

Проверено: `python -m pytest tests -q` → 276 passed; `npx tsc --noEmit` → 0.

Не перенесено: инкрементальный nested logit и frequency-based insertion — воркер с ними удалён как непроверяемый (0 вызовов, нет исходников), а assignment-движку они не нужны: он считает распределение по одному набору обобщённых стоимостей.

### Этап 6.5 — Assignment в браузере

Статус: 🔄 в работе

Цель: убрать `/api/v1/assignment` из рантайма, чтобы серверная реализация не была вторым runtime-путём для тех же алгоритмов (см. принцип в Этапе 14).

Перенесено и покрыто паритом с Python:

- `frontend/src/assignment-model.ts` — ядро расчёта потоков: `assignOnce` (mode split, strict-fit boarding, потоки по маршрутам/остановкам/секциям, классификация потерь), `segmentCrowdingPenalties`, `blendFeedback`, `maxFeedbackDelta`, профили режимов, уровни переполненности, `crowdingTimeMultiplier`, `headwayUnevennessFactor`.
- `frontend/src/assignment-network.ts` — сетевой слой: `sectionCapacityAndPlatforms` (с разделением общей физической шины между сервисами), `stopDwellCoefficients`, `nearestStopId` с ограничением по `access_m` режима, `serviceHeadwayFactors`, `serviceDepartures`, `segmentRunTimes`.

Ключевое решение по париту: роутеры у TS и Python различаются намеренно, поэтому journey **инъецируются** в ядро, а сравнивается сам расчёт потоков. Вместимости, времена в пути и dwell подаются из эталона — их порт впереди по цепочке.

Тесты: `tests/test_assignment_core_parity.py`, `tests/test_assignment_network_parity.py`. Оба проверены мутациями: сценарии специально включают высадку, совпадающую с посадкой, и рельс, где предел профиля режима реально ограничивает вместимость — иначе соответствующие ветки остаются непроверенными.

Осталось: `/api/v1/demand/streets` для батчей, `compareScenarios` и `calculateCityAssignment` всё ещё смотрят в сервер; строгая провязка остальных сценариев к reference-периодам.

Сделано в этой итерации:

- `frontend/src/workers/routing-kernel.ts` — ядро rRAPTOR вынесено из воркера в обычный модуль: `routeSingle` и `routeAlternatives` (diversity-цикл + Pareto). `routing.worker.ts` стал тонкой обёрткой, а assignment импортирует ядро напрямую — перемаршрутизировать каждую пару на каждой итерации через вложенный воркер неподъёмно.
- `packRaptorInput` принимает интервальные множители и пересчитывает отправления: `headway *= max(1, factor)`, как в `TransitRouter._patterns`. Множители меньше единицы игнорируются.
- `frontend/src/workers/assignment-runtime.ts` — цикл итераций `assign_demand`: повтор `assignOnce`, смешивание штрафов переполненности и интервальных множителей с `damping`, выход по `convergence_tolerance`. Рейсы роутера разворачиваются в ноги по секциям, как в эталоне.
- `frontend/src/workers/assignment-client.ts` — вызов воркера и отображение на snake_case-контракт, который уже рендерит UI, плюс `demandStreetsFromLoads`.
- `app.ts`: `runPreview` считает пассажиропотоки **в браузере** по спросу выбранного периода (не за сутки) и строит оверлей demand streets локально; `runEconomics` использует тот же спрос вместо синтетической пары и даёт явную ошибку, если расчёт пассажиропотоков ещё не выполнялся.
- Доля households без автомобиля приходит из подвижностного профиля модели, а не из литерала в воркере.

Попутно исправлены два бага:

- `waitMin` первой ноги считался как `departures[0] - arrivals[-1]`, то есть `NaN`. Теперь ожидание берётся от фактического времени готовности к посадке (время доступа у первой ноги, время пересадки у следующих).
- Колонки зон в `buildReferenceDemand` собирались в обработчике ответа, то есть **после** `postMessage` с `transfer`. Переданные массивы на главном потоке отсоединены, чтение давало `undefined`, а из него `NaN`: в браузере зоны приходили с NaN-координатами, и все метрики assignment становились нечисловыми (в интерфейсе — `transit NaN%`). Колонки теперь считаются до отправки, а `noCarShare` добавлен в список переноса. Найдено только headless-прогоном в настоящем Chrome; регресс защищён `tests/test_worker_transfer_safety.py`.

Проверено в headless Chrome: приложение поднимается, воркеры demand/routing/assignment стартуют, `build-road` строит маршрут по графу улиц, предпросмотр доводится до конца без ошибок консоли, и список запрошенных `/api/*` не содержит ни `/v1/assignment`, ни `/v1/demand/reference`, ни `/v1/demand/streets`. Отдельным контрольным сетью с метро подтверждено, что транзитная доля ненулевая (81%) — нулевая доля в синтетическом городе объясняется его геометрией и режимом, а не отказом расчёта.

Стенд лежит в `tools/browser_check.py` и повторяет этот прогон:

```
cd frontend && npx vite --port 5199 --strictPort
python tools/browser_check.py
```

Бэкенд не нужен: ответы Overture подменяются на синтетические, граф улиц кодируется энкодером репозитория. Стенд требует установленный Chrome и `playwright` для Python; без них печатает `SKIP` и завершается с кодом 0.

Покрыто: `tests/test_routing_kernel.py` (ядро роутера — ранее не было покрыто вовсе), `tests/test_journey_alternatives_parity.py` (perceived time, Pareto, diversity-цикл), `tests/test_assignment_loop.py` (сходимость, damping, ожидание, согласованность потоков), `tests/test_assignment_wiring.py` (оверлей и запрет возврата к серверному assignment). Каждый набор проверен мутациями.

Проверено: `python -m pytest tests -q` → 276 passed; `npx tsc --noEmit` → 0; `npx vite build` → `assignment.worker` 29.7 kB; `uvx vulture . --min-confidence 80` → чисто.

### Этап 8 — Network Evaluation Worker

Статус: 🔄 частично готов

Reference evaluation (init/run/cancel, epoch, baseline T, layers) выключена целиком: удалены `evaluation-runtime.js`, хеш-бандл, пул `EvaluationClient`, `evaluation.worker.ts` и вызов из `runPreview`. Причина — движок содержал цикл равновесия и штраф за тесноту, которых нет ни в одном игровом модуле, а исходников для проверки нет. Вместо вызова в поднятый, но не отвечающий воркер (это зависание) вызов убран; проверочный пассажиропоток считает assignment-воркер, demand — `demand-choice.worker.ts`.

Добавить: reuseError; trackId + segment invalidation; incremental evaluation; planningPreview поверх worker-результатов; полный расчёт network → segment loads → PLF → fleet → headway → CAPEX/OPEX → city result; probe(base, candidate).

### Этап 9 — Urban Context

Статус: 🔄 в работе

Уже сделано: Overture buildings; Overture water; `UrbanContext`; segment multipliers; `built_up`; `water_share`; `roof_share`; `building_count`; `building_area_m2`; подключение к CAPEX; использование в city assignment; отдельный API urban context.

Дальше: точный parity с `builtUp`, `waterShare`, `roofShare`, `buildingsHit`, `buildingRings`; влияние urban context на travel time; упаковка buildings/water в city pack; packed spatial index; исключение runtime-загрузки для уже собранного pack.

### Этап 10 — IndexedDB

Статус: 🔄 частично готов

Есть: IndexedDB для проектов и dataset cache, versioned stores, TTL для dataset records, gzip через CompressionStream, atomic save/delete.

Сделать: writer queue; `begin()/commit()`; active promise pool; ABA-safe writes; reconciliation по `savedAt`; version prefixes; size limit; atomic restore; cross-tab invalidation.

Fallback localStorage для данных проекта после завершения этапа запрещён; localStorage остаётся только для небольших UI-настроек.

### Этап 11 — Editor

Статус: 🔄 частично готов

Есть: карта; выбор объекта; панель свойств; узлы/участки/nodes; crossovers и signal blocks; split/merge; undo/redo; пересчёт только затронутых сегментов (segment signatures).

Сделать: создание маршрута и изменение трассы мышью; добавление/удаление остановок в каноническом редакторе; изменение режима и пути; snapping к уличному графу (после Этапа 3).

### Этап 12 — Производительность

Статус: 🔄 в работе

Принцип: `ArrayBuffer → Worker → ArrayBuffer`; JSON используется для метаданных, а не для горячих массивов.

Уже есть typed-array worker paths для routing/evaluation и packed street graph.

Добавить: worker pool; 4-ary heap; memory accounting; GC profiling; route/matrix/demand/evaluation caches; batch ranges `[start,end)`; transferable buffers; reuseError; сквозная incremental invalidation.

Запрещено: object-heavy hot loops для крупных OD/матриц, повторная сериализация больших массивов и полный пересчёт неизменённых сегментов.

### Этап 13 — UI

Статус: 🔄 миграция

Целевая реализация: Vanilla TypeScript + Vite + ручной DOM + MapLibre GL JS.

Сделать: завершить удаление Svelte runtime/component layer; keyboard navigation; ARIA; Escape cancellation; inline SVG; responsive layout; единый composition/bootstrap entry.

Svelte не должен оставаться архитектурной зависимостью production runtime.

### Этап 14 — Сервер

Статус: 🔜

Python не является обязательным runtime-компонентом интерактивной симуляции.

Python: `Overture → pack builder → validation → city pack`.
Браузер: `city pack → IndexedDB → workers → simulation → editor → evaluation`.

API остаётся только для подготовки данных, импорта, batch processing, диагностики и share.
Серверная city/assignment/economics реализация не должна быть вторым runtime-путём для тех же алгоритмов.

### Этап 15 — Share / Import / Export

Статус: 🔜

Формат `.takt`: header; version; city; model; network; scenario; simulation state; compressed payload; sha256.

Сделать: gzip; checksum; version migration; import validation; export; share endpoint.

### Этап 16 — Production city packs

Статус: 🔜

Первый полноценный: Voronezh. Затем: Berlin; Amsterdam; Hong Kong; другие города.

Каждый город проходит: Overture → pack builder → validation → hash → browser → matrix → demand → mode choice → assignment → evaluation.

### Этап 17 — Полное удаление старого кода

Статус: 🔜

После миграции удалить:

- старые frontend profiles;
- старые modes;
- duplicate demand model;
- duplicate choice model;
- duplicate matrix;
- старые runtime adapters;
- все fallback/synthetic paths;
- API-only runtime calculations;
- Svelte-компоненты;
- устаревшие storage adapters;
- compatibility shims;
- неиспользуемые assets.

Критерий: каждый алгоритм имеет одну реализацию, один контракт и один источник данных.

### Этап 18 — Strict Runtime Gate

Статус: 🔜

Добавить автоматический release-gate:

- поиск `fallback`, `synthetic`, `default`, `??`, `||` и silent `continue` по runtime-коду с whitelist только для намеренно необязательных UI-настроек;
- проверка, что обязательный worker не заменяется синхронным вычислением;
- проверка, что обязательный city-pack слой не заменяется live API;
- проверка, что повреждённый pack не принимается;
- проверка, что release/version/hash mismatch является ошибкой;
- проверка отсутствия duplicate algorithm entry points;
- проверка отсутствия synthetic demand;
- проверка отсутствия прямолинейного route fallback;
- Vulture + Python tests + TypeScript build + production smoke test.

`No fallback` становится машинно проверяемым свойством проекта, а не только архитектурным соглашением.

### Итоговая архитектура

```
                    OVERTURE
                       │
                       ▼
                ┌──────────────┐
                │  Pack Builder│
                └──────┬───────┘
                       │
             ┌─────────▼─────────┐
             │     CITY PACK     │
             │ JSON + TKBL/bin   │
             └─────────┬─────────┘
                       │
                       ▼
                ┌──────────────┐
                │  IndexedDB   │
                └──────┬───────┘
                       │
          ┌────────────▼────────────┐
          │     VANILLA TS APP      │
          │                          │
          │       MapLibre           │
          │          │               │
          │      Network Editor      │
          └──────────┬───────────────┘
                     │
       ┌─────────────┼──────────────┐
       ▼             ▼              ▼
   Matrix         Routing        Demand
   Worker         Worker         Worker
       │             │              │
       └─────────────┼──────────────┘
                     ▼
                Mode Choice
                   Worker
                     │
                     ▼
              Evaluation Worker
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
        Load       Fleet      Economics
          │          │          │
          └──────────┼──────────┘
                     ▼
                CITY RESULT
```

Ключевое изменение: система строится без «основного пути + fallback». Один авторитетный вычислительный слой на каждую функцию; недоступные данные или worker — ошибка с понятным состоянием.

