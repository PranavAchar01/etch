"""Cheap local screen in front of the picker (QA round 4, 2026-10-17).

Text that is not a board ask (prompt injection, SQL or script, keyboard mash, emoji, a huge paste, an impossible
part count, a language Etch cannot read) gets a plain answer here, with no model call. A few asks are rewritten
instead of stopped: repeated words collapse ("LED LED LED" -> "LED"), and short asks in Spanish, French, German,
Portuguese, Italian, Chinese, Japanese, Korean, Russian or Hindi that name a light, dark, blink or button map to the
English ask for the same recipe (keyword matching, no translation service).

screen(text) -> Verdict | None. None means: pass the text on unchanged.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

MAX_CHARS = 1200  # a spoken or typed board ask is a sentence or two; round 1's longest real ask was ~180 chars
MAX_DISCRETE = 24  # more single parts than this does not fit a hand-solderable board; strips are fine

ASK_EXAMPLE = "like a light that turns on when it gets dark"

# every decision, one JSON line, no ask text and nothing about who asked (QA round 5): time, kind, stop or rewrite,
# design or edit, ask length, language guess and intent. ETCH_PREFILTER_LOG overrides the path ("" turns it off).
LOG = os.environ.get("ETCH_PREFILTER_LOG", str(Path(__file__).resolve().parents[1] / "logs" / "prefilter.jsonl"))


@dataclass
class Verdict:
    kind: str  # empty | injection | code | long | too_many | repeat | language
    say: str
    stop: bool = True
    ask: str | None = None  # the rewritten ask when stop is False


INJECTION = re.compile(
    r"\b(ignore|disregard|forget|override|bypass)\b[\w\s,'-]{0,30}\b(instructions?|prompts?|rules|guidelines|"
    r"directions|system)\b"
    r"|\bsystem\s+prompt\b|\byou\s+are\s+now\b|\bjailbreak\w*\b|\bdeveloper\s+mode\b"
    r"|\b(reveal|print|show|repeat|leak|tell me)\b[\w\s]{0,20}\b(your|the)\s+(prompt|instructions|system|api\s*keys?|"
    r"secrets?|env\w*)\b"
    r"|\bapi[\s_-]*key\b|\bpretend\s+(to\s+be|you\s+are)\b|\bact\s+as\s+(an?\s+)?(ai|assistant|chatgpt|gpt|llm)\b",
    re.I,
)

CODE = re.compile(
    r"\b(drop|truncate|alter)\s+(table|database)\b|\bunion\s+(all\s+)?select\b|\bselect\s+\*"
    r"|\bselect\s+[\w,\s]+?\s+from\s+\w+\s+where\b|\binsert\s+into\s+\w+\s*(\(|values\b)"
    r"|\bdelete\s+from\s+\w+\s*(where\b|;)|\bupdate\s+\w+\s+set\s+\w+\s*="
    r"|'\s*(or|and)\s+'?\w+'?\s*=\s*'?\w+|;\s*--|\bor\s+1\s*=\s*1\b|<\s*script\b|\$\{|\{\{"
    r"|\brm\s+-rf\b|\bsudo\s|\bcurl\s+https?://|\beval\s*\(",
    re.I,
)

PART_NOUN = (
    r"(?:leds?|lights?|bulbs?|lamps?|buttons?|switch(?:es)?|motors?|servos?|resistors?|capacitors?|"
    r"sensors?|speakers?|buzzers?|transistors?|chips?|parts?|components?|batter(?:y|ies))"
)
# only plain adjectives may sit between the number and the part ("500 red LEDs"), so part numbers
# ("a 555 timer led") and units ("5000 mAh battery") never count
ADJ = (r"(?:red|green|blue|yellow|white|warm|cool|orange|purple|pink|rgb|small|tiny|little|big|large|bright|"
       r"separate|single|individual|more|extra|tactile|push|status|indicator|of|the|those|these)")
COUNT = re.compile(r"(?<![\w.-])(\d[\d,]*)\s+(?:" + ADJ + r"\s+){0,2}" + PART_NOUN + r"\b", re.I)
STRIP = re.compile(
    r"\b(strip|ring|matrix|neopixel|ws2812\w*|sk6812|addressable|panel)\b", re.I
)

# ---- other languages: script share, then stopwords for Latin-script languages
SCRIPTS = (
    "CJK",
    "HIRAGANA",
    "KATAKANA",
    "HANGUL",
    "CYRILLIC",
    "ARABIC",
    "HEBREW",
    "DEVANAGARI",
    "THAI",
    "GREEK",
    "BENGALI",
    "TAMIL",
    "TELUGU",
    "GUJARATI",
    "GURMUKHI",
    "KANNADA",
    "MALAYALAM",
)
STOP = {
    "es": {
        "el",
        "la",
        "los",
        "las",
        "una",
        "uno",
        "que",
        "con",
        "por",
        "para",
        "quiero",
        "cuando",
        "hace",
        "y",
        "se",
        "del",
        "luz",
        "placa",
        "enciende",
        "haz",
    },
    "fr": {
        "le",
        "les",
        "une",
        "un",
        "avec",
        "quand",
        "pour",
        "je",
        "veux",
        "qui",
        "est",
        "et",
        "du",
        "des",
        "la",
        "carte",
        "allume",
        "fais",
    },
    "de": {
        "der",
        "die",
        "das",
        "ein",
        "eine",
        "einen",
        "mit",
        "wenn",
        "ich",
        "möchte",
        "und",
        "ist",
        "zu",
        "es",
        "wird",
        "platine",
        "mach",
    },
    "pt": {
        "um",
        "uma",
        "com",
        "quando",
        "quero",
        "para",
        "que",
        "não",
        "e",
        "o",
        "os",
        "placa",
        "acende",
        "faça",
    },
    "it": {
        "il",
        "una",
        "uno",
        "con",
        "quando",
        "voglio",
        "che",
        "per",
        "e",
        "si",
        "della",
        "scheda",
        "accende",
    },
}
ENGLISH = {
    "the",
    "a",
    "an",
    "with",
    "when",
    "and",
    "that",
    "it",
    "i",
    "to",
    "of",
    "make",
    "want",
    "board",
    "on",
    "light",
    "led",
    "button",
    "is",
    "for",
    "me",
    "my",
    "lights",
    "turns",
    "up",
    "in",
    "build",
    "please",
}

# intent keywords (lowercase substrings) -> English asks that hit a recipe without the model
KW = {
    "dark": (
        "oscur",
        "noche",
        "nuit",
        "noir",
        "obscur",
        "dunkel",
        "nacht",
        "escur",
        "noite",
        "buio",
        "notte",
        "暗",
        "黑",
        "夜",
        "어두",
        "밤",
        "темн",
        "ноч",
        "अंधेर",
        "रात",
        "ظلام",
        "مظلم",
        "الظلام",
    ),
    "blink": (
        "parpade",
        "intermit",
        "clignot",
        "blink",
        "pisca",
        "lampeggi",
        "闪",
        "点滅",
        "깜빡",
        "мига",
        "мерца",
        "टिमटिम",
        "झपक",
        "يومض",
        "وميض",
    ),
    "button": (
        "botón",
        "boton",
        "pulsador",
        "bouton",
        "taster",
        "knopf",
        "botão",
        "botao",
        "pulsante",
        "按钮",
        "按键",
        "ボタン",
        "버튼",
        "кнопк",
        "बटन",
        "زر",
    ),
    "light": (
        "luz",
        "lámpara",
        "lampara",
        "lumière",
        "lumiere",
        "lampe",
        "licht",
        "lâmpada",
        "lampada",
        "luce",
        "灯",
        "亮",
        "ライト",
        "明かり",
        "불",
        "조명",
        "라이트",
        "свет",
        "лампа",
        "светодиод",
        "रोशनी",
        "बत्ती",
        "लाइट",
        "led",
        "ضوء",
        "مصباح",
        "إضاءة",
        "اضاءة",
    ),
}
INTENT_ASK = {
    "dark": "a light that turns on when it gets dark",
    "blink": "a 555 timer led flasher",
    "button": "a button that lights up a red led",
    "light": "a red led",
}


def _letters(text: str) -> list[str]:
    return [c for c in text if c.isalpha()]


def _foreign(text: str) -> str | None:
    """A language code when the ask is not English, else None."""
    letters = _letters(text)
    if not letters:
        return None
    other = sum(
        1 for c in letters if any(s in unicodedata.name(c, "") for s in SCRIPTS)
    )
    if other / len(letters) > 0.3:
        return "other script"
    words = re.findall(r"[^\W\d_]+", text.lower())
    en = sum(w in ENGLISH for w in words)
    best, hits = None, 0
    for lang, stop in STOP.items():
        n = sum(w in stop for w in words)
        if n > hits:
            best, hits = lang, n
    if best and hits >= 2 and hits > en:
        return best
    return None


def _intent(text: str) -> str | None:
    t = text.lower()
    for name in ("dark", "blink", "button", "light"):
        if any(k in t for k in KW[name]):
            return name
    return None


def _collapse(text: str) -> tuple[str, str | None, int]:
    """Runs of the same word squeezed to one. Returns (text, the repeated word, how many times)."""
    words = text.split()
    out: list[str] = []
    top, top_n, run = None, 1, 1
    for w in words:
        if (
            out
            and re.sub(r"\W", "", w.lower()) == re.sub(r"\W", "", out[-1].lower())
            and re.sub(r"\W", "", w)
        ):
            run += 1
            if run > top_n:
                top, top_n = out[-1], run
            continue
        run = 1
        out.append(w)
    return " ".join(out), top, top_n


def _log(t: str, edit: bool, v: Verdict | None) -> None:
    if not LOG:
        return
    rec = {
        "ts": round(time.time(), 1),
        "kind": v.kind if v else "pass",
        "action": "pass" if v is None else "stop" if v.stop else "rewrite",
        "edit": edit,
        "chars": len(t),
    }
    if v and v.kind == "language":
        rec["lang"] = _foreign(t)
        rec["intent"] = None if v.stop else _intent(t)
    try:
        Path(LOG).parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass  # the log is a QA aid; a full disk must never stop a board


def screen(text: str, edit: bool = False) -> Verdict | None:
    """Verdict for text that should not go on as written, else None. edit=True for a change to a built board."""
    t = (text or "").strip()
    v = _screen(t, edit)
    _log(t, edit, v)
    return v


def _screen(t: str, edit: bool) -> Verdict | None:
    if not re.search(r"[^\W_]", t):  # nothing but spaces, punctuation or emoji
        return Verdict(
            "empty",
            f"I did not catch a board there. Tell me what it should do, {ASK_EXAMPLE}."
            if not edit
            else "I did not catch a change there. Tell me what to change, like add a buzzer or make it blue.",
        )
    if len(t) > MAX_CHARS:
        return Verdict(
            "long",
            f"That is a long paste ({len(t):,} characters). I read up to {MAX_CHARS:,}, so tell me the board in a "
            "sentence or two: what it does and how it gets power.",
        )
    if INJECTION.search(t):
        return Verdict(
            "injection",
            f"I only design circuit boards, so I skipped that. Tell me what the board should do, {ASK_EXAMPLE}.",
        )
    if CODE.search(t):
        return Verdict(
            "code",
            f"That looks like code, not a board, and I did not run it. Tell me what the board should do, {ASK_EXAMPLE}.",
        )
    for m in COUNT.finditer(t):
        n = int(m.group(1).replace(",", ""))
        if n > MAX_DISCRETE and not STRIP.search(t):
            noun = re.search(PART_NOUN, m.group(0), re.I).group(0)
            led = noun.lower().startswith(("led", "light", "bulb", "lamp"))
            return Verdict(
                "too_many",
                f"{n:,} separate {noun} will not fit one hand-solderable board: I place up to {MAX_DISCRETE}."
                + (
                    " For lots of LEDs, use an addressable LED strip on a header instead: try "
                    '"an rgb led strip controller with a knob".'
                    if led
                    else f" Ask for {MAX_DISCRETE} or fewer, or split it over several boards."
                ),
            )
    collapsed, word, times = _collapse(t)
    if times >= 3:
        if (
            not re.search(r"[^\W_]", collapsed.replace(word or "", "", 1))
            or len(collapsed.split()) <= 2
        ):
            return Verdict(
                "repeat",
                f'I heard "{word}" {times} times and built one. Say "{times} {word}s" if you meant {times}.',
                stop=False,
                ask=collapsed,
            )
        return Verdict(
            "repeat",
            f'I squeezed the repeated "{word}" down to one.',
            stop=False,
            ask=collapsed,
        )
    lang = _foreign(t)
    if lang:
        intent = _intent(t)
        if intent and not edit and len(t) <= 200:
            ask = INTENT_ASK[intent]
            return Verdict(
                "language",
                f'I read English best, so I took this as "{ask}". Tell me in English if you meant something else.',
                stop=False,
                ask=ask,
            )
        return Verdict(
            "language",
            f"I only read English for now. Tell me in English what the board should do, {ASK_EXAMPLE}.",
        )
    return None
