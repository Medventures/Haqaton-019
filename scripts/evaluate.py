"""Замер точности на приёмах с эталоном.

Эталон готовит врач: для каждой записи перечисляет факты, которые должны
попасть в каждое поле листа (samples/gold/*.json). Скрипт прогоняет запись
через всю цепочку и сверяет результат с эталоном кодом, без модели.

Что считается:
  факты        — доля эталонных фактов, найденных в нужном поле
  поля         — поле засчитано, если найдены все его факты
  выдумки      — поля, которые по эталону пусты, а система их заполнила
  числа и даты — показатели, код МКБ-10, даты и срок беременности сверяются точно

Запуск из корня проекта:
  .venv/bin/python scripts/evaluate.py                 все эталоны
  .venv/bin/python scripts/evaluate.py therapist_1     один эталон
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import llm  # noqa: E402
import pipeline  # noqa: E402


def norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def check(gold: dict, result: dict) -> list[dict]:
    fields, rows = result["fields"], []
    for fid, want in gold["fields"].items():
        f = fields.get(fid) or {}
        got = norm(f.get("value", ""))
        title = f.get("title", fid)
        if isinstance(want, list) and not want:          # поле должно остаться пустым
            rows.append({"field": title, "kind": "пустое", "found": int(not got), "total": 1,
                         "got": f.get("value", ""), "invented": bool(got)})
        elif isinstance(want, list) and isinstance(want[0], dict):   # назначения
            drugs = [norm(p.get("drug", "") + " " + p.get("dose", "")) for p in f.get("items", [])]
            found = sum(1 for w in want
                        if any(norm(w["drug"]) in d and norm(w.get("dose", "")) in d for d in drugs))
            rows.append({"field": title, "kind": "назначения", "found": found, "total": len(want),
                         "got": f.get("value", ""),
                         "extra": max(0, len(drugs) - len(want))})
        elif isinstance(want, list):                      # текстовые факты
            found = sum(1 for w in want if norm(w) in got)
            rows.append({"field": title, "kind": "факты", "found": found, "total": len(want),
                         "got": f.get("value", ""),
                         "missed": [w for w in want if norm(w) not in got]})
        elif fid == "vitals":
            have = {i["key"]: i["value"] for i in f.get("items", [])}
            found = sum(1 for k, v in want.items() if have.get(k) == v)
            extra = [k for k, v in have.items() if v and k not in want]
            rows.append({"field": title, "kind": "числа", "found": found, "total": len(want),
                         "got": f.get("value", ""), "invented": bool(extra),
                         "missed": [k for k, v in want.items() if have.get(k) != v]})
        elif isinstance(want, dict):                      # диагноз
            facts = want.get("facts", [])
            found = sum(1 for w in facts if norm(w) in got)
            code = (f.get("icd") or {}).get("code", "")
            options = [o["code"] for o in (f.get("icd") or {}).get("options", [])]
            icd_ok = code.startswith(want["icd"]) or any(o.startswith(want["icd"]) for o in options[:3])
            rows.append({"field": title, "kind": "диагноз", "found": found + int(icd_ok),
                         "total": len(facts) + 1, "got": f"{f.get('value', '')} [{code or 'код не подобран'}]"})
        else:                                             # дата
            rows.append({"field": title, "kind": "дата", "found": int(f.get("date") == want), "total": 1,
                         "got": f.get("date") or f.get("value", "")})

    comp = gold.get("computed")
    if comp:
        p = (fields.get("lmp") or {}).get("pregnancy") or {}
        ok = (p.get("weeks") == comp["pregnancy_weeks"] and p.get("days") == comp["pregnancy_days"]
              and p.get("edd") == comp["edd"])
        rows.append({"field": "Срок беременности и дата родов", "kind": "расчёт", "found": int(ok),
                     "total": 1, "got": f"{p.get('weeks')} нед. {p.get('days')} дн., {p.get('edd')}"})
    for fid in gold.get("warnings", []):
        ok = any(x["level"] == "danger" for x in (fields.get(fid) or {}).get("flags", []))
        rows.append({"field": "Предупреждение: " + (fields.get(fid) or {}).get("title", fid),
                     "kind": "проверка", "found": int(ok), "total": 1,
                     "got": "сработало" if ok else "не сработало"})
    return rows


def main() -> None:
    names = sys.argv[1:]
    golds = sorted((ROOT / "samples" / "gold").glob("*.json"))
    if names:
        golds = [g for g in golds if g.stem in names]
    report, total_found, total_all, fields_ok, fields_all, invented = [], 0, 0, 0, 0, 0
    for path in golds:
        gold = json.loads(path.read_text(encoding="utf-8"))
        audio = ROOT / gold["audio"]
        if not audio.exists():
            print(f"{path.stem}: нет записи {gold['audio']}, пропускаем")
            continue
        t0 = time.time()
        result = pipeline.run(str(audio), gold["template"], gold["language"],
                              patient_name=gold.get("patient", ""),
                              visit=date.fromisoformat(gold["visit_date"]))
        rows = check(gold, result)
        found, allf = sum(r["found"] for r in rows), sum(r["total"] for r in rows)
        ok = sum(1 for r in rows if r["found"] == r["total"])
        inv = sum(1 for r in rows if r.get("invented"))
        total_found += found; total_all += allf; fields_ok += ok; fields_all += len(rows); invented += inv
        print(f"\n## {path.stem}: {gold.get('note', '')}")
        print(f"запись {result['timings']['audio']:.0f} с, обработка {time.time() - t0:.0f} с, "
              f"модель {llm.current()}")
        print("| Поле | Найдено | Результат |")
        print("|---|---|---|")
        for r in rows:
            mark = "верно" if r["found"] == r["total"] else f"{r['found']} из {r['total']}"
            tail = f" (нет: {', '.join(r['missed'])})" if r.get("missed") else ""
            tail += " (заполнено лишнее)" if r.get("invented") else ""
            print(f"| {r['field']} | {mark}{tail} | {str(r['got'])[:110]} |")
        print(f"\nполей верно: {ok} из {len(rows)}, фактов найдено: {found} из {allf}, выдумок: {inv}")
        report.append({"sample": path.stem, "note": gold.get("note", ""), "rows": rows,
                       "timings": result["timings"], "model": llm.current()})
    if report:
        print(f"\n# Итог: полей верно {fields_ok} из {fields_all} "
              f"({100 * fields_ok / fields_all:.0f}%), фактов {total_found} из {total_all} "
              f"({100 * total_found / total_all:.0f}%), выдумок {invented}")
        out = ROOT / "docs" / "eval_last.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"подробности: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
