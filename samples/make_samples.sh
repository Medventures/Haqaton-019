#!/bin/bash
# Тестовые записи приёмов: голос macOS читает сценарий.
# Нужны только для проверки цепочки, настоящие записи даёт врач.
set -e
cd "$(dirname "$0")"
for name in therapist_1 obgyn_1; do
  tmp=$(mktemp -d)
  i=0; : > "$tmp/list.txt"
  ffmpeg -y -f lavfi -i anullsrc=r=22050:cl=mono -t 0.7 "$tmp/gap.wav" -loglevel error
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    who="${line%%:*}"; text="${line#*: }"
    rate=178; [ "$who" = "П" ] && rate=165
    say -v Milena -r $rate -o "$tmp/$i.aiff" "$text"
    ffmpeg -y -i "$tmp/$i.aiff" -ar 22050 -ac 1 "$tmp/$i.wav" -loglevel error
    echo "file '$tmp/$i.wav'" >> "$tmp/list.txt"
    echo "file '$tmp/gap.wav'" >> "$tmp/list.txt"
    i=$((i+1))
  done < "$name.txt"
  ffmpeg -y -f concat -safe 0 -i "$tmp/list.txt" -c:a pcm_s16le "$name.wav" -loglevel error
  rm -rf "$tmp"
  echo "$name.wav: $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$name.wav") сек"
done
