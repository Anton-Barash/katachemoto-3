# -*- coding: utf-8 -*-
"""HTTP-клиент к Doubao API (Volcano Engine, OpenAI-compatible)."""

import os
import logging
import requests
from typing import Optional

DEFAULT_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
DEFAULT_MODEL = "doubao-seed-2-1-pro-260628"

logger = logging.getLogger(__name__)


def _get_api_key() -> Optional[str]:
    """Получить API-ключ из переменной окружения."""
    return os.getenv("ARK_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")


def _get_api_url() -> str:
    return os.getenv("LLM_API_URL", DEFAULT_API_URL)


def _get_model() -> str:
    return os.getenv("LLM_MODEL", DEFAULT_MODEL)


def chat_completion(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> str:
    """Отправить запрос к LLM и получить текстовый ответ.

    Args:
        system_prompt: Системный промпт.
        user_prompt: Пользовательский запрос.
        temperature: Температура генерации.
        max_tokens: Максимум токенов в ответе.

    Returns:
        Текст ответа от LLM.

    Raises:
        RuntimeError: Если API недоступен или вернул ошибку.
    """
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "LLM API key not found. Set ARK_API_KEY, DEEPSEEK_API_KEY or LLM_API_KEY in .env"
        )

    url = _get_api_url()
    model = _get_model()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.info("LLM request: model=%s, url=%s", model, url)

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        logger.info("LLM response received (%d chars)", len(content))
        return content.strip()
    except requests.exceptions.Timeout:
        raise RuntimeError("LLM API timeout after 120s")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"LLM API error: {e}")
    except (KeyError, IndexError, ValueError) as e:
        raise RuntimeError(f"LLM API unexpected response: {e}")