"""Перевод устных дат в календарные.

Делается кодом, а не моделью: правило должно быть объяснимым и повторяемым.
Рядом с датой всегда сохраняется формулировка, которая прозвучала на приёме,
и правило, по которому дата получена.

Две задачи:
  future — повторная явка («через три дня», «в пятницу», «15 октября»)
  past   — события в прошлом, например дата последней менструации
"""

import re
from datetime import date, timedelta

MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "ма": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
    # казахские названия месяцев
    "қаңтар": 1, "ақпан": 2, "наурыз": 3, "сәуір": 4, "мамыр": 5, "маусым": 6,
    "шілде": 7, "тамыз": 8, "қыркүйек": 9, "қазан": 10, "қараша": 11, "желтоқсан": 12,
}
WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3,
    "пятниц": 4, "суббот": 5, "воскресень": 6,
}
# Порядок важен: «пятнадцатого» начинается с «пят», поэтому длинные основы раньше.
ORDINALS = [
    ("одиннадцат", 11), ("двенадцат", 12), ("тринадцат", 13), ("четырнадцат", 14),
    ("пятнадцат", 15), ("шестнадцат", 16), ("семнадцат", 17), ("восемнадцат", 18),
    ("девятнадцат", 19), ("двадцат", 20), ("тридцат", 30), ("десят", 10),
    ("перв", 1), ("втор", 2), ("трет", 3), ("четв", 4), ("пят", 5), ("шест", 6),
    ("седьм", 7), ("восьм", 8), ("девят", 9),
]
COUNTS = {
    "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "четырнадцать": 14, "двадцать": 20, "тридцать": 30,
}


# Казахские формулировки: врач может назвать срок по-казахски.
KK_COUNTS = {"бір": 1, "екі": 2, "үш": 3, "төрт": 4, "бес": 5, "алты": 6, "жеті": 7,
             "сегіз": 8, "тоғыз": 9, "он": 10}
# Порядок важен: «сенбі» входит в «дүйсенбі» и «жексенбі».
KK_WEEKDAYS = [("дүйсенбі", 0), ("сейсенбі", 1), ("сәрсенбі", 2), ("бейсенбі", 3),
               ("жексенбі", 6), ("жұма", 4), ("сенбі", 5)]


def _kk_count(s: str, unit: str) -> int | None:
    """«бір аптадан кейін», «3 күннен кейін» -> число единиц."""
    m = re.search(r"(\d+|[а-яәғқңөұүһі]+)\s+" + unit, s)
    if not m:
        return None
    word = m.group(1)
    return int(word) if word.isdigit() else KK_COUNTS.get(word)


def _kazakh_future(s: str, visit: date) -> dict | None:
    if "бүрсігүні" in s:
        return {"date": visit + timedelta(days=2), "rule": "послезавтра от дня приёма"}
    if "ертең" in s:
        return {"date": visit + timedelta(days=1), "rule": "завтра от дня приёма"}
    n = _kk_count(s, "апта")
    if n:
        return {"date": visit + timedelta(weeks=n), "rule": f"{n} нед. от дня приёма ({visit:%d.%m.%Y})"}
    n = _kk_count(s, "күн")
    if n:
        return {"date": visit + timedelta(days=n), "rule": f"{n} дн. от дня приёма ({visit:%d.%m.%Y})"}
    n = _kk_count(s, "ай")
    if n:
        return {"date": visit + timedelta(days=30 * n),
                "rule": f"{n} мес. от дня приёма, месяц считается за 30 дней"}
    for name, idx in KK_WEEKDAYS:
        if name in s:
            delta = (idx - visit.weekday()) % 7 or 7
            return {"date": visit + timedelta(days=delta),
                    "rule": "ближайший такой день недели после приёма"}
    return None


def _ordinal(word: str) -> int | None:
    for stem, num in ORDINALS:
        if word.startswith(stem):
            return num
    return None


def _month(word: str) -> int | None:
    for prefix, num in MONTHS.items():
        if word.startswith(prefix):
            # «ма» совпадает и с «март», и с «май»: март проверен раньше по «мар»
            return num
    return None


def _day_month(s: str) -> tuple[int, int, int | None] | None:
    """Ищет «15 октября», «пятнадцатого октября», «15.10», «15.10.2026»."""
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", s)
    if m:
        year = int(m.group(3)) if m.group(3) else None
        if year is not None and year < 100:
            year += 2000
        return int(m.group(1)), int(m.group(2)), year

    words = re.findall(r"[^\W_]+", s)
    for i, w in enumerate(words):
        mon = _month(w) if not w.isdigit() else None
        if mon is None or i == 0:
            continue
        prev = words[i - 1]
        day = None
        if prev.isdigit():
            day = int(prev)
        else:
            unit = _ordinal(prev)
            if unit is not None:
                day = unit
                # «двадцать пятого», «тридцать первого»
                if i >= 2 and unit < 10 and words[i - 2].startswith(("двадцат", "тридцат")):
                    day += 20 if words[i - 2].startswith("двадцат") else 30
        if day and 1 <= day <= 31:
            year = None
            if i + 1 < len(words) and re.fullmatch(r"\d{4}", words[i + 1]):
                year = int(words[i + 1])
            return day, mon, year
    return None


def _count(s: str, unit_stem: str) -> int | None:
    """«через 3 дня», «через две недели», «через неделю» -> число единиц."""
    m = re.search(r"(\d+)\s*" + unit_stem, s)
    if m:
        return int(m.group(1))
    m = re.search(r"([а-яё]+)\s+" + unit_stem, s)
    if m and m.group(1) in COUNTS:
        return COUNTS[m.group(1)]
    if re.search(r"\b" + unit_stem, s):
        return 1
    return None


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def resolve_future(raw: str, visit: date) -> dict:
    """Повторная явка: дата в будущем относительно дня приёма."""
    if not raw or not raw.strip():
        return {"date": None, "rule": "срок на приёме не назван"}
    s = raw.lower().strip()

    dm = _day_month(s)
    if dm:
        day, mon, year = dm
        d = _safe(year or visit.year, mon, day)
        if d and not year and d < visit:
            d = _safe(visit.year + 1, mon, day)
        if d:
            return {"date": d, "rule": "названа конкретная дата"}

    kk = _kazakh_future(s, visit)
    if kk:
        return kk

    if "послезавтра" in s:
        return {"date": visit + timedelta(days=2), "rule": "послезавтра от дня приёма"}
    if "завтра" in s:
        return {"date": visit + timedelta(days=1), "rule": "завтра от дня приёма"}

    n = _count(s, "недел")
    if n:
        return {"date": visit + timedelta(weeks=n),
                "rule": f"{n} нед. от дня приёма ({visit:%d.%m.%Y})"}
    n = _count(s, r"(?:дн|день|дня|сут)")
    if n:
        return {"date": visit + timedelta(days=n),
                "rule": f"{n} дн. от дня приёма ({visit:%d.%m.%Y})"}
    n = _count(s, "месяц")
    if n:
        return {"date": visit + timedelta(days=30 * n),
                "rule": f"{n} мес. от дня приёма, месяц считается за 30 дней"}

    for name, idx in WEEKDAYS.items():
        if name in s:
            delta = (idx - visit.weekday()) % 7 or 7
            return {"date": visit + timedelta(days=delta),
                    "rule": "ближайший такой день недели после приёма"}

    return {"date": None, "rule": f"формулировку «{raw}» не удалось перевести в дату"}


def resolve_past(raw: str, visit: date) -> dict:
    """Событие в прошлом: ближайшая такая дата не позже дня приёма."""
    if not raw or not raw.strip():
        return {"date": None, "rule": "дата на приёме не названа"}
    s = raw.lower().strip()

    dm = _day_month(s)
    if dm:
        day, mon, year = dm
        d = _safe(year or visit.year, mon, day)
        if d and not year and d > visit:
            d = _safe(visit.year - 1, mon, day)
        if d:
            return {"date": d, "rule": "названа конкретная дата"}

    n = _count(s, "недел")
    if n and "назад" in s:
        return {"date": visit - timedelta(weeks=n), "rule": f"{n} нед. назад от дня приёма"}
    n = _count(s, r"(?:дн|день|дня)")
    if n and "назад" in s:
        return {"date": visit - timedelta(days=n), "rule": f"{n} дн. назад от дня приёма"}

    return {"date": None, "rule": f"формулировку «{raw}» не удалось перевести в дату"}


if __name__ == "__main__":
    v = date(2026, 9, 30)
    for raw in ["через три дня", "в пятницу", "второго октября", "через две недели",
                "через неделю", "15.10", "завтра", "через месяц", "когда станет хуже",
                "бір аптадан кейін", "үш күннен кейін", "жұма күні", "дүйсенбіде", "ертең",
                "15 қазан"]:
        r = resolve_future(raw, v)
        print(f"{raw:<22} -> {r['date']}  {r['rule']}")
    for raw in ["десятого апреля", "10 апреля", "25.12", "двадцать пятого июля", "две недели назад"]:
        r = resolve_past(raw, v)
        print(f"{raw:<22} -> {r['date']}  {r['rule']}")
