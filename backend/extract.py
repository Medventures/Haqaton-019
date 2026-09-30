"""Раскладка разговора по полям листа консультации.

Модель маленькая и локальная, поэтому работа разбита на короткие запросы:
сначала роли (врач или пациент), затем по два-три поля за раз. Ответ всегда
приходит в JSON по строгой схеме. К каждому полю модель указывает номера строк
разговора, из которых взяты сведения: по ним врач проверяет результат.

Модель видит только замаскированный текст. Числа, даты и коды после неё
доводит обычный код (calc.py, dates.py).
"""

import json
import re
from datetime import date
from pathlib import Path

import calc
import dates
import llm

TEMPLATES = Path(__file__).parent / "templates"

# Разговор идёт первым и одинаков во всех запросах приёма: модель не перечитывает
# его заново на каждом запросе, и раскладка идёт быстрее.
HEAD = """Ты помощник врача. Ниже запись разговора врача и пациента на приёме. Строки пронумерованы.

Разговор:
{lines}

"""

RULES = """Задание: заполни поля листа консультации по этому разговору.

Правила:
1. Пиши только то, что прозвучало в разговоре. Ничего не добавляй от себя и не делай выводов.
2. Если для поля в разговоре ничего нет, верни пустую строку.
3. Пиши кратко, по-русски, медицинским языком, без слов «пациент говорит». Реплики на казахском переведи на русский.
4. В lines перечисли номера строк, из которых взяты сведения, не больше трёх.
5. Метки в квадратных скобках, например [ПАЦИЕНТ_1], переписывай без изменений.
6. Числа пиши цифрами.
7. О чём в разговоре не говорили, о том не пиши вообще. Никаких «не указано», «не упоминалось», «нет данных».
8. Не переноси сведения из одного поля в другое: в каждое поле только то, что к нему относится.

Поля:
{fields}
"""

ROLES = """Задание: для каждой строки определи, кто говорит.
В — врач: задаёт вопросы, осматривает, называет давление, пульс, вес и другие измерения, результаты осмотра, диагноз, назначения и рекомендации.
П — пациент: рассказывает о самочувствии и отвечает на вопросы врача.
Верни все строки от 1 до {count}.
"""


VITALS = """Ниже строки из разговора врача и пациента, в которых есть числа. Строки пронумерованы.

{lines}

Задание: выпиши показатели, которые врач измерил на этом приёме.
Правила:
1. Бери только слова врача. То, что пациент рассказал о прошлых днях, не бери.
2. Пиши только число, как оно прозвучало. Давление пиши как 120/80.
3. Если показатель не назван, оставь пустую строку.
4. В lines перечисли номера строк с показателями.
5. Числа, сказанные словами, запиши цифрами. Казахские слова: қан қысымы — давление, тамыр соғуы — пульс, дене қызуы — температура, салмақ — вес.

Показатели:
{items}
"""


TOKEN = re.compile(r"\[[А-ЯЁ_]+_\d+\]")

TRANSLATE = """Ты медицинский переводчик с казахского на русский. Ниже реплики врача и пациента на приёме.

Переведи каждую реплику на русский язык.
Правила:
1. Переводи точно. Ничего не добавляй и ничего не пропускай.
2. Медицинские слова переводи принятыми русскими терминами. Словарь ниже.
3. Русские слова и названия лекарств оставляй как есть.
4. Числа пиши цифрами: «жүз жиырмаға сексен» это «120 на 80».
5. Метки в квадратных скобках, например [ПАЦИЕНТ_1], переписывай без изменений.
6. Верни перевод для каждой реплики с её номером.

Словарь:
{glossary}

Реплики:
{lines}
"""


TRANSLATE_ONE = """Ты переводчик. Переведи фразу врача или пациента с казахского языка на русский язык.
Ответ только на русском. Названия лекарств и метки в квадратных скобках оставь как есть.
{glossary}
Фраза: {line}
"""


def _glossary_for(text: str, terms: dict) -> str:
    """Слова словаря, которые есть в этой фразе. Длинные выражения идут первыми."""
    low = text.lower()
    hits = [(k, v) for k, v in terms.items() if k in low or k[:max(4, len(k) - 2)] in low]
    hits.sort(key=lambda kv: -len(kv[0]))
    if not hits:
        return ""
    return "Словарь: " + "; ".join(f"{k} — {v}" for k, v in hits[:8]) + "."


def _left_kazakh(text: str) -> bool:
    """В переводе остались казахские буквы, то есть часть фразы не переведена."""
    return bool(set(TOKEN.sub("", text).lower()) & set("әғқңөұүһі"))


def translate(segments: list[dict], log: list) -> None:
    """Переводит казахские реплики на русский отдельным шагом.

    Маленькая модель плохо заполняет поля прямо по казахскому тексту: часть фраз
    оставляет без перевода, путает термины. Перевод реплик у неё получается лучше,
    а по русскому тексту поля заполняются уже надёжно. Перевод сохраняется рядом
    с оригиналом: врач видит оба варианта и может проверить.
    """
    kk = [s for s in segments if s.get("lang") == "kk"]
    if not kk:
        return
    with open(Path(__file__).parent / "refdata" / "kk_glossary.json", encoding="utf-8") as f:
        terms = json.load(f)["terms"]
    talk = " ".join(s["masked"] for s in kk).lower()
    # в подсказку идут только слова, которые есть в этом разговоре
    used = {k: v for k, v in terms.items() if k.split()[0][:4] in talk}
    glossary = "\n".join(f"{k} — {v}" for k, v in used.items()) or "(нет)"
    schema = {"type": "object", "properties": {"lines": {"type": "array", "items": {
        "type": "object",
        "properties": {"n": {"type": "integer"}, "ru": {"type": "string"}},
        "required": ["n", "ru"]}}}, "required": ["lines"]}
    model = llm.kazakh_model()
    by_n = {s["n"]: s for s in kk}
    for i in range(0, len(kk), 15):
        part = kk[i:i + 15]
        lines = "\n".join(f"{s['n']}. {s['masked']}" for s in part)
        try:
            data = llm.ask_json(TRANSLATE.format(glossary=glossary, lines=lines), schema,
                                "перевод казахских реплик", log,
                                max_tokens=sum(len(s["masked"]) for s in part) + 400, model=model)
        except Exception:  # noqa: BLE001  без перевода реплика идёт дальше как есть
            continue
        for row in data.get("lines", []):
            s = by_n.get(row.get("n"))
            if s and (row.get("ru") or "").strip():
                s["masked_ru"] = row["ru"].strip()

    # Проверка кодом: в русском переводе не должно остаться казахских букв.
    # Такие реплики переводим повторно по одному предложению: на короткой фразе
    # со своим словарём модель точнее.
    one = {"type": "object", "properties": {"ru": {"type": "string"}}, "required": ["ru"]}
    for s in kk:
        if s.get("masked_ru") and not _left_kazakh(s["masked_ru"]):
            continue
        parts = []
        for sentence in re.split(r"(?<=[.?!])\s+", s["masked"]):
            if not sentence.strip():
                continue
            # сначала без словаря: на одной фразе он модели мешает; со словарём — запасной вариант
            best = sentence
            for glossary_line in ("", _glossary_for(sentence, terms)):
                try:
                    data = llm.ask_json(
                        TRANSLATE_ONE.format(glossary=glossary_line, line=sentence),
                        one, "повторный перевод реплики", log, max_tokens=len(sentence) + 120,
                        model=model)
                except Exception:  # noqa: BLE001
                    continue
                answer = (data.get("ru") or "").strip()
                if answer and not _left_kazakh(answer):
                    best = answer
                    break
            parts.append(best)
        s["masked_ru"] = " ".join(parts)
        if _left_kazakh(s["masked_ru"]):
            s["untranslated"] = True        # врач увидит отметку у реплики


def load_template(template_id: str) -> dict:
    with open(TEMPLATES / f"{template_id}.json", encoding="utf-8") as f:
        return json.load(f)


def list_templates() -> list[dict]:
    out = []
    for path in sorted(TEMPLATES.glob("*.json")):
        t = json.loads(path.read_text(encoding="utf-8"))
        out.append({"id": t["id"], "title": t["title"]})
    return out


def _lines(segments: list[dict], with_roles: bool) -> str:
    rows = []
    for s in segments:
        who = {"doctor": "Врач: ", "patient": "Пациент: "}.get(s.get("role", ""), "") if with_roles else ""
        rows.append(f"{s['n']}. {who}{s.get('masked_ru') or s['masked']}")
    return "\n".join(rows)


def assign_roles(segments: list[dict], log: list) -> None:
    """Расставляет роли. Если модель сбилась, остаётся правило: вопрос задаёт врач."""
    schema = {
        "type": "object",
        "properties": {"roles": {"type": "array", "items": {
            "type": "object",
            "properties": {"n": {"type": "integer"}, "who": {"type": "string", "enum": ["В", "П"]}},
            "required": ["n", "who"],
        }}},
        "required": ["roles"],
    }
    got: dict[int, str] = {}
    try:
        prompt = HEAD.format(lines=_lines(segments, False)) + ROLES.format(count=len(segments))
        data = llm.ask_json(prompt, schema, "роли говорящих", log,
                            max_tokens=len(segments) * 16 + 80)
        for row in data.get("roles", []):
            got[int(row["n"])] = "doctor" if row["who"] == "В" else "patient"
    except Exception:  # noqa: BLE001
        pass
    for s in segments:
        s["role"] = got.get(s["n"]) or ("doctor" if s["text"].rstrip().endswith("?") else "patient")


def _field_schema(field: dict) -> dict:
    lines = {"type": "array", "items": {"type": "integer"}}
    if field["kind"] == "vitals":
        props = {i["key"]: {"type": "string"} for i in field["items"]}
        return {"type": "object", "properties": {**props, "lines": lines},
                "required": [*props, "lines"]}
    if field["kind"] == "prescriptions":
        item = {"type": "object", "properties": {
            "drug": {"type": "string"}, "dose": {"type": "string"},
            "frequency": {"type": "string"}, "duration": {"type": "string"}, "lines": lines,
        }, "required": ["drug", "dose", "frequency", "duration", "lines"]}
        return {"type": "object", "properties": {"items": {"type": "array", "items": item}},
                "required": ["items"]}
    return {"type": "object", "properties": {"value": {"type": "string"}, "lines": lines},
            "required": ["value", "lines"]}


def _field_task(field: dict) -> str:
    if field["kind"] == "vitals":
        parts = "; ".join(f"{i['key']} — {i['hint']}" for i in field["items"])
        return (f"- {field['id']} ({field['title']}): показатели, которые назвал врач на приёме. "
                f"Только число, как прозвучало. Если показатель не назван, пустая строка. {parts}.")
    if field["kind"] == "prescriptions":
        return (f"- {field['id']} ({field['title']}): лекарства, которые врач назначил на этом приёме. "
                "Для каждого: drug — название, dose — разовая доза, frequency — сколько раз в день "
                "или при каком условии, duration — на какой срок. Чего не прозвучало, оставь пустым. "
                "То, что пациент принимал раньше сам, сюда не входит. Если врач ничего не назначил, "
                "верни пустой список.")
    return f"- {field['id']} ({field['title']}): {field['hint']}."


EMPTY_TALK = re.compile(
    r"не (?:упомина|упомян|указа|сообща|прозвуч|уточн|обсужда|называ)|нет данных|неизвестн|не известн",
    flags=re.I,
)


def _stems(text: str, min_len: int = 5) -> list[str]:
    return [w[:5] for w in re.findall(r"[^\W\d_]+", text.lower()) if len(w) >= min_len]


ECHO = re.compile(r"(?:[^:;.]{3,40}:\s*(?:нет|не было|отсутству\w+)[;.,]?\s*){2,}", flags=re.I)


def _clean(value: str) -> str:
    """Убирает фразы о том, чего в разговоре не было: пустое поле честнее."""
    # модель иногда переписывает подсказку списком «хронические болезни: нет; травмы: нет»
    value = ECHO.sub("", value).strip()
    parts = re.split(r"(?<=[.!?;])\s+", value)
    kept = [p for p in parts if p and not EMPTY_TALK.search(p)]
    return " ".join(kept).strip()


def _not_from_talk(value: str, quotes: list[dict], segments: list[dict]) -> str | None:
    """Находит фразу, слова которой в разговоре не звучали.

    Сверяем по основам слов: если в предложении меньше половины значимых слов
    встречается в разговоре, врач получает отметку «проверьте». Для реплик
    на казахском проверка отключена: там перевод, слова и должны отличаться.
    """
    by_n = {s["n"]: s for s in segments}
    if any(by_n.get(q["n"], {}).get("lang") == "kk" for q in quotes):
        return None
    heard = set(_stems(" ".join(s["text"] for s in segments), 4))
    for sentence in re.split(r"(?<=[.!?;])\s+", value):
        words = _stems(sentence)
        if len(words) >= 2 and sum(w in heard for w in words) / len(words) < 0.5:
            return sentence.strip()
    return None


def _quotes(lines: list, segments: list[dict]) -> list[dict]:
    by_n = {s["n"]: s for s in segments}
    out = []
    for n in lines or []:
        s = by_n.get(n) if isinstance(n, int) else None
        if s and all(q["n"] != n for q in out):
            out.append({"n": n, "start": s["start"], "text": s["text"]})
    return out[:3]


def extract(segments: list[dict], template: dict, masker, visit: date,
            log: list, on_progress=None) -> dict:
    """Возвращает поля листа: {id: {value, quotes, status, flags, ...}}."""
    head = HEAD.format(lines=_lines(segments, True))
    # для проверок «это прозвучало» годятся и оригинал, и перевод
    transcript = " ".join(s["text"] + " " + s.get("ru", "") for s in segments)
    by_id = {f["id"]: f for f in template["fields"]}
    raw: dict = {}

    for i, group in enumerate(template["groups"], 1):
        fields = [by_id[g] for g in group]
        if fields[0]["kind"] == "vitals":
            raw[fields[0]["id"]] = _vitals(fields[0], segments, log)
            if on_progress:
                on_progress(i, len(template["groups"]))
            continue
        schema = {"type": "object",
                  "properties": {f["id"]: _field_schema(f) for f in fields},
                  "required": [f["id"] for f in fields]}
        prompt = head + RULES.format(fields="\n".join(_field_task(f) for f in fields))
        try:
            raw.update(llm.ask_json(prompt, schema, "поля: " + ", ".join(f["title"] for f in fields), log))
        except Exception as e:  # noqa: BLE001  одно сбойное поле не должно ронять весь лист
            for f in fields:
                raw[f["id"]] = {"error": str(e)}
        if on_progress:
            on_progress(i, len(template["groups"]))

    result = {}
    for field in template["fields"]:
        result[field["id"]] = _finish(field, raw.get(field["id"]) or {}, segments,
                                      masker, visit, transcript)
    _no_allergy(result, segments)
    _cross_checks(result, template, visit)
    return result


# Числа словами: казахская модель речи пишет «жүз жиырма», а не «120».
NUMBER_WORDS = re.compile(
    r"\b(?:нөл|бір|екі|үш|төрт|бес|алты|жеті|сегіз|тоғыз|он|жиырма|отыз|қырық|елу|алпыс|жетпіс|"
    r"сексен|тоқсан|жүз|мың|сто|двести|двадцать|тридцать|сорок|пятьдесят|шестьдесят|семьдесят|"
    r"восемьдесят|девяносто)\b", flags=re.I)


# Слова, по которым понятно, что показатель назвали. Правило простое и жёсткое:
# если названия показателя в разговоре не было, значение в лист не попадает,
# что бы ни ответила модель. Так отсекаются выдуманные «120/80» и «36,6».
VITAL_WORDS = {
    "bp": r"давлени|\bад\b|қысым",
    "pulse": r"пульс|\bчсс\b|сердцебиени|тамыр соғ|жүрек соғ",
    "temperature": r"температур|градус|қызу",
    "spo2": r"сатурац|кислород|spo|оттег",
    "resp_rate": r"\bчдд\b|частота дыхани|дыханий|тыныс алу жиілі",
    "weight": r"\bвес\b|\bвеса\b|\bвесит|килограмм|\bкг\b|салмақ|салмағ",
    "weight_gain": r"прибав|салмақ қос",
    "fundal_height": r"\bвдм\b|дна матки|жатыр түб",
    "abdomen": r"\bож\b|окружность живота|іш шеңбер|іштің шеңбер",
    "fetal_hr": r"сердцебиение плода|чсс плода|ұрықтың жүрек|ұрық жүрек",
}


def _named(item: dict, transcript: str) -> bool:
    pattern = item.get("match") or VITAL_WORDS.get(item["key"])
    return bool(pattern and re.search(pattern, transcript.lower()))


def _has_numbers(masked: str) -> bool:
    """Есть ли в строке числа: цифрами или словами. Метки вида [ПАЦИЕНТ_1] не в счёт."""
    text = TOKEN.sub("", masked)
    return bool(re.search(r"\d", text) or NUMBER_WORDS.search(text))


def _vitals(field: dict, segments: list[dict], log: list) -> dict:
    """Показатели ищем отдельным коротким запросом: только строки с числами.

    На полном разговоре маленькая модель теряется и возвращает пустые значения,
    а на нескольких строках с числами отвечает точно и отличает измеренное
    врачом от рассказанного пациентом.
    """
    talk = " ".join(s["text"] + " " + s.get("ru", "") for s in segments)
    if not any(_named(i, talk) for i in field["items"]):
        return {}                   # показатели не называли: модель даже не спрашиваем
    rows = [s for s in segments if _has_numbers(s.get("masked_ru") or s["masked"])]
    if not rows:
        return {}
    items = "\n".join(f"- {i['key']}: {i['hint']}" for i in field["items"])
    try:
        return llm.ask_json(VITALS.format(lines=_lines(rows, True), items=items),
                            _field_schema(field), "объективные показатели", log, max_tokens=300)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


NO_ALLERGY = re.compile(r"нет аллерги|аллерги\w* нет|аллерги\w* не было|аллерги\w* жоқ|аллергиям жоқ", flags=re.I)


ALLERGY_TALK = re.compile(r"аллерг|непереносим", flags=re.I)


def _no_allergy(fields: dict, segments: list[dict]) -> None:
    """Поле аллергий проверяет код, в обе стороны.

    Если об аллергии в разговоре не говорили, поле остаётся пустым, что бы ни
    написала модель: запись «аллергии нет» без вопроса врача опасна.
    Если пациент прямо сказал, что аллергии нет, а модель оставила поле пустым,
    фразу находит код."""
    f = fields.get("allergies")
    if not f:
        return
    talk = " ".join(s["text"] + " " + s.get("ru", "") for s in segments)
    if f.get("value") and not ALLERGY_TALK.search(talk):
        f.update({"value": "", "quotes": [], "status": "empty", "flags": []})
    if f.get("value"):
        return
    for s in segments:
        if NO_ALLERGY.search(s["text"] + " " + s.get("ru", "")):
            f["value"] = "Аллергии нет, со слов пациента."
            f["quotes"] = [{"n": s["n"], "start": s["start"], "text": s["text"]}]
            f["status"] = "ok"
            return


def _finish(field: dict, data: dict, segments: list[dict], masker, visit: date,
            transcript: str) -> dict:
    """Доводит ответ модели: возвращает данные вместо меток, нормализует, помечает."""
    out = {"title": field["title"], "kind": field["kind"], "required": field.get("required", False),
           "flags": [], "edited": False}
    if data.get("error"):
        out["flags"].append({"level": "warn", "text": "Модель не смогла заполнить поле, заполните вручную."})

    if field["kind"] == "vitals":
        items = []
        for item in field["items"]:
            value, note = calc.norm_vital(item, masker.unmask(str(data.get(item["key"], "") or "")))
            if value and not _named(item, transcript):
                value, note = "", None
            if value and not calc.heard(value.replace("/", " "), transcript):
                if NUMBER_WORDS.search(transcript):
                    # число могло прозвучать словами: оставляем, но просим проверить
                    note = "число записано со слов, проверьте"
                else:
                    # такого числа никто не произносил: в лист оно не попадает
                    value, note = "", None
            items.append({"key": item["key"], "title": item["title"], "unit": item["unit"],
                          "value": value, "note": note})
            if note:
                out["flags"].append({"level": "warn", "text": f"{item['title']}: {note}"})
        out["items"] = items
        out["quotes"] = _quotes(data.get("lines"), segments)
        out["value"] = "; ".join(f"{i['title']} {i['value']} {i['unit']}" for i in items if i["value"])

    elif field["kind"] == "prescriptions":
        items = []
        for p in data.get("items") or []:
            drug = masker.unmask((p.get("drug") or "").strip())
            if not drug:
                continue
            # модель иногда пишет дозу как время: «5:00 мг» вместо «500 мг»
            p["dose"] = re.sub(r"(\d):(\d\d)(?=\s*(?:мг|мкг|г|мл|ед))", r"\1\2", p.get("dose") or "")
            items.append({
                "drug": drug[0].upper() + drug[1:],
                "dose": masker.unmask((p.get("dose") or "").strip()),
                "frequency": masker.unmask((p.get("frequency") or "").strip()),
                "duration": masker.unmask((p.get("duration") or "").strip()),
                "quotes": _quotes(p.get("lines"), segments),
            })
        out["items"] = items
        seen: set[int] = set()
        out["quotes"] = [q for p in items for q in p["quotes"]
                         if not (q["n"] in seen or seen.add(q["n"]))][:3]
        out["value"] = "; ".join(
            " ".join(x for x in (p["drug"], p["dose"], p["frequency"], p["duration"]) if x)
            for p in items)

    else:
        value = _clean(masker.unmask((data.get("value") or "").strip()))
        if re.fullmatch(r"(нет|не указано|не прозвучало|отсутствует|-|—)\.?", value.lower()):
            value = ""
        out["value"] = value
        out["quotes"] = _quotes(data.get("lines"), segments) if value else []
        if value and field["kind"] == "text":
            odd = _not_from_talk(value, out["quotes"], segments)
            if odd:
                out["flags"].append({"level": "warn",
                                     "text": f"Этих слов не было в разговоре, проверьте: «{odd}»"})

        if field["kind"] == "diagnosis" and value:
            out["icd"] = calc.icd_pick(value)
            if not out["icd"]["code"]:
                out["flags"].append({"level": "warn", "text": "Код МКБ-10 не подобран, выберите из списка."})
            elif not out["icd"]["sure"]:
                out["flags"].append({"level": "warn", "text": "Проверьте код МКБ-10: подходит несколько."})

        if field["kind"] in ("date_future", "date_past") and value:
            resolver = dates.resolve_future if field["kind"] == "date_future" else dates.resolve_past
            r = resolver(value, visit)
            out["raw"] = value
            out["date"] = r["date"].isoformat() if r["date"] else None
            out["rule"] = r["rule"]
            if not r["date"]:
                out["flags"].append({"level": "warn", "text": "Дату не удалось определить, укажите вручную."})

    if not out["value"]:
        out["status"] = "empty"
    elif out["flags"]:
        out["status"] = "check"
    else:
        out["status"] = "ok"
    return out


def dictated(field: dict, segments: list[dict], masker, visit: date, log: list) -> dict:
    """Разбор продиктованного врачом поля: назначения или показатели."""
    translate(segments, log)
    for s in segments:
        if s.get("masked_ru"):
            s["ru"] = masker.unmask(s["masked_ru"])
    transcript = " ".join(s["text"] + " " + s.get("ru", "") for s in segments)
    if field["kind"] == "vitals":
        data = _vitals(field, segments, log)
    else:
        schema = {"type": "object", "properties": {field["id"]: _field_schema(field)},
                  "required": [field["id"]]}
        prompt = HEAD.format(lines=_lines(segments, True)) + RULES.format(fields=_field_task(field))
        data = llm.ask_json(prompt, schema, "диктовка: " + field["title"], log).get(field["id"]) or {}
    return _finish(field, data, segments, masker, visit, transcript)


def _cross_checks(fields: dict, template: dict, visit: date) -> None:
    """Проверки между полями: аллергия против назначений, расчёт срока беременности."""
    rx = fields.get("prescriptions", {})
    conflicts = calc.allergy_conflicts(fields.get("allergies", {}).get("value", ""), rx.get("items", []))

    lmp = fields.get("lmp")
    if lmp and lmp.get("date"):
        preg = calc.pregnancy(date.fromisoformat(lmp["date"]), visit)
        if preg:
            lmp["pregnancy"] = preg
            if template.get("pregnancy_checks"):
                conflicts += calc.pregnancy_conflicts(rx.get("items", []))

    for c in conflicts:
        rx.setdefault("flags", []).append({"level": "danger", "text": c["text"], "drug": c["drug"]})
    if conflicts:
        rx["status"] = "danger"


def recheck(fields: dict, template: dict, visit: date) -> dict:
    """Пересчёт после правки врача: даты, срок, код МКБ-10, предупреждения."""
    for f in fields.values():
        f["flags"] = [x for x in f.get("flags", []) if x["level"] != "danger"]
        if f["kind"] in ("date_future", "date_past"):
            raw = f.get("raw") or f.get("value") or ""
            resolver = dates.resolve_future if f["kind"] == "date_future" else dates.resolve_past
            r = resolver(raw, visit) if raw else {"date": None, "rule": ""}
            f["date"] = r["date"].isoformat() if r["date"] else None
            f["rule"] = r["rule"]
            f.pop("pregnancy", None)
        if f["kind"] == "prescriptions":
            f["value"] = "; ".join(
                " ".join(x for x in (p.get("drug"), p.get("dose"), p.get("frequency"), p.get("duration")) if x)
                for p in f.get("items", []))
        if f["kind"] == "vitals":
            f["value"] = "; ".join(f"{i['title']} {i['value']} {i['unit']}"
                                   for i in f.get("items", []) if i.get("value"))
        if f.get("status") == "danger":
            f["status"] = "ok"
        if not f.get("value"):
            f["status"] = "empty"
        elif f.get("status") == "empty":
            f["status"] = "ok"
    _cross_checks(fields, template, visit)
    return fields
