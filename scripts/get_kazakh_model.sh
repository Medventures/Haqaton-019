#!/bin/bash
# Казахская модель распознавания речи: whisper large-v3-turbo, дообученный на корпусе
# KSC2 (ISSAI, около 1000 часов казахской речи). Автор модели: abilmansplus, лицензия MIT.
# Скрипт скачивает её с Hugging Face (3,1 ГБ) и переводит в формат whisper.cpp (1,5 ГБ).
# Нужны: python с пакетами torch, transformers, numpy, safetensors.
set -e
cd "$(dirname "$0")/.."
OUT=models/ggml-whisper-turbo-ksc2.bin
[ -f "$OUT" ] && { echo "уже есть: $OUT"; exit 0; }
TMP=$(mktemp -d)
SRC="$TMP/model"; mkdir -p "$SRC" "$TMP/whisper/whisper/assets" "$TMP/out"
BASE=https://huggingface.co/abilmansplus/whisper-turbo-ksc2/resolve/main
for f in config.json vocab.json added_tokens.json model.safetensors; do
  curl -L --fail -o "$SRC/$f" "$BASE/$f"
done
curl -sL --fail -o "$TMP/convert.py" https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/models/convert-h5-to-ggml.py
curl -sL --fail -o "$TMP/whisper/whisper/assets/mel_filters.npz" https://raw.githubusercontent.com/openai/whisper/main/whisper/assets/mel_filters.npz
python3 "$TMP/convert.py" "$SRC" "$TMP/whisper" "$TMP/out"
mv "$TMP/out/ggml-model.bin" "$OUT"
rm -rf "$TMP"
ls -lh "$OUT"
