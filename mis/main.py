"""Демо-МИС: заглушка медицинской информационной системы клиники.

Нужна, чтобы показать интеграцию: Хатшы берёт отсюда пациентов и передаёт сюда
готовый лист консультации. Настоящая МИС подключается заменой адреса MIS_URL.
Описание API открывается по адресу /docs.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

HERE = Path(__file__).parent
DATA = Path(os.environ.get("MIS_DATA", HERE / "data"))
DATA.mkdir(parents=True, exist_ok=True)
STORE = DATA / "consultations.json"

app = FastAPI(title="Демо-МИС клиники", description="Приём листов консультации и справочник пациентов")


def patients_all() -> list[dict]:
    return json.loads((HERE / "patients.json").read_text(encoding="utf-8"))


def stored() -> list[dict]:
    return json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else []


@app.get("/api/v1/patients", summary="Поиск пациента по ФИО или ИИН")
def patients(q: str = ""):
    q = q.strip().lower()
    return [p for p in patients_all() if not q or q in p["name"].lower() or q in p["iin"]]


@app.get("/api/v1/patients/{iin}", summary="Карта пациента")
def patient(iin: str):
    for p in patients_all():
        if p["iin"] == iin:
            sheets = [c for c in stored() if c.get("patient", {}).get("iin") == iin]
            return {**p, "consultations": sheets}
    raise HTTPException(404, "Пациент не найден")


@app.post("/api/v1/consultations", status_code=201, summary="Принять лист консультации")
def accept(sheet: dict):
    items = stored()
    sheet["id"] = len(items) + 1
    sheet["received_at"] = datetime.now().isoformat(timespec="seconds")
    items.append(sheet)
    STORE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": sheet["id"], "status": "accepted"}


@app.get("/api/v1/consultations", summary="Принятые листы")
def consultations():
    return list(reversed(stored()))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def page():
    return (HERE / "index.html").read_text(encoding="utf-8")
