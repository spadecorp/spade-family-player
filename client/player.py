import json
import os
import re
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import SimpleQueue
from urllib.parse import urlsplit

from profiles import Profiles
from session import Session

APP = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
CACHE = Path(os.environ.get("LOCALAPPDATA", Path.home()/".cache"))/"SPaDe"/"FamilyPlayer"/"filters.sqlite3"


class PlayerApp:
    def __init__(self, cache=CACHE):
        self.cache = cache
        self.profiles = Profiles(Path(cache).with_name("providers.sqlite3"))
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.sid, self.session = None, None
        self.picks = SimpleQueue()
        self.picking = False

    def open(self, path, options=None):
        with self.lock:
            if self.session:
                self.session.close()
            self.sid = secrets.token_urlsafe(12)
            self.session = Session(path, self.cache, options)

    def stop(self):
        with self.lock:
            if self.session:
                self.session.close()
            self.session, self.sid = None, None


def serve(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data=b"", kind="application/json", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            for key, val in (headers or {}).items():
                self.send_header(key, val)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (ConnectionError, OSError):
                pass

        def local(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def auth(self):
            return self.local() and secrets.compare_digest(self.headers.get("X-Player-Key",""), app.token)

        def do_GET(self):
            if not self.local():
                return self.send(403)
            path = urlsplit(self.path).path
            assets = {"/":("player.html","text/html; charset=utf-8"), "/player.js":("player.js","text/javascript"),
                      "/settings.js":("settings.js","text/javascript"), "/player.css":("player.css","text/css")}
            if path in assets:
                name, kind = assets[path]
                return self.send(200, (APP/name).read_bytes(), kind)
            with app.lock:
                session = app.session
                if path == "/profiles" and self.auth():
                    return self.send(200,json.dumps(app.profiles.public()).encode())
                if path == "/state" and self.auth():
                    state = session.state() if session else {}
                    destinations=""
                    if session and session.options:
                        opt=session.options
                        vision=opt["vision"]
                        speech=opt["speech"]
                        destinations=f"Images and dialogue text go to {vision['label']} ({vision['url']}). "
                        destinations+=(f"For movies with audio, speech goes to {speech['label']} ({speech['url']})." if speech else "Dialogue text comes from English captions. No audio is uploaded.")
                    return self.send(200,json.dumps(dict(state, sid=app.sid, picking=app.picking, destinations=destinations)).encode())
                if session and path.startswith("/media/"):
                    with session.condition:
                        file = session.files.get(path[6:])
                        if file is None:
                            return self.send(404)
                        data = file.read_bytes()
                    headers = {"Accept-Ranges":"bytes"}
                    if r := self.headers.get("Range"):
                        m = re.fullmatch(r"bytes=(\d+)-(\d*)", r)
                        if not m or int(m[1]) >= len(data):
                            return self.send(416,headers={"Content-Range":f"bytes */{len(data)}"})
                        first = int(m[1])
                        last = min(int(m[2]) if m[2] else len(data)-1, len(data)-1)
                        if last < first:
                            return self.send(416)
                        headers["Content-Range"] = f"bytes {first}-{last}/{len(data)}"
                        return self.send(206,data[first:last+1],"video/mp4",headers)
                    return self.send(200,data,"video/mp4",headers)
            self.send(404)

        def do_POST(self):
            if not self.auth() or self.headers.get("Origin") not in (None,"http://"+self.headers["Host"]):
                return self.send(403)
            try:
                size = int(self.headers.get("Content-Length",0))
                if not 0 < size <= 8192:
                    return self.send(413)
                args = json.loads(self.rfile.read(size))
                with app.lock:
                    if self.path == "/profile-save":
                        new_id = app.profiles.save(args)
                        return self.send(200,json.dumps({"id":new_id}).encode())
                    elif self.path == "/profile-delete":
                        app.profiles.delete(args["id"])
                    elif self.path == "/settings":
                        choice={name:args.get(name) for name in ("vision","audio","fps","level")}
                        app.profiles.options(choice)
                        app.profiles.select(choice)
                    elif self.path == "/choose":
                        choice=app.profiles.public()["selection"]
                        if not choice:
                            raise ValueError("Choose your providers and save playback settings first.")
                        options=app.profiles.options(choice)
                        if not app.picking:
                            app.picking = True
                            app.picks.put((choice,options))
                    elif not app.session or args.get("sid") != app.sid:
                        return self.send(409)
                    elif self.path == "/consent":
                        # Resolve credentials only when a section actually needs a network call.
                        # Connections are immutable and must still match the current movie.
                        opts=app.profiles.options(app.movie_choice,True)
                        with app.session.condition:
                            app.session.options = opts
                            app.session.authorize()
                    elif self.path == "/seek":
                        app.session.seek(int(args["pos"]))
                    elif self.path == "/close":
                        app.stop()
                    else:
                        return self.send(404)
                self.send(200,b"{}")
            except (TypeError, ValueError, KeyError) as exc:
                self.send(400,json.dumps({"error":str(exc)}).encode())
    return ThreadingHTTPServer(("127.0.0.1",0),Handler)


def main():
    import tkinter as tk
    from tkinter import filedialog, ttk
    root = tk.Tk()
    root.title("SPaDe Family Player")
    app = PlayerApp()
    server = serve(app)
    address = f"http://127.0.0.1:{server.server_port}/#{app.token}"
    threading.Thread(target=server.serve_forever,daemon=True).start()
    def pick():
        if not app.picks.empty():
            choice,options = app.picks.get()
            try:
                root.lift()
                root.attributes("-topmost", True)
                name = filedialog.askopenfilename(parent=root,title="Open movie",filetypes=[
                    ("Movie files","*.mp4 *.mkv *.mov *.avi *.m4v *.webm"),("All files","*.*")])
                if name:
                    with app.lock:
                        app.movie_choice=choice
                        app.open(name,options)
            finally:
                root.attributes("-topmost", False)
                app.picking = False
        root.after(120,pick)
    def quit_player():
        app.stop()
        root.destroy()
    ttk.Label(root,text="Family Player is open in your browser.").pack(padx=24,pady=12)
    ttk.Button(root,text="Show player",command=lambda:webbrowser.open(address)).pack(fill="x",padx=24)
    ttk.Button(root,text="Quit player",command=quit_player).pack(fill="x",padx=24,pady=12)
    root.protocol("WM_DELETE_WINDOW",quit_player)
    root.after(100,pick)
    root.after(200,lambda:webbrowser.open(address))
    root.mainloop()
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    main()
