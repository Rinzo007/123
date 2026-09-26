# Transit Planner Web

Веб-редактор транспортной сети с Overture Maps как источником городских данных.

## Стек

- React 19.3
- TypeScript 7.0
- Vite 8.3
- MapLibre GL JS 6.11
- Overture Maps Transportation + Base Infrastructure

## Источник данных

Рабочий поток использует Overture:

- дороги: Transportation / Segment;
- физические узлы дорожной сети: Transportation / Connector;
- остановки и станции: Base / Infrastructure, subtype=transit.

Для транспорта используются темы Overture напрямую. Карта получает GeoJSON через FastAPI, а тяжёлые выборки выполняются через DuckDB.

По умолчанию используется релиз Overture 2026-09-23.1. Релизы Overture распространяются по AWS и Azure; версия релиза может быть переопределена на API-уровне.

## Запуск

```
cd transit_planner/frontend
npm install
npm run dev
```

Backend:

```
cd transit_planner
pip install -e ".[api,test]"
uvicorn transit_planner.api:app --reload
```

Vite проксирует /api на локальный FastAPI.

## Возможности

- карта MapLibre;
- загрузка Overture для текущего окна карты;
- отображение дорог и транспортных остановок из Overture;
- добавление собственных остановок кликами;
- выбор вида транспорта;
- настройка интервала;
- проверка сети;
- экспорт текущей сети в JSON.

Источник стиля карты можно переопределить через VITE_MAP_STYLE_URL.
