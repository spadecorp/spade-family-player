"""Exercise the installed client's HTTP and media paths using synthetic provider replies."""
import io
import json
import re
import sys
import tempfile
import threading
import time
import wave
from contextlib import ExitStack
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"client"))
from media import ff,run
from player import PlayerApp,serve
from profiles import PRESETS
from provider_api import caption_bytes

class Mock(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_POST(self):
        path=self.path.strip("/").split("/")
        kind=path[0]
        data=self.rfile.read(int(self.headers["Content-Length"]))
        if kind=="anthropic":
            assert self.headers["x-api-key"]=="key-anthropic" and self.headers["anthropic-version"]=="2023-06-01"
        else:
            assert self.headers["Authorization"]=="Bearer key-"+kind
        self.server.calls.append(self.path)
        if path[-1]=="transcriptions":
            message=BytesParser(policy=policy.default).parsebytes(("Content-Type: "+self.headers["Content-Type"]+"\r\n\r\n").encode()+data)
            fields={p.get_param("name",header="Content-Disposition"):p.get_payload(decode=True) for p in message.iter_parts()}
            with wave.open(io.BytesIO(fields["file"])) as wav:
                assert wav.getframerate()==16000 and wav.getnframes()>1000
            assert fields["timestamp_granularities" if kind=="mistral" else "timestamp_granularities[]"]==b"segment"
            response={"text":"sample","segments":[{"text":"sample","start":4.0,"end":5.0}]}
        else:
            body=json.loads(data)
            parts=body["messages"][-1]["content"]
            prompt=parts[0]["text"]
            if len(parts)>1:
                if kind=="anthropic":assert parts[1]["source"]["media_type"]=="image/jpeg"
                elif kind=="mistral":assert parts[1]["image_url"].startswith("data:image/jpeg;base64,")
                else:assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
                n=int(re.search(r"first (\d+) tiles",prompt)[1])
                flags=[True]+[False]*(n-1)
            else:
                flags=[True]
            if self.server.invalid:flags=[]
            txt=json.dumps({"flags":flags})
            response=({"stop_reason":"end_turn","content":[{"type":"text","text":txt}]} if kind=="anthropic"
                else {"choices":[{"finish_reason":"stop","message":{"content":txt}}]})
        raw=json.dumps(response).encode()
        self.send_response(200);self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)

def main():
    with tempfile.TemporaryDirectory() as work, ExitStack() as cleanup:
        root=Path(work)
        movie=root/"Movie.mp4"
        ff("-f","lavfi","-i","color=navy:s=160x96:r=12","-f","lavfi","-i","sine=frequency=440","-t",6,
           "-c:v","libx264","-c:a","aac",movie)
        sub=movie.with_suffix(".en.srt")
        sub.write_text("1\n00:00:04,000 --> 00:00:05,000\nsample\n")
        embedded=root/"Embedded.mkv"
        ff("-i",movie,"-i",sub,"-map","0","-map","1","-c","copy","-metadata:s:s:0","language=eng",embedded)
        assert b"sample" in caption_bytes(embedded)
        mock=ThreadingHTTPServer(("127.0.0.1",0),Mock)
        mock.calls=[];mock.invalid=False
        app=PlayerApp(root/"filters.db")
        ui=serve(app)
        for server in (mock,ui):
            threading.Thread(target=server.serve_forever,daemon=True).start()
            cleanup.callback(server.server_close);cleanup.callback(server.shutdown)
        cleanup.callback(app.stop)
        url=f"http://127.0.0.1:{ui.server_port}"
        def api(path,args=None):
            req=request.Request(url+path,None if args is None else json.dumps(args).encode(),
                                headers={"X-Player-Key":app.token,"Content-Type":"application/json"})
            with request.urlopen(req) as reply:return json.load(reply)
        ids={}
        for kind,(_,_,model,speech) in PRESETS.items():
            ids[kind]=api("/profile-save",dict(kind=kind,label=kind,url=f"http://127.0.0.1:{mock.server_port}/{kind}/v1",
                     model=model or "test-vision",speech=speech or "test-asr",token="key-"+kind,remember=False))["id"]
        assert "key-groq" not in json.dumps(api("/profiles"))
        def open_movie(kind,audio="",level="standard"):
            api("/settings",dict(vision=ids[kind],audio=audio,level=level,fps=2))
            api("/choose",{})
            choice,opts=app.picks.get(timeout=2)
            app.movie_choice=choice;app.open(movie,opts);app.picking=False
        def state(wanted):
            end=time.monotonic()+15
            while time.monotonic()<end:
                s=api("/state")
                if s.get("phase")=="error" and wanted!="error":raise AssertionError(s["message"])
                if s.get("phase")==wanted or (wanted=="part" and s.get("part")):return s
                time.sleep(.04)
            raise AssertionError(s)
        def close_movie():
            current=app.session
            api("/close",dict(sid=app.sid));current.worker.join(5)
        for kind in PRESETS:
            open_movie(kind,"captions" if kind=="anthropic" else "")
            before=len(mock.calls)
            s=state("consent")
            assert len(mock.calls)==before
            api("/consent",dict(sid=s["sid"]))
            out=state("part")
            target=app.session.files[out["part"]["id"]]
            assert 4.7 < float(run(["ffprobe","-v","error","-of","csv=p=0","-show_entries","format=duration",target]))<5.2
            pcm=ff("-ss",3.25,"-i",target,"-t",.3,"-vn","-ac","1","-ar","16000","-f","s16le","pipe:1")
            assert pcm and max(abs(v) for v in memoryview(pcm).cast("h"))<10
            assert all(r.startswith("/"+kind+"/") for r in mock.calls[before:])
            close_movie()
            before=len(mock.calls)
            open_movie(kind,"captions" if kind=="anthropic" else "")
            assert state("part")["cached"]==1 and len(mock.calls)==before
            close_movie()
        open_movie("anthropic",ids["groq"],level="strict")
        s=state("consent");before=len(mock.calls)
        assert "groq" in s["destinations"]
        api("/consent",{"sid":s["sid"]});state("part")
        assert mock.calls[before].endswith("/audio/transcriptions") and all("/anthropic/" in r for r in mock.calls[before+1:])
        close_movie()
        mock.invalid=True
        open_movie("groq",level="strict")
        s=state("consent");api("/consent",{"sid":s["sid"]})
        s=state("error");assert not s["part"] and not s["cached"]
        close_movie()
        print("PASS: five provider configurations, auth isolation, transcription/captions, visual skip, decoded mute, offline replay, policy isolation, incomplete decisions.")
if __name__=="__main__":main()
