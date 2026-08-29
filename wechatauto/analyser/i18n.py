# -*- coding: utf-8 -*-
"""Мультиязычная поддержка UI-текстов.

Входные данные могут быть на любом языке, но результаты анализа
выводятся ТОЛЬКО на русском языке.
"""

UI_TEXTS = {
    "ru": {
        "btn_analys": "Анализ",
        "btn_analys_loading": "Анализ...",
        "btn_meta_analys": "Анализ анализов",
        "btn_meta_analys_loading": "Анализ анализов...",
        "btn_show_chat": "Переписка",
        "btn_show_analys": "Анализ",
        "has_new_messages": "Есть новые сообщения",
        "analys_title": "Анализ чата",
        "meta_analys_title": "Анализ всех анализов",
        "analys_error": "Ошибка анализа",
        "analys_empty": "Анализ пока не выполнен",
        "analys_history": "История анализов",
        "loading": "Загрузка...",
        "no_analys_yet": "Анализов пока нет",
        "chat_link": "Перейти к чату",
        "msg_link": "Сообщение",
    },
}


def get_text(key: str, lang: str = "ru") -> str:
    """Получить локализованный текст по ключу."""
    return UI_TEXTS.get(lang, UI_TEXTS["ru"]).get(key, key)