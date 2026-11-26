"""AI Shorts Studio - local web app. Run: python app.py  then open http://localhost:8000"""
import os, shutil, sys, webbrowser, threading
import requests
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from engine import config, pipeline

app = FastAPI(title="AI Shorts Studio")
os.makedirs(config.OUTPUTS, exist_ok=True)
os.makedirs(config.DATA, exist_ok=True)
pipeline.load_history()
STATIC = os.path.join(config.ROOT, "static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/settings")
def get_settings():
    return {"settings": config.masked(config.load()), "defaults": config.masked(config.DEFAULTS), "presets": config.PROVIDER_PRESETS}


@app.post("/api/settings")
def set_settings(payload: dict):
    return {"settings": config.masked(config.save(payload))}


@app.post("/api/settings/reset")
def reset_settings():
    cur = config.load()
    fresh = {k: v for k, v in config.DEFAULTS.items()}
    fresh = __import__("copy").deepcopy(fresh)
    for sec, key in config.SECRET_PATHS:  # keep the keys when resetting
        fresh[sec][key] = cur[sec][key]
    return {"settings": config.masked(config.save(fresh))}


@app.post("/api/test/{kind}")
def test_key(kind: str):
    s = config.load()
    try:
        if kind == "llm":
            c = s["llm"]
            if not c["api_key"]:
                return {"ok": False, "message": "No LLM API key saved yet."}
            r = requests.post(c["base_url"].rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + c["api_key"]},
                              json={"model": c["model"], "messages": [{"role": "user", "content": "Reply with the word OK."}], "max_tokens": 5}, timeout=30)
            return {"ok": r.status_code < 400, "message": "Connected." if r.status_code < 400 else "Error %d: %s" % (r.status_code, r.text[:300])}
        if kind == "transcription":
            c = s["transcription"]
            if c["provider"] == "local":
                import faster_whisper  # noqa
                return {"ok": True, "message": "faster-whisper is installed."}
            if not c["api_key"]:
                return {"ok": False, "message": "No transcription API key saved yet."}
            r = requests.get(c["base_url"].rstrip("/") + "/models", headers={"Authorization": "Bearer " + c["api_key"]}, timeout=30)
            return {"ok": r.status_code < 400, "message": "Key accepted." if r.status_code < 400 else "Error %d: %s" % (r.status_code, r.text[:300])}
    except ImportError:
        return {"ok": False, "message": "faster-whisper is not installed (pip install faster-whisper)."}
    except Exception as e:
        return {"ok": False, "message": str(e)[:300]}
    raise HTTPException(404)


@app.post("/api/jobs")
async def create_job(url: str = Form(""), file: UploadFile = File(None), count: int = Form(0), aspect: str = Form(""),
                     min_seconds: int = Form(0), max_seconds: int = Form(0), mode: str = Form("api")):
    ov = {}
    if count: ov["count"] = count
    if aspect: ov["aspect"] = aspect
    if min_seconds: ov["min_seconds"] = min_seconds
    if max_seconds: ov["max_seconds"] = max_seconds
    if ov:
        config.save({"clips": ov})  # remember the last-used values
    url = url.strip()
    if not url and not (file and file.filename):
        raise HTTPException(400, "Paste a video link or choose a file.")
    up = None
    if file and file.filename:
        tmp = os.path.join(config.OUTPUTS, "_upload_" + os.path.basename(file.filename))
        with open(tmp, "wb") as f:
            shutil.copyfileobj(file.file, f)
        up = tmp
    job = pipeline.new_job(None if up else url, up, (file.filename if up else None), mode if mode in ("api", "local") else "api")
    return {"id": job["id"]}


def _public(j):
    return {k: v for k, v in j.items() if k not in ("dir", "_up")}


@app.get("/api/jobs")
def list_jobs():
    return [_public(j) for j in sorted(pipeline.JOBS.values(), key=lambda x: -x["created"])]


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = pipeline.JOBS.get(jid)
    if not j:
        raise HTTPException(404)
    return _public(j)


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    j = pipeline.JOBS.pop(jid, None)
    if j:
        shutil.rmtree(j["dir"], ignore_errors=True)
    return {"ok": True}


app.mount("/outputs", StaticFiles(directory=config.OUTPUTS), name="outputs")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:%d" % port)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
