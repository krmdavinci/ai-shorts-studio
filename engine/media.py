"""ffmpeg / yt-dlp helpers."""
import array, json, os, subprocess, sys

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NOWIN)
    if p.returncode != 0:
        raise RuntimeError("Command failed: %s\n%s" % (" ".join(cmd[:3]), (p.stderr or "")[-1500:]))
    return p.stdout


def check_ffmpeg():
    try:
        run(["ffmpeg", "-version"]); run(["ffprobe", "-version"])
    except Exception:
        raise RuntimeError("ffmpeg/ffprobe not found. Install ffmpeg and make sure it is on PATH.")


def probe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    d = json.loads(out)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise RuntimeError("No video stream in file.")
    return {"duration": float(d["format"]["duration"]), "width": int(v["width"]), "height": int(v["height"])}


def extract_audio(src, dst, start=None, dur=None):
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if dur is not None:
        cmd += ["-t", str(dur)]
    cmd += ["-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", dst]
    run(cmd)


def decode_audio_array(src):
    """Whole file as 16 kHz mono float32 numpy array, decoded by ffmpeg.
    faster-whisper accepts a numpy array directly, which skips its PyAV decoder
    (old av versions crash there with: open() got an unexpected keyword argument
    'metadata_errors')."""
    import numpy as np
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
                        "-f", "f32le", "-"],
                       capture_output=True, creationflags=NOWIN)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg could not decode the audio from this file: %s"
                           % p.stderr.decode("utf-8", "replace")[-400:])
    if not p.stdout:
        raise RuntimeError("This file has no audio track to transcribe.")
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def loudness_per_second(src, duration):
    """RMS per second (0..1) from raw 16 kHz mono PCM, stdlib only."""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         stdout=subprocess.PIPE, creationflags=NOWIN)
    out, n, buf = [], 0, array.array("h")
    chunk = 16000 * 2
    while True:
        b = p.stdout.read(chunk)
        if not b:
            break
        a = array.array("h"); a.frombytes(b[: len(b) // 2 * 2])
        s = sum(x * x for x in a)
        out.append((s / max(1, len(a))) ** 0.5 / 32768.0)
    p.wait()
    return out


def download(url, dest_dir, settings, log):
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp is not installed. Run: pip install yt-dlp")
    mh = int(settings.get("max_height", 1080))
    last = {"p": -10}

    def hook(d):
        if d.get("status") == "downloading" and d.get("total_bytes"):
            pct = int(100 * d["downloaded_bytes"] / d["total_bytes"])
            if pct >= last["p"] + 10:
                last["p"] = pct; log("Downloading video... %d%%" % pct)
    opts = {
        "format": "bv*[height<=%d]+ba/b[height<=%d]/best" % (mh, mh),
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(dest_dir, "source.%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "progress_hooks": [hook],
    }
    if settings.get("cookies_from_browser"):
        opts["cookiesfrombrowser"] = (settings["cookies_from_browser"],)
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
    for f in os.listdir(dest_dir):
        if f.startswith("source."):
            return os.path.join(dest_dir, f), info.get("title") or "video"
    raise RuntimeError("Download finished but no file was found.")
