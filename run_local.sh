#!/bin/bash
# Запуск на этом компьютере без Docker: так работает ускорение распознавания речи на Mac.
# Модель: ollama serve (если ещё не запущен) и ollama pull qwen3:4b
cd "$(dirname "$0")"
pgrep -x ollama >/dev/null || (ollama serve >/tmp/hatshy_ollama.log 2>&1 &)
(cd mis && ../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8100 >/tmp/hatshy_mis.log 2>&1 &)
cd backend && exec ../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
