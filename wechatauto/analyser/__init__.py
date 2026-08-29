# -*- coding: utf-8 -*-
"""Модуль ИИ-анализа чатов.

Публичные функции:
    - run_analys(username) — анализ одного чата
    - run_meta_analys() — анализ всех анализов
    - get_analys_status(username) — статус анализа
    - get_chat_analys(username) — получение анализа чата
"""

from .core import run_analys, run_meta_analys, get_analys_status, get_chat_analys, get_meta_analyses_history

__all__ = [
    "run_analys",
    "run_meta_analys",
    "get_analys_status",
    "get_chat_analys",
    "get_meta_analyses_history",
]