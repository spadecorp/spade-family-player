import math
import json
import subprocess
import sys
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

CHUNK = 24
OVERLAP = 3
POLICY = "spade-en-v1"

def run(args):
    name, *rest = args
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / (name + ".exe")
    cmd = [str(bundled) if bundled.is_file() else name, *map(str, rest)]
    p = subprocess.run(cmd, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode:
        raise ValueError(p.stderr.decode(errors="replace")[-1200:] or f"{name} failed")
    return p.stdout

def ff(*args):
    return run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y",
                "-threads", "2", "-filter_threads", "2", "-filter_complex_threads", "2",
                *args[:-1], "-threads", "2", args[-1]])

def inspect(path):
    data = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]))
    video = next((s for s in data["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if not video:
        raise ValueError("This file has no movie video track.")
    duration = float(data["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Cannot determine movie length.")
    tracks = [s for s in data["streams"] if s["codec_type"] == "audio"]
    def language(s):
        return s.get("tags", {}).get("language", "und").lower()
    audio = next((s for s in tracks if language(s) in ("eng","en")), None)
    if tracks and audio is None:
        audio = next((s for s in tracks if language(s) in ("und","")), None)
        if audio is None:
            raise ValueError("This prototype requires English audio. No English or untagged track was found.")
    return duration, video["index"], audio["index"] if audio else None


def merge(intervals, low, high):
    result = []
    for a, b in sorted(intervals):
        a, b = max(low, a), min(high, b)
        if b <= a:
            continue
        if result and a <= result[-1][1] + 0.04:
            result[-1] = (result[-1][0], max(result[-1][1], b))
        else:
            result.append((a,b))
    return result


def kept_intervals(skips, lo, hi):
    cuts = merge(skips,lo,hi)
    keep = []
    cursor = lo
    for start,end in cuts:
        if start-cursor >= 1/24:
            keep.append((cursor,start))
        cursor = end
    if hi-cursor >= 1/24:
        keep.append((cursor,hi))
    return keep


def render(clip, target, keep, mutes, audio):
    if not keep:
        return False
    filters, labels = [], []
    for i,(a,b) in enumerate(keep):
        filters.append(f"[0:v]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
        if audio:
            chain = f"[0:a]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS"
            for u,v in merge(mutes,a,b):
                chain += f",volume=0:enable='between(t,{max(0,u-a-.03):.6f},{v-a+.03:.6f})'"
            filters.append(chain+f"[a{i}]")
            labels.append(f"[a{i}]")
    filters.append("".join(labels)+f"concat=n={len(keep)}:v=1:a={int(audio)}[v]"+("[a]" if audio else ""))
    maps = ["-map","[v]"]+(["-map","[a]","-c:a","aac"] if audio else [])
    ff("-i",clip,"-filter_complex",";".join(filters),*maps,"-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",target)
    return True



def extract_clip(path, clip, left, right, video, audio):
    maps = ["-map", f"0:{video}"]
    if audio is not None:
        maps += ["-map", f"0:{audio}", "-af", "aresample=48000:async=1:first_pts=0",
                 "-ac", "2", "-c:a", "aac"]
    ff("-ss", left, "-i", path, "-t", right-left, *maps, "-sn", "-dn",
       "-vf", "fps=24:start_time=0,scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
       "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", clip)


def make_payload(clip, folder, audio):
    output = BytesIO()
    with tempfile.TemporaryDirectory(dir=folder) as work, zipfile.ZipFile(output, "w") as z:
        pattern = Path(work) / "%04d.jpg"
        ff("-i", clip, "-an", "-vf", "fps=4:start_time=0:round=up,scale=224:224",
           "-q:v", "5", pattern)
        frames = sorted(Path(work).glob("*.jpg"))
        if not frames:
            ff("-i", clip, "-an", "-frames:v", "1", "-vf", "scale=224:224", pattern)
            frames = sorted(Path(work).glob("*.jpg"))
        if not frames:
            raise ValueError("No picture could be sampled.")
        for frame in frames:
            z.write(frame, frame.name)
        if audio:
            pcm = ff("-i", clip, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1")
            if not pcm:
                raise ValueError("The selected audio track could not be sampled.")
            z.writestr("audio.pcm", pcm, compress_type=zipfile.ZIP_DEFLATED, compresslevel=3)
    return output.getvalue()
