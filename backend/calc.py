"""Расчёты и проверки, которые делает код, а не модель.

Модель понимает текст, но в числах и датах ошибается. Поэтому показатели
приводятся к единому виду и проверяются правилами, сроки беременности
считаются по формуле, код МКБ-10 берётся из справочника, а назначения
сверяются с аллергиями по таблице.
"""

import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path

from rapidfuzz import fuzz, process

REF = Path(__file__).parent / "refdata"


# --- Показатели ------------------------------------------------------------------

def number(raw: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", raw or "")
    return float(m.group(0).replace(",", ".")) if m else None


def norm_bp(raw: str) -> str:
    """«120 на 80», «120-80», «120\\80» -> «120/80»."""
    m = re.search(r"(\d{2,3})\s*(?:/|\\|на|-|–|и)\s*(\d{2,3})", raw or "")
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def norm_vital(item: dict, raw: str) -> tuple[str, str | None]:
    """Приводит показатель к виду для листа. Возвращает (значение, замечание)."""
    raw = (raw or "").strip()
    if not raw:
        return "", None
    if item.get("type") == "bp":
        value = norm_bp(raw)
        if not value:
            return raw, "не удалось разобрать давление, проверьте"
        sys_, dia = (int(x) for x in value.split("/"))
        if not (60 <= sys_ <= 260 and 30 <= dia <= 160 and sys_ > dia):
            return value, "необычное значение давления, проверьте"
        return value, None
    num = number(raw)
    if num is None:
        return raw, "не удалось разобрать число, проверьте"
    value = f"{num:g}".replace(".", ",")
    lo, hi = item.get("min"), item.get("max")
    if (lo is not None and num < lo) or (hi is not None and num > hi):
        return value, "значение вне обычных границ, проверьте"
    return value, None


def heard(value: str, transcript: str) -> bool:
    """Все числа из значения должны встречаться в тексте разговора.

    Защита от выдумок: если модель написала число, которого никто не произносил,
    поле помечается для проверки."""
    nums = re.findall(r"\d+(?:[.,]\d+)?", value or "")
    text = transcript.replace(",", ".")
    return all(n.replace(",", ".") in text for n in nums)


# --- Беременность ----------------------------------------------------------------

def pregnancy(lmp: date, visit: date) -> dict | None:
    """Срок и предполагаемая дата родов по первому дню последней менструации.

    Правило Негеле: дата родов = первый день последней менструации + 280 дней.
    Срок = число полных недель и дней от этой даты до дня приёма.
    """
    days = (visit - lmp).days
    if not 0 < days <= 310:
        return None
    return {
        "weeks": days // 7,
        "days": days % 7,
        "edd": (lmp + timedelta(days=280)).isoformat(),
        "rule": "по дате последней менструации: срок считается от неё, "
                "дата родов = она плюс 280 дней",
    }


# --- МКБ-10 ----------------------------------------------------------------------

_icd: list[tuple[str, str]] = []


def _load_icd() -> list[tuple[str, str]]:
    if not _icd:
        with open(REF / "mkb10.csv", encoding="utf-8") as f:
            _icd.extend((code, name) for code, name in csv.reader(f, delimiter=";"))
    return _icd


def icd_search(query: str, limit: int = 6) -> list[dict]:
    """Поиск кода по тексту диагноза или по самому коду."""
    query = (query or "").strip()
    if not query:
        return []
    rows = _load_icd()
    m = re.match(r"^[A-Za-zА-Яа-я]\d{2}(?:\.\d{0,2})?$", query)
    if m:
        q = query.upper()
        return [{"code": c, "name": n, "score": 100} for c, n in rows if c.startswith(q)][:limit]

    names = [n.lower() for _, n in rows]
    hits = process.extract(query.lower(), names, scorer=fuzz.WRatio, limit=limit * 3)
    out = []
    for _name, score, idx in hits:
        code, name = rows[idx]
        # при равном сходстве короткое название ближе к тому, что сказал врач
        out.append({"code": code, "name": name, "score": round(score - len(name) / 200, 1)})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def _icd_name(code: str) -> str:
    return next((n for c, n in _load_icd() if c == code), "")


def _words(text: str) -> list[str]:
    """Значимые слова диагноза: без чисел, предлогов и слова «недель»."""
    return [w[:5] for w in re.findall(r"[^\W\d_]+", text.lower())
            if len(w) > 3 and not w.startswith("недел")]


def icd_pick(diagnosis: str) -> dict:
    """Код для диагноза и варианты на выбор.

    Порядок такой:
    1. Таблица соответствий, проверенная врачом (refdata/icd_aliases.json).
    2. Поиск по справочнику. Код подставляется, только если в названии есть
       все значимые слова диагноза. Иначе код остаётся пустым, врач выбирает
       из вариантов: лучше пустое поле, чем правдоподобная ошибка.
    Если совпала трёхзначная рубрика (J03), предлагается её подрубрика
    «неуточнённый» (J03.9): уточнить возбудителя из разговора нельзя.
    """
    text = (diagnosis or "").lower()
    if not text.strip():
        return {"code": "", "name": "", "options": [], "sure": False}

    for alias in _ref("icd_aliases.json")["aliases"]:
        if all(m in text for m in alias["match"]):
            options = [{"code": c, "name": _icd_name(c), "score": 100} for c in alias["codes"]]
            return {"code": options[0]["code"], "name": options[0]["name"],
                    "options": options, "sure": len(options) == 1, "source": "таблица соответствий"}

    options = icd_search(diagnosis)
    if not options:
        return {"code": "", "name": "", "options": [], "sure": False}
    best = options[0]
    name = best["name"].lower()
    if not all(w in name for w in _words(text)):
        return {"code": "", "name": "", "options": options, "sure": False}
    if "." not in best["code"]:
        child = _icd_name(best["code"] + ".9")
        if child:
            best = {"code": best["code"] + ".9", "name": child}
    return {"code": best["code"], "name": best["name"], "options": options,
            "sure": True, "source": "справочник МКБ-10"}


# --- Проверка назначений ---------------------------------------------------------

def _ref(name: str) -> dict:
    with open(REF / name, encoding="utf-8") as f:
        return json.load(f)


def allergy_conflicts(allergy_text: str, prescriptions: list[dict]) -> list[dict]:
    """Назначен препарат из группы, на которую у пациента аллергия."""
    text = (allergy_text or "").lower()
    if not text:
        return []
    out = []
    for group in _ref("allergy_groups.json")["groups"]:
        if not any(t in text for t in group["triggers"]):
            continue
        for p in prescriptions:
            drug = (p.get("drug") or "").lower()
            if any(d in drug for d in group["drugs"]):
                out.append({
                    "drug": p.get("drug"),
                    "text": f"У пациента аллергия на {group['title']}. "
                            f"{p.get('drug')} относится к этой группе. Проверьте назначение.",
                })
    return out


def pregnancy_conflicts(prescriptions: list[dict]) -> list[dict]:
    """Назначен препарат из списка нежелательных при беременности."""
    out = []
    for item in _ref("pregnancy_drugs.json")["drugs"]:
        for p in prescriptions:
            if item["stem"] in (p.get("drug") or "").lower():
                out.append({
                    "drug": p.get("drug"),
                    "text": f"{p.get('drug')}: {item['note']} Проверьте назначение.",
                })
    return out


if __name__ == "__main__":
    print(norm_bp("120 на 80"), norm_vital({"min": 34, "max": 42}, "38,2"))
    print(pregnancy(date(2026, 4, 10), date(2026, 9, 30)))
    for q in ["острый тонзиллит", "беременность 24 недели", "гипертоническая болезнь 2 степени", "острый бронхит", "хронический гастрит", "боль в спине"]:
        r = icd_pick(q)
        print(q, "->", r["code"] or "(не подобран)", r["name"], "|", [o["code"] for o in r["options"]])
    print(allergy_conflicts("пенициллин: сыпь", [{"drug": "Амоксициллин"}]))
