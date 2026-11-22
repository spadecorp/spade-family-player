// Exercise the shipped event handlers with a deterministic media element.
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const nodes = new Map();
function element(id) {
    if (!nodes.has(id)) nodes.set(id, {
        value:"", events:new Map(), paused:true, readyState:0,
        addEventListener(name, fn) { this.events.set(name,fn); },
        removeAttribute(name) { delete this[name]; },
        pause() { this.paused=true; }, load() {},
        async play() { this.paused=false; }
    });
    return nodes.get(id);
}
const video = element("screen");
let position=0, ready=true, consent=true, current=true, queued;
const response = (data) => ({ok:true, json:async()=>data});
async function fetch(url, opt) {
    const args = opt.body && JSON.parse(opt.body);
    if (url === "/state") return response(current ? {
        pos:position, sid:"fixture", count:3, duration:52, cached:0,
        phase:consent ? "consent" : "working", message:"working",
        part: !ready || consent ? null : {id:"part"+position, index:position,
            keep:position===0 ? [[0,2],[4,24]] : [[position*24,Math.min(52,(position+1)*24)]]}
    } : {sid:null});
    if (url === "/consent") consent=false;
    if (url === "/seek") { position=args.pos; ready=false; }
    if (url === "/close") current=false;
    return response({});
}
const storage = {getItem:()=>"",setItem(){}};
const context = {document:{getElementById:element},location:{hash:"#key"},history:{replaceState(){}},
    sessionStorage:storage,localStorage:storage,fetch,setTimeout(fn){queued=fn;}};
const script=fs.readFileSync(path.join(__dirname,"../client/player.js"),"utf8");
vm.runInNewContext(script,context);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function main() {
    await settle();
    assert.equal(element("consent").hidden,false);
    assert.equal(video.src,undefined);
    await element("consent").onsubmit({preventDefault(){}});
    await queued();
    assert.equal(video.src,"/media/part0");
    video.duration=22; video.readyState=4;
    video.events.get("loadedmetadata")();
    await settle();
    assert.equal(video.paused,false);
    video.currentTime=3; video.events.get("timeupdate")();
    assert.equal(element("clock").value,"0:05");
    element("play").onclick();
    assert.equal(video.paused,true);
    element("seek").value=50;
    element("seek").onchange();
    await settle(); await queued();
    assert.equal(position,2);
    assert.equal(video.src,undefined);
    ready=true; await queued();
    video.duration=4; video.events.get("loadedmetadata")();
    assert.equal(video.currentTime,2);
    assert.equal(video.paused,true);
    element("play").onclick();
    await settle(); assert.equal(video.paused,false);
    video.ended=true;
    video.events.get("ended")();
    await queued();
    assert.equal(element("status").textContent,"Playback complete.");
    element("play").onclick(); await settle();
    assert.equal(position,0);
    await element("stop").onclick(); await queued();
    assert.equal(video.src,undefined);
    assert.equal(element("stop").hidden,true);
    console.log("PASS: consent, playback, source-time mapping, pause, seek gating, resume, replay, close.");
}
main().catch(e=>{ console.error(e); process.exitCode=1; });
