# Transit Planner Web

Веб-редактор транспортной сети для нового независимого ядра.

## Стек
- React 19.3
- TypeScript 7.0
- Vite 8.3
- MapLibre GL JS 6.11

Версии зависимостей зафиксированы в package.json.

## Запуск
cd transit_planner/frontend
npm install
npm run dev

Backend:
cd transit_planner
pip install -e ".[api,test]"
uvicorn transit_planner.api:app --reload

Vite проксирует /api на локальный FastAPI.

## Возможности
- карта MapLibre;
- добавление остановок кликами;
- редактирование имени маршрута;
- выбор вида транспорта;
- настройка интервала;
- список остановок;
- проверка сети через FastAPI;
- экспорт текущей сети в JSON.

Источник стиля карты можно переопределить через VITE_MAP_STYLE_URL.