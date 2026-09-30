"""Загрузка клинического протокола МЗ РК в библиотеку Хатшы.

По ссылке на страницу протокола в справочной системе MedElement скрипт вырезает
раздел диагностики амбулаторного уровня и сохраняет его файлом в backend/protocols.
Модель не используется: работают только правила разбора текста.

Результат всегда черновик (verified_by_doctor: false, auto_imported: true):
перечень должен сверить врач. Протоколы с пометкой «Утратил силу» не загружаются.

Запуск из корня проекта:
  .venv/bin/python scripts/import_protocol.py <ссылка> [<ссылка> ...]
  .venv/bin/python scripts/import_protocol.py --show <ссылка>     только показать, не сохранять
"""

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "backend" / "protocols"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36", "Accept-Language": "ru"}

BULLET = re.compile(r"^\s*(?:[·•−–\-*▪●]|\d{1,2}[.)])\s*")
# Разделы, из которых берём обследования. Порядок важен: первое совпадение выигрывает.
HEADS = [
    ("labs", "Лабораторные исследования", r"лабораторн\w+ (?:исследовани|обследовани)\w*"),
    ("instr", "Инструментальные исследования", r"инструментальн\w+ (?:исследовани|обследовани)\w*"),
    ("main", "Основные диагностические обследования",
     r"основные \(обязательные\) диагностические обследования[^\n]*амбулаторн|перечень основных диагностических мероприятий"),
    ("extra", "Дополнительные диагностические обследования",
     r"дополнительные диагностические обследования[^\n]*амбулаторн|перечень дополнительных диагностических мероприятий"),
    ("list", "Перечень диагностических мероприятий", r"перечень основных и дополнительных диагностических мероприятий"),
]
# Частые обследования: по каким словам понять, что оно уже есть в листе.
KNOWN = [
    (r"\bоак\b|общий анализ крови|клинический анализ крови", ["анализ крови", "оак"]),
    (r"\bоам\b|общий анализ мочи|анализ мочи", ["анализ мочи", "оам"]),
    (r"\bэкг\b|электрокардиог", ["экг", "электрокардиог"]),
    (r"\bэхокг\b|эхокардиог", ["эхокг", "эхокардиог"]),
    (r"\bузи\b|ультразвук", ["узи", "ультразвук"]),
    (r"рентген", ["рентген"]),
    (r"флюорограф", ["флюорограф"]),
    (r"\bкт\b|компьютерная томограф", ["кт", "компьютерная томограф"]),
    (r"\bмрт\b|магнитно", ["мрт", "магнитно"]),
    (r"глюкоз|сахар", ["глюкоз", "сахар"]),
    (r"биохими", ["биохими"]),
    (r"мазок", ["мазок"]),
    (r"\bпцр\b", ["пцр"]),
    (r"\bифа\b", ["ифа"]),
    (r"креатинин", ["креатинин"]),
    (r"холестерин|липид", ["холестерин", "липид"]),
    (r"пульсоксиметр|сатурац", ["сатурац", "spo2", "пульсоксиметр"]),
    (r"спирометр|спирограф", ["спиро"]),
    (r"фарингоскоп", ["фарингоскоп", "миндалин"]),
    (r"измерение ад|артериального давления|\bсмад\b", ["ад", "давлени", "смад"]),
]


def fetch(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    safe = urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(urllib.parse.unquote(p.path)),
                                    p.query, ""))
    raw = urllib.request.urlopen(urllib.request.Request(safe, headers=UA), timeout=40).read()
    return raw.decode("utf-8", "ignore")


def to_text(raw: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>|</td>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t\xa0]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t)


def header(text: str) -> dict:
    i = text.find("Версия:")
    lines = text[:i].strip().split("\n")
    title = lines[-1].strip()
    version = re.sub(r"\s*\(Казахстан\)", "", text[i + 7:text.find("\n", i)].strip()).replace(" - ", ", ")
    block = text[i:text.find("Разделы медицины", i)]
    icd = sorted(set(re.findall(r"\(([A-Z]\d{2}(?:\.\d{1,2})?)\)", block)))
    # рубрика покрывает свои подрубрики: J20 и J20.9 -> достаточно J20
    icd = [c for c in icd if not any(c != o and c.startswith(o) for o in icd)]
    approved = ""
    m = re.search(r"((?:Одобрен|Утвержден|Рекомендован)[^\n]*(?:\n[^\n]*){1,5})", text[i:i + 3000])
    if m:
        chunk = re.sub(r"\s+", " ", m.group(1))
        date = re.search(r"от\s*[«\"]?(\d{1,2})[»\"]?\s*([а-яё]+)\s*(\d{4})|(\d{1,2}\.\d{1,2}\.\d{4})", chunk)
        num = re.search(r"(?:Протокол\s*)?№\s*(\d+)", chunk)
        body = "Объединённой комиссией по качеству медицинских услуг" if "комисси" in chunk and "качеств" in chunk \
            else "Экспертной комиссией по вопросам развития здравоохранения" if "Экспертн" in chunk else ""
        when = ""
        if date:
            when = date.group(4) or f"{date.group(1)} {date.group(2)} {date.group(3)}"
        approved = " ".join(x for x in ("одобрен", body, when, f"протокол №{num.group(1)}" if num else "") if x)
    archived = "Утратил силу" in text[max(0, i - 400):i + 400]
    return {"title": title, "version": version, "icd": icd, "approved": approved, "archived": archived}


def ambulatory(text: str) -> str:
    """Текст раздела диагностики амбулаторного уровня."""
    start = re.search(r"Диагностика \(амбулатория\)|ДИАГНОСТИКА НА АМБУЛАТОРНОМ УРОВНЕ", text)
    if not start:
        start = re.search(r"\n ?Диагностика ?\n|Перечень основных и дополнительных диагностических мероприятий", text)
    if not start:
        return ""
    rest = text[start.start():]
    end = re.search(r"\n ?(?:Диагностика \(стационар\)|Диагностика \(скорая помощь\)|Дифференциальный диагноз|"
                    r"Лечение \(амбулатория\)|Лечение ?\n)", rest[30:])
    return rest[:end.start() + 30] if end else rest[:12000]


def _item(line: str) -> dict | None:
    text = BULLET.sub("", line).strip().rstrip(";.").strip()
    if len(text) < 2:
        return None
    note = ""
    m = re.match(r"(.{2,90}?)\s*(?:[–—:]| - |\()\s*(.+)$", text)
    if m and len(m.group(1)) >= 2:
        text, note = m.group(1).strip(), m.group(2).strip(" ).;")
    if len(text) > 110:
        text, note = text[:110].rsplit(" ", 1)[0], (text[110:] + " " + note).strip()
    low = (text + " " + note[:60]).lower()
    keywords: list[str] = []
    for pattern, words in KNOWN:
        if re.search(pattern, low):
            keywords += words
    if not keywords:
        stems = [w[:7].lower() for w in re.findall(r"[^\W\d_]{5,}", text)][:2]
        abbr = [w.lower() for w in re.findall(r"\b[А-ЯЁA-Z]{2,6}\b", text)]
        keywords = abbr + stems
    out = {"text": text[0].upper() + text[1:], "keywords": list(dict.fromkeys(keywords))}
    if note:
        out["note"] = note[:170] + ("…" if len(note) > 170 else "")
    return out


def sections(block: str) -> list[dict]:
    lines = block.split("\n")
    out: list[dict] = []
    seen_keys: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        hit = None
        if len(line) < 160 and not BULLET.match(line):
            for key, title, pattern in HEADS:
                if re.match(pattern, line.lower()):
                    hit = (key, title)
                    break
        if not hit or hit[0] in seen_keys:
            i += 1
            continue
        items = []
        tail = line.split(":", 1)[1].strip() if ":" in line else ""
        if tail and len(tail) > 3 and not re.match(r"нет\b", tail.lower()):
            # перечень в одну строку после двоеточия
            for part in re.split(r";\s*", tail):
                it = _item(part)
                if it:
                    items.append(it)
        j = i + 1
        while j < len(lines):
            cur = lines[j].strip()
            if not cur:
                j += 1
                continue
            if BULLET.match(cur):
                it = _item(cur)
                if it:
                    items.append(it)
                j += 1
                continue
            # вводные строки вроде «при остром тонзиллите:» пропускаем, остальное — конец перечня
            if items or not cur.endswith(":"):
                break
            j += 1
        if items:
            out.append({"key": hit[0], "title": hit[1], "items": items[:25]})
            seen_keys.add(hit[0])
        i = j
    return out


def slug(title: str, version: str) -> str:
    table = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                          "abvgdeezzijklmnoprstufhccss_y_eua")
    year = (re.search(r"\d{4}", version) or [""])[0]
    base = re.sub(r"[^a-z0-9]+", "_", title.lower().translate(table)).strip("_")[:50]
    return f"{base}_{year}" if year else base


def build(url: str) -> dict:
    text = to_text(fetch(url))
    head = header(text)
    return {
        "title": head["title"], "version": head["version"], "approved": head["approved"],
        "source": urllib.parse.unquote(url), "icd": head["icd"],
        "verified_by_doctor": False, "auto_imported": True, "archived": head["archived"],
        "_comment": "Загружено автоматически из раздела диагностики амбулаторного уровня. Сверяет врач.",
        "sections": sections(ambulatory(text)),
    }


def main() -> None:
    args = sys.argv[1:]
    show = "--show" in args
    urls = [a for a in args if not a.startswith("--")]
    for n, url in enumerate(urls):
        if n:
            time.sleep(1.5)     # не нагружаем справочную систему
        try:
            p = build(url)
        except Exception as e:  # noqa: BLE001
            print(f"ОШИБКА {url}: {e}")
            continue
        count = sum(len(s["items"]) for s in p["sections"])
        print(f"\n{p['title']} | {p['version']} | {p['approved']} | МКБ: {', '.join(p['icd'][:8])}"
              f"{' …' if len(p['icd']) > 8 else ''}")
        for s in p["sections"]:
            print(f"  {s['title']}:")
            for it in s["items"]:
                print(f"    - {it['text']}" + (f" ({it['note'][:70]})" if it.get("note") else ""))
        if p.pop("archived"):
            print("  ПРОПУЩЕН: протокол утратил силу")
            continue
        if not count:
            print("  ПРОПУЩЕН: не удалось найти перечень обследований, нужен ручной разбор")
            continue
        if not show:
            path = OUT / f"{slug(p['title'], p['version'])}.json"
            path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  сохранён: {path.relative_to(ROOT)} ({count} пунктов)")


if __name__ == "__main__":
    main()
