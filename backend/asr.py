"""Распознавание речи приёма. Всё локально, через whisper.cpp.

Основа взята из Hattama: тот же whisper large-v3-turbo и тот же приём
для смешанной речи (два прохода и выбор варианта по каждой реплике).
Отличие одно: в подсказку модели уходит не список участников совещания,
а словарь специальности врача — препараты, сокращения, термины.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pick_model() -> str:
    """Модель ищем в трёх местах: переменная окружения, папка проекта, папка Hattama."""
    candidates = [
        os.environ.get("WHISPER_MODEL"),
        str(ROOT / "models" / "ggml-large-v3-turbo.bin"),
        os.path.expanduser("~/Desktop/models/ggml-large-v3-turbo.bin"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    raise FileNotFoundError(
        "Не найдена модель распознавания речи. Положите ggml-large-v3-turbo.bin "
        "в папку models или укажите путь в WHISPER_MODEL."
    )


def kazakh_model() -> str | None:
    """Отдельная модель для казахской речи: whisper large-v3-turbo, дообученный
    на корпусе KSC2 (около 1000 часов казахской речи, ISSAI). Стандартный whisper
    на казахском ошибается слишком часто. Если файла нет, работает стандартная модель."""
    candidates = [
        os.environ.get("WHISPER_MODEL_KK"),
        str(ROOT / "models" / "ggml-whisper-turbo-ksc2.bin"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


WHISPER = os.environ.get("WHISPER_BIN") or shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"
THREADS = str(min(8, os.cpu_count() or 4))

# Только мягкое выравнивание громкости: пациент обычно сидит дальше от микрофона,
# чем врач. Агрессивное шумоподавление съедает тихие слова.
AUDIO_FILTERS = "highpass=f=70,speechnorm=e=6.25:r=0.00001:l=1"
USE_CLEAN = os.environ.get("HATSHY_AUDIO_CLEAN", "on").lower() != "off"


def duration(path: str) -> float:
    """Длительность в секундах. У записи из браузера её в файле нет,
    поэтому при неудаче считаем по расшифрованному звуку."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def loudness(wav: str) -> tuple[float, float]:
    """Средняя и пиковая громкость записи в децибелах."""
    mean, peak = -99.0, -99.0
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", wav, "-af", "volumedetect",
             "-f", "null", "-"],
            capture_output=True, text=True, check=True,
        )
        for line in out.stderr.splitlines():
            if "mean_volume" in line:
                mean = float(line.split(":")[1].strip().split()[0])
            if "max_volume" in line:
                peak = float(line.split(":")[1].strip().split()[0])
    except Exception:
        pass
    return mean, peak


# На тишине whisper не молчит, а выдумывает фразы из субтитров к видео.
# На приёме тишина бывает всегда (осмотр, измерение давления), поэтому такие
# фразы убираем. Список собран на записях с тишиной.
HALLUCINATIONS = re.compile(
    r"субтитр|dimatorzok|продолжение следует|спасибо за просмотр|дякую за перегляд"
    r"|подписывайтесь|ставьте лайк|amara\.org|thanks for watching|редактор .{0,20}корректор"
    r"|до новых встреч|всем пока",
    flags=re.I,
)


def drop_hallucinations(segments: list[dict]) -> list[dict]:
    out = []
    for s in segments:
        if HALLUCINATIONS.search(s["text"]):
            continue
        # зацикливание: одна и та же фраза три раза подряд
        if len(out) >= 2 and out[-1]["text"] == s["text"] == out[-2]["text"]:
            continue
        out.append(s)
    return out


def to_wav(src: str, clean: bool = True) -> str:
    """Любое аудио -> моно 16 кГц, как требует модель."""
    out = tempfile.mktemp(suffix=".wav")
    base = ["ffmpeg", "-y", "-i", src, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le"]
    tail = [out, "-loglevel", "error"]
    try:
        subprocess.run(base + (["-af", AUDIO_FILTERS] if clean else []) + tail, check=True)
    except subprocess.CalledProcessError:
        # если фильтры недоступны в этой сборке ffmpeg, работаем без них
        subprocess.run(base + tail, check=True)
    return out


KZ_LETTERS = set("әғқңөұүһі")
PROMPT_CHARS = 300


def build_prompt(vocabulary: list[str] | None) -> str:
    """Словарь для модели: препараты и сокращения специальности.

    Три ограничения, все проверены на записях:
    - только отдельные слова через запятую: связный русский текст склоняет
      модель к русскому, и казахская речь распознаётся хуже;
    - без казахских слов и фраз: с ними подсказка работает нестабильно;
    - не длиннее 300 символов: на длинной подсказке whisper молча пропускает
      куски речи (на тестовой записи терял 13 секунд из 85).
    Слова, которые не поместились, исправляются по словарю уже после
    распознавания, в fix_terms.
    """
    if not vocabulary:
        return ""
    picked, size = [], 0
    for term in vocabulary:
        if " " in term or set(term.lower()) & KZ_LETTERS:
            continue
        if size + len(term) + 2 > PROMPT_CHARS:
            break
        picked.append(term)
        size += len(term) + 2
    return ", ".join(picked) + "." if picked else ""


def words(wav: str, language: str = "auto", prompt: str = "", model: str | None = None) -> list[dict]:
    """Слова с метками времени: [{start, end, text}] в секундах."""
    out_prefix = tempfile.mktemp()
    cmd = [
        WHISPER, "-m", model or _pick_model(), "-f", wav,
        "-l", language,
        "-oj", "-of", out_prefix,
        "-t", THREADS,
        "-bs", "5", "-bo", "5",     # поиск лучшего варианта вместо первого попавшегося
        "-et", "2.6",
        *(["--prompt", prompt] if prompt else []),
        "-ml", "1", "-sow",         # по одному слову в элементе вывода
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    with open(out_prefix + ".json", encoding="utf-8") as f:
        data = json.load(f)
    try:
        os.remove(out_prefix + ".json")
    except OSError:
        pass

    out = []
    for seg in data.get("transcription", []):
        text = seg.get("text", "")
        if not text.strip():
            continue
        offsets = seg.get("offsets", {})
        out.append({
            "start": offsets.get("from", 0) / 1000,
            "end": offsets.get("to", 0) / 1000,
            "text": text,
        })
    return out


def transcribe(wav: str, language: str = "auto", prompt: str = "",
               model: str | None = None) -> list[dict]:
    """Возвращает реплики: [{start, end, text}] в секундах.

    Whisper сам режет запись на длинные куски, в один кусок попадают и вопрос
    врача, и ответ пациента. Поэтому просим у него отдельные слова с метками
    времени и сами собираем из них предложения: так у каждой фразы своё время,
    роли расставляются точнее, а цитата к полю получается короткой.
    """
    return to_sentences(words(wav, language, prompt, model))


# --- Казахская и смешанная речь: распознавание по фразам ---------------------------
# Казахская модель точнее в словах, но не ставит знаки и не даёт меток времени:
# весь приём выходит одним куском. Поэтому запись режется на фразы по паузам,
# и каждая фраза распознаётся отдельно. У фразы сразу есть своё время, а русский
# кусок не тянет за собой соседний казахский.

def speech_chunks(wav: str, min_silence: float = 0.35, pad: float = 0.12,
                  max_len: float = 18.0) -> list[tuple[float, float]]:
    """Интервалы речи между паузами, в секундах."""
    total = duration(wav)
    mean, _peak = loudness(wav)
    noise = max(-50.0, min(-28.0, mean - 10.0))     # порог тишины относительно громкости записи
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", wav, "-af",
         f"silencedetect=noise={noise:.0f}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    silences, start = [], None
    for line in out.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None:
        silences.append((start, total))

    speech, prev = [], 0.0
    for s0, s1 in silences:
        if s0 - prev > 0.25:
            speech.append((prev, s0))
        prev = s1
    if total - prev > 0.25:
        speech.append((prev, total))

    chunks: list[tuple[float, float]] = []
    for a, b in speech:
        a, b = max(0.0, a - pad), min(total, b + pad)
        while b - a > max_len:                      # слишком длинная фраза без пауз
            chunks.append((a, a + max_len))
            a += max_len
        chunks.append((a, b))
    return chunks


def _cut(wav: str, chunks: list[tuple[float, float]], folder: str) -> list[str]:
    """Нарезает wav на фразы. Формат уже приведён: 16 кГц, моно, 16 бит."""
    import wave

    paths = []
    with wave.open(wav, "rb") as src:
        rate, width, channels = src.getframerate(), src.getsampwidth(), src.getnchannels()
        for i, (a, b) in enumerate(chunks):
            src.setpos(min(src.getnframes(), int(a * rate)))
            frames = src.readframes(int((b - a) * rate))
            path = os.path.join(folder, f"c{i:04d}.wav")
            with wave.open(path, "wb") as dst:
                dst.setnchannels(channels)
                dst.setsampwidth(width)
                dst.setframerate(rate)
                dst.writeframes(frames)
            paths.append(path)
    return paths


def _batch(paths: list[str], language: str, prompt: str, model: str | None) -> list[str]:
    """Распознаёт много коротких файлов за один запуск: модель грузится один раз."""
    if not paths:
        return []
    cmd = [
        WHISPER, "-m", model or _pick_model(), "-l", language, "-oj", "-nt",
        "-t", THREADS, "-bs", "5", "-bo", "5",
        *(["--prompt", prompt] if prompt else []),
        *paths,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    texts = []
    for path in paths:
        try:
            with open(path + ".json", encoding="utf-8") as f:
                data = json.load(f)
            texts.append(" ".join(x.get("text", "").strip() for x in data.get("transcription", [])).strip())
        except (OSError, json.JSONDecodeError):
            texts.append("")
    return texts


# Звуки раздумья: «ыыы», «эээ», «м-м». В лист они не нужны и мешают модели.
FILLER = re.compile(r"(?<![^\W\d_])(?:[ыэаоуе]{2,}|м+-?м+|э-э+|а-а+|ы)(?![^\W\d_])[,.]?\s*", flags=re.I)


def drop_fillers(text: str) -> str:
    return re.sub(r"\s+", " ", FILLER.sub("", text)).strip(" ,")


KK_QUESTION = {"ма", "ме", "ба", "бе", "па", "пе", "ше", "ша", "қашан", "қанша", "қалай", "қандай",
               "неше", "кім", "қайда", "неге", "қай"}


def _kk_sentence(text: str) -> str:
    """Казахская модель пишет без заглавных букв и знаков: добавляем их по правилам."""
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        return ""
    words_ = re.findall(r"[^\W\d_]+", text.lower())
    ask = bool(words_) and (words_[-1] in KK_QUESTION or "бар ма" in text.lower()
                            or any(w in KK_QUESTION for w in words_[:2]) or words_[0] == "не")
    return text[0].upper() + text[1:] + ("?" if ask else ".")


def by_phrases(wav_clean: str, wav_raw: str, language: str, prompt: str, kk_model: str) -> list[dict]:
    """Распознавание по фразам: для казахского и смешанного приёма."""
    chunks = speech_chunks(wav_raw)
    folder = tempfile.mkdtemp(prefix="hatshy_")
    try:
        paths = _cut(wav_clean, chunks, folder)
        kk = _batch(paths, "kk", "", kk_model)
        ru = _batch(paths, "ru", prompt, None) if language == "mixed" else [""] * len(paths)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    segments = []
    for (a, b), kk_text, ru_text in zip(chunks, kk, ru):
        # В смешанном приёме фраза считается казахской, если казахская модель
        # услышала в ней заметную долю казахских слов. Иначе берём русский вариант:
        # у стандартной модели на русском точнее слова и есть знаки препинания.
        kk_text, ru_text = drop_fillers(kk_text), drop_fillers(ru_text)
        if language == "mixed" and kazakh_score(kk_text) < 0.3:
            text, lang = ru_text, "ru"
        else:
            text, lang = _kk_sentence(kk_text), "kk"
        if len(re.findall(r"[^\W\d_]", text)) < 2:     # от фразы остались одни знаки
            continue
        if text:
            segments.append({"start": a, "end": b, "text": text, "lang": lang})
    return segments


def to_sentences(words: list[dict], pause: float = 1.2, max_chars: int = 240) -> list[dict]:
    """Слова -> предложения. Граница: знак конца предложения, длинная пауза
    или слишком длинная фраза без знаков."""
    out: list[dict] = []
    cur: dict | None = None
    for i, w in enumerate(words):
        if cur is None:
            cur = {"start": w["start"], "end": w["end"], "text": ""}
        # whisper ставит пробел перед началом слова; если пробела нет, это продолжение слова
        cur["text"] += w["text"]
        cur["end"] = w["end"]
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = cur["text"].rstrip().endswith((".", "?", "!", "…"))
        # «38.» перед «5» — это десятичная дробь, а не конец предложения
        if ends and nxt and cur["text"].rstrip()[-2:-1].isdigit() and nxt["text"].strip()[:1].isdigit() \
                and not nxt["text"].startswith(" "):
            ends = False
        gap = (nxt["start"] - w["end"]) if nxt else 0
        if nxt is None or ends or gap > pause or len(cur["text"]) > max_chars:
            cur["text"] = re.sub(r"\s+", " ", cur["text"]).strip()
            if cur["text"]:
                out.append(cur)
            cur = None
    return out


# --- Исправление терминов по словарю ----------------------------------------------

def fix_terms(text: str, vocabulary: list[str] | None) -> tuple[str, list[dict]]:
    """Исправляет искажённые термины по словарю специальности.

    «танзиллит» -> «тонзиллит», «бупрофин» -> «ибупрофен». Правим осторожно:
    только длинные слова, только при большом сходстве и только если слово
    не является формой термина (падеж не трогаем). Каждая правка возвращается
    списком, чтобы её было видно.
    """
    if not vocabulary:
        return text, []
    from rapidfuzz import fuzz

    terms = [t.lower() for t in vocabulary if " " not in t and len(t) >= 7]
    fixes: list[dict] = []

    def repl(m: re.Match) -> str:
        word = m.group(0)
        low = word.lower()
        if len(low) < 6:
            return word
        best, score = None, 0.0
        for t in terms:
            if low == t or low.startswith(t[:-2]):   # уже этот термин или его падеж
                return word
            r = fuzz.ratio(low, t)
            if r > score:
                best, score = t, r
        if best and score >= 82:
            fixed = best.capitalize() if word[0].isupper() else best
            fixes.append({"from": word, "to": fixed})
            return fixed
        return word

    return re.sub(r"[^\W\d_]+", repl, text), fixes


# --- Смешанная речь ------------------------------------------------------------
# Whisper определяет язык один раз на всю запись. На приёме, где русский
# и казахский чередуются, казахские фразы превращаются в похожие русские слова.
# Поэтому запись прогоняется дважды, и по каждой реплике выбирается тот вариант,
# который действительно на своём языке.

KZ_WORDS = {
    "керек", "қажет", "және", "үшін", "болады", "деп", "бар", "жоқ", "мен", "сіз",
    "біз", "осы", "бұл", "сол", "енді", "жақсы", "рахмет", "ия", "иә", "ме", "ма",
    "ба", "бе", "па", "пе", "да", "де", "та", "те", "күн", "күні", "апта", "ай",
    "ауырады", "ауырып", "дәрі", "бас", "іш", "жүрек", "бала", "дене", "қызу",
    "жөтел", "тамақ", "ұйқы", "қан", "жүкті", "салмақ",
}


def kazakh_score(text: str) -> float:
    """Доля казахских слов в реплике. Считаем по словам, а не по буквам:
    одна случайная казахская буква не должна объявлять всю фразу казахской."""
    words = re.findall(r"[^\W\d_]+", text.lower(), flags=re.UNICODE)
    if not words:
        return 0.0
    kz = sum(1 for w in words if set(w) & KZ_LETTERS or w in KZ_WORDS)
    return kz / len(words)


def _overlap(a: dict, b: dict) -> float:
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    shorter = min(a["end"] - a["start"], b["end"] - b["start"]) or 1
    return inter / shorter


def merge_languages(ru: list[dict], kk: list[dict], threshold: float = 0.25) -> list[dict]:
    """Русский проход берём за основу. Реплику заменяем казахским вариантом,
    только если он уверенно казахский: четверть слов и больше."""
    out = []
    for seg in ru:
        best = {**seg, "lang": "ru"}
        for other in kk:
            if _overlap(seg, other) < 0.5:
                continue
            score_kk, score_ru = kazakh_score(other["text"]), kazakh_score(seg["text"])
            if score_kk >= threshold and score_kk > score_ru:
                best = {**seg, "text": other["text"], "lang": "kk"}
            break
        out.append(best)

    # казахские реплики, которых русский проход не услышал вовсе
    for other in kk:
        if kazakh_score(other["text"]) < threshold:
            continue
        if any(_overlap(other, s) > 0.5 for s in out):
            continue
        out.append({**other, "lang": "kk"})

    out.sort(key=lambda s: s["start"])
    return out


class NoSpeech(RuntimeError):
    pass


def process(audio_path: str, language: str = "mixed",
            vocabulary: list[str] | None = None) -> tuple[list[dict], float]:
    """Запись приёма -> реплики [{n, start, end, text, lang}] и длительность записи.

    language: ru, kk или mixed (врач выбирает перед приёмом).
    """
    prompt = build_prompt(vocabulary)
    wav = to_wav(audio_path, clean=USE_CLEAN)
    try:
        total = duration(wav)
        raw = to_wav(audio_path, clean=False)
        _mean, peak = loudness(raw)
        if peak < -45:
            os.remove(raw)
            raise NoSpeech("В записи тишина. Проверьте, что микрофон включён и выбран в браузере.")
        kk_model = kazakh_model()
        if kk_model and language in ("kk", "mixed"):
            try:
                segments = by_phrases(wav, raw, language, prompt, kk_model)
            finally:
                os.remove(raw)
        elif language == "mixed":
            ru = transcribe(wav, "ru", prompt)
            kk = transcribe(wav, "kk", prompt)
            segments = merge_languages(ru, kk) if (ru and kk) else [{**s, "lang": "ru" if ru else "kk"} for s in (ru or kk)]
        else:
            lang = language if language in ("ru", "kk") else "auto"
            segments = [{**s, "lang": lang} for s in transcribe(wav, lang, prompt)]
        if os.path.exists(raw):
            os.remove(raw)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    segments = drop_hallucinations(segments)
    for i, s in enumerate(segments, 1):
        s["n"] = i
        s.setdefault("lang", "ru")
        fixed, fixes = fix_terms(s["text"], vocabulary)
        if fixes:
            s["heard"] = s["text"]      # как услышала модель, до словаря
            s["text"] = fixed
            s["fixes"] = fixes
    return segments, total


def silent_gaps(segments: list[dict], total: float, limit: float = 8.0) -> list[dict]:
    """Участки записи, где текста нет дольше limit секунд.

    Это либо настоящая тишина (врач осматривает пациента), либо пропуск
    распознавания. Отличить одно от другого без прослушивания нельзя, поэтому
    врач получает отметку с временем и может прослушать это место.
    """
    gaps, prev = [], 0.0
    for s in segments:
        if s["start"] - prev > limit:
            gaps.append({"from": round(prev, 1), "to": round(s["start"], 1)})
        prev = s["end"]
    if total and total - prev > limit:
        gaps.append({"from": round(prev, 1), "to": round(total, 1)})
    return gaps


if __name__ == "__main__":
    import sys
    import time

    t0 = time.time()
    vocab = None
    if len(sys.argv) > 3:
        import json as _json
        vocab = _json.load(open(ROOT / "backend" / "templates" / f"{sys.argv[3]}.json",
                                encoding="utf-8"))["vocabulary"]
    segs, total = process(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "mixed", vocab)
    print(f"Реплик: {len(segs)} за {time.time() - t0:.1f} сек, запись {total:.0f} сек\n")
    for s in segs:
        print(f"{s['n']:>3} [{s['start']:6.1f}] ({s['lang']}) {s['text']}")
