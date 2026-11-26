"""Settings storage. Everything the user can change lives in data/settings.json."""
import copy, json, os, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUTPUTS = os.path.join(ROOT, "outputs")
SETTINGS_FILE = os.path.join(DATA, "settings.json")
_lock = threading.Lock()

DEFAULT_PROMPT = """You are an expert short-form video editor for TikTok, Reels and YouTube Shorts.
You get a timestamped transcript of a long video. Pick the {count} best moments that work as standalone clips.

Rules:
- Each clip must be between {min} and {max} seconds long.
- Start on a strong hook (a bold claim, a question, a surprise) and end on a finished thought, never mid-sentence.
- Clips must not overlap.
- Prefer moments with emotion, humor, a clear lesson, a story, or a surprising fact.
- Use the exact start/end times from the transcript (seconds).

Return ONLY valid JSON in this shape:
{"clips": [{"start": 12.5, "end": 58.0, "title": "short catchy title", "score": 87, "reason": "one sentence why this works", "hook": "the exact opening words of the clip, quoted from the transcript"}]}
"""

DEFAULTS = {
    "transcription": {
        "provider": "local",  # local (no key, runs on your PC) | groq | openai_compatible
        "api_key": "",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "whisper-large-v3-turbo",
        "language": "",  # empty = auto-detect, or "en"
        "local_model": "base",  # tiny | base | small | medium (faster-whisper)
    },
    "llm": {
        "provider": "groq",  # groq | gemini | openrouter | openai | custom | none
        "api_key": "",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.3,
        "json_mode": True,
    },
    "prompt": DEFAULT_PROMPT,
    "clips": {
        "count": 3,
        "min_seconds": 20,
        "max_seconds": 60,
        "aspect": "9:16",  # 9:16 | 1:1 | 4:5 | 16:9
        "framing": "center",  # center (crop) | blur (fit + blurred background)
        "crop_x": 0.5,  # 0 = left edge, 1 = right edge, for the center crop
    },
    "captions": {
        "enabled": True,
        "font": "Arial",
        "size": 64,
        "bold": True,
        "uppercase": True,
        "text_color": "#FFFFFF",
        "highlight_color": "#FFD400",
        "outline_color": "#000000",
        "outline": 5,
        "position": "bottom",  # bottom | middle | top
        "margin": 260,
        "words_per_line": 3,
        "highlight_active_word": True,
    },
    "render": {"height": 1920, "crf": 20, "preset": "veryfast", "audio_bitrate": "160k"},
    "download": {"cookies_from_browser": "", "max_height": 1080},
}

PROVIDER_PRESETS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
    "freellmapi": {"base_url": "http://127.0.0.1:3001/v1", "model": "auto"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.0-flash"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "meta-llama/llama-3.3-70b-instruct:free"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "custom": {"base_url": "", "model": ""},
}

SECRET_PATHS = [("transcription", "api_key"), ("llm", "api_key")]


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    with _lock:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return _merge(DEFAULTS, json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return copy.deepcopy(DEFAULTS)


def save(new):
    cur = load()
    # keep stored secret when the UI sends the masked placeholder back
    for sec, key in SECRET_PATHS:
        v = (new.get(sec) or {}).get(key)
        if isinstance(v, str) and v.startswith("\u2022"):
            new[sec][key] = cur[sec][key]
    merged = _merge(DEFAULTS, _merge(cur, new))
    os.makedirs(DATA, exist_ok=True)
    with _lock:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
    return merged


def masked(s):
    s = copy.deepcopy(s)
    for sec, key in SECRET_PATHS:
        v = s[sec].get(key, "")
        s[sec][key] = ("\u2022\u2022\u2022\u2022" + v[-4:]) if v else ""
    return s
