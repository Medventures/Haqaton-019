"""Хатшы — сервер.

Всё работает внутри клиники: распознавание речи, маскирование, локальная модель,
сборка листа. В интернет не уходит ни один запрос; единственный внешний адресат —
МИС клиники, куда лист передаётся после подтверждения врачом.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path

import requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

import calc  # noqa: E402
import export_docx  # noqa: E402
import extract  # noqa: E402
import llm  # noqa: E402
import mask as mask_mod  # noqa: E402
import pipeline  # noqa: E402
import protocols  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("HATSHY_DATA", ROOT / "data"))
AUDIO = DATA / "audio"
AUDIO.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "hatshy.db"
MIS_URL = os.environ.get("MIS_URL", "http://127.0.0.1:8100").rstrip("/")
ORGANIZATION = os.environ.get("HATSHY_ORG", "")

app = FastAPI(title="Хатшы", description="Лист консультации по записи приёма. Всё локально.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

JSON_COLS = ("patient", "segments", "fields", "fields_raw", "mask_table", "netlog", "timings",
             "gaps", "acks")


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS doctors (
                id INTEGER PRIMARY KEY, name TEXT, position TEXT, specialty TEXT
            );
            CREATE TABLE IF NOT EXISTS consultations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doctor_id INTEGER, patient TEXT, language TEXT, template TEXT,
                status TEXT, step TEXT, step_detail TEXT, error TEXT,
                created_at TEXT, visit_date TEXT, audio_path TEXT,
                segments TEXT, fields TEXT, mask_table TEXT, netlog TEXT,
                timings TEXT, gaps TEXT, acks TEXT, sent_at TEXT, mis_id TEXT
            );
            CREATE TABLE IF NOT EXISTS edits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                consultation_id INTEGER, field TEXT, before TEXT, after TEXT, at TEXT
            );
            """
        )
        # Сырой ответ модели хранится отдельно от правок врача: по нему считается,
        # сколько полей принято без изменений, и его нельзя исправить задним числом.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(consultations)")]
        if "fields_raw" not in cols:
            conn.execute("ALTER TABLE consultations ADD COLUMN fields_raw TEXT")
        if not conn.execute("SELECT 1 FROM doctors").fetchone():
            seed = json.loads((Path(__file__).parent / "doctors.json").read_text(encoding="utf-8"))
            for d in seed:
                conn.execute("INSERT INTO doctors (id, name, position, specialty) VALUES (?,?,?,?)",
                             (d["id"], d["name"], d["position"], d["specialty"]))


init_db()


def load(cid: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM consultations WHERE id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "Приём не найден")
    c = dict(row)
    for col in JSON_COLS:
        c[col] = json.loads(c[col]) if c[col] else None
    return c


def save(cid: int, **values) -> None:
    for k in list(values):
        if k in JSON_COLS and values[k] is not None:
            values[k] = json.dumps(values[k], ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in values)
    with db() as conn:
        conn.execute(f"UPDATE consultations SET {cols} WHERE id=?", (*values.values(), cid))


def doctor_of(c: dict) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM doctors WHERE id=?", (c["doctor_id"],)).fetchone()
    if not row:
        return {}
    doc = dict(row)
    titles = {t["id"]: t["title"] for t in extract.list_templates()}
    doc["specialty_title"] = titles.get(doc["specialty"], doc["specialty"])
    return doc


def public(c: dict) -> dict:
    """Приём для интерфейса: без таблицы замен и пути к файлу."""
    out = {k: v for k, v in c.items() if k not in ("mask_table", "audio_path", "fields_raw")}
    out["doctor"] = doctor_of(c)
    out["has_audio"] = bool(c.get("audio_path"))
    out["blocking"] = blocking(c)
    return out


def blocking(c: dict) -> list[dict]:
    """Красные предупреждения, которые врач ещё не разобрал: с ними лист в МИС не уходит."""
    acks = set(c.get("acks") or [])
    out = []
    for fid, f in (c.get("fields") or {}).items():
        for flag in f.get("flags", []):
            if flag["level"] == "danger" and f"{fid}:{flag.get('drug', '')}" not in acks:
                out.append({"field": fid, **flag})
    return out


# --- Справочники -----------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"model": llm.available(), "model_kk": llm.kazakh_model(),
            "external_model_blocked": True,
            "mis": MIS_URL, "mis_local": llm.is_local(MIS_URL)}


@app.get("/api/doctors")
def doctors():
    titles = {t["id"]: t["title"] for t in extract.list_templates()}
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM doctors ORDER BY id")]
    for r in rows:
        r["specialty_title"] = titles.get(r["specialty"], r["specialty"])
        r["patient_sex"] = extract.load_template(r["specialty"]).get("patient_sex")
    return rows


@app.get("/api/templates/{template_id}")
def template(template_id: str):
    try:
        return extract.load_template(template_id)
    except FileNotFoundError:
        raise HTTPException(404, "Шаблон не найден")


@app.get("/api/patients")
def patients(q: str = ""):
    """Пациенты берутся из МИС клиники. Если МИС недоступна, врач вводит данные вручную."""
    try:
        r = requests.get(f"{MIS_URL}/api/v1/patients", params={"q": q}, timeout=4)
        r.raise_for_status()
        return {"source": "mis", "items": r.json()}
    except Exception:  # noqa: BLE001
        return {"source": "offline", "items": []}


@app.get("/api/icd")
def icd(q: str):
    return calc.icd_search(q, limit=8)


# --- Приём -----------------------------------------------------------------------

class NewConsultation(BaseModel):
    doctor_id: int
    patient: dict
    language: str = "mixed"


@app.post("/api/consultations")
def create(body: NewConsultation):
    with db() as conn:
        doc = conn.execute("SELECT * FROM doctors WHERE id=?", (body.doctor_id,)).fetchone()
        if not doc:
            raise HTTPException(404, "Врач не найден")
        # Шаблон может ограничивать пол пациента: к акушеру-гинекологу
        # записываются только пациентки. Проверяем на сервере, а не только в интерфейсе.
        need = extract.load_template(doc["specialty"]).get("patient_sex")
        if need:
            sex = (body.patient.get("sex") or "").strip().lower()[:1]
            if not sex:
                raise HTTPException(400, "Укажите пол пациента")
            if sex != need:
                raise HTTPException(400, "Акушер-гинеколог принимает только пациенток. "
                                         "Выберите другого пациента или другого врача.")
        cur = conn.execute(
            """INSERT INTO consultations
               (doctor_id, patient, language, template, status, created_at, visit_date)
               VALUES (?,?,?,?,?,?,?)""",
            (body.doctor_id, json.dumps(body.patient, ensure_ascii=False), body.language,
             doc["specialty"], "new", datetime.now().isoformat(timespec="seconds"),
             date.today().isoformat()),
        )
        return {"id": cur.lastrowid}


@app.get("/api/consultations")
def history(doctor_id: int | None = None):
    sql = ("SELECT id, doctor_id, patient, status, created_at, visit_date, sent_at, template, "
           "fields, timings FROM consultations")
    args: tuple = ()
    if doctor_id:
        sql += " WHERE doctor_id=?"
        args = (doctor_id,)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT 100", args)]
    for r in rows:
        r["patient"] = json.loads(r["patient"]) if r["patient"] else {}
        fields = json.loads(r.pop("fields")) if r["fields"] else {}
        r["timings"] = json.loads(r["timings"]) if r["timings"] else None
        diag = fields.get("diagnosis", {})
        r["diagnosis"] = diag.get("value", "")
        r["icd"] = (diag.get("icd") or {}).get("code", "")
    return rows


@app.get("/api/consultations/{cid}")
def get(cid: int):
    return public(load(cid))


def process(cid: int) -> None:
    """Полный разбор записи в фоне. Интерфейс опрашивает статус и показывает шаги."""
    c = load(cid)
    doctor = doctor_of(c)
    try:
        result = pipeline.run(
            c["audio_path"], c["template"], c["language"],
            patient_name=(c["patient"] or {}).get("name", ""),
            doctor_name=doctor.get("name", ""),
            visit=date.fromisoformat(c["visit_date"]),
            on_step=lambda key, detail="": save(cid, step=key, step_detail=detail),
        )
        save(cid, status="ready", step="done", step_detail="", error=None,
             segments=result["segments"], fields=result["fields"], fields_raw=result["fields"],
             mask_table=result["mask_table"], netlog=result["netlog"],
             timings=result["timings"], gaps=result["gaps"], acks=[])
    except Exception as e:  # noqa: BLE001
        save(cid, status="error", error=str(e))


@app.post("/api/consultations/{cid}/audio")
async def upload_audio(cid: int, file: UploadFile = File(...)):
    load(cid)
    suffix = Path(file.filename or "").suffix or ".webm"
    path = AUDIO / f"{cid}{suffix}"
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    save(cid, audio_path=str(path), status="processing", step="asr", step_detail="", error=None)
    threading.Thread(target=process, args=(cid,), daemon=True).start()
    return {"status": "processing"}


@app.post("/api/consultations/{cid}/reprocess")
def reprocess(cid: int):
    c = load(cid)
    if not c.get("audio_path"):
        raise HTTPException(400, "У приёма нет записи")
    save(cid, status="processing", step="asr", step_detail="", error=None)
    threading.Thread(target=process, args=(cid,), daemon=True).start()
    return {"status": "processing"}


@app.get("/api/consultations/{cid}/audio")
def audio(cid: int):
    """Запись для прослушивания. Браузер не умеет перематывать собственную запись
    (в ней нет оглавления), поэтому отдаём копию в формате с перемоткой."""
    c = load(cid)
    if not c.get("audio_path") or not os.path.exists(c["audio_path"]):
        raise HTTPException(404, "Записи нет")
    play = AUDIO / f"{cid}.play.m4a"
    if not play.exists() or play.stat().st_mtime < os.path.getmtime(c["audio_path"]):
        try:
            subprocess.run(["ffmpeg", "-y", "-i", c["audio_path"], "-ac", "1", "-c:a", "aac",
                            "-b:a", "64k", "-movflags", "+faststart", str(play), "-loglevel", "error"],
                           check=True)
        except Exception:  # noqa: BLE001
            return FileResponse(c["audio_path"])
    return FileResponse(play, media_type="audio/mp4")


# --- Проверка и правка врачом ----------------------------------------------------

class FieldPatch(BaseModel):
    value: str | None = None
    items: list[dict] | None = None
    icd: dict | None = None


@app.patch("/api/consultations/{cid}/fields/{field_id}")
def edit_field(cid: int, field_id: str, patch: FieldPatch):
    c = load(cid)
    fields = c.get("fields") or {}
    f = fields.get(field_id)
    if not f:
        raise HTTPException(404, "Поле не найдено")
    before = json.dumps({"value": f.get("value"), "items": f.get("items"),
                         "icd": (f.get("icd") or {}).get("code")}, ensure_ascii=False)

    if patch.items is not None:
        f["items"] = patch.items
    if patch.value is not None:
        f["value"] = patch.value.strip()
        if f["kind"] in ("date_future", "date_past"):
            f["raw"] = f["value"]
        if f["kind"] == "diagnosis" and patch.icd is None:
            f["icd"] = calc.icd_pick(f["value"])
    if patch.icd is not None:
        f["icd"] = {**(f.get("icd") or {}), "code": patch.icd.get("code", ""),
                    "name": patch.icd.get("name", ""), "sure": True, "source": "выбрал врач"}
    f["edited"] = True
    # врач посмотрел поле: жёлтые отметки снимаются, красные пересчитываются
    f["flags"] = []
    f["status"] = "ok" if (f.get("value") or f.get("items")) else "empty"

    template = extract.load_template(c["template"])
    fields = extract.recheck(fields, template, date.fromisoformat(c["visit_date"]))
    after = json.dumps({"value": f.get("value"), "items": f.get("items"),
                        "icd": (f.get("icd") or {}).get("code")}, ensure_ascii=False)
    with db() as conn:
        conn.execute("INSERT INTO edits (consultation_id, field, before, after, at) VALUES (?,?,?,?,?)",
                     (cid, field_id, before, after, datetime.now().isoformat(timespec="seconds")))
    save(cid, fields=fields)
    return public(load(cid))


class Confirm(BaseModel):
    note: str = ""


@app.post("/api/consultations/{cid}/fields/{field_id}/confirm")
def confirm_field(cid: int, field_id: str, body: Confirm):
    """Врач посмотрел поле и согласен: «без особенностей» или «оставить как есть»."""
    c = load(cid)
    fields = c.get("fields") or {}
    f = fields.get(field_id)
    if not f:
        raise HTTPException(404, "Поле не найдено")
    acks = c.get("acks") or []
    for flag in f.get("flags", []):
        if flag["level"] == "danger":
            acks.append(f"{field_id}:{flag.get('drug', '')}")
    if body.note and not f.get("value"):
        f["value"] = body.note
        f["edited"] = True
    f["flags"] = [x for x in f.get("flags", []) if x["level"] == "danger"]
    f["confirmed"] = True
    if f.get("value") or f.get("items"):
        f["status"] = "ok"
    save(cid, fields=fields, acks=acks)
    with db() as conn:
        conn.execute("INSERT INTO edits (consultation_id, field, before, after, at) VALUES (?,?,?,?,?)",
                     (cid, field_id, "", "подтверждено врачом" + (f": {body.note}" if body.note else ""),
                      datetime.now().isoformat(timespec="seconds")))
    return public(load(cid))


@app.post("/api/consultations/{cid}/dictate/{field_id}")
def dictate(cid: int, field_id: str, file: UploadFile = File(...)):
    """Врач диктует поле голосом вместо набора на клавиатуре.

    Запись распознаётся на этом же сервере с тем же словарём специальности.
    Для текстовых полей возвращается текст, для назначений и показателей —
    разобранные строки. В лист ничего не попадает, пока врач не нажмёт «Сохранить».
    """
    c = load(cid)
    template = extract.load_template(c["template"])
    field = next((f for f in template["fields"] if f["id"] == field_id), None)
    if not field:
        raise HTTPException(404, "Поле не найдено")
    suffix = Path(file.filename or "").suffix or ".webm"
    path = AUDIO / f"dictate_{cid}_{field_id}{suffix}"
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        language = c["language"] if c["language"] in ("ru", "kk") else "ru"
        segments, _ = pipeline.asr.process(str(path), language, template.get("vocabulary"))
    except pipeline.asr.NoSpeech as e:
        raise HTTPException(422, str(e))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    text = " ".join(s["text"] for s in segments).strip()
    if not text:
        raise HTTPException(422, "Не расслышали. Повторите диктовку.")
    if field["kind"] not in ("vitals", "prescriptions"):
        return {"text": text}

    doctor = doctor_of(c)
    masker = mask_mod.Masker((c["patient"] or {}).get("name", ""), doctor.get("name", ""))
    for s in segments:
        s["masked"] = masker.mask(s["text"], s.get("lang", "ru"))
        s["role"] = "doctor"
    log: list = []
    done = extract.dictated(field, segments, masker, date.fromisoformat(c["visit_date"]), log)
    return {"text": text, "items": done.get("items", [])}


class RolePatch(BaseModel):
    role: str


@app.patch("/api/consultations/{cid}/segments/{n}")
def edit_role(cid: int, n: int, patch: RolePatch):
    c = load(cid)
    for s in c.get("segments") or []:
        if s["n"] == n:
            s["role"] = "doctor" if patch.role == "doctor" else "patient"
    save(cid, segments=c["segments"])
    return {"ok": True}


# --- Сверка с клиническим протоколом ---------------------------------------------

@app.get("/api/consultations/{cid}/protocol")
def protocol(cid: int):
    c = load(cid)
    return protocols.check(c.get("fields") or {})


class AddItem(BaseModel):
    section: str = ""
    text: str


@app.post("/api/consultations/{cid}/protocol/add")
def protocol_add(cid: int, body: AddItem):
    """Врач одним нажатием переносит обследование из протокола в план обследования."""
    c = load(cid)
    fields = c.get("fields") or {}
    f = fields.get("plan")
    if not f:
        raise HTTPException(404, "Поле не найдено")
    before = f.get("value", "")
    f["value"] = (before.rstrip(". ") + ". " if before else "") + body.text.strip().rstrip(".") + "."
    f["edited"] = True
    f["status"] = "ok"
    save(cid, fields=fields)
    with db() as conn:
        conn.execute("INSERT INTO edits (consultation_id, field, before, after, at) VALUES (?,?,?,?,?)",
                     (cid, "plan", before, f["value"], datetime.now().isoformat(timespec="seconds")))
    return public(load(cid))


# --- Контур данных ---------------------------------------------------------------

@app.get("/api/consultations/{cid}/contour")
def contour(cid: int):
    """Что видела модель и куда уходили запросы. Главный экран про безопасность."""
    c = load(cid)
    segments = c.get("segments") or []
    netlog = c.get("netlog") or []
    masker = mask_mod.Masker.load(c.get("mask_table") or [])
    masked_text = " ".join(s.get("masked", "") for s in segments)
    return {
        "lines": [{"n": s["n"], "role": s.get("role"), "text": s["text"],
                   "masked": s.get("masked", "")} for s in segments],
        "table": masker.public_table(),
        "leaks": mask_mod.leaks(masked_text),
        "requests": netlog,
        "external_requests": sum(1 for r in netlog if not r.get("local")),
        "model": llm.available(),
        "storage": {"database": str(DB_PATH), "audio": str(AUDIO)},
        "mis": {"url": MIS_URL, "local": llm.is_local(MIS_URL), "sent_at": c.get("sent_at")},
    }


# --- Завершение: МИС и Word ------------------------------------------------------

def mis_payload(c: dict) -> dict:
    fields = c["fields"]
    doctor = doctor_of(c)

    def val(fid: str) -> str:
        return (fields.get(fid) or {}).get("value", "")

    diag = fields.get("diagnosis") or {}
    lmp = fields.get("lmp") or {}
    payload = {
        "patient": c["patient"],
        "encounter": {
            "date": c["created_at"],
            "doctor": doctor.get("name"),
            "position": doctor.get("position"),
            "specialty": c["template"],
        },
        "sheet": {fid: val(fid) for fid, f in fields.items()
                  if f["kind"] == "text"},
        "vitals": {i["key"]: i["value"] for i in (fields.get("vitals") or {}).get("items", [])
                   if i.get("value")},
        "diagnosis": {"text": diag.get("value", ""), "icd10": (diag.get("icd") or {}).get("code", "")},
        "prescriptions": [{k: p.get(k, "") for k in ("drug", "dose", "frequency", "duration")}
                          for p in (fields.get("prescriptions") or {}).get("items", [])],
        "follow_up": (fields.get("follow_up") or {}).get("date"),
        "review": {
            "doctor_confirmed": True,
            "edited_fields": [fid for fid, f in fields.items() if f.get("edited")],
        },
        "privacy": {
            "audio_left_clinic": False,
            "external_requests": sum(1 for r in (c.get("netlog") or []) if not r.get("local")),
        },
    }
    if lmp.get("date"):
        payload["pregnancy"] = {"lmp": lmp["date"], **{
            k: v for k, v in (lmp.get("pregnancy") or {}).items() if k != "rule"}}
    return payload


@app.post("/api/consultations/{cid}/send")
def send(cid: int):
    c = load(cid)
    if c["status"] not in ("ready", "sent"):
        raise HTTPException(400, "Лист ещё не готов")
    if blocking(c):
        raise HTTPException(409, "Сначала разберите красные предупреждения")
    try:
        r = requests.post(f"{MIS_URL}/api/v1/consultations", json=mis_payload(c), timeout=10)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"МИС не приняла лист: {e}")
    netlog = (c.get("netlog") or []) + [{
        "to": MIS_URL, "local": llm.is_local(MIS_URL), "purpose": "передача листа в МИС",
        "chars": len(json.dumps(mis_payload(c), ensure_ascii=False)), "seconds": 0,
    }]
    save(cid, status="sent", sent_at=datetime.now().isoformat(timespec="seconds"),
         mis_id=str(r.json().get("id", "")), netlog=netlog)
    return public(load(cid))


@app.get("/api/consultations/{cid}/docx")
def docx(cid: int):
    c = load(cid)
    if not c.get("fields"):
        raise HTTPException(400, "Лист ещё не готов")
    path = DATA / f"list_{cid}.docx"
    export_docx.build(str(path), c, extract.load_template(c["template"]), doctor_of(c), ORGANIZATION)
    name = (c["patient"] or {}).get("name", "пациент").split()[0]
    return FileResponse(
        path, filename=f"Лист консультации {name} {c['visit_date']}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.get("/api/stats")
def stats():
    """Цифры для оценки: сколько полей врач принял без правок и сколько времени ушло."""
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT fields, timings FROM consultations WHERE fields IS NOT NULL")]
    total = filled = edited = 0
    seconds, audio = [], []
    for r in rows:
        for f in json.loads(r["fields"]).values():
            total += 1
            filled += 1 if (f.get("value") or f.get("items")) else 0
            edited += 1 if f.get("edited") else 0
        t = json.loads(r["timings"]) if r["timings"] else {}
        if t.get("total"):
            seconds.append(t["total"])
            audio.append(t.get("audio", 0))
    return {
        "consultations": len(rows), "fields_total": total, "fields_filled": filled,
        "fields_edited": edited,
        "accepted_without_edits": round(100 * (filled - edited) / filled, 1) if filled else None,
        "avg_processing_seconds": round(sum(seconds) / len(seconds), 1) if seconds else None,
        "avg_audio_seconds": round(sum(audio) / len(audio), 1) if audio else None,
    }


# Собранный интерфейс отдаёт этот же сервер: так в клинике один адрес и один порт.
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
