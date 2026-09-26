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
- физические узлы дорожной сети: Transportation / Connector — следующий слой маршрутизации;
- остановки и станции: Base / Infrastructure с subtype=transit.

В текущем релизе Overture 2026-09-23.0 был выпущен schema v2.0.0; затем Overture опубликовал patch 2026-09-23.1. В приложении по умолчанию используется patch 2026-09-23.1. citeturn289065search0turn289065search1turn889581view0

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
