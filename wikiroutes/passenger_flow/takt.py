"""Совместимый реэкспорт констант Takt (старый путь импорта).

Фактическое определение — ``.base.takt``; данный модуль сохранён, чтобы
внешние импорты ``passenger_flow.takt`` (config, od, тесты) продолжали
работать. Всё пространство имён базового модуля (включая служебные
``_TAKT_*``) зеркалируется сюда.
"""

from __future__ import annotations

from .base import takt as _takt

globals().update({k: v for k, v in vars(_takt).items() if not k.startswith("__")})