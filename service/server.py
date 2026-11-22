"""Deploy behind HTTPS. Never write uploaded frames, audio, or transcripts to disk."""
import asyncio
import hmac
import io
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from zipfile import ZipFile, ZIP_STORED, ZIP_DEFLATED

from fastapi import FastAPI, HTTPException, Request
from PIL import Image

POLICY = "spade-en-v1"
MAX_BYTES = 12 * 1024 * 1024


def unpack(body, duration, audio):
    import numpy as np
    with ZipFile(io.BytesIO(body)) as z:
        files = z.infolist()
        count = len(files) - int(audio)
        if not 1 <= count <= math.ceil(duration * 4) + 1 or count < math.floor(duration * 4) - 1:
            raise ValueError("Frame count")
        expected = [f"{n:04}.jpg" for n in range(1, count + 1)]
        if audio:
            expected.append("audio.pcm")
        if [f.filename for f in files] != expected or any(f.compress_type != ZIP_STORED for f in files[:count]):
            raise ValueError("Invalid package")
        frames = []
        for item in files[:count]:
            if item.file_size > 256 * 1024:
                raise ValueError("Invalid frame")
            with Image.open(io.BytesIO(z.read(item))) as img:
                if img.format != "JPEG" or img.size != (224, 224):
                    raise ValueError("Invalid frame")
                frames.append(img.convert("RGB"))
        samples = None
        if audio:
            item = files[-1]
            if item.compress_type != ZIP_DEFLATED or item.file_size > (duration + .25) * 32000:
                raise ValueError("Invalid audio")
            data = z.read(item)
            if not data or len(data) % 2 or len(data) > (duration + 0.25) * 32000:
                raise ValueError("Invalid audio")
            samples = np.frombuffer(data, dtype="<i2").astype("float32") / 32768
        return frames, samples


def create_app(factory=None, tokens=None):
    @asynccontextmanager
    async def lifespan(app):
        keys = tokens if tokens is not None else os.environ.get("SPADE_TOKENS", "").split(",")
        app.state.keys = [k.strip().encode() for k in keys if k.strip()]
        if not app.state.keys:
            raise RuntimeError("Set SPADE_TOKENS before starting the service.")
        if factory is None:
            from models import Analyzer
            app.state.model = await asyncio.to_thread(Analyzer)
        else:
            app.state.model = factory()
        app.state.slot = asyncio.Lock()
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health():
        return {"ready": True, "policy": POLICY}

    @app.post("/v1/analyze")
    async def analyze(request: Request):
        token = request.headers.get("authorization", "").encode()
        if not any(hmac.compare_digest(token, b"Bearer " + k) for k in app.state.keys):
            raise HTTPException(401, "Invalid access token.")
        if app.state.slot.locked():
            raise HTTPException(503, "Analysis worker is busy.", headers={"Retry-After": "2"})
        async with app.state.slot:
            try:
                duration = float(request.headers["x-duration"])
                if not (0 < duration <= 30.001):
                    raise ValueError()
                audio = {"1": True, "0": False}[request.headers["x-audio"]]
                if request.headers["x-policy"] != POLICY:
                    raise HTTPException(409, "Filter version mismatch.")
                body = bytearray()
                async for block in request.stream():
                    if len(body) + len(block) > MAX_BYTES:
                        raise HTTPException(413, "Section too large.")
                    body.extend(block)
                started = time.perf_counter()
                frames, samples = await asyncio.to_thread(unpack, body, duration, audio)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(422, "Invalid analysis section.") from None
            try:
                skip, mute = await asyncio.to_thread(app.state.model.scan, frames, samples)
            except Exception:
                logging.error("Section inference failed.")
                raise HTTPException(500, "Section inference failed.") from None
            def bounded(spans):
                return [[max(0., a), min(duration, b)] for a,b in spans if min(duration,b) > max(0.,a)]
            return {"policy": POLICY, "skip": bounded(skip), "mute": bounded(mute),
                    "worker_seconds": round(time.perf_counter() - started, 4)}
    return app


app = create_app()
