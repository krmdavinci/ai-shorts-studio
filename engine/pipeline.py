import json, os, shutil, time, traceback, threading, uuid
from . import config, media, transcribe, analyze, render

JOBS = {}


def _save(job):
    with open(os.path.join(job["dir"], "job.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in job.items() if k != "dir"}, f, ensure_ascii=False, indent=1)


def new_job(url, upload_path, name_hint, mode="api"):
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = os.path.join(config.OUTPUTS, jid); os.makedirs(d, exist_ok=True)
    job = {"id": jid, "dir": d, "status": "queued", "progress": 0.0, "log": [], "clips": [], "title": name_hint or url or "video",
           "source": url or "upload", "error": None, "created": time.time(), "picker": "", "mode": mode}
    if upload_path:
        dst = os.path.join(d, 'source' + os.path.splitext(upload_path)[1]); os.replace(upload_path, dst); upload_path = dst
    JOBS[jid] = job
    threading.Thread(target=run, args=(job, url, upload_path, mode), daemon=True).start()
    return job


def run(job, url, upload_path, mode="api"):
    def log(m):
        job["log"].append("%s  %s" % (time.strftime("%H:%M:%S"), m)); job["log"] = job["log"][-200:]; _save(job)

    def prog(p):
        job["progress"] = round(min(1.0, p), 3); _save(job)

    try:
        settings = config.load()
        if mode == "local":  # Local mode card: fully offline, like the original tool
            settings["transcription"]["provider"] = "local"
            settings["llm"]["provider"] = "none"
        elif settings["transcription"]["provider"] == "local":
            # API mode card but no API provider configured: use the Groq defaults with the saved key
            settings["transcription"]["provider"] = "groq"
            settings["transcription"]["base_url"] = "https://api.groq.com/openai/v1"
            if settings["transcription"]["model"] in ("", "base", "tiny", "small", "medium"):
                settings["transcription"]["model"] = "whisper-large-v3-turbo"
        media.check_ffmpeg()
        job["status"] = "running"; prog(0.02)
        if url:
            log("Downloading video from URL...")
            src, title = media.download(url, job["dir"], settings["download"], log)
            job["title"] = title
        else:
            src = upload_path
        info = media.probe(src)
        log("Video: %.0f s, %dx%d" % (info["duration"], info["width"], info["height"]))
        prog(0.1)
        tr = transcribe.transcribe(settings, src, job["dir"], info["duration"], log, prog)
        log("Transcript ready: %d segments, %d words, language '%s'." % (len(tr["segments"]), len(tr["words"]), tr["language"]))
        with open(os.path.join(job["dir"], "transcript.json"), "w", encoding="utf-8") as f:
            json.dump(tr, f, ensure_ascii=False)
        prog(0.4)
        clips, used = analyze.pick(settings, tr, src, log)
        job["picker"] = used
        log("Picked %d clips (%s)." % (len(clips), "LLM" if used == "llm" else "local heuristic"))
        prog(0.5)
        for i, c in enumerate(clips, 1):
            log("Rendering clip %d/%d (%.0f-%.0f s)..." % (i, len(clips), c["start"], c["end"]))
            fn = render.render_clip(settings, src, info, c, tr["words"], job["dir"], "clip_%02d" % i, log)
            job["clips"].append(dict(c, file=fn, index=i))
            prog(0.5 + 0.5 * i / len(clips))
        job["status"] = "done"; prog(1.0); log("All done.")
    except Exception as e:
        job["status"] = "error"; job["error"] = str(e)
        log("ERROR: %s" % e); traceback.print_exc()
    _save(job)


def load_history():
    if not os.path.isdir(config.OUTPUTS):
        return
    for jid in sorted(os.listdir(config.OUTPUTS)):
        p = os.path.join(config.OUTPUTS, jid, "job.json")
        if os.path.exists(p) and jid not in JOBS:
            try:
                j = json.load(open(p, encoding="utf-8")); j["dir"] = os.path.join(config.OUTPUTS, jid)
                if j.get("status") in ("running", "queued"):
                    j["status"] = "error"; j["error"] = "Interrupted (app was restarted)."
                JOBS[jid] = j
            except Exception:
                pass
