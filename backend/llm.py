"""Обращение к локальной модели через Ollama.

Модель стоит на сервере клиники, наружу запросы не уходят. Каждое обращение
записывается в журнал приёма: на экране «Контур данных» врач видит, куда и что
отправлялось.
"""

import json
import os
import re
import threading
import time
from urllib.parse import urlparse

import requests

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "qwen3:4b")

# Для приёмов на казахском и смешанном языке можно задать отдельную модель:
# маленькая qwen3 плохо понимает казахский. Если модель не скачана, берётся основная.
MODEL_KK = os.environ.get("LLM_MODEL_KK", "gemma3:4b")

_local = threading.local()


def installed() -> list[str]:
    try:
        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3).json()
        return [m["name"] for m in tags.get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def kazakh_model() -> str:
    """Модель для перевода казахских реплик. Если отдельная не скачана, берётся основная."""
    return MODEL_KK if MODEL_KK and MODEL_KK in installed() else MODEL


def use(model: str | None) -> None:
    """Модель для текущего потока обработки (один приём — один поток)."""
    _local.model = model


def current() -> str:
    return getattr(_local, "model", None) or MODEL


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "ollama", "host.docker.internal"}


def is_local(url: str) -> bool:
    """Адрес внутри контура клиники: этот компьютер или частная сеть."""
    host = urlparse(url).hostname or ""
    if host in LOCAL_HOSTS:
        return True
    return host.startswith(("10.", "192.168.", "172.")) or "." not in host


def context_size(prompt: str, max_tokens: int) -> int:
    """Объём памяти модели под запрос. Короткий приём помещается в 8 тысяч
    токенов; для приёма на 15–20 минут окно увеличивается, иначе модель
    молча обрежет начало разговора. На русский и казахский текст уходит
    примерно один токен на два символа."""
    need = len(prompt) // 2 + max_tokens + 256
    for size in (8192, 16384, 32768):
        if need <= size:
            return size
    return 65536


class ExternalModelBlocked(RuntimeError):
    pass


def guard() -> None:
    """Запрет внешних адресов. Модель должна стоять в контуре клиники: на этом
    компьютере или в частной сети. Адрес в интернете отклоняется до отправки
    текста, даже если его по ошибке прописали в настройках."""
    if not is_local(OLLAMA_URL):
        raise ExternalModelBlocked(
            f"Адрес модели {OLLAMA_URL} находится вне сети клиники. Запрос не отправлен.")


def ask_json(prompt: str, schema: dict, purpose: str, log: list | None = None,
             max_tokens: int = 700, timeout: int = 300, model: str | None = None) -> dict:
    """Запрос с ответом строго по схеме JSON. Одна повторная попытка:
    маленькая модель иногда сбивается."""
    guard()
    last_error = None
    for attempt in range(2):
        t0 = time.time()
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model or current(),
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "options": {"temperature": 0.1,
                                "num_ctx": context_size(prompt, max_tokens),
                                "num_predict": max_tokens},
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = resp.json()["message"]["content"]
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
            data = json.loads(raw)
            if log is not None:
                log.append({
                    "to": OLLAMA_URL, "local": is_local(OLLAMA_URL), "model": model or current(),
                    "purpose": purpose, "chars": len(prompt),
                    "seconds": round(time.time() - t0, 1),
                })
            return data
        except Exception as e:  # noqa: BLE001
            last_error = e
    raise RuntimeError(f"Модель не ответила ({purpose}): {last_error}")


def available() -> dict:
    try:
        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3).json()
        names = [m["name"] for m in tags.get("models", [])]
        return {"ok": MODEL in names, "model": MODEL, "installed": names,
                "url": OLLAMA_URL, "local": is_local(OLLAMA_URL)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "model": MODEL, "error": str(e), "url": OLLAMA_URL,
                "local": is_local(OLLAMA_URL)}
