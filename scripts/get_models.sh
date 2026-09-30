#!/bin/bash
# Скачивает модель распознавания речи (1,6 ГБ) в папку models.
set -e
cd "$(dirname "$0")/.."
mkdir -p models
if [ ! -f models/ggml-large-v3-turbo.bin ]; then
  curl -L --fail -o models/ggml-large-v3-turbo.bin \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
fi
ls -lh models/ggml-large-v3-turbo.bin
