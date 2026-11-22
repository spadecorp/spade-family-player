"""Stored provider connections. Only Windows user-protected key blobs reach disk."""
import ctypes
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

PRESETS = {
    "openai": ["OpenAI", "https://api.openai.com/v1", "gpt-5.4-mini", "whisper-1"],
    "anthropic": ["Claude", "https://api.anthropic.com/v1", "claude-haiku-5-5", ""],
    "groq": ["Groq", "https://api.groq.com/openai/v1", "qwen/qwen3.8-27b", "whisper-large-v3-turbo"],
    "mistral": ["Mistral", "https://api.mistral.ai/v1", "mistral-small-latest", "voxtral-mini-transcribe-3"],
    "custom": ["OpenAI-compatible", "", "", ""],
}
LEVELS = {
    "standard": "Flag explicit sexual imagery, nudity, obscene gestures and clearly vulgar or profane text, speech or sexual dialogue.",
    "strict": "Also flag suggestive sexual imagery, partial nudity, crude gestures, mild swearing, sexual innuendo and vulgar jokes, including visible text.",
}

def address(value):
    u = urlsplit(value)
    local = u.hostname in {"127.0.0.1", "::1", "localhost"}
    if not u.hostname or u.username is not None or u.password is not None or u.query or u.fragment or not (
        u.scheme == "https" or (local and u.scheme == "http")
    ):
        raise ValueError("Enter an HTTPS API base URL, or HTTP on localhost.")
    return value.rstrip("/")

def protect(data, decrypt=False):
    if os.name != "nt":
        raise ValueError("Windows key protection is unavailable. Uncheck Remember key for a session-only key.")
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]
    buf = ctypes.create_string_buffer(data)
    source, dest = Blob(len(data), ctypes.cast(buf, ctypes.c_void_p)), Blob()
    func = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest)):
        raise ValueError("Windows could not unlock or save that API key. Enter the key again.")
    try:
        return ctypes.string_at(dest.data, dest.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(dest.data))

class Profiles:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.memory = {}
        with closing(sqlite3.connect(path)) as db:
            db.executescript("CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, config TEXT, secret BLOB); CREATE TABLE IF NOT EXISTS prefs(id INTEGER PRIMARY KEY CHECK(id=1), config TEXT)")

    def public(self):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT id,config,length(secret)>0 FROM profiles ORDER BY rowid").fetchall()
            pref = db.execute("SELECT config FROM prefs WHERE id=1").fetchone()
        return dict(presets=PRESETS, profiles=[dict(json.loads(c),id=i,hasKey=bool(k or self.memory.get(i))) for i,c,k in rows],
                    selection=json.loads(pref[0]) if pref else {}, windows=os.name=="nt")

    def save(self, args):
        kind = args["kind"]
        if kind not in PRESETS:
            raise ValueError("Choose a provider preset.")
        config = {k:str(args.get(k,"")).strip() for k in ("label","url","model","speech")}
        config.update(kind=kind,url=address(config["url"]))
        if not config["label"] or not config["model"]:
            raise ValueError("Enter a name and an image-capable model ID.")
        if kind=="anthropic":
            config["speech"]=""
        token = args.get("token","").strip()
        if not token and urlsplit(config["url"]).hostname not in ("localhost","127.0.0.1","::1"):
            raise ValueError("Enter an API key for this connection.")
        secret = protect(token.encode()) if token and args.get("remember") else b""
        i = uuid.uuid4().hex
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO profiles VALUES(?,?,?)",(i,json.dumps(config),secret))
        if not secret:
            self.memory[i]=token
        return i

    def delete(self,i):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM profiles WHERE id=?", (i,))
        self.memory.pop(i,None)

    def profile(self,i,with_key):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT config,secret FROM profiles WHERE id=?",(i,)).fetchone()
        if not row:
            raise ValueError("Select a saved provider connection.")
        config=json.loads(row[0])
        if with_key:
            config["token"] = protect(row[1],True).decode() if row[1] else self.memory.get(i,"")
            if not config["token"] and urlsplit(config["url"]).hostname not in ("localhost","127.0.0.1","::1"):
                raise ValueError("This session-only key has expired. Save a connection with your key.")
        return config

    def select(self, choice):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR REPLACE INTO prefs VALUES(1,?)",(json.dumps(choice),))

    def options(self, choice, with_key=False):
        level, fps=choice.get("level"),choice.get("fps")
        if level not in LEVELS or fps not in (1,2,4):
            raise ValueError("Choose a filter level and scan rate.")
        vision=self.profile(choice["vision"],with_key)
        audio_id=choice.get("audio") or choice["vision"]
        speech = None if audio_id=="captions" else self.profile(audio_id,with_key)
        return dict(vision=vision,speech=speech,level=level,fps=fps)

def cache_tag(opts):
    safe = {**opts}
    for key in ("vision","speech"):
        if safe[key]:
            safe[key]={k:v for k,v in safe[key].items() if k not in ("label","token")}
    return hashlib.sha256(json.dumps(safe,sort_keys=True).encode()).hexdigest()
