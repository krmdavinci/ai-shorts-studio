"""Speech to text. Returns {"language", "duration", "segments":[{start,end,text}], "words":[{start,end,word}]}."""
import os, requests
from . import media

MAX_CHUNK = 600  # seconds per API upload, keeps files well under the 25 MB API limit


class EndpointUnsupported(RuntimeError):
    """The configured API cannot transcribe (no endpoint, non-JSON reply, unreachable). A local fallback may apply."""


def _api(cfg, audio_path, offset, log):
    if not cfg.get("api_key"):
        raise RuntimeError("Transcription API key is missing. Open Settings and add it (or switch transcription to 'local').")
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    if "groq" in base.lower() and not base.startswith("https://api.groq.com"):
        raise RuntimeError("The transcription base URL looks wrong: '%s'. For Groq it must be "
                           "https://api.groq.com/openai/v1 (api.groq.com, NOT console.groq.com)." % base)
    url = base + "/audio/transcriptions"
    data = [("model", cfg["model"]), ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]
    if cfg.get("language"):
        data.append(("language", cfg["language"]))
    try:
        with open(audio_path, "rb") as f:
            r = requests.post(url, headers={"Authorization": "Bearer " + cfg["api_key"]}, data=data,
                              files={"file": (os.path.basename(audio_path), f, "audio/mpeg")}, timeout=600)
    except requests.RequestException as e:
        raise EndpointUnsupported("Could not reach the transcription API at %s (%s). Check that the server is running and your internet works; "
                                  "if the site is blocked on your network, try a VPN or switch Transcription provider to 'local' in Settings."
                                  % (base or "(empty base URL)", e.__class__.__name__))
    if r.status_code >= 400:
        hint = {400: "the model name in Settings may be wrong",
                401: "the API key was rejected, check it in Settings",
                403: "this key has no access to that model",
                413: "the audio chunk is too large for the API",
                429: "rate limit, wait a minute and try again"}.get(r.status_code, "")
        msg = "Transcription API error %d: %s%s" % (r.status_code, (r.text or "")[:400],
                                                    (" Hint: " + hint + ".") if hint else "")
        if r.status_code in (404, 405):
            msg = ("The server at %s has no transcription endpoint (/audio/transcriptions answered %d). "
                   "Chat-only servers like FreeLLMAPI cannot turn speech into text. "
                   "In Settings set Transcription > Provider to 'local' (runs on your PC, no key needed) "
                   "or use a Whisper API such as Groq." % (base, r.status_code))
            raise EndpointUnsupported(msg)
        raise RuntimeError(msg)
    try:
        j = r.json()
    except ValueError:
        body = (r.text or "").strip().replace("\n", " ")[:200] or "empty reply"
        raise EndpointUnsupported("The transcription API answered HTTP %d but the reply was not JSON (content-type: %s): '%s'. "
                                  "This usually means the base URL in Settings is wrong (for Groq use https://api.groq.com/openai/v1, "
                                  "not console.groq.com) or the server cannot transcribe (FreeLLMAPI has no speech-to-text endpoint - "
                                  "set Transcription provider to 'local' and keep FreeLLMAPI for the AI clip picker only)."
                                  % (r.status_code, r.headers.get("content-type", "?"), body))
    segs = [{"start": s["start"] + offset, "end": s["end"] + offset, "text": s["text"].strip()} for s in j.get("segments", [])]
    words = [{"start": w["start"] + offset, "end": w["end"] + offset, "word": w["word"].strip()} for w in j.get("words", [])]
    if not segs and j.get("text"):
        segs = [{"start": offset, "end": offset + MAX_CHUNK, "text": j["text"].strip()}]
    return segs, words, j.get("language", "")


def _local(cfg, src, log):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("Local mode needs faster-whisper. Run: pip install faster-whisper  (or use an API provider in Settings).")
    log("Loading local Whisper model '%s' (first run downloads it)..." % cfg.get("local_model", "base"))
    m = WhisperModel(cfg.get("local_model", "base"), compute_type="int8")
    audio = media.decode_audio_array(src)  # ffmpeg decode, immune to PyAV version issues
    it, info = m.transcribe(audio, language=cfg.get("language") or None, word_timestamps=True, vad_filter=False)
    segs, words = [], []
    for s in it:
        segs.append({"start": s.start, "end": s.end, "text": s.text.strip()})
        for w in (s.words or []):
            words.append({"start": w.start, "end": w.end, "word": w.word.strip()})
        log("Transcribed up to %d s" % s.end)
    return segs, words, info.language


def transcribe(settings, src, workdir, duration, log, progress):
    cfg = settings["transcription"]
    if cfg["provider"] == "local":
        segs, words, lang = _local(cfg, src, log)
        return {"language": lang, "duration": duration, "segments": segs, "words": words}
    try:
        segs, words, lang = [], [], ""
        n = max(1, int(-(-duration // MAX_CHUNK)))
        for i in range(n):
            off = i * MAX_CHUNK
            a = os.path.join(workdir, "audio_%d.mp3" % i)
            media.extract_audio(src, a, start=off, dur=MAX_CHUNK)
            log("Transcribing part %d/%d..." % (i + 1, n))
            s, w, l = _api(cfg, a, off, log)
            segs += s; words += w; lang = lang or l
            progress(0.1 + 0.3 * (i + 1) / n)
            os.remove(a)
        return {"language": lang, "duration": duration, "segments": segs, "words": words}
    except EndpointUnsupported as e:
        try:
            import faster_whisper  # noqa
        except ImportError:
            raise  # no local fallback available; show the original clear error
        log("Transcription API cannot transcribe (%s). Falling back to local Whisper on your PC." % str(e)[:200])
        segs, words, lang = _local(cfg, src, log)
        return {"language": lang, "duration": duration, "segments": segs, "words": words}
