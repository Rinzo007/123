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

Функции build_city_demand и build_city_temporal_demand принимают CityDemandConfig с параметрами trip_rate, decay и reference_speed_kph. В API те же параметры доступны через trip_rate, decay, reference_speed_kph, а для городского назначения — внутри demand_config.

При выборе остановки глобальный предел доступа дополнительно ограничивается нормативом access_m соответствующего обслуживающего режима. Для экономики учитывается требуемое число транспортных средств по времени кругового оборота, интервалу и режимным значениям времени стоянки и оборота.


## Веб-редактор

Frontend построен на Vanilla TypeScript + Vite/Rollup + MapLibre GL JS 5.24.0. Состояние сети и проекта хранится непосредственно в runtime, проекты и кэш Overture хранятся в IndexedDB, настройки интерфейса — в localStorage.

Архитектура интерфейса ориентирована на предоставленный референс: один entry `src/main.ts` → `src/app.ts`, ручной DOM, MapLibre, reference worker runtime (matrix/routing/demand-choice/evaluation) и бинарный TLC1-кэш дорожной геометрии. Манифесты city packs с SHA-256 и TKBL-контейнеры — цель Этапа 2; битые заглушки предыдущих попыток удалены на Этапе 1.


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

Расчётная часть использует существующий backend и каноническую модель спроса; frontend не дублирует алгоритмы assignment.

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
│   ├── reference-runtime.ts
│   ├── evaluation-runtime.js
│   ├── evaluation.worker.ts
│   ├── matrix.worker.ts
│   ├── demand-choice.worker.ts
│   ├── routing.ts
│   └── routing.worker.ts
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

Статус: ✅ завершён (`8ad8352`, `94782a0`)

Правило после этапа: если обязательный источник/расчёт недоступен — выдаётся явная ошибка. Никакого молчаливого перехода на упрощённую модель.

Frontend:

- fallback маршрутизации: подмена Overture-маршрута прямой линией удалена (`routeGeoJSON` → `emptyRouteGeoJSON`), ошибка при недоступности расчёта;
- synthetic demand: стаб `toDemandInput` (плоские 1000 поездок) удалён, `runRuntimePreview` требует authoritative OD и бросает явную ошибку;
- fallback evaluation: `runClientPreview` без воркера переименован в честный `networkCounts`, reference-модель `/data/model.json` больше не деградирует в `{}`;
- fallback сборки: хеш-бандл `/assets/evaluation.worker-jRuvxHc_.js` заменён статическим импортом `evaluation-runtime.js` (Vite переименовывает чанк сам);
- дублирующие TS-реализации: удалены 15 мёртвых модулей (~1000 строк), включая `rraptor.ts` с битым импортом, дубли валидаторов, редакторов и кэшей;
- worker-контракты: `matrix/routing/demand-choice` workers изолированы (`export {}`), устранены глобальные коллизии;
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

Статус: 🔜

Overture streets → street graph → compressed binary graph → routing worker.

Сделать: узлы; рёбра; классы дорог; ограничения скорости; направления; длины; геометрию; snapping остановок; snapping маршрутов; disconnected-component detection.

### Этап 4 — Matrix Worker

Статус: 🔄 начат

Ядро уже есть: `matrix.worker.ts` — Dijkstra на typed arrays, 4-ary heap, пакет `[start,end)`, transferable ArrayBuffer, отмена по job-номеру.

Осталось: кэширование матриц; инвалидация по версии street graph; вход матрицы строго через ArrayBuffer (без post-структур); transferable-возврат previous-массива в main.

### Этап 5 — Routing Worker

Статус: 🔄 начат

Ядро уже есть: `routing.worker.ts` — RAPTOR поверх packed typed arrays (route patterns, departures, transfers, access/egress), transferable buffers.

Осталось: stop→stop поверх уличного графа Этапа 3; transit path и route geometry в результатах; time-dependent routing; route cache; incremental invalidation.

### Этап 6 — Demand Worker

Статус: 🔄 частично готов

Уже перенесено: commuter demand; purpose layers; Overture places; purpose generators; периоды; WGS84 точки; reference generator En().

Осталось: полностью перенести typed-array представление; убрать Python demand как runtime dependency; передавать OD через ArrayBuffer; реализовать полный reference demand pipeline в worker; synthetic/резервные demand paths удалены на Этапе 1.

### Этап 7 — Mode Choice

Статус: 🔜

Перенести основной nested logit: car; transit; walk; bike / two-wheel.

Сделать: инкрементальный nested logit; frequency-based insertion (референс: `demand-choice.worker.ts`); per-mode generalized cost; mobility constraints; typed-array output; incremental recalculation.

### Этап 8 — Network Evaluation Worker

Статус: 🔄 частично готов

Reference evaluation живёт в `evaluation-runtime.js` (init/run/cancel, epoch, baseline T, layers) и вызывается только через `EvaluationClient` — worker недоступен/не готов → явная ошибка.

Добавить: reuseError; trackId + segment invalidation; incremental evaluation; planningPreview поверх worker-результатов; полный расчёт network → segment loads → PLF → fleet → headway → CAPEX/OPEX → city result; probe(base, candidate).

### Этап 9 — Urban Context

Статус: 🔄 начат

Уже сделано: Overture buildings; Overture water; UrbanContext; segment multipliers; подключение к CAPEX; city assignment metadata.

Дальше: builtUp; waterShare; roofShare; buildingsHit; buildingRings; влияние на travel time; влияние на construction cost; сохранение в city pack; убрать runtime-загрузку urban data для уже собранного pack.

### Этап 10 — IndexedDB

Статус: 🔄 частично готов

Есть: база `takt/kv`, gzip через CompressionStream, writer-лок на localStorage, TTL-записи, atomic save/delete.

Сделать: writer queue; begin()/commit(); active promise pool; ABA-safe writes; reconciliation; version prefixes; size limit; atomic restore; cross-tab invalidation.

### Этап 11 — Editor

Статус: 🔄 частично готов

Есть: карта; выбор объекта; панель свойств; узлы/участки/nodes; crossovers и signal blocks; split/merge; undo/redo; пересчёт только затронутых сегментов (segment signatures).

Сделать: создание маршрута и изменение трассы мышью; добавление/удаление остановок в каноническом редакторе; изменение режима и пути; snapping к уличному графу (после Этапа 3).

### Этап 12 — Производительность

Статус: 🔄 начат

Принцип: NO JSON→Worker→JSON; YES ArrayBuffer→Worker→ArrayBuffer. already выполнено для matrix/routing/demand/evaluation.

Добавить: worker pool для routing; memory accounting; GC profiling; route cache; matrix cache; demand cache; evaluation cache (есть одно-slot кэш run); сквозная incremental invalidation.

### Этап 13 — UI

Статус: 🔄 частично готов

Есть: Vanilla TS, один entry, ручной DOM, MapLibre 5.24, inline SVG-иконки, responsive layout, быстрый preview.

Сделать: keyboard navigation; ARIA; Escape cancellation; финальный отказ от Svelte как архитектурной зависимости.

### Этап 14 — Сервер

Статус: 🔜

Backend перестаёт быть обязательным для интерактивной симуляции.

Python: Overture → pack builder → city pack.
Браузер: city pack → simulation → editor → evaluation.

API остаётся для: подготовки данных; импорта; batch processing; диагностики; share API.

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

После миграции удалить: старые frontend profiles; старые modes; duplicate demand model; duplicate choice model; duplicate matrix; старые runtime adapters; fallback paths; synthetic fallback demand (frontend-часть удалена на Этапе 1); старые API-only расчёты; Svelte-компоненты; устаревшие storage adapters.

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

