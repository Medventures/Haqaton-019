"""Диагностика по клиническому протоколу МЗ РК.

Диагноз ставит врач. Когда он подтвердил код МКБ-10, система показывает, какие
обследования для этого диагноза записаны в протоколе, и отмечает, что из них
уже есть в листе. Лечение не подсказывается.

Модель здесь не участвует. Перечни заранее выписаны из текста протокола
(папка protocols, один файл на протокол) и сверяются врачом. Во время приёма
работает только сравнение строк, поэтому результат всегда одинаков и объясним.

Протокол по беременности расписан по посещениям. Нужное посещение выбирается
по сроку, который посчитан из даты последней менструации.
"""

import json
import re
from pathlib import Path

DIR = Path(__file__).parent / "protocols"
# поля листа, в которых ищем уже сделанное и назначенное
SHEET_FIELDS = ("plan", "status", "vitals", "recommendations")


def _all() -> list[dict]:
    out = []
    for path in sorted(DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def find(icd_code: str) -> dict | None:
    """Протокол по коду МКБ-10: J03.9 подходит к протоколу, где указан J03."""
    if not icd_code:
        return None
    for p in _all():
        if any(icd_code.upper().startswith(code.upper()) for code in p.get("icd", [])):
            return p
    return None


def _present(item: dict, sheet: str) -> bool:
    """Пункт уже есть в листе, если там встречается одно из его ключевых слов.

    Короткие слова (АД, ОАК, ЭКГ) ищутся только целиком, иначе «ад» найдётся
    в слове «назад». Длинные ищутся как начало слова.
    """
    for kw in item.get("keywords", []):
        kw = kw.strip().lower()
        if not kw:
            continue
        tail = r"(?![а-яёa-z])" if len(kw) < 4 else ""
        if re.search(r"(?<![а-яёa-z])" + re.escape(kw) + tail, sheet):
            return True
    return False


def _visit(p: dict, weeks: int) -> tuple[dict, bool]:
    """Посещение по сроку беременности и признак «срок точно в окне посещения»."""
    for v in p["visits"]:
        if v["weeks"][0] <= weeks <= v["weeks"][1]:
            return v, v["official"][0] <= weeks <= v["official"][1]
    return p["visits"][-1], False


def check(fields: dict) -> dict:
    code = ((fields.get("diagnosis") or {}).get("icd") or {}).get("code", "")
    p = find(code)
    if not p:
        return {"found": False, "icd": code, "available": [
            {"title": x["title"], "icd": x.get("icd", [])} for x in _all()]}

    out = {
        "found": True, "icd": code, "title": p["title"], "version": p.get("version", ""),
        "approved": p.get("approved", ""), "source": p.get("source", ""),
        "verified_by_doctor": p.get("verified_by_doctor", False), "sections": [],
    }
    sections = p.get("sections", [])
    if p.get("visits"):
        preg = (fields.get("lmp") or {}).get("pregnancy")
        if not preg:
            out["need"] = ("Протокол расписан по срокам беременности. Укажите дату последней "
                           "менструации, и появятся обследования для текущего срока.")
            return out
        visit, exact = _visit(p, preg["weeks"])
        out["visit"] = {"title": visit["title"], "exact": exact,
                        "weeks": preg["weeks"], "days": preg["days"]}
        sections = visit["sections"]

    sheet = " ".join((fields.get(fid) or {}).get("value", "") for fid in SHEET_FIELDS).lower()
    for s in sections:
        items = [{"text": i["text"], "note": i.get("note", ""), "present": _present(i, sheet)}
                 for i in s["items"]]
        out["sections"].append({"key": s["key"], "title": s["title"], "items": items})
    return out
