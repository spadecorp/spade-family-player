"""Integration check with synthetic media and a deterministic test analyzer."""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from contextlib import ExitStack
from types import SimpleNamespace
from urllib import request, error

import numpy as np
import uvicorn

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE/"client"), str(BASE/"service")]
from media import ff, run
from models import Analyzer
from player import PlayerApp, serve
from server import create_app
from session import Session, intervals


class Fixture:
    def __init__(self):
        self.calls = 0
        self.skip_all = False

    def scan(self, frames, samples):
        assert frames and all(frame.size == (224,224) for frame in frames)
        assert samples is None or np.max(np.abs(samples)) > .01
        self.calls += 1
        # A known skip and mute make the rendered output independently testable.
        return ([(0.,30.)], []) if self.skip_all else ([(2.,4.)], [(6.,7.)])


def until(check, timeout=60):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        item = check()
        if item:
            return item
        time.sleep(.03)
    raise AssertionError("Timed out waiting for " + str(check))


def await_part(s):
    def check():
        state = s.state()
        if state["phase"] == "error":
            raise AssertionError(state["message"])
        return state["part"]
    return until(check)


def stop(s):
    s.close()
    s.worker.join(20)
    assert not s.worker.is_alive()
    assert not s.folder.exists()


def movie(path, seconds, with_audio=True):
    inputs = ["-f","lavfi","-i","color=navy:size=160x96:rate=4"]
    if with_audio:
        inputs += ["-f","lavfi","-i","sine=frequency=440:sample_rate=16000"]
    ff(*inputs,"-t",seconds,"-c:v","libx264","-preset","ultrafast", "-pix_fmt","yuv420p",
       *(["-c:a","aac","-metadata:s:a:0","language=eng"] if with_audio else []),path)


def start_service(model):
    app = create_app(lambda:model, ["test-only-token"])
    sock = socket.socket()
    sock.bind(("127.0.0.1",0))
    server = uvicorn.Server(uvicorn.Config(app,log_level="error",access_log=False))
    thread = threading.Thread(target=server.run,kwargs={"sockets":[sock]},daemon=True)
    thread.start()
    until(lambda:server.started)
    return f"http://127.0.0.1:{sock.getsockname()[1]}", server, thread


def peak(file, t, length):
    pcm = ff("-ss",t,"-i", file, "-t",length,"-vn","-ac","1","-ar","16000","-f","s16le","pipe:1")
    return np.max(np.abs(np.frombuffer(pcm,dtype="<i2")))


def check_model_adapter():
    # Exercise timestamp conversion and label handling without loading model weights.
    analyzer = Analyzer.__new__(Analyzer)
    analyzer.visual = lambda frames, **kwargs: [
        [{"label":"normal", "score":.98}, {"label":"nsfw", "score":.02}],
        [{"label":"nsfw", "score":.9}, {"label":"normal", "score":.1}]]
    word = SimpleNamespace(word="shit", start=5., end=5.3)
    segment = SimpleNamespace(start=4., end=6., text="sample passage", words=[word])
    analyzer.speech = SimpleNamespace(transcribe=lambda *args, **kwargs: ([segment], None))
    analyzer.dialogue = lambda *args, **kwargs: {"labels":["sexually explicit dialogue"],"scores":[.9]}
    skips, mutes = analyzer.scan([None,None], np.zeros(16000))
    assert skips == [(-.5,1.25)]
    assert np.allclose(mutes, [(4.82,5.48),(3.75,6.25)])
    assert analyzer.scan([None,None], None) == (skips,[])


def main():
    check_model_adapter()
    with tempfile.TemporaryDirectory() as work, ExitStack() as cleanup:
        root = Path(work)
        source = root/"unseen.mp4"
        movie(source,52)
        model = Fixture()
        url, server, worker = start_service(model)
        def end_service():
            server.should_exit = True
            worker.join(10)
        cleanup.callback(end_service)
        cache = root/"cache.db"
        app = PlayerApp(cache)
        ui = serve(app)
        threading.Thread(target=ui.serve_forever,daemon=True).start()
        cleanup.callback(ui.server_close)
        cleanup.callback(ui.shutdown)
        app.open(source)
        s = app.session
        cleanup.callback(stop, s)
        until(lambda:s.state()["phase"] == "consent")
        assert model.calls == 0
        try:
            request.urlopen(url+"/v1/analyze", data=b"")
            raise AssertionError("Unauthorized analysis allowed")
        except error.HTTPError as exc:
            assert exc.code == 401

        s.grant(url, "expired-token")
        def rejected():
            state = s.state()
            if state["phase"] == "error":
                raise AssertionError(state["message"])
            return "rejected" in state["message"]
        until(rejected)
        assert not s.ready and s.cached == 0 and model.calls == 0

        s.grant(url,"test-only-token")

        s.seek(0)
        part = await_part(s)
        clip=s.files[part["id"]]
        d = float(run(["ffprobe","-v","error","-of","csv=p=0","-show_entries","format=duration",clip]))
        assert abs(d-22) < .1, d
        assert peak(clip,4.15,.5) < 10 and peak(clip,3,.5) > 200
        for n in range(1,3):
            s.seek(n)
            await_part(s)
        assert s.state()["cached"] == 3
        stop(s)
        silent = root/"silent.mp4"
        movie(silent,3,False)
        model.skip_all = True
        silent_session = Session(silent,cache)
        cleanup.callback(stop,silent_session)
        silent_session.grant(url,"test-only-token")
        assert await_part(silent_session) == {"index":0,"id":None,"keep":[]}
        assert not silent_session.files
        stop(silent_session)
        ui.shutdown()
        ui.server_close()
        server.should_exit = True
        worker.join(5)
        replay = Session(source,cache)
        cleanup.callback(stop,replay)
        for n in range(3):
            until(lambda:replay.state()["count"])
            replay.seek(n)
            await_part(replay)
        assert replay.state()["cached"] == 3 and replay.service is None
        stop(replay)

        # Same filename, new contents: no previous filter instructions apply.
        movie(source,4,False)
        changed = Session(source,cache)
        cleanup.callback(stop,changed)
        until(lambda:changed.state()["phase"]=="consent")
        assert changed.state()["cached"] == 0
        changed.grant(url,"test-only-token")
        until(lambda:changed.state()["phase"]=="error")
        assert not changed.ready and changed.cached == 0
        stop(changed)
        for bad in ({"policy":"old"}, {"policy":"spade-en-v1","mute":[],"skip":[[4,2]]}):
            try:
                intervals(bad,10)
            except ValueError:
                continue
            raise AssertionError("Invalid decisions accepted")
        print("PASS: no upload before consent, authentication, unknown movie, skip duration, decoded mute, seeking, offline replay, changed-file isolation, malformed decisions.")


if __name__ == "__main__":
    main()
