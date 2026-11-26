"""Pick the best clips: LLM (any OpenAI-compatible API) with a local heuristic fallback."""
import json, re, requests
from . import media

POWER = re.compile(r"\b(never|always|secret|mistake|truth|money|free|important|biggest|best|worst|crazy|insane|why|how|you won't|nobody|everyone|shocking|learn|lesson|story|rule)\b", re.I)


def transcript_text(tr, step=1):
    lines = []
    for s in tr["segments"]:
        lines.append("[%.1f-%.1f] %s" % (s["start"], s["end"], s["text"]))
    return "\n".join(lines)


def _llm(settings, tr, log):
    cfg, c = settings["llm"], settings["clips"]
    if not cfg.get("api_key"):
        raise RuntimeError("no LLM key")
    system = settings["prompt"].replace("{count}", str(c["count"])).replace("{min}", str(c["min_seconds"])).replace("{max}", str(c["max_seconds"]))
    user = "Transcript (seconds):\n" + transcript_text(tr)
    body = {"model": cfg["model"], "temperature": cfg["temperature"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if cfg.get("json_mode"):
        body["response_format"] = {"type": "json_object"}
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    url = base + "/chat/completions"
    h = {"Authorization": "Bearer " + cfg["api_key"]}
    try:
        r = requests.post(url, headers=h, json=body, timeout=300)
        if r.status_code == 400 and "response_format" in body:
            body.pop("response_format"); r = requests.post(url, headers=h, json=body, timeout=300)
    except requests.RequestException as e:
        raise RuntimeError("could not reach the LLM API at %s (%s)" % (base or "(empty base URL)", e.__class__.__name__))
    if r.status_code >= 400:
        raise RuntimeError("LLM API error %d: %s" % (r.status_code, (r.text or "")[:400]))
    try:
        txt = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError):
        snippet = (r.text or "").strip().replace("\n", " ")[:200] or "empty reply"
        raise RuntimeError("the LLM API answered HTTP %d but the reply was not the expected JSON: '%s'. "
                           "Check the LLM base URL and model in Settings." % (r.status_code, snippet))
    m = re.search(r"\{.*\}", txt, re.S)
    data = json.loads(m.group(0) if m else txt)
    return data.get("clips", data if isinstance(data, list) else [])


def _heuristic(settings, tr, src, log):
    c = settings["clips"]
    log("Using local heuristic picker (speech density, keywords, loudness).")
    loud = media.loudness_per_second(src, tr["duration"])
    avg = (sum(loud) / len(loud)) if loud else 0.01
    segs = tr["segments"]
    cands = []
    for i, s0 in enumerate(segs):
        for target in (c["min_seconds"] * 1.15, (c["min_seconds"] + c["max_seconds"]) / 2.0, c["max_seconds"] * 0.95):
            j = i
            while j + 1 < len(segs) and segs[j]["end"] - s0["start"] < target:
                j += 1
            end = segs[j]["end"]
            dur = end - s0["start"]
            if dur < c["min_seconds"] or dur > c["max_seconds"]:
                continue
            text = " ".join(x["text"] for x in segs[i:j + 1])
            a, b = int(s0["start"]), int(end)
            lv = loud[a:b] or [0]
            score = len(text.split()) / dur * 10 + 3 * len(POWER.findall(text)) + text.count("?") * 2 + text.count("!") * 2
            score += 8 * (sum(lv) / len(lv)) / max(avg, 1e-4)
            if segs[i]["text"].strip()[:1].isupper():
                score += 2  # starts at a sentence start
            hook = re.split(r"(?<=[.!?])\s", text.strip())[0][:140]
            cands.append({"start": s0["start"], "end": end, "score": score, "title": (segs[i]["text"].strip()[:60] or "Clip"),
                          "reason": "High speech density, hook words and energy", "hook": hook})
    return cands


def _clean(cands, settings, duration):
    c = settings["clips"]
    out = []
    for x in cands:
        try:
            s, e = float(x["start"]), float(x["end"])
        except (KeyError, TypeError, ValueError):
            continue
        s, e = max(0.0, s), min(duration, e)
        if e - s > c["max_seconds"]:
            e = s + c["max_seconds"]
        if e - s < max(5, c["min_seconds"] * 0.6):
            continue
        out.append({"start": s, "end": e, "title": str(x.get("title") or "Clip")[:80],
                    "score": float(x.get("score") or 0), "reason": str(x.get("reason") or ""),
                    "hook": str(x.get("hook") or "")[:160]})
    out.sort(key=lambda z: -z["score"])
    final = []
    for x in out:
        if all(x["end"] <= y["start"] or x["start"] >= y["end"] for y in final):
            final.append(x)
        if len(final) >= c["count"]:
            break
    final.sort(key=lambda z: z["start"])
    return final


def pick(settings, tr, src, log):
    cands, used = [], "heuristic"
    if settings["llm"]["provider"] != "none" and settings["llm"].get("api_key"):
        try:
            log("Asking the LLM (%s) to pick the best moments..." % settings["llm"]["model"])
            cands = _llm(settings, tr, log); used = "llm"
        except Exception as e:
            log("LLM step failed (%s). Falling back to the local picker." % str(e)[:300])
    if not cands:
        cands = _heuristic(settings, tr, src, log)
    res = _clean(cands, settings, tr["duration"])
    if not res:
        raise RuntimeError("No usable clips found. Try a longer video or lower the minimum clip length in Settings.")
    return res, used
