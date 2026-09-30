"""Весь путь одной записи: звук -> реплики -> маскирование -> поля листа.

Чистая функция без базы и сервера: её вызывает сервер (main.py) и проверка
из командной строки. Все шаги идут на этом компьютере.
"""

import time
from datetime import date

import asr
import extract
import llm
from mask import Masker, leaks

STEPS = [
    ("asr", "Распознавание речи"),
    ("mask", "Маскирование персональных данных"),
    ("translate", "Перевод казахских реплик"),
    ("fields", "Заполнение полей листа"),
    ("checks", "Расчёты и проверки"),
]


def run(audio_path: str, template_id: str, language: str = "mixed",
        patient_name: str = "", doctor_name: str = "", visit: date | None = None,
        on_step=None) -> dict:
    visit = visit or date.today()
    template = extract.load_template(template_id)
    timings: dict[str, float] = {}
    netlog: list[dict] = []

    def step(key: str, detail: str = ""):
        if on_step:
            on_step(key, detail)

    step("asr")
    t0 = time.time()
    segments, audio_seconds = asr.process(audio_path, language, template.get("vocabulary"))
    timings["asr"] = round(time.time() - t0, 1)
    if not segments:
        raise RuntimeError("В записи не найдено речи. Проверьте микрофон и повторите запись.")

    step("mask")
    t0 = time.time()
    masker = Masker(patient_name, doctor_name)
    for s in segments:
        s["masked"] = masker.mask(s["text"], s.get("lang", "ru"))
    leaked = leaks(" ".join(s["masked"] for s in segments))
    if leaked:
        # второй слой защиты: с незамаскированным ИИН или телефоном текст к модели не уходит
        raise RuntimeError("После маскирования в тексте остались персональные данные. Запрос к модели остановлен.")
    timings["mask"] = round(time.time() - t0, 1)

    if any(s.get("lang") == "kk" for s in segments):
        step("translate")
        t0 = time.time()
        extract.translate(segments, netlog)
        for s in segments:
            if s.get("masked_ru"):
                s["ru"] = masker.unmask(s["masked_ru"])
        timings["translate"] = round(time.time() - t0, 1)

    step("fields", "роли говорящих")
    t0 = time.time()
    extract.assign_roles(segments, netlog)
    fields = extract.extract(
        segments, template, masker, visit, netlog,
        on_progress=lambda i, n: step("fields", f"{i} из {n}"),
    )
    timings["fields"] = round(time.time() - t0, 1)

    step("checks")
    timings["total"] = round(sum(timings.values()), 1)
    timings["audio"] = round(audio_seconds, 1)
    return {
        "segments": segments,
        "gaps": asr.silent_gaps(segments, timings["audio"]),
        "fields": fields,
        "mask_table": masker.dump(),
        "mask_public": masker.public_table(),
        "netlog": netlog,
        "timings": timings,
    }


if __name__ == "__main__":
    import json
    import sys

    audio, template_id = sys.argv[1], sys.argv[2]
    language = sys.argv[3] if len(sys.argv) > 3 else "ru"
    patient = sys.argv[4] if len(sys.argv) > 4 else ""
    res = run(audio, template_id, language, patient_name=patient,
              on_step=lambda k, d="": print(f"  шаг: {k} {d}", file=sys.stderr))
    print("\n== Разговор (что видела модель)")
    for s in res["segments"]:
        print(f"{s['n']:>3} {s['role'][:1].upper()} {s['masked']}")
        if s.get("masked_ru"):
            print(f"      → {s['masked_ru']}")
    print("\n== Поля")
    for fid, f in res["fields"].items():
        extra = ""
        if f.get("icd"):
            extra += f"  [МКБ: {f['icd']['code'] or 'не подобран'}]"
        if f.get("date"):
            extra += f"  [дата: {f['date']}]"
        if f.get("pregnancy"):
            p = f["pregnancy"]
            extra += f"  [срок {p['weeks']} нед {p['days']} дн, ПДР {p['edd']}]"
        q = ",".join(str(x["n"]) for x in f.get("quotes", []))
        print(f"- {f['title']} ({f['status']}; строки {q or '-'}): {f['value'] or '—'}{extra}")
        for fl in f["flags"]:
            print(f"    ! {fl['level']}: {fl['text']}")
    for g in res["gaps"]:
        print(f"\n!! Участок без текста: {g['from']}–{g['to']} сек, прослушайте")
    print("\n== Время, сек:", json.dumps(res["timings"], ensure_ascii=False))
    print("== Обращений к модели:", len(res["netlog"]), "| все локальные:", all(x["local"] for x in res["netlog"]))
