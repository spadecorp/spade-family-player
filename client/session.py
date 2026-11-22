import hashlib
import json
import math
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib import error, parse, request

from profiles import cache_tag
from provider_api import scan, read_captions, caption_bytes
from media import CHUNK, OVERLAP, POLICY, extract_clip, inspect, make_payload, kept_intervals, render


class AccessDenied(ValueError):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def endpoint(url):
    parts = parse.urlsplit(url.strip())
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not parts.hostname or (parts.scheme != "https" and not (local and parts.scheme == "http"))
            or parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError("Use an HTTPS service address, or HTTP on localhost for development.")
    return url.strip().rstrip("/") + "/v1/analyze"


def intervals(result, duration):
    if not isinstance(result, dict) or result.get("policy") != POLICY:
        raise ValueError("Incompatible filter response.")
    for key in ("skip", "mute"):
        rows = result.get(key)
        if not isinstance(rows, list) or len(rows) > 2048:
            raise ValueError("Invalid filter intervals.")
        for r in rows:
            if (not isinstance(r, list) or len(r) != 2 or
                    any(type(x) not in (float, int) or not math.isfinite(x) for x in r)
                    or not 0 <= r[0] < r[1] <= duration + .001):
                raise ValueError("Invalid filter interval.")
    return result


def analyze(url, token, payload, duration, has_audio, cancelled):
    headers = {"Content-Type":"application/zip", "Authorization":"Bearer " + token,
               "X-Duration":str(duration), "X-Audio":str(int(has_audio)), "X-Policy":POLICY}
    req = request.Request(url, data=payload, headers=headers)
    opener = request.build_opener(NoRedirect())
    for attempt in range(4):
        if cancelled():
            raise InterruptedError()
        try:
            with opener.open(req, timeout=120) as response:
                raw = response.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise ValueError("Oversized service response.")
                return intervals(json.loads(raw), duration)
        except error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AccessDenied("Access token was rejected. Enter a valid pilot token.") from None
            if exc.code != 503 or attempt == 3:
                raise ValueError(f"Analysis service returned HTTP {exc.code}. Check the service and access token.") from None
            for _ in range(10 * (attempt + 1)):
                if cancelled():
                    raise InterruptedError()
                time.sleep(.2)
    raise RuntimeError("Analysis service unavailable.")


def fingerprint(path, stop):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(4 * 1024 * 1024):
            if stop.is_set():
                raise InterruptedError()
            digest.update(block)
    return digest.hexdigest()


class Session:
    def __init__(self, path, cache_file, options=None):
        self.options = options
        self.path, self.cache_file = Path(path), Path(cache_file)
        self.folder = Path(tempfile.mkdtemp(prefix="spade-family-"))
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.pos = 0
        self.epoch = 0
        self.ready = {}
        self.files = {}
        self.service = None
        self.upload_bytes = 0
        self.worker_seconds = 0.0
        self.phase, self.message = "working", "Identifying movie and audio track…"
        self.duration, self.count, self.cached = 0, 0, 0
        self.worker = threading.Thread(target=self.work, daemon=False)
        self.worker.start()

    def state(self):
        with self.condition:
            return dict(name=self.path.name, phase=self.phase, message=self.message, pos=self.pos,
                        duration=self.duration, count=self.count, cached=self.cached,
                        part=self.ready.get(self.pos))

    def grant(self, url, token):
        address = endpoint(url)
        if not token.strip():
            raise ValueError("Enter your access token.")
        with self.condition:
            self.service = (address, token.strip())
            self.condition.notify_all()

    def authorize(self):
        if not self.options:
            raise ValueError("Select a provider profile first.")
        with self.condition:
            self.service = True
            self.condition.notify_all()

    def seek(self, pos):
        with self.condition:
            if not self.count or not 0 <= pos < self.count:
                raise ValueError("Section outside movie.")
            if pos != self.pos:
                self.pos = pos
                self.epoch += 1
                self.condition.notify_all()
            if self.phase == "error":
                self.phase = "working"
                self.condition.notify_all()

    def close(self):
        self.stop.set()
        with self.condition:
            self.condition.notify_all()

    def work(self):
        try:
            self._work()
        except InterruptedError:
            pass
        except Exception as exc:
            with self.condition:
                self.phase, self.message = "error", str(exc)
        finally:
            shutil.rmtree(self.folder, ignore_errors=True)

    def _work(self):
        stat = self.path.stat()
        duration, video, audio = inspect(self.path)
        key = f"{POLICY}/{fingerprint(self.path, self.stop)}/{video}/{audio}"
        cues = None
        if self.options:
            key += "/byok-v1/" + cache_tag(self.options)
            if audio is not None and self.options["speech"] is None:
                data = caption_bytes(self.path)
                key += "/" + hashlib.sha256(data).hexdigest()
                cues = read_captions(data)
            elif audio is not None and not self.options["speech"]["speech"]:
                raise ValueError("This provider needs a separate speech connection for audio. Choose one in Providers, or select matching English SRT captions.")
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.cache_file, timeout=20)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS filters (movie TEXT, section INTEGER, data TEXT, PRIMARY KEY(movie,section))")
            def ensure_same():
                current = self.path.stat()
                if (current.st_size,current.st_mtime_ns) != (stat.st_size,stat.st_mtime_ns):
                    raise ValueError("Movie changed during playback. Reopen the file.")
            ensure_same()
            with self.condition:
                self.count = math.ceil(duration/CHUNK)
                self.duration = duration
                self.cached = db.execute("SELECT COUNT(*) FROM filters WHERE movie=?", (key,)).fetchone()[0]
            while not self.stop.is_set():
                with self.condition:
                    for i in list(self.ready):
                        if i < self.pos or i > self.pos + 1:
                            item = self.ready.pop(i)
                            if item["id"]:
                                old = self.files.pop(item["id"])
                                old.unlink(missing_ok=True)
                    todo = next((i for i in range(self.pos, min(self.pos + 2, self.count)) if i not in self.ready), None)
                    if todo is None:
                        self.phase, self.message = "ready", "Filtered sections ready."
                        self.condition.wait()
                        continue
                    epoch = self.epoch
                stale = lambda: self.stop.is_set() or self.epoch != epoch
                try:
                    ensure_same()
                    start, end = todo*CHUNK, min((todo+1)*CHUNK, duration)
                    left, right = max(0, start-OVERLAP), min(duration, end+OVERLAP)
                    row = db.execute("SELECT data FROM filters WHERE movie=? AND section=?", (key,todo)).fetchone()
                    decisions = None
                    if row:
                        try:
                            decisions = intervals(json.loads(row[0]), right-left)
                        except (ValueError, TypeError):
                            with db:
                                db.execute("DELETE FROM filters WHERE movie=? AND section=?", (key,todo))
                            with self.condition:
                                self.cached = db.execute("SELECT COUNT(*) FROM filters WHERE movie=?", (key,)).fetchone()[0]
                            row = None
                    with self.condition:
                        if not row and self.service is None:
                            self.phase, self.message = "consent", "This section needs online analysis."
                            self.condition.wait_for(lambda: self.service or stale())
                            continue
                        service = self.service
                        self.phase, self.message = "working", f"Preparing section {todo+1} of {self.count}…"
                    clip = self.folder / "source.mp4"
                    extract_clip(self.path, clip, left, right, video, audio)
                    if stale():
                        continue
                    if not row:
                        with self.condition:
                            self.message = f"Analyzing section {todo+1} of {self.count}…"
                        if self.options:
                            decisions = intervals(scan(clip,self.options,right-left,audio is not None,cues,left,stale),right-left)
                        else:
                            payload = make_payload(clip, self.folder, audio is not None)
                            decisions = analyze(*service, payload, right-left, audio is not None, stale)
                            self.upload_bytes += len(payload)
                        elapsed = decisions.pop("worker_seconds", 0)
                        if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
                            self.worker_seconds += elapsed
                        ensure_same()
                        with db:
                            db.execute("INSERT OR REPLACE INTO filters VALUES (?,?,?)", (key,todo,json.dumps(decisions)))
                        with self.condition:
                            self.cached = db.execute("SELECT COUNT(*) FROM filters WHERE movie=?", (key,)).fetchone()[0]
                    if stale():
                        continue
                    lo, hi = start-left, end-left
                    spans = kept_intervals(decisions["skip"], lo, hi)
                    keep = [[left+a, left+b] for a,b in spans]
                    uid = secrets.token_urlsafe(18)
                    out = self.folder / (uid+".mp4")
                    produced = render(clip,out,spans,decisions["mute"],audio is not None)
                    ensure_same()
                    with self.condition:
                        if stale():
                            out.unlink(missing_ok=True)
                        else:
                            if produced:
                                self.files[uid] = out
                            self.ready[todo] = {"id":uid if produced else None, "keep":keep, "index":todo}
                except InterruptedError:
                    continue
                except AccessDenied as exc:
                    with self.condition:
                        if not stale():
                            self.service = None
                            self.phase, self.message = "consent", str(exc)
                            self.condition.wait_for(lambda: self.service or stale())
                except Exception as exc:
                    with self.condition:
                        if not stale():
                            self.phase, self.message = "error", str(exc)
                            self.condition.wait_for(lambda: stale() or self.phase != "error")
                finally:
                    (self.folder / "source.mp4").unlink(missing_ok=True)
