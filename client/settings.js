"use strict";
let catalog;
const box = el("settings");
function option(value,label) {
    const o=document.createElement("option");
    o.value=value;
    o.textContent=label;
    return o;
}
function preset() {
    const [name,url,model,speech]=catalog.presets[el("kind").value];
    el("nickname").value=name;
    el("url").value=url;
    el("model").value=model;
    el("speech").value=speech;
    el("token").value="";
    el("speech").disabled=el("kind").value==="anthropic";
}
function hint() {
    const visual=catalog.profiles.find(p=>p.id===el("vision").value);
    const audio=el("audio").value;
    el("speechHint").textContent=audio==="captions" ?
        "Uses MovieName.en.srt or an embedded English text subtitle track. Missing or mistimed captions can leave dialogue audible." :
        !audio && visual && !visual.speech ? "For a movie with audio, choose a separate transcription connection or matching English SRT captions." :
        "Flagged images are skipped. Flagged spoken passages are muted. Sampling can miss brief scenes.";
}
async function refresh(selected) {
    catalog=await api("/profiles");
    const p=catalog.selection;
    const rows=catalog.profiles;
    el("kind").replaceChildren(...Object.entries(catalog.presets).map(([k,v])=>option(k,v[0])));
    el("vision").replaceChildren(...rows.map(r=>option(r.id,r.label+" · "+r.model)));
    el("audio").replaceChildren(option("","Use image provider for transcription"),option("captions","English captions · embedded or MovieName.en.srt"),
        ...rows.filter(r=>r.speech).map(r=>option(r.id,r.label+" · "+r.speech)));
    el("vision").value=selected || (rows.some(r=>r.id===p.vision)?p.vision:rows[0]?.id || "");
    el("audio").value=p.audio || "";
    if (!el("audio").value && rows.find(r=>r.id===el("vision").value)?.kind==="anthropic") el("audio").value="captions";
    el("level").value=p.level || "standard";
    el("fps").value=p.fps || 2;
    el("remember").checked=catalog.windows;
    el("remember").disabled=!catalog.windows;
    el("remove").disabled=!rows.length;
    preset(); hint();
}
const errors=e=>{el("settingsStatus").textContent=e.message;};
el("configure").onclick=()=>{box.hidden=!box.hidden;if(!box.hidden) refresh().catch(errors);};
el("kind").onchange=preset;
el("vision").onchange=()=>{
    const p=catalog.profiles.find(row=>row.id===el("vision").value);
    if (p && !p.speech && !el("audio").value) el("audio").value="captions";
    hint();
};
el("audio").onchange=hint;
el("connection").onsubmit=async e=>{
    e.preventDefault();
    try {
        const result=await api("/profile-save",{kind:el("kind").value,label:el("nickname").value,url:el("url").value,
            model:el("model").value,speech:el("speech").value,token:el("token").value,remember:el("remember").checked});
        el("token").value="";
        await refresh(result.id);
        el("settingsStatus").textContent="Connection saved. Choose your dialogue source and save playback settings.";
    } catch (err) {errors(err);}
};
el("preferences").onsubmit=async e=>{
    e.preventDefault();
    try {
        await api("/settings",{vision:el("vision").value,audio:el("audio").value,level:el("level").value,fps:Number(el("fps").value)});
        box.hidden=true;
        el("error").textContent=sid?"Settings saved. Reopen the movie to use them.":"Settings saved. Open a movie to begin.";
    } catch (err) {errors(err);}
};
el("remove").onclick=async()=>{
    if(!el("vision").value || !confirm("Remove this connection and its saved key?")) return;
    try {
        await api("/profile-delete",{id:el("vision").value});
        await refresh();el("settingsStatus").textContent="Connection removed.";
    } catch(err) {errors(err);}
};
refresh().then(()=>{if (!catalog.profiles.some(p=>p.id===catalog.selection.vision))box.hidden=false;}).catch(errors);
