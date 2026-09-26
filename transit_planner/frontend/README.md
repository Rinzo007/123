# Transit Planner Web

Веб-редактор транспортной сети с Overture Maps как источником городских данных.

## Источник данных

Рабочий поток использует Overture:

- дороги: Transportation / Segment;
- топологические узлы: Transportation / Connector;
- остановки и станции: Base / Infrastructure, subtype=transit.

Карта получает GeoJSON через FastAPI. Тяжёлые выборки выполняются DuckDB напрямую по Parquet и фильтруются по текущей области карты.

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

## Возможности

- карта MapLibre;
- загрузка Overture для текущего окна карты;
- отображение дорожных сегментов;
- отображение Overture-коннекторов;
- отображение транспортных остановок;
- добавление собственных остановок;
- выбор вида транспорта;
- настройка интервала;
- проверка сети;
- экспорт сети в JSON.

Источник стиля карты можно переопределить через VITE_MAP_STYLE_URL.
