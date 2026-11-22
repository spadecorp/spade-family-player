"use strict";
const el = id => document.getElementById(id);
const video = el("screen");
const key = location.hash.slice(1) || sessionStorage.getItem("player-key");
if (key) sessionStorage.setItem("player-key", key);
history.replaceState(null, "", "/");
let sid = null, section = 0, part = null, desired = 0, playing = true;
let completed = false, moving = false, dragging = false, generation = 0, lastState = {};
video.volume = .8;

async function api(path, body) {
    const opts = {headers: {"X-Player-Key": key || ""}, cache: "no-store"};
    if (body) {
        opts.method = "POST";
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify({...body, sid});
    }
    const response = await fetch(path, opts);
    if (!response.ok) {
        const info = await response.json().catch(() => ({}));
        throw Error(info.error || "Player request failed (" + response.status + ").");
    }
    return response.json();
}
function problem(e) { el("error").textContent = e.message; }
function clearVideo() {
    part = null;
    video.pause();
    video.removeAttribute("src");
    video.load();
}
function label(t) {
    const n = Math.max(0, Math.floor(t));
    const s = String(n % 60).padStart(2,"0");
    return n >= 3600 ? Math.floor(n / 3600) + ":" + String(Math.floor(n/60)%60).padStart(2,"0") + ":" + s : Math.floor(n/60) + ":" + s;
}
function sourceTime(t) {
    for (const [start,end] of part.keep) {
        if (t < end-start) return start+t;
        t -= end-start;
    }
    return part.keep.at(-1)[1];
}
function outputTime(source) {
    let t=0;
    for (const [start,end] of part.keep) {
        if (source <= start) break;
        t += Math.min(source,end)-start;
        if (source < end) break;
    }
    return t;
}
async function requestPlay() {
    if (!playing || !part || !video.src) return;
    const current = part;
    try { await video.play(); } catch (e) {
        if (part !== current || e.name === "AbortError") return;
        playing = false;
        if (e.name !== "NotAllowedError") problem(e);
    }
}
async function seekTo(t) {
    if (!sid || !lastState.count || moving) return;
    moving = true;
    generation++;
    clearVideo();
    completed = false;
    desired = Math.min(Math.max(0,t), Math.max(0,lastState.duration - .001));
    section = Math.floor(desired / 24);
    el("seek").value = desired;
    el("clock").value = label(desired);
    try { await api("/seek",{pos:section}); }
    catch (e) { problem(e); }
    finally { moving = false; }
}
async function next() {
    if (moving || completed) return;
    if (section + 1 < lastState.count) return seekTo((section+1)*24);
    completed = true;
    clearVideo();
    el("seek").value = lastState.duration;
    el("clock").value = label(lastState.duration);
}
video.addEventListener("loadedmetadata", () => {
    if (!part) return;
    const offset = outputTime(desired);
    if (offset >= video.duration) { next(); return; }
    video.currentTime = offset;
    requestPlay();
});
video.addEventListener("timeupdate", () => {
    if (!part || !video.readyState || dragging || moving) return;
    const t = sourceTime(video.currentTime);
    el("seek").value = t;
    el("clock").value = label(t);
});
video.addEventListener("ended", () => { if (part && video.ended) next(); });
video.addEventListener("error", () => {
    if (part && video.error) { playing = false; problem(Error("Your browser could not play this filtered MP4. Try Microsoft Edge.")); }
});
async function update() {
    const stamp = generation;
    const s = await api("/state");
    if (moving || stamp !== generation) return;
    if (s.sid !== sid) {
        clearVideo();
        sid = s.sid; section = 0; desired = 0; completed = false; playing = true;
        el("seek").value = 0; el("clock").value = "0:00";
        el("error").textContent = "";
    }
    lastState = s;
    el("destinations").textContent = s.destinations || "";
    el("choose").disabled = !!s.picking;
    el("title").textContent = s.name || "Choose a movie to watch";
    el("consent").hidden = s.phase !== "consent";
    el("stop").hidden = !sid || s.phase === "consent";
    el("retry").hidden = s.phase !== "error" || !s.count;
    el("cache").textContent = !s.count ? "" : s.cached === s.count ? "All filter timestamps saved. This file is ready for offline replay." : s.cached + " of " + s.count + " sections saved for offline replay.";
    el("seek").disabled = !s.count;
    el("seek").max = s.duration || 0;
    el("play").disabled = !s.count || (!part && (s.phase === "consent" || s.phase === "error"));
    el("play").textContent = completed ? "Replay" : playing && sid ? "Pause" : "Play";
    el("status").textContent = completed ? "Playback complete." : !sid ? "Open a local movie to start." :
        s.phase === "error" || !part ? s.message : playing ? "Playing filtered section " + (section+1) + "." : "Paused.";
    if (completed || section !== s.pos || !s.part || part) return;
    if (!s.part.id) { if (playing) await next(); return; }
    part = s.part;
    video.src = "/media/" + part.id;
}
async function poll() {
    try { await update(); }
    catch (e) { problem(e); }
    setTimeout(poll, 300);
}
el("choose").onclick = () => api("/choose",{}).catch(problem);
async function closeMovie() {
    generation++;
    clearVideo();
    try { await api("/close",{}); } catch (e) { problem(e); }
}
el("cancel").onclick = el("stop").onclick = closeMovie;
el("consent").onsubmit = async e => {
    e.preventDefault();
    try {
        await api("/consent",{});
        el("error").textContent = "";
    } catch (err) { problem(err); }
};
el("retry").onclick = () => seekTo(section*24);
el("play").onclick = () => {
    if (completed) { playing = true; seekTo(0); return; }
    playing = !playing;
    if (playing) requestPlay(); else video.pause();
};
el("seek").oninput = () => { dragging = true; el("clock").value = label(+el("seek").value); };
el("seek").onchange = () => { dragging = false; seekTo(+el("seek").value); };
el("volume").oninput = () => { video.volume = +el("volume").value; };
el("full").onclick = () => {
    const action = document.fullscreenElement ? document.exitFullscreen() : document.querySelector("main").requestFullscreen();
    action.catch(problem);
};
poll();
