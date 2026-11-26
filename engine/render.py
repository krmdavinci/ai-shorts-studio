"""Cut, reframe and caption with ffmpeg."""
import os
from . import media

ASPECTS = {"9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "16:9": (16, 9)}


def _ass_color(hexcol):
    h = hexcol.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return "&H00%s%s%s&" % (b, g, r)


def _ts(t):
    t = max(0, t); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return "%d:%02d:%05.2f" % (h, m, s)


def build_ass(words, start, end, W, H, cap):
    ws = [w for w in words if w["end"] > start and w["start"] < end and w["word"]]
    n = max(1, int(cap["words_per_line"]))
    align = {"bottom": 2, "middle": 5, "top": 8}[cap["position"]]
    size = int(cap["size"] * H / 1920.0)
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n\n[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: Cap,%s,%d,%s,%s,%s,&H64000000&,%d,0,0,0,100,100,0,0,1,%d,1,%d,60,60,%d,1\n\n[Events]\n"
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n") % (
        W, H, cap["font"], size, _ass_color(cap["text_color"]), _ass_color(cap["text_color"]),
        _ass_color(cap["outline_color"]), -1 if cap["bold"] else 0, max(0, int(cap["outline"] * H / 1920.0)),
        align, int(cap["margin"] * H / 1920.0))
    lines = []
    tf = (lambda x: x.upper()) if cap["uppercase"] else (lambda x: x)
    for i in range(0, len(ws), n):
        grp = ws[i:i + n]
        for k, w in enumerate(grp):
            t0 = max(0.0, w["start"] - start)
            t1 = (grp[k + 1]["start"] - start) if k + 1 < len(grp) else min(end, w["end"] + 0.25) - start
            t1 = max(t1, t0 + 0.08)
            parts = []
            for j, x in enumerate(grp):
                txt = tf(x["word"]).replace("{", "(").replace("}", ")")
                if cap["highlight_active_word"] and j == k:
                    parts.append("{\\c%s}%s{\\c%s}" % (_ass_color(cap["highlight_color"]), txt, _ass_color(cap["text_color"])))
                else:
                    parts.append(txt)
            lines.append("Dialogue: 0,%s,%s,Cap,,0,0,0,,%s" % (_ts(t0), _ts(t1), " ".join(parts)))
    return head + "\n".join(lines) + "\n"


def render_clip(settings, src, src_info, clip, words, outdir, name, log):
    r, c, cap = settings["render"], settings["clips"], settings["captions"]
    ar = ASPECTS.get(c["aspect"], (9, 16))
    base = int(r["height"]) * 1080 / 1920.0  # short side in px (1080 at the default 1920 setting)
    if ar[0] <= ar[1]:
        W = int(round(base / 2.0)) * 2; H = int(round(base * ar[1] / ar[0] / 2.0)) * 2
    else:
        H = int(round(base / 2.0)) * 2; W = int(round(base * ar[0] / ar[1] / 2.0)) * 2
    sw, sh = src_info["width"], src_info["height"]
    if c["framing"] == "blur":
        fg = "[0:v]scale=%d:%d:force_original_aspect_ratio=decrease[fg]" % (W, H)
        bg = "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,boxblur=30:5[bg]" % (W, H, W, H)
        chain = "%s;%s;[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]" % (fg, bg)
    else:
        x = float(c.get("crop_x", 0.5))
        crop = "crop='min(iw,ih*%d/%d)':'min(ih,iw*%d/%d)':'(iw-ow)*%.3f':'(ih-oh)/2'" % (ar[0], ar[1], ar[1], ar[0], x)
        chain = "[0:v]%s,scale=%d:%d[v0]" % (crop, W, H)
    cmd_filter = chain
    ass_name = None
    if cap["enabled"] and words:
        ass_name = name + ".ass"
        with open(os.path.join(outdir, ass_name), "w", encoding="utf-8") as f:
            f.write(build_ass(words, clip["start"], clip["end"], W, H, cap))
        cmd_filter += ";[v0]ass=%s[v]" % ass_name
    else:
        cmd_filter += ";[v0]null[v]"
    out = name + ".mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % clip["start"], "-to", "%.3f" % clip["end"], "-i", os.path.abspath(src),
           "-filter_complex", cmd_filter, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", r["preset"],
           "-crf", str(r["crf"]), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", r["audio_bitrate"], "-movflags", "+faststart", out]
    media.run(cmd, cwd=outdir)  # run inside outdir so the subtitle path stays relative (works on Windows)
    return out
