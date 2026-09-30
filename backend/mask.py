"""Маскирование персональных данных перед обращением к модели.

Модель локальная, но персональные данные ей всё равно не нужны: для листа
консультации важно, что сказано, а не чей это ИИН. Маскирование закрывает
требование кейса и даёт второй слой защиты: в журналы и в запрос к модели
попадают метки, а таблица замен остаётся в базе приёма.

Что маскируется:
  ИИН, телефоны, почта           — правилами
  адреса                          — правилами по словам «улица», «көшесі» и т. п.
  ФИО пациента и врача            — известны заранее, ищутся во всех падежах
  остальные имена и места         — Natasha (открытая библиотека)
"""

import re

try:
    from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter

    _segmenter = Segmenter()
    _ner = NewsNERTagger(NewsEmbedding())
except Exception:  # noqa: BLE001  библиотека не установлена: работаем правилами
    _segmenter = _ner = None

# Фамилии в названиях болезней, проб и шкал — это термины, а не люди.
EPONYMS = {
    "нечипоренко", "зимницкому", "зимницкого", "пастернацкого", "крона", "паркинсона",
    "альцгеймера", "дауна", "боткина", "леопольда", "апгар", "папаниколау",
    "вассермана", "кесарево", "щеткина", "блюмберга", "ортнера", "мерфи", "рейно",
    "кушинга", "грейвса", "хашимото", "бехтерева", "манту", "реберга", "холтер",
    "холтера", "короткова", "пирке",
}

IIN = re.compile(
    r"(?<!\d)(?:\d{12}|\d{6}[\s\-]\d{6}|\d{6}[\s\-]\d{3}[\s\-]\d{3}"
    r"|\d{3}[\s\-]\d{3}[\s\-]\d{3}[\s\-]\d{3}|\d{4}[\s\-]\d{4}[\s\-]\d{4})(?!\d)"
)
PHONE = re.compile(
    r"(?<![\d\w])(?:\+7|8|7)[\s\-\(]*\d{3}[\s\-\)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
)
# Цифры, продиктованные группами: «90 01 01 40 00 13» или «8, 701, 000, 00, 00».
# Двенадцать цифр подряд — ИИН, десять-одиннадцать с началом на 7 или 8 — телефон.
DIGIT_GROUPS = re.compile(r"(?<![\d\w])\d{1,6}(?:[\s,\-]{1,2}\d{1,6}){1,11}(?![\d\w])")
# После слов «ИИН» и «телефон» маскируем любые цифры, даже если их число не сошлось.
AFTER_WORD = re.compile(
    r"(?:\bИИН\b|\bЖСН\b|\bтелефон\w*|\bномер\w*\s+телефон\w*)[^\d\[]{0,12}"
    r"((?:\d[\s,\-]{0,2}){6,14}\d)",
    flags=re.I,
)
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
ADDRESS_RU = re.compile(
    r"(?:\bул\.|\bулиц\w*|\bпроспект\w*|\bпр-т|\bмкр\.?|\bмикрорайон\w*|\bпереул\w*)"
    r"\s+[А-ЯЁӘҒҚҢӨҰҮҺІа-яёәғқңөұүһі\-]+(?:\s+[А-ЯЁӘҒҚҢӨҰҮҺІ][а-яёәғқңөұүһі\-]+)?"
    r"(?:[,\s]+(?:д\.|дом)?\s*\d+[а-я]?\b(?:[,\s]+(?:кв\.|квартира)\s*\d+)?)?",
    flags=re.I,
)
ADDRESS_KK = re.compile(
    r"\b[А-ЯЁӘҒҚҢӨҰҮҺІ][\w\-]+\s+(?:көшесі|көшесінде|даңғылы|даңғылында|ықшамауданы)"
    r"(?:[,\s]+\d+[а-я]?\b(?:[,\s]+\d+\s*(?:пәтер))?)?",
)
BIRTH = re.compile(
    r"(?:дата рождения|родил\w+|туған\w*)\D{0,20}"
    r"(\d{1,2}[.\s/]\d{1,2}[.\s/]\d{2,4}|\d{1,2}\s+[а-яё]+\s+\d{4})",
    flags=re.I,
)


def iin_valid(digits: str) -> bool:
    """Контрольная цифра ИИН: так отличаем настоящий ИИН от случайных 12 цифр."""
    d = [int(c) for c in re.sub(r"\D", "", digits)]
    if len(d) != 12:
        return False
    s = sum((i + 1) * d[i] for i in range(11)) % 11
    if s == 10:
        weights = [3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2]
        s = sum(weights[i] * d[i] for i in range(11)) % 11
    return s == d[11]


def _name_pattern(full_name: str) -> re.Pattern | None:
    """ФИО во всех падежах: основа слова плюс до трёх букв окончания."""
    parts = []
    for word in re.findall(r"[^\W\d_]+", full_name or ""):
        if len(word) < 3:
            continue
        stem = word[:-1] if len(word) > 4 else word
        parts.append(re.escape(stem) + r"\w{0,3}")
    if not parts:
        return None
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", flags=re.I)


class Masker:
    """Одна таблица замен на весь приём: одинаковые данные получают одну метку."""

    def __init__(self, patient_name: str = "", doctor_name: str = ""):
        self.table: list[dict] = []          # [{token, value, kind}]
        self._by_value: dict[str, str] = {}
        self._count: dict[str, int] = {}
        self._patient = _name_pattern(patient_name)
        self._doctor = _name_pattern(doctor_name)

    def _token(self, kind: str, value: str) -> str:
        key = kind + ":" + re.sub(r"\s+", " ", value.strip().lower())
        if key in self._by_value:
            return self._by_value[key]
        self._count[kind] = self._count.get(kind, 0) + 1
        token = f"[{kind}_{self._count[kind]}]"
        self._by_value[key] = token
        self.table.append({"token": token, "value": value.strip(), "kind": kind})
        return token

    def _sub(self, pattern: re.Pattern, kind: str, text: str) -> str:
        return pattern.sub(lambda m: self._token(kind, m.group(0)), text)

    def mask(self, text: str, lang: str = "ru") -> str:
        text = self._sub(IIN, "ИИН", text)
        text = self._sub(PHONE, "ТЕЛ", text)
        text = AFTER_WORD.sub(self._after_word, text)
        text = DIGIT_GROUPS.sub(self._digit_groups, text)
        text = self._sub(EMAIL, "ПОЧТА", text)
        text = BIRTH.sub(
            lambda m: m.group(0).replace(m.group(1), self._token("ДАТА_РОЖД", m.group(1))), text)
        text = self._sub(ADDRESS_RU, "АДРЕС", text)
        text = self._sub(ADDRESS_KK, "АДРЕС", text)
        if self._patient:
            text = self._sub(self._patient, "ПАЦИЕНТ", text)
        if self._doctor:
            text = self._sub(self._doctor, "ВРАЧ", text)
        # Библиотека поиска имён обучена на русском. На казахском тексте она принимает
        # обычные слова за имена («созылмалы гастрит» становился [МЕСТО_1]) и портит фразу.
        # Поэтому казахские реплики маскируются только правилами и по известным ФИО.
        if lang == "kk":
            return text
        return self._mask_names(text)

    def _after_word(self, m: re.Match) -> str:
        kind = "ТЕЛ" if m.group(0).lower().lstrip().startswith(("тел", "ном")) else "ИИН"
        return m.group(0).replace(m.group(1), self._token(kind, m.group(1)))

    def _digit_groups(self, m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 12:
            return self._token("ИИН", m.group(0))
        if len(digits) in (10, 11) and digits[0] in "78":
            return self._token("ТЕЛ", m.group(0))
        return m.group(0)

    def _mask_names(self, text: str) -> str:
        if _ner is None:
            return text
        doc = Doc(text)
        doc.segment(_segmenter)
        doc.tag_ner(_ner)
        # с конца, чтобы не сбивать позиции
        for span in sorted(doc.spans, key=lambda s: -s.start):
            if span.type not in ("PER", "LOC"):
                continue
            chunk = text[span.start:span.stop]
            if "[" in chunk or "]" in chunk:
                continue
            # Одиночное слово в начале фразы библиотека часто принимает за имя
            # («Напомните, когда…», «Менструации с какого возраста»). ФИО пациента
            # и врача ищутся отдельно, поэтому такие совпадения пропускаем.
            if not text[:span.start].strip() and " " not in chunk.strip():
                continue
            if any(w.lower() in EPONYMS for w in chunk.split()):
                continue
            token = self._token("ИМЯ" if span.type == "PER" else "МЕСТО", chunk)
            text = text[:span.start] + token + text[span.stop:]
        return text

    def unmask(self, text: str) -> str:
        """Метки обратно в данные. Каждое написание (падеж) хранится своей меткой,
        поэтому текст восстанавливается точно."""
        if not text:
            return text
        for row in self.table:
            text = re.sub(re.escape(row["token"]), lambda _m, v=row["value"]: v, text, flags=re.I)
        return text

    def public_table(self) -> list[dict]:
        """Таблица замен для экрана: значения прикрыты, чтобы их не было видно с плеча."""
        return [{"token": r["token"], "kind": r["kind"], "value": cover(r["value"])}
                for r in self.table]

    def dump(self) -> list[dict]:
        return list(self.table)

    @classmethod
    def load(cls, table: list[dict]) -> "Masker":
        m = cls()
        m.table = list(table)
        return m


def cover(value: str) -> str:
    """Иванова -> Ив•••ва, 900101400013 -> 9001••••••13."""
    v = value.strip()
    if len(v) <= 4:
        return v[0] + "•" * (len(v) - 1)
    return v[:4 if v[:4].isdigit() else 2] + "•" * max(3, len(v) - 6) + v[-2:]


def leaks(text: str) -> list[str]:
    """Проверка перед отправкой модели: не остались ли ИИН или телефон."""
    found = [m.group(0) for m in IIN.finditer(text)]
    found += [m.group(0) for m in PHONE.finditer(text)]
    for m in DIGIT_GROUPS.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 12 or (len(digits) in (10, 11) and digits[0] in "78"):
            found.append(m.group(0))
    return found


if __name__ == "__main__":
    m = Masker("Нурланова Айгерим", "Ахметов Данияр")
    sample = (
        "Здравствуйте, Айгерим. ИИН 900101400013, телефон +7 701 000 00 00. "
        "Живу на улице Кенесары, дом 40, дома мама, Сауле, тоже болеет. "
        "Анализ мочи по Нечипоренко сдавала. Нурлановой назначен амоксициллин."
    )
    masked = m.mask(sample)
    print(masked)
    print(m.public_table())
    print(leaks(masked))
    print(m.unmask(masked))
