"use strict";
const $ = id => document.getElementById(id);
const state = {
  mode: "text", prompts: {text:"",frames:"",refs:""}, width: 1280, height: 704, refs: [], guides: [], first: null, last: null,
  running: null, started: 0, renderStarted: 0, samplingStart: 0, stepAt: 0, lastStep: 0,
  stepDurations: [], estimated: null, uploads: [], results: [], current: null, visibleResults: 8,
  gpu: "unknown", ramLimit: null, clientId: sessionStorage.getItem("h3studio.client.id.v1")||crypto.randomUUID(), socket: null, reconnect: 0,
  estimateTimer: null, pendingRefKind: null, busy: false,
  loras: [], lorasLoaded: false, libraryLoadedFor: null, socketEpoch: 0,
  serverSamples: [], historySamples: [],
  generationEpoch: 0, abortController: null,
  sessionRecoveredFor: null,
  modelsReady: null,
  nodesReady: null,
  phaseEtaAt: null,
  queueMissingSince: null,
  serverMissingSince: null,
};
const aspectPresets = {
  "16:9": [
    [1280,704,"Near 720p"],
    [1344,768,"Native detail"],
    [1024,576,"Compact"],
    [864,480,"Draft"],
  ],
  "9:16": [
    [704,1280,"Vertical 720p"],
    [768,1344,"Vertical max"],
    [576,1024,"Vertical compact"],
    [480,864,"Vertical draft"],
  ],
  "1:1": [
    [960,960,"Square 960p"],
    [896,896,"Square 896p"],
  ],
  "4:3": [
    [1152,864,"Standard 4:3"],
    [1024,768,"Classic 4:3"],
  ],
  "3:4": [
    [864,1152,"Portrait 3:4"],
    [768,1024,"Classic 3:4"],
  ],
  "21:9": [
    [1344,576,"Cinema 21:9"],
    [1472,640,"Ultrawide 21:9"],
  ],
};
const landscapeSizes = aspectPresets["16:9"];
const sizes = Object.values(aspectPresets).flat();
const maxRefs = {image:9,video:3,audio:3};
const mediaRules={
  image:{ext:/\.(png|jpe?g|webp)$/i,max:25*1024*1024},
  video:{ext:/\.(mp4|mov|webm)$/i,max:500*1024*1024},
  audio:{ext:/\.(wav|mp3|flac|m4a)$/i,max:100*1024*1024},
};
const modelFL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors";
const modelRef = "minimax_h3_ref2va_pruned_int8_convrot.safetensors";
const turboName = "experimental/minimax_h3_fl2v_lightx2v_turbo_4to8step_v0.1-v1.0_768p_v4_step600_dareties.safetensors";
const methodInfo = {
  native: {title:"Original quality", detail:"Full H3 sampling. No accelerator changes the model trajectory."},
  spectrum: {title:"Spectrum · experimental", detail:"Forecasts some denoiser calls. Faster on some workloads; motion and audio need comparison with Original quality."},
  motioncache: {title:"MotionCache · experimental", detail:"Reuses selected denoiser calls. Review faces, movement, lip sync and sound."},
  turbo: {title:"Turbo LoRA · experimental", detail:"Uses the installed 4–8 step adapter at strength 0.9. The model output changes; compare motion and audio."},
};
const method = () => $("renderMethod").value;
const storeKey = "h3studio.render.v2";
const baseKey = "h3studio.comfyBase";
const draftKey = "h3studio.draft.v3";
const sessionKey = "h3studio.session.v3";
const sessionIdKey = "h3studio.session.id.v3";
const sessionId = sessionStorage.getItem(sessionIdKey)||crypto.randomUUID();
sessionStorage.setItem(sessionIdKey,sessionId);
sessionStorage.setItem("h3studio.client.id.v1",state.clientId);
const api = path => (localStorage.getItem(baseKey) || "").replace(/\/$/,"") + path;
const seconds = () => Number($("duration").value) / 24;
const refMemoryRisk = () => state.ramLimit && state.ramLimit < 56 && state.mode === "refs"
  && state.width*state.height >= 1280*704 && Number($("duration").value) >= 294;
function durationFrames(requestedSeconds){
  const step=Math.round((requestedSeconds*24-124)/17);
  return 124+17*Math.max(0,Math.min(14,step));
}
function syncDuration(){
  const input=$("durationSeconds"),hint=$("durationHint"),requested=Number(input.value);
  if(input.value===""||!Number.isFinite(requested)||requested<5||requested>15.1){
    hint.textContent="Enter a duration from 5 to 15.1 seconds.";hint.classList.add("error");return false;
  }
  const frames=durationFrames(requested);
  $("duration").value=String(frames);
  hint.textContent=`Actual H3 length: ${(frames/24).toFixed(2)} s · ${frames} frames at 24 fps.`;
  hint.classList.remove("error");
  return true;
}
const sizeKey = () => state.width + "x" + state.height;
const fmt = n => {
  if (!Number.isFinite(n) || n < 0) return "—";
  n = Math.round(n);
  return n >= 60 ? Math.floor(n/60) + "m " + (n%60) + "s" : n + "s";
};
const fmtBytes = n => n >= 1024*1024 ? (n/(1024*1024)).toFixed(1)+" MB" : n >= 1024 ? Math.round(n/1024)+" KB" : n+" B";
function uploadMessage(message,pct=null){
  $("uploadStatus").textContent=message;$("uploadStatus").hidden=!message;
  $("uploadProgress").hidden=pct===null;
  if(pct!==null)$("uploadProgressFill").style.width=Math.max(0,Math.min(100,pct))+"%";
}
function loraModeCompatible(name,mode){
  if(name===turboName)return mode!=="refs";
  if(mode==="refs"){
    if(/fl2v|fl2va|t2v/i.test(name)&&!/ref2v|r2v/i.test(name))return false;
  }else if(/ref2v|r2v/i.test(name)&&!/fl2v|t2v/i.test(name))return false;
  return true;
}
const info = (message, bad=false) => {
  $("error").textContent = message;
  $("error").classList.toggle("hidden", !message);
  $("error").classList.toggle("error", bad);
};
function updateCapabilities(){
  $("generate").disabled=state.busy||state.projectLoading||state.labCapabilities?.inference_enabled===false;
  if(!state.nodesReady||!state.modelsReady)return;
  document.querySelectorAll(".mode").forEach(button=>{
    const mode=button.dataset.mode;
    const available=mode==="refs"?state.modelsReady.ref2va&&state.nodesReady.MiniMaxH3ReferenceToVideo:state.modelsReady.fl2va&&state.nodesReady.MiniMaxH3ImageToVideo&&(mode!=="frames"||state.nodesReady.LoadImage);
    button.disabled=state.busy||!available;
    button.title=available?"":mode==="refs"?"Install the Ref2VA checkpoint and native reference node.":"Install the FL2VA checkpoint and required image node.";
  });
  [...$("refKind").options].forEach(option=>{option.disabled=option.value==="image"?!state.nodesReady.LoadImage:option.value==="video"?!state.nodesReady.LoadVideo||!state.nodesReady.GetVideoComponents:!state.nodesReady.LoadAudio;});
  if($("refKind").selectedOptions[0]?.disabled){const first=[...$("refKind").options].find(option=>!option.disabled);if(first)$("refKind").value=first.value;}
  updateMethodAvailability();
}
function methodAvailable(value){
  if(value==="native")return true;
  if(value==="turbo")return state.mode!=="refs"&&state.lorasLoaded&&state.loras.some(item=>item.name===turboName)&&state.nodesReady?.LoraLoaderModelOnly===true;
  return state.nodesReady?.[value==="spectrum"?"SpectrumApplyMiniMaxH3":"MiniMaxH3MotionCache"]===true;
}
function updateMethodAvailability(){
  const select=$("renderMethod");
  for(const option of select.options){
    option.disabled=!methodAvailable(option.value);
    option.title=option.disabled&&option.value==="turbo"&&state.mode==="refs"?
      "This installed Turbo adapter targets FL2VA. References uses Ref2VA.":
      option.disabled?"Install the optional method on this ComfyUI server.":"";
  }
  if(!methodAvailable(select.value)){
    select.value="native";
    $("steps").value="20";
  }
  const ready=[...select.options].filter(option=>!option.disabled&&option.value!=="native").map(option=>option.textContent.split(" · ")[0]);
  $("methodAvailability").textContent=(state.nodesReady?ready.length?"Ready on this server: "+ready.join(", ")+".":"Original quality is ready. Optional methods are not installed yet.":"Connect ComfyUI to check installed speed methods.")
    +(state.mode==="refs"&&state.loras.some(item=>item.name===turboName)?" Turbo is FL2VA-only; use Text or Frames.":"");
  select.disabled=state.busy;
  renderMethodInfo();
}
function renderMethodInfo(){
  const value=method(),steps=Number($("steps").value),info=methodInfo[value];
  $("steps").min=value==="turbo"?"4":"20";
  $("steps").max=value==="turbo"?"8":"100";
  $("stepPresets").hidden=value==="turbo";
  document.querySelector(".steps-hint").innerHTML=value==="turbo"?"<strong>6 recommended</strong> · Enter 4–8 steps for Turbo.":"<strong>20 recommended</strong> · Choose a preset or enter a custom value.";
  $("stepAdvice").textContent=value==="turbo"?"Turbo uses 4–8 steps. More steps do not make it equivalent to Original quality.":"20 is the standard H3 setting. More steps take longer and may not improve quality. Custom range: 20–100.";
  document.querySelectorAll("#stepPresets button").forEach(button=>button.classList.toggle("active",Number(button.dataset.steps)===Number($("steps").value)));
  $("profile").replaceChildren();
  const strong=document.createElement("strong");strong.textContent=info.title+(Number.isInteger(steps)&&steps>=Number($("steps").min)&&steps<=Number($("steps").max)?" · "+steps+" steps":" · choose valid steps");
  $("profile").append(strong,document.createElement("br"),document.createTextNode(info.detail));
}
const post = async (path, body) => {
  const r = await fetch(api(path), {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  if (!r.ok) throw Error((await r.text()).slice(0,300));
  return r.json();
};

function selectMode(mode, internal=false) {
  if (state.busy || state.projectLoading&&!internal) return;
  if(!internal&&document.querySelector('.mode[data-mode="'+mode+'"]')?.disabled)return;
  state.prompts[state.mode]=$("prompt").value;
  state.mode = mode;
  const disabledLoras=state.loras.filter(item=>item.enabled&&!loraModeCompatible(item.name,mode));
  disabledLoras.forEach(item=>item.enabled=false);
  $("prompt").value=state.prompts[mode]||"";
  document.querySelectorAll(".mode").forEach(b => {
    const active = b.dataset.mode === mode;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", String(active));
  });
  $("framesSection").hidden = mode !== "frames";
  $("refsSection").hidden = mode !== "refs";
  $("promptTip").textContent = mode === "refs"
    ? "Type @ and select a named asset. Its thumbnail and H3 reference number appear below the prompt."
    : mode === "frames"
      ? "Use @start and @end to refer to uploaded frames. Their thumbnails and H3 picture numbers appear below."
      : "Sent directly to H3. Describe both the motion and the sound.";
  updateMethodAvailability();
  renderEstimates();
  renderLoras();
  renderPromptAssets();
  info(disabledLoras.length?"Disabled incompatible LoRA(s) for this mode: "+disabledLoras.map(item=>item.name).join(", "):"");
  saveDraft();
  if(state.modelsReady)checkConnection();
}
document.querySelectorAll(".mode").forEach(b => b.onclick = () => selectMode(b.dataset.mode));

function saveDraft(){
  try{localStorage.setItem(draftKey,JSON.stringify({
    mode:state.mode,prompts:{...state.prompts,[state.mode]:$("prompt").value},width:state.width,height:state.height,
    duration:$("duration").value,durationRequested:$("durationSeconds").value,steps:$("steps").value,seed:$("seed").value,refSize:$("refSize").value,renderMethod:method()
  }));}catch{}
}
function restoreDraft(){
  let draft;try{draft=JSON.parse(localStorage.getItem(draftKey)||"null");}catch{}
  if(!draft)return false;
  if(["text","frames","refs"].includes(draft.mode))state.mode=draft.mode;
  if(draft.prompts&&typeof draft.prompts==="object"){
    for(const mode of ["text","frames","refs"])state.prompts[mode]=String(draft.prompts[mode]||"");
  }else state.prompts[state.mode]=String(draft.prompt||"");
  if(sizes.some(([w,h])=>w===draft.width&&h===draft.height)){state.width=draft.width;state.height=draft.height;}
  for(const id of ["duration","steps","seed","refSize","renderMethod"]){
    if(draft[id]!==undefined&&[...$(id).options||[]].length){
      if([...$(id).options].some(option=>option.value===String(draft[id])))$(id).value=draft[id];
    }else if(draft[id]!==undefined)$(id).value=draft[id];
  }
  const storedFrames=Number($("duration").value);
  if(!Number.isInteger(storedFrames)||storedFrames<124||storedFrames>362||(storedFrames-5)%17!==0)$("duration").value="362";
  $("durationSeconds").value=Number.isFinite(Number(draft.durationRequested))&&Number(draft.durationRequested)>=5&&Number(draft.durationRequested)<=15.1
    ?String(draft.durationRequested):(Number($("duration").value)/24).toFixed(1);
  syncDuration();
  $("prompt").value=state.prompts[state.mode]||"";
  const draftSteps=Number($("steps").value);
  if($("renderMethod").value==="turbo"){
    if(!Number.isInteger(draftSteps)||draftSteps<4||draftSteps>8)$("steps").value="6";
  }else if(!Number.isInteger(draftSteps)||draftSteps<20||draftSteps>100)$("steps").value="20";
  return state.mode!=="text";
}

function unitsFor({width,height,length,steps,mode,refSize,imageCount=0,videoCount=0,audioCount=0,frameCount=0}) {
  const pixels = (width*height)/(1344*768);
  const frames = length/124;
  const stepFactor = steps/20;
  let media = 1;
  if (mode === "frames") media += frameCount*0.08;
  if (mode === "refs") {
    media = 1.25;
    media += imageCount*0.07;
    media += videoCount*0.35;
    media += audioCount*0.12;
    if (refSize === "max"&&imageCount) media *= 2.2;
  }
  return Math.pow(pixels,.9) * Math.pow(frames,1.25) * stepFactor * media;
}
function workUnits(w=state.width,h=state.height) {
  return unitsFor({width:w,height:h,length:Number($("duration").value),steps:Number($("steps").value),
    mode:state.mode,refSize:$("refSize").value,
    imageCount:state.refs.filter(r=>r.kind==="image").length,
    videoCount:state.refs.filter(r=>r.kind==="video").length,
    audioCount:state.refs.filter(r=>r.kind==="audio").length,
    frameCount:Number(!!state.first)+Number(!!state.last)});
}
function readSamples() {
  try { const parsed=JSON.parse(localStorage.getItem(storeKey) || "[]");return Array.isArray(parsed)?parsed.filter(x=>x&&Number.isFinite(x.seconds)&&Number.isFinite(x.units)&&x.units>0&&(!x.filename||isStudioOutputName(x.filename))):[]; } catch { return []; }
}
function allSamples(){
  const named=new Map(),anonymous=[];
  const currentBase=localStorage.getItem(baseKey)||location.origin;
  const persisted=state.serverSamples.map(sample=>({...sample,
    key:currentBase+"|"+String(sample.key||"").split("|").slice(1).join("|")}));
  for(const sample of [...readSamples(),...state.historySamples,...persisted]){
    if(!sample||!Number.isFinite(sample.seconds)||!Number.isFinite(sample.units)||sample.units<=0||!sample.key)continue;
    if(sample.filename)named.set(sample.filename,sample);else anonymous.push(sample);
  }
  return [...anonymous,...named.values()];
}
function sampleKey() {
  return (localStorage.getItem(baseKey)||location.origin) + "|" + state.gpu + "|" + state.mode + "|" + (state.mode==="refs"?$("refSize").value:"base")
    + "|method:" + method()
    + "|" + state.loras.filter(item=>item.enabled).map(item=>item.name+":"+item.strength).join(",");
}
function captureSettings(){
  const refs=state.mode==="refs";
  const adapters=state.loras.filter(item=>item.enabled).map(item=>({name:item.name,strength:item.strength}));
  if(method()==="turbo")adapters.push({name:"H3 Turbo",strength:0.9});
  return {
    mode:state.mode,compiled_prompt:state.lastCompiledPrompt||null,model:refs?"MiniMax H3 Ref2VA":"MiniMax H3 FL2VA",
    canvas:state.width+"×"+state.height,quality:sizes.find(([w,h])=>w===state.width&&h===state.height)?.[2]||"Custom",
    duration_seconds:(Number($("duration").value)-(state.continuation?.context_length||0))/24,frames:Number($("duration").value)-(state.continuation?.context_length||0),target_frames:Number($("duration").value),target_duration_seconds:seconds(),fps:24,
    steps:Number($("steps").value),seed:Number($("seed").value),render_method:method(),
    loras:adapters,reference_detail:refs?$("refSize").value:null,
    references:refs?state.refs.map(ref=>({name:"@"+ref.alias,type:ref.kind,use_as:ref.role,
      file:ref.file.name,video_soundtrack:!!ref.useAudio,
      trim:ref.kind==="video"&&ref.trimEnabled?{start:Number(ref.trimStart),duration:Number(ref.trimDuration)}:null,
      auto_fit:ref.kind==="video"?ref.fitVideo!==false:ref.kind==="image"?$("fitImages").checked:null})):[],
    start_frame:state.mode==="frames"?state.first?.file.name||null:null,
    end_frame:state.mode==="frames"?state.last?.file.name||null:null,
    prompt:$("prompt").value.slice(0,16000),
  };
}
function estimateFor(w=state.width,h=state.height) {
  const unit = workUnits(w,h);
  const key=sampleKey(),available=allSamples();
  const samples = available.filter(x=>x.key===key).slice(-12);
  if (samples.length >= 1) {
    const rates = samples.map(x=>x.seconds/x.units).sort((a,b)=>a-b);
    const median = rates[Math.floor(rates.length/2)];
    const early=samples.length===1;
    const low=Math.max(1,median*unit*(early?.6:.75));
    return {low, high:Math.max(low*1.1,median*unit*(early?1.65:1.35)),
      basis:(early?"Preliminary estimate from the first completed render":"Calibrated from " + samples.length + " completed renders") + " on this GPU and mode"};
  }
  const parts=key.split("|"),targetMethod=parts[4],sameServerGpu=parts.slice(0,2).join("|")+"|";
  const related=available.filter(sample=>sample.key.startsWith(sameServerGpu)
    &&sample.key.split("|")[4]===targetMethod);
  const sameMode=related.filter(sample=>sample.key.split("|")[2]===state.mode);
  const useful=(sameMode.length?sameMode:related).slice(-20);
  if(useful.length){
    const rates=useful.map(sample=>sample.seconds/sample.units).sort((a,b)=>a-b);
    const median=rates[Math.floor(rates.length/2)],crossMode=!sameMode.length;
    const low=Math.max(1,median*unit*(crossMode?.42:.68));
    return {low,high:Math.max(low*1.2,median*unit*(crossMode?1.75:1.45)),
      basis:"Updated from "+useful.length+" completed "+(crossMode?"render(s) in a different H3 mode; low confidence":"render(s) on this GPU with different adapter settings")};
  }
  const baseline=available.filter(sample=>sample.key.startsWith(sameServerGpu)
    &&sample.key.split("|")[4]==="method:native").slice(-20);
  if(baseline.length){
    const rates=baseline.map(sample=>sample.seconds/sample.units).sort((a,b)=>a-b);
    const center=rates[Math.floor(rates.length/2)]*unit;
    return {low:Math.max(1,center*.4),high:center*1.85,
      basis:"Updated from "+baseline.length+" Original render(s) on this GPU; this method still needs its own timing sample"};
  }
  const pro = /RTX PRO 6000|PRO 6000 Blackwell/i.test(state.gpu);
  const fallback = state.mode === "refs" ? [195,440] : [145,340];
  const gpuScale = pro ? 1 : /5090/i.test(state.gpu) ? 1.15 : 1.35;
  const methodRange=method()==="spectrum"?[0.65,1.2]:method()==="motioncache"?[0.7,1.2]:[1,1];
  return {low:fallback[0]*unit*gpuScale*methodRange[0],high:fallback[1]*unit*gpuScale*methodRange[1],
    basis:(method()==="native"?"Broad initial estimate":"Uncalibrated method; baseline-derived range, not a speed promise") + (pro?" for RTX PRO 6000 Blackwell":" before GPU calibration")};
}
function currentAspectFormat() {
  for(const [fmt, list] of Object.entries(aspectPresets)) {
    if(list.some(([w,h]) => w===state.width && h===state.height)) return fmt;
  }
  return state.height > state.width ? "9:16" : "16:9";
}

function renderEstimates() {
  const wrap = $("estimates");
  wrap.replaceChildren();
  const currentFmt = state.aspectFormat || currentAspectFormat();
  const btnMap = [
    ["aspectLandscape", "16:9"],
    ["aspectPortrait", "9:16"],
    ["aspectSquare", "1:1"],
    ["aspect4_3", "4:3"],
    ["aspect3_4", "3:4"],
    ["aspect21_9", "21:9"],
  ];
  for(const [id, fmt] of btnMap){
    const el = $(id);
    if(el){
      const pressed = fmt === currentFmt;
      el.setAttribute("aria-pressed",String(pressed));
      el.classList.toggle("selected",pressed);
      el.disabled=state.busy;
    }
  }
  const activeSizes = aspectPresets[currentFmt] || aspectPresets["16:9"];
  activeSizes.forEach(([w,h,label]) => {
    const est = estimateFor(w,h);
    const b = document.createElement("button");
    b.className = "estrow" + (w===state.width && h===state.height ? " selected":"");
    b.type = "button";
    b.setAttribute("aria-pressed",String(w===state.width && h===state.height));
    const strong = document.createElement("strong");
    strong.textContent = label + " · " + w + "×" + h;
    const time = document.createElement("span");
    time.textContent = fmt(est.low) + " – " + fmt(est.high);
    b.append(strong,time);
    b.disabled=state.busy;
    b.onclick = () => {if(state.busy)return;state.width=w;state.height=h;renderEstimates();saveDraft();};
    wrap.append(b);
  });
  state.estimated = estimateFor();
  $("estimateNote").textContent = "Estimated " + sizeKey() + ": " + fmt(state.estimated.low) + " – " + fmt(state.estimated.high);
  $("estimateBasis").textContent = state.estimated.basis + ". Includes model load, video and audio. First run may take longer.";
  const risk=refMemoryRisk();
  $("memoryNotice").classList.toggle("hidden",!risk);
  $("memoryNotice").textContent=risk?`This server has ${state.ramLimit} GB system RAM. Long Ref2VA renders at this canvas may run out of system RAM during video decoding. You can still render; a shorter duration, smaller canvas, or 64 GB RAM server lowers that risk.`:"";
}
function renderLoras() {
  const list=$("loraList");list.replaceChildren();
  const count=state.loras.length;
  $("loraCount").textContent=count?"("+count+" installed)":"";
  $("loraRecommendation").hidden=!(state.loras.some(item=>item.enabled)||method()==="turbo");
  renderMethodInfo();
  if(!count){const empty=document.createElement("div");empty.className="tip";empty.textContent="No LoRAs found in ComfyUI/models/loras.";list.append(empty);return;}
  state.loras.forEach(item=>{
    const combat=/^H3_Combat_V2\.safetensors$/i.test(item.name);
    const realism=/^h3-realism-people-t2v-i2v-r2v\.safetensors$/i.test(item.name);
    const managed=item.name===turboName;
    const incompatible=!loraModeCompatible(item.name,state.mode);
    const row=document.createElement("div");row.className="ref";
    const main=document.createElement("label");main.style.cssText="display:flex;align-items:center;gap:8px;margin:0;color:var(--text)";
    const toggle=document.createElement("input");toggle.type="checkbox";toggle.checked=managed?method()==="turbo":item.enabled;toggle.disabled=managed||state.busy||incompatible||state.nodesReady?.LoraLoaderModelOnly===false;
    toggle.onchange=()=>{item.enabled=toggle.checked;renderLoras();renderEstimates();};
    const name=document.createElement("span");name.style.cssText="overflow-wrap:anywhere;font-size:11px";name.textContent=managed?"H3 Turbo · controlled by Render method":combat?"Combat V2 · action / impact":realism?"Realism People · faces / movement":item.name;
    main.append(toggle,name);row.append(main);
    if(managed){const note=document.createElement("div");note.className="tip";note.textContent=state.mode==="refs"?"This Turbo adapter targets FL2VA; use Text or Frames.":"Select Turbo LoRA above to apply this adapter at strength 0.9.";row.append(note);}
    if(combat||realism){const source=document.createElement("div");source.className="tip";source.textContent=combat?"Creator tested FL2VA only · optional triggers prfight2, prfin1":"All three modes · trigger r34l1sm · 1.0 intended strength";row.append(source);}
    if(combat&&state.mode==="refs"){const warning=document.createElement("div");warning.className="tip";warning.textContent="Experimental with Ref2VA: the creator tested FL2VA only. Appearance, motion and audio may change.";row.append(warning);}
    if(incompatible&&!managed){const warning=document.createElement("div");warning.className="tip error";warning.textContent="This adapter appears to target a different H3 checkpoint.";row.append(warning);}
    if(item.enabled&&!managed){
      const strengthLabel=document.createElement("label");strengthLabel.textContent="MODEL STRENGTH";row.append(strengthLabel);
      const strength=document.createElement("input");strength.type="number";strength.min="0";strength.max="2";strength.step=".05";strength.value=String(item.strength);strength.disabled=state.busy;
      strength.onchange=()=>{item.strength=Number(strength.value);renderEstimates();};row.append(strength);
      const note=document.createElement("div");note.className="tip";note.textContent="Applied to the H3 model. Compatibility and quality depend on this adapter.";row.append(note);
    }
    list.append(row);
  });
}
async function loadLoras(){
  try{
    const r=await fetch(api("/h3_studio/loras"));if(!r.ok)throw Error("unavailable");
    const names=(await r.json()).items||[];
    const existing=new Map(state.loras.map(item=>[item.name,item]));
    state.loras=names.map(name=>existing.get(name)||{name,enabled:false,strength:1});
    if(state.desiredLoras){state.loras=state.loras.map(x=>({...x,enabled:!!state.desiredLoras.find(l=>l.name===x.name),strength:state.desiredLoras.find(l=>l.name===x.name)?.strength??x.strength}));}
    const managed=state.loras.find(item=>item.name===turboName);if(managed)managed.enabled=false;
    state.lorasLoaded=true;
    updateMethodAvailability();
    renderLoras();renderEstimates();
  }catch{
    state.lorasLoaded=false;
    $("loraList").innerHTML="<div class='tip'>Connect ComfyUI to see installed LoRAs.</div>";
    $("loraRecommendation").hidden=true;
  }
}
$("refreshLoras").onclick=loadLoras;
["steps","refSize"].forEach(id => $(id).addEventListener("change",renderEstimates));
$("durationSeconds").addEventListener("input",()=>{if(syncDuration())renderEstimates();saveDraft();});
$("steps").addEventListener("input",()=>{renderMethodInfo();const value=Number($("steps").value);if(Number.isInteger(value)&&value>=Number($("steps").min)&&value<=Number($("steps").max))renderEstimates();else $("estimateNote").textContent="Enter "+$("steps").min+"–"+$("steps").max+" steps to update the estimate.";});
$("renderMethod").addEventListener("change",()=>{if(method()==="turbo")$("steps").value="6";else if(Number($("steps").value)<20)$("steps").value="20";renderMethodInfo();renderLoras();renderEstimates();saveDraft();});
document.querySelectorAll("#stepPresets button").forEach(button=>button.onclick=()=>{$("steps").value=button.dataset.steps;renderMethodInfo();renderEstimates();saveDraft();});
["prompt","steps","seed","refSize","renderMethod"].forEach(id => $(id).addEventListener("input",saveDraft));
$("randomSeed").onclick = () => {$("seed").value = Math.floor(Math.random()*2**31);saveDraft();};

function frameBox(which) {
  const file = $(which+"File"), box = $(which+"Box"), clear = $("clear"+(which==="first"?"First":"Last"));
  box.onclick = () => {if(!state.busy)file.click();};
  box.onkeydown = e => {if(!state.busy&&(e.key==="Enter"||e.key===" ")){e.preventDefault();file.click();}};
  file.onchange = () => {
    const selected = file.files[0];
    if (!selected||state.busy) return;
    if (!mediaRules.image.ext.test(selected.name)||!selected.size||selected.size>mediaRules.image.max) {info("Use a non-empty PNG, JPG, or WebP image under 25 MB.",true);return;}
    if (state[which]?.url) URL.revokeObjectURL(state[which].url);
    state[which] = {file:selected,url:URL.createObjectURL(selected)};
    box.replaceChildren();
    const img=document.createElement("img"); img.src=state[which].url; img.alt=which+" frame"; box.append(img);
    clear.classList.remove("hidden");
    renderEstimates();renderPromptAssets();saveDraft();
  };
  clear.onclick = () => {
    if (state[which]?.url) URL.revokeObjectURL(state[which].url);
    state[which]=null; file.value="";box.textContent=which==="first"?"+ Add start frame":"+ Add end frame";
    clear.classList.add("hidden");renderEstimates();renderPromptAssets();saveDraft();
  };
}
frameBox("first");frameBox("last");

function aliasName(fileName) {
  let name = fileName.replace(/\.[^.]+$/,"").trim().replace(/[^\p{L}\p{N}_-]+/gu,"_").replace(/^_+|_+$/g,"").slice(0,32);
  if (!name) name="ref";
  let unique=name,i=2;
  while(state.refs.some(r=>r.alias===unique)) {const suffix="_"+i++;unique=name.slice(0,32-suffix.length)+suffix;}
  return unique;
}
function setAspectFormat(fmt){
  if(state.busy)return;
  state.aspectFormat=fmt;
  const list=aspectPresets[fmt]||aspectPresets["16:9"];
  state.width=list[0][0];
  state.height=list[0][1];
  renderEstimates();saveDraft();
}
function setAspect(portrait){
  setAspectFormat(portrait ? "9:16" : "16:9");
}
$("aspectLandscape").onclick=()=>setAspectFormat("16:9");
$("aspectPortrait").onclick=()=>setAspectFormat("9:16");
if($("aspectSquare")) $("aspectSquare").onclick=()=>setAspectFormat("1:1");
if($("aspect4_3")) $("aspect4_3").onclick=()=>setAspectFormat("4:3");
if($("aspect3_4")) $("aspect3_4").onclick=()=>setAspectFormat("3:4");
if($("aspect21_9")) $("aspect21_9").onclick=()=>setAspectFormat("21:9");
function fitReferenceTrim(ref) {
  const source=Number(ref.meta?.source_duration||ref.localDuration||0);
  const start=Number(ref.trimStart);
  if(!ref.trimEnabled||!Number.isFinite(source)||!Number.isFinite(start)||start<0||source-start<2)return false;
  const available=Math.min(15,Math.floor((source-start+0.000001)*100)/100);
  if(Number.isFinite(Number(ref.trimDuration))&&Number(ref.trimDuration)>=2&&Number(ref.trimDuration)<=available)return false;
  ref.trimDuration=available;
  return true;
}
function videoReferenceDuration() {
  const videos=state.refs.filter(ref=>ref.kind==="video");
  const known=videos.map(ref=>Number(ref.trimEnabled?ref.trimDuration:ref.meta?.duration||ref.localDuration));
  return videos.length&&known.every(value=>Number.isFinite(value)&&value>0)
    ?known.reduce((sum,value)=>sum+value,0):null;
}
function updateRefDurationNotice() {
  const notice=$("refDurationNotice");
  const total=videoReferenceDuration();
  const count=state.refs.filter(ref=>ref.kind==="video").length;
  const audio=state.refs.filter(ref=>ref.kind==="audio"||ref.kind==="video"&&ref.useAudio);
  const audioKnown=audio.map(ref=>Number(ref.kind==="video"&&ref.trimEnabled?ref.trimDuration:ref.meta?.duration||ref.localDuration));
  const audioTotal=audio.length&&audioKnown.every(value=>Number.isFinite(value)&&value>0)?audioKnown.reduce((sum,value)=>sum+value,0):null;
  if(!count&&!audio.length){notice.textContent="";return;}
  notice.style.color=total!==null&&total>15.1||audioTotal!==null&&audioTotal>15.1?"var(--bad)":"var(--muted)";
  const videoText=!count?"":total===null
    ?"Checking reference video lengths · 15 s combined maximum."
    :total>15.1
      ?"Video references total "+total.toFixed(2)+" s; H3 allows 15 s combined. Shorten the selected clips by "+(total-15).toFixed(2)+" s."
      :"Video references: "+total.toFixed(2)+" / 15 s combined.";
  const audioText=!audio.length?"":audioTotal===null?"Checking selected audio lengths.":"Selected audio, including video soundtracks: "+audioTotal.toFixed(2)+" / 15 s combined.";
  notice.textContent=[videoText,audioText].filter(Boolean).join(" ");
}
function renderRefs() {
  $("refs").replaceChildren();
  state.refs.forEach((ref,index) => {
    const card=document.createElement("div");card.className="ref";
    const head=document.createElement("div");head.className="refhead";
    const icon=document.createElement("div");icon.className="icon";
    if(ref.previewUrl&&(ref.kind==="image"||ref.kind==="video")){
      const preview=document.createElement(ref.kind==="image"?"img":"video");
      preview.src=ref.previewUrl;preview.alt="Preview of @"+ref.alias;
      if(ref.kind==="video"){
        preview.muted=true;preview.playsInline=true;preview.preload="metadata";
        preview.onloadedmetadata=()=>{
          if(Number.isFinite(preview.duration)&&preview.duration>0){ref.localDuration=preview.duration;if(preview.duration>15.1&&!ref.trimEnabled){ref.trimEnabled=true;ref.trimDuration=15;renderRefs();}else if(fitReferenceTrim(ref)){renderRefs();saveDraft();}else updateRefDurationNotice();}
          try{preview.currentTime=Math.min(.1,Math.max(0,(preview.duration||1)-.01));}catch{}
        };
      }
      preview.onerror=()=>{icon.textContent=ref.kind==="video"?"▶":"I";};
      icon.append(preview);
    }else icon.textContent=ref.kind==="audio"?"♫":ref.kind==="video"?"▶":"I";
    const title=document.createElement("strong");title.textContent="@"+ref.alias;
    const remove=document.createElement("button");remove.className="button alt smallbtn";remove.textContent="Remove";remove.disabled=state.busy;
    remove.onclick=()=>{state.refs.splice(index,1);if(ref.previewUrl)URL.revokeObjectURL(ref.previewUrl);renderRefs();renderEstimates();saveDraft();};
    const up=document.createElement("button"),down=document.createElement("button");up.textContent="↑";down.textContent="↓";for(const button of [up,down]){button.className="button alt smallbtn";button.disabled=state.busy;}for(const [button,direction] of [[up,-1],[down,1]])button.onclick=()=>{const same=state.refs.map((r,i)=>r.kind===ref.kind?i:-1).filter(i=>i>=0),target=same[same.indexOf(index)+direction];if(target===undefined)return;[state.refs[index],state.refs[target]]=[state.refs[target],state.refs[index]];renderRefs();updatePreviewLive();};head.append(icon,title,up,down,remove);
    const meta=document.createElement("div");meta.className="refmeta";
    meta.textContent=({image:"Image",video:"Video",audio:"Audio"}[ref.kind])+" · "+ref.file.name
      +(ref.kind==="video"&&ref.localDuration?" · source "+ref.localDuration.toFixed(2)+"s":ref.kind==="audio"&&ref.localDuration?" · "+ref.localDuration.toFixed(2)+"s":"")
      +(ref.meta?.duration?" · used "+ref.meta.duration+"s":ref.kind==="video"&&ref.trimEnabled?" · selected "+Number(ref.trimDuration).toFixed(2)+"s":"")
      +(ref.meta?.normalized_from_fps?" · converted "+ref.meta.normalized_from_fps+"→24 fps":"")
      +(ref.uploadProgress!==undefined?" · Uploading "+ref.uploadProgress+"%":ref.uploadReady?" · Uploaded and checked":" · Selected locally · uploads when you generate");
    const fields=document.createElement("div");fields.className="ref-fields";
    const nameWrap=document.createElement("div"), nameLabel=document.createElement("label"), name=document.createElement("input");
    nameLabel.textContent="MENTION NAME";name.value=ref.alias;name.setAttribute("aria-label","Reference name");name.disabled=state.busy;
    name.onchange=()=>{
      const value=name.value.trim();
      if(!/^[\p{L}\p{N}_-]{1,32}$/u.test(value)||state.refs.some(r=>r!==ref&&r.alias===value)){
        name.value=ref.alias; info("Use a unique name with letters, numbers, _ or -, and no spaces.",true);return;
      }
      const old=ref.alias;ref.alias=value;
      $("prompt").value=$("prompt").value.replace(new RegExp("@"+old+"(?=$|[^\\p{L}\\p{N}_-])","gu"),"@"+value);
      renderRefs();saveDraft();
    };
    nameWrap.append(nameLabel,name);
    const roleWrap=document.createElement("div"),roleLabel=document.createElement("label"),role=document.createElement("select");
    roleLabel.textContent="USE AS";
    const options=ref.kind==="image"?
      [["character identity","Character"],["custom","Custom — describe in prompt"],["storyboard","Storyboard · shot guide"],["location","Location"],["visual style","Style"],["object","Object"]]:
      ref.kind==="video"?[["motion","Motion"],["custom","Custom — describe in prompt"],["motion and camera","Performance + camera"],["camera movement","Camera"],["action","Action"],["whole scene","Whole scene · guided remake"]]:
      [["voice","Voice"],["custom","Custom — describe in prompt"],["music","Music"],["sound effects","Effects"]];
    options.forEach(([value,label])=>{const o=document.createElement("option");o.value=value;o.textContent=label;role.append(o);});
    role.value=ref.role||options[0][0];
    role.disabled=state.busy;
    role.onchange=()=>{ref.role=role.value;saveDraft();renderRefs();updatePreviewLive();};
    roleWrap.append(roleLabel,role);fields.append(nameWrap,roleWrap);
    card.append(head,meta,fields);
    if(ref.kind==="image"){
      const label=document.createElement("label"),fit=document.createElement("select");label.textContent="IMAGE FIT";for(const [value,title] of [["preserve","Preserve original proportions"],["crop","Crop to canvas"],["contain","Contain on canvas"]]){const o=document.createElement("option");o.value=value;o.textContent=title;fit.append(o);}fit.value=ref.imageFit||"preserve";fit.disabled=state.busy;fit.onchange=()=>{ref.imageFit=fit.value;updateRefFitPreview(ref).catch(e=>info(e.message,true));};card.append(label,fit);
    }
    if(ref.role==="custom"||ref.role==="storyboard"){
      const instWrap=document.createElement("div");instWrap.className="section";instWrap.style.marginTop="6px";instWrap.style.paddingTop="6px";
      const instLabel=document.createElement("label");instLabel.textContent=ref.role==="storyboard"?"PANEL ORDER / SHOT MAP (OPTIONAL)":"CUSTOM INSTRUCTION (OPTIONAL)";instLabel.style.margin="4px 0 3px";
      const instInput=document.createElement("input");instInput.placeholder="e.g. use for lighting direction only; replace subject";
      instInput.value=ref.role==="storyboard"?ref.panelOrder||"":ref.instruction||"";instInput.disabled=state.busy;
      instInput.oninput=()=>{if(ref.role==="storyboard")ref.panelOrder=instInput.value.trim();else ref.instruction=instInput.value.trim();saveDraft();updatePreviewLive();};
      instWrap.append(instLabel,instInput);card.append(instWrap);
    }
    if(ref.kind==="audio"){
      const label=document.createElement("label"),toggle=document.createElement("input");toggle.type="checkbox";toggle.checked=!!ref.trimEnabled;toggle.disabled=state.busy;toggle.onchange=()=>{ref.trimEnabled=toggle.checked;renderRefs();};label.append(toggle,document.createTextNode(" Trim audio range"));card.append(label);
      if(ref.trimEnabled)for(const [key,title,min,max] of [["trimStart","Audio start (s)",0,3600],["trimDuration","Use audio (s)",2,15]]){const label=document.createElement("label"),input=document.createElement("input");label.textContent=title;input.type="number";input.min=min;input.max=max;input.step=".01";input.value=ref[key]??(key==="trimStart"?0:15);input.disabled=state.busy;input.onchange=()=>{ref[key]=Number(input.value);updateRefDurationNotice();};card.append(label,input);}
    }
    const insert=document.createElement("button");insert.type="button";insert.className="button alt smallbtn ref-insert";insert.textContent="Insert @"+ref.alias+" into prompt";insert.title=insert.textContent;insert.disabled=state.busy;insert.onclick=()=>insertReferenceMention(ref.alias,true);card.append(insert);
    if(ref.kind==="video"){
      const trim=document.createElement("div");trim.className="section";
      const trimToggle=document.createElement("label"),toggle=document.createElement("input");toggle.type="checkbox";toggle.checked=!!ref.trimEnabled;toggle.disabled=state.busy;
      toggle.onchange=()=>{ref.trimEnabled=toggle.checked;fitReferenceTrim(ref);renderRefs();saveDraft();};
      trimToggle.append(toggle,document.createTextNode(" Trim this video to a selected range"));trim.append(trimToggle);
      if(ref.trimEnabled){const range=document.createElement("div");range.className="row";let useInput;const hint=document.createElement("div");hint.className="tip";const updateHint=()=>{const source=Number(ref.meta?.source_duration||ref.localDuration||0),available=source-Number(ref.trimStart);hint.textContent=!source?"Checking source video length…":available<2?"Start must leave at least 2 seconds of video.":"Available from start: "+available.toFixed(2)+" s. Use up to "+Math.min(15,available).toFixed(2)+" s.";hint.style.color=source&&available<2?"var(--bad)":"";};for(const [key,label,min,max] of [["trimStart","Start (s)",0,3600],["trimDuration","Use (s)",2,15]]){const wrap=document.createElement("div"),caption=document.createElement("label"),input=document.createElement("input");caption.textContent=label;input.type="number";input.min=min;input.max=max;input.step="0.01";input.setAttribute("aria-label",label+" for @"+ref.alias);input.value=ref[key]??(key==="trimStart"?0:15);input.disabled=state.busy;if(key==="trimDuration")useInput=input;input.oninput=()=>{ref[key]=Number(input.value);if(key==="trimStart"&&Number.isFinite(ref.trimStart)){try{const video=icon.querySelector("video");if(video)video.currentTime=ref.trimStart;}catch{}if(fitReferenceTrim(ref))useInput.value=ref.trimDuration;}updateHint();updateRefDurationNotice();saveDraft();};wrap.append(caption,input);range.append(wrap);}trim.append(range,hint);updateHint();}
      const fitLabel=document.createElement("label"),fit=document.createElement("input");fit.type="checkbox";fit.checked=ref.fitVideo!==false;fit.disabled=state.busy;fit.onchange=()=>{ref.fitVideo=fit.checked;saveDraft();};fitLabel.append(fit,document.createTextNode(" Auto fit oversized video to 1920×1080"));trim.append(fitLabel);card.append(trim);
      const guidance=document.createElement("div");guidance.className="tip video-guidance";
      guidance.textContent=ref.role==="whole scene"
        ?"Whole scene guides the original subjects and setting too. For a new actor or location, choose Performance + camera. H3 cannot make a frame-locked actor replacement."
        :"Performance + camera guides movement and framing without asking H3 to retain the source actor or location. Reference videos are still generative guides, not frame-locked edits.";
      card.append(guidance);
      const sound=document.createElement("label");sound.style.marginTop="8px";
      const check=document.createElement("input");check.type="checkbox";check.checked=!!ref.useAudio;check.disabled=state.busy||ref.meta?.has_audio===false;
      check.onchange=()=>{
        ref.useAudio=check.checked;
        if(state.refs.filter(item=>item.kind==="audio"||item.kind==="video"&&item.useAudio).length>3){
          ref.useAudio=false;check.checked=false;info("H3 accepts at most three audio references, including video soundtracks.",true);
        }
        updateRefDurationNotice();renderEstimates();
      };
      sound.append(check,document.createTextNode(" Also use this video's soundtrack as an audio reference"));card.append(sound);
      const soundHint=document.createElement("div");soundHint.className="tip";soundHint.textContent="Audio reference may also carry the original voice. For exact words with a new voice, write the dialogue and language in the prompt; H3 still generates new audio.";card.append(soundHint);
    }
    $("refs").append(card);
  });
  updateRefDurationNotice();
  renderPromptAssets();
}
$("addRef").onclick=()=>{
  if(state.busy)return;
  const kind=$("refKind").value;
  if(state.refs.filter(r=>r.kind===kind).length>=maxRefs[kind]){
    info("This reference type reached the H3 limit.",true);return;
  }
  if(state.refs.length>=12){info("H3 accepts at most 12 reference files in total.",true);return;}
  if(kind==="audio"&&state.refs.filter(r=>r.kind==="audio"||r.kind==="video"&&r.useAudio).length>=3){info("H3 accepts at most three audio references, including video soundtracks.",true);return;}
  state.pendingRefKind=kind;
  $("refFile").accept={image:".png,.jpg,.jpeg,.webp",video:".mp4,.mov,.webm",audio:".wav,.mp3,.flac,.m4a"}[kind];
  const picker=$("refFile");
  try{picker.showPicker();}catch{picker.click();}
};
$("refFile").onchange=()=>{
  const file=$("refFile").files[0],kind=state.pendingRefKind;
  if(!file||!kind||state.busy)return;
  if(!mediaRules[kind].ext.test(file.name)||!file.size||file.size>mediaRules[kind].max){
    $("refFile").value="";
    info("This "+kind+" file is empty, unsupported, or above the upload size limit.",true);return;
  }
  state.refs.push({kind,file,alias:aliasName(file.name),role:{image:"character identity",video:"motion",audio:"voice"}[kind],useAudio:false,meta:null,trimEnabled:false,trimStart:0,trimDuration:15,fitVideo:true,
    previewUrl:kind==="audio"?null:URL.createObjectURL(file)});
  if(kind==="audio"){
    const ref=state.refs[state.refs.length-1],url=URL.createObjectURL(file),probe=document.createElement("audio");probe.preload="metadata";probe.src=url;
    probe.onloadedmetadata=()=>{if(Number.isFinite(probe.duration)&&probe.duration>0)ref.localDuration=probe.duration;URL.revokeObjectURL(url);renderRefs();};
    probe.onerror=()=>URL.revokeObjectURL(url);
  }
  $("refFile").value="";renderRefs();renderEstimates();info("");saveDraft();
};

function mentionItems(){
  if(state.mode==="frames"){
    const items=[];
    if(state.first)items.push({alias:"start",kind:"image",previewUrl:state.first.url,tag:"Picture 1",label:"Start frame"});
    if(state.last)items.push({alias:"end",kind:"image",previewUrl:state.last.url,tag:"Picture "+(state.first?2:1),label:"End frame"});
    return items;
  }
  if(state.mode!=="refs")return [];
  let bindings=[];
  try{bindings=H3PromptCompiler.compilePrompt({mode:"refs",source_prompt:state.refs.map(r=>"@"+r.alias).join(" "),references:currentRenderSpec().references}).bindings||[];}catch{}
  return orderedRefs().map(ref=>({alias:ref.alias,kind:ref.kind,previewUrl:ref.previewUrl,tag:(()=>{const b=bindings.find(b=>b.alias===ref.alias);return b?b.tag+(b.paired_audio_tag?" + "+b.paired_audio_tag:""):ref.kind;})(),label:ref.kind}));
}
function mentionPreview(item){
  if(!item.previewUrl||item.kind==="audio")return null;
  const preview=document.createElement(item.kind==="video"?"video":"img");
  preview.src=item.previewUrl;
  if(item.kind==="video"){
    preview.muted=true;preview.playsInline=true;preview.preload="metadata";
    preview.onloadedmetadata=()=>{try{preview.currentTime=Math.min(.1,Math.max(0,(preview.duration||1)-.01));}catch{}};
  }else preview.alt=item.label+" preview";
  return preview;
}
function renderPromptAssets(){
  const wrap=$("promptAssets");wrap.replaceChildren();
  const prompt=$("prompt").value;
  const items=mentionItems();
  for(const item of items){
    const chip=document.createElement("button");chip.type="button";chip.className="prompt-asset";
    if(new RegExp("@"+item.alias+"(?=$|[^\\p{L}\\p{N}_-])","u").test(prompt))chip.classList.add("used");
    chip.disabled=state.busy;
    chip.title="Insert @"+item.alias+" into the prompt";
    chip.onclick=()=>insertReferenceMention(item.alias,false);
    const preview=mentionPreview(item);if(preview)chip.append(preview);
    const name=document.createElement("span");name.textContent="@"+item.alias;
    const tag=document.createElement("small");tag.textContent=item.tag;
    chip.append(name,tag);wrap.append(chip);
  }
}
function insertReferenceMention(alias,scroll){
  const input=$("prompt"),at=input.selectionStart;
  input.setRangeText("@"+alias+" ",at,input.selectionEnd,"end");
  input.focus();if(scroll)input.scrollIntoView({behavior:"smooth",block:"center"});
  renderPromptAssets();saveDraft();
}
function mentionState() {
  const input=$("prompt"),before=input.value.slice(0,input.selectionStart);
  const match=before.match(/@([\p{L}\p{N}_-]*)$/u);
  if(!match){$("mentionMenu").classList.add("hidden");renderPromptAssets();return;}
  const names=mentionItems().filter(item=>item.alias.toLocaleLowerCase().startsWith(match[1].toLocaleLowerCase()));
  const menu=$("mentionMenu");menu.replaceChildren();
  names.forEach(item=>{
    const b=document.createElement("button");b.type="button";
    const preview=mentionPreview(item);if(preview){preview.alt="";b.append(preview);}
    const label=document.createElement("span");label.textContent="@"+item.alias+" · "+item.tag;b.append(label);
    b.onmousedown=e=>{
      e.preventDefault();
      const start=input.selectionStart-match[0].length,end=input.selectionStart;
      input.setRangeText("@"+item.alias+" ",start,end,"end");
      menu.classList.add("hidden");input.focus();renderPromptAssets();saveDraft();
    };
    menu.append(b);
  });
  menu.classList.toggle("hidden",!names.length);
  renderPromptAssets();
}
$("prompt").addEventListener("input",mentionState);
$("prompt").addEventListener("click",mentionState);
$("prompt").addEventListener("blur",()=>setTimeout(()=>$("mentionMenu").classList.add("hidden"),120));

function currentRenderSpec() {
  const refs = state.mode === "refs";
  return {
    mode: state.mode,
    prompt_mode: state.promptMode || "guided",
    source_prompt: $("prompt").value.trim(),
    references: refs ? state.refs.map(r => ({
      asset_id: r.assetId || r.alias,
      panel_order:r.panelOrder||"",
      alias: r.alias,
      kind: r.kind,
      role: r.role || (r.kind === "image" ? "character identity" : r.kind === "video" ? "motion" : "voice"),
      instruction: r.instruction || "",
      use_audio: !!r.useAudio,
      file: r.file?.name
    })) : [],
    guides: state.guides.map(g=>({...g,file:undefined,filename:g.file?.name,relative_to_new_content:!!state.continuation})),
    capabilities: state.labCapabilities || {},
    frames: { first: state.mode === "frames" && state.first ? {file:state.first.file?.name} : null,
      last: state.mode === "frames" && state.last ? {file:state.last.file?.name} : null },
    first: state.mode === "frames" && state.first ? { file: state.first.file?.name } : null,
    last: state.mode === "frames" && state.last ? { file: state.last.file?.name } : null,
    canvas: { width: state.width, height: state.height },
    width: state.width,
    height: state.height,
    target_frames: Number($("duration").value),
    duration: Number($("duration").value),
    duration_seconds: seconds(),
    target_seconds: seconds(),
    seed: Number($("seed").value),
    steps: Number($("steps").value),
    render_method: method(),
    ref_image_size: $("refSize") ? $("refSize").value : "match",
    loras: state.loras.filter(x => x.enabled),
    continuation: state.continuation || null,
  };
}

function updatePreviewLive() {
  const display = $("compiledPromptDisplay");
  const tagsWrap = $("tagBindingsDisplay");
  if (!display) return;
  const spec = currentRenderSpec();
  if (!spec.source_prompt) {
    display.textContent = "Write a prompt to see the compiled preview.";
    if (tagsWrap) tagsWrap.replaceChildren();
    return;
  }
  try {
    const res = H3PromptCompiler.compilePrompt(spec);
    display.textContent = res.compiled_prompt;
    if (tagsWrap) {
      tagsWrap.replaceChildren();
      (res.bindings || []).forEach(b => {
        const span = document.createElement("span");
        span.className = "prompt-asset used";
        span.textContent = `@${b.alias} → ${b.tag}${b.paired_audio_tag?" + "+b.paired_audio_tag:""} · ${b.kind}${b.role ? " · " + b.role : ""}`;
        tagsWrap.append(span);
      });
      if (res.warnings && res.warnings.length) {
        res.warnings.forEach(w => {
          const wSpan = document.createElement("div");
          wSpan.className = "notice";
          wSpan.style.fontSize = "11px";
          wSpan.textContent = "Warning: " + w;
          tagsWrap.append(wSpan);
        });
      }
    }
  } catch (e) {
    display.textContent = "Prompt preview: " + e.message;
  }
}

function resolvedPrompt() {
  const spec = currentRenderSpec();
  if (!spec.source_prompt) throw Error("Write the scene prompt first.");
  if (state.mode === "refs") {
    if (!state.refs.length) throw Error("Add at least one reference.");
    if (state.refs.length > 12) throw Error("H3 accepts at most 12 reference files.");
    const videoDuration = videoReferenceDuration();
    if (videoDuration !== null && videoDuration > 15.1)
      throw Error("Video references total " + videoDuration.toFixed(2) + " seconds. H3 allows 15 seconds combined; trim at least " + (videoDuration - 15).toFixed(2) + " seconds and retry.");
    if (state.refs.filter(ref => ref.kind === "audio" || ref.kind === "video" && ref.useAudio).length > 3)
      throw Error("H3 accepts at most three audio references, including video soundtracks.");
  } else if (state.mode === "frames") {
    if (!state.first && !state.last) throw Error("Add a start frame, an end frame, or both.");
  }
  const res = H3PromptCompiler.compilePrompt(spec);
  state.lastCompiledPrompt = res.compiled_prompt;
  state.lastBindings = res.bindings;
  updatePreviewLive();
  return res.compiled_prompt;
}
function orderedRefs(){return ["image","video","audio"].flatMap(k=>state.refs.filter(r=>r.kind===k));}

function graph(prompt,uploads,token) {
  const spec = currentRenderSpec();
  spec.compiled_prompt = prompt;
  spec.token = token;

  const resolvedAssets = {
    first: uploads.first,
    last: uploads.last,
  };
  if (uploads.refs) {
    state.refs.forEach(r => {
      const serverFile = uploads.refs.get(r);
      if (serverFile) {
        resolvedAssets[r.alias] = serverFile;
        if (r.assetId) resolvedAssets[r.assetId] = serverFile;
      }
    });
  }
  if (state.guides && uploads.guides) {
    state.guides.forEach(g => {
      resolvedAssets[g.asset_id] = uploads.guides.get(g) || g.filename;
    });
  }

  return H3GraphBuilder.buildGraph(spec, resolvedAssets, state.labCapabilities || {});
}

function setProgress(label,pct=null,remaining=null,busy=false) {
  $("status").textContent=label;
  $("progress").classList.toggle("busy",busy);
  if(pct!==null){$("progressFill").style.width=Math.max(0,Math.min(100,pct))+"%";$("percent").textContent=Math.round(pct)+"%";}
  if(remaining!==null)state.phaseEtaAt=Date.now()+Math.max(0,remaining)*1000;
  else if(!state.busy||pct===100)state.phaseEtaAt=null;
  const left=state.phaseEtaAt?(state.phaseEtaAt-Date.now())/1000:null;
  $("remaining").textContent=pct===100?"Time remaining: 0s":left===null?"Time remaining: —":left<=0&&state.busy?"Estimate exceeded · still working":"Approx. time remaining: "+fmt(Math.max(0,left));
}
function showStageMessage(title,detail){
  const panel=document.createElement("div");panel.className="stage-empty";
  const heading=document.createElement("b");heading.textContent=title;
  panel.append(heading,document.createTextNode(detail));
  $("stage").replaceChildren(panel);
  $("resultCard").classList.add("hidden");
}
function focusWorkspace(){
  if(matchMedia("(max-width: 720px)").matches)$("stage").scrollIntoView({behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth",block:"start"});
}
function setBusy(value) {
  state.busy=value;
  $("generate").classList.toggle("hidden",value);$("cancel").classList.toggle("hidden",!value);
  document.querySelectorAll(".mode").forEach(b=>b.disabled=value);
  for(const id of ["extendSourceMethod","guideKind","resultRole","mediaFormat","resultFrameIndex","promptMode","frameFit","projectSelect","refreshProjects","importProject","btnNewProject","btnSaveProject","btnExportProject","btnAssembleSequence","extendVideo","clearContinuation","addGuide","purgeContext","acceptTake","restoreTake","useResult","exportMedia","prompt","durationSeconds","steps","seed","refSize","refKind","addRef","refreshLoras","server","saveServer","randomSeed","clearFirst","clearLast","renderMethod","fitImages","fitFrames"])if($(id))$(id).disabled=value;
  document.querySelectorAll("#stepPresets button").forEach(button=>button.disabled=value);
  updateCapabilities();
  renderLoras();renderRefs();renderGuides();renderEstimates();
  refreshLabControls();
}
function uploadOne(file,kind,onProgress=()=>{},onSent=()=>{},options={}) {
  return new Promise((resolve,reject)=>{
    const data=new FormData();data.append("file",file);
    const xhr=new XMLHttpRequest();
    const signal=state.abortController?.signal;
    const abort=()=>xhr.abort();
    const finish=()=>signal?.removeEventListener("abort",abort);
    const query=new URLSearchParams({kind});if(options.resize)query.set("resize","1");if((kind==="video"||kind==="audio")&&options.trimEnabled){query.set("trim_start",String(options.trimStart||0));query.set("trim_duration",String(options.trimDuration));}
    xhr.open("POST",api("/h3_studio/upload_ref?"+query));
    xhr.responseType="json";
    xhr.upload.onprogress=event=>onProgress(Math.min(event.loaded,file.size),file.size);
    xhr.upload.onload=onSent;
    xhr.onload=()=>{
      finish();
      const value=xhr.response||{};
      if(xhr.status<200||xhr.status>=300){reject(Error(value.error||"Reference upload failed (HTTP "+xhr.status+")"));return;}
      if(!value.name){reject(Error("Server did not return an uploaded filename."));return;}
      state.uploads.push(value.name);
      resolve(value);
    };
    xhr.onerror=()=>{finish();reject(Error("Upload connection failed. Check the server and retry."));};
    xhr.onabort=()=>{finish();reject(new DOMException("Upload cancelled","AbortError"));};
    if(signal?.aborted){reject(new DOMException("Upload cancelled","AbortError"));return;}
    signal?.addEventListener("abort",abort,{once:true});
    xhr.send(data);
  });
}
async function cleanupUploads() {
  const names=state.uploads.splice(0);
  await Promise.allSettled(names.map(filename=>post("/h3_studio/discard",{filename})));
  state.refs.forEach(ref=>{ref.uploadProgress=undefined;ref.uploadReady=false;});
  renderRefs();
}
async function cancelPromptId(id){
  if(String(id).startsWith("request:")){
    const result=await post(`/h3_studio/lab/jobs/by_request/${encodeURIComponent(state.runMeta.request_id)}/cancel`,{});
    if(result.job_id){state.labJobId=result.job_id;state.runMeta.lab_job_id=result.job_id;saveSession();}
    return result.state==="cancelled";
  }
  const jobId=state.labJobId||state.runMeta?.lab_job_id;
  if(jobId){const result=await post(`/h3_studio/lab/jobs/${encodeURIComponent(jobId)}/cancel`,{});return result.state==="cancelled";}
  const r=await fetch(api("/queue"));
  if(!r.ok)throw Error("Queue unavailable");
  const queue=await r.json();
  if((queue.queue_running||[]).some(item=>item[1]===id)){
    await post("/interrupt",{});
    return true;
  }
  if((queue.queue_pending||[]).some(item=>item[1]===id)){
    await post("/queue",{delete:[id]});
    return true;
  }
  return false;
}
async function loadLibrary(force=false){
  const key=(localStorage.getItem(baseKey)||location.origin);
  if(state.libraryLoadedFor===key&&!force)return;
  try{
    const r=await fetch(api("/h3_studio/library"));if(!r.ok)throw Error("library unavailable");
    const items=(await r.json()).items||[];
    const known=new Map(state.results.map(e=>[e.file.filename,e]));
    const next=[];
    for(const file of items){
      const existing=known.get(file.filename);
      const entry=existing||{file,meta:{size:"H3 video"},kept:false};
      entry.file=file;
      entry.takeId=file.filename.match(/^h3_studio_([a-f0-9]{12})_/)?.[1]||null;
      entry.kept=!!file.kept;
      entry.meta.duration=entry.meta.duration||file.duration||null;
      entry.meta.elapsed=entry.meta.elapsed||file.render_seconds||null;
      entry.meta.completedAt=entry.meta.completedAt||(file.modified?Number(file.modified)*1000:null);
      entry.meta.settings=entry.meta.settings||file.settings||null;
      next.push(entry);
    }
    state.serverSamples=items.filter(file=>Number.isFinite(file.render_seconds)&&Number.isFinite(file.units)&&file.units>0&&file.sample_key)
      .map(file=>({filename:file.filename,key:file.sample_key,units:file.units,seconds:file.render_seconds,at:Number(file.modified)*1000}));
    state.results=next;appendProjectResults(state.currentProject);
    if(state.current&&!next.includes(state.current)){
      state.current=null;
      $("resultCard").classList.add("hidden");
      if(!state.busy)showStageMessage("Video no longer on server","It may have been deleted from another tab.");
    }
    state.libraryLoadedFor=key;
    $("libraryStatus").classList.add("hidden");
    renderResults();
    renderEstimates();
  }catch(e){
    $("libraryStatus").textContent=state.libraryLoadedFor?
      "Library refresh failed. Showing the last known list; use Retry above to reconnect.":
      "Could not load generated videos. Use Retry above after reconnecting.";
    $("libraryStatus").classList.remove("hidden");
  }
}
async function loadHistorySamples(){
  try{
    const r=await fetch(api("/history?max_items=100"));if(!r.ok)return;
    const history=await r.json(),samples=[];
    const prefix=(localStorage.getItem(baseKey)||location.origin)+"|"+state.gpu+"|";
    for(const job of Object.values(history)){
      const filename=findVideoOutput(job.outputs?.["12"])?.filename;
      if(!filename)continue;
      const events=job.status?.messages||[];
      const started=events.find(([name])=>name==="execution_start")?.[1]?.timestamp;
      const finished=events.find(([name])=>name==="execution_success")?.[1]?.timestamp;
      if(!Number.isFinite(started)||!Number.isFinite(finished)||finished<=started)continue;
      const graph=job.prompt?.[2]||{},main=graph["6"],sampler=graph["8"];
      if(!main||!sampler)continue;
      const inputs=main.inputs||{},keys=Object.keys(inputs);
      const mode=main.class_type==="MiniMaxH3ReferenceToVideo"?"refs":inputs.first_frame||inputs.last_frame?"frames":"text";
      const refs=mode==="refs",refSize=refs?inputs.ref_image_size||"match":"base";
      const loras=Object.values(graph).filter(node=>node.class_type==="LoraLoaderModelOnly").map(node=>node.inputs||{});
      const methodName=Object.values(graph).some(node=>node.class_type==="SpectrumApplyMiniMaxH3")?"spectrum":
        Object.values(graph).some(node=>node.class_type==="MiniMaxH3MotionCache")?"motioncache":
        loras.some(item=>item.lora_name===turboName)?"turbo":"native";
      const signature=loras.filter(item=>item.lora_name!==turboName).map(item=>item.lora_name+":"+item.strength_model).join(",");
      const units=unitsFor({width:Number(inputs.width),height:Number(inputs.height),length:Number(inputs.length),
        steps:Number(sampler.inputs?.steps),mode,refSize,
        imageCount:keys.filter(key=>key.startsWith("ref_images.ref_image_")).length,
        videoCount:keys.filter(key=>key.startsWith("ref_videos.ref_video_")).length,
        audioCount:keys.filter(key=>key.startsWith("ref_audios.ref_audio_")).length,
        frameCount:Number(!!inputs.first_frame)+Number(!!inputs.last_frame)});
      if(!Number.isFinite(units)||units<=0)continue;
      samples.push({filename,key:prefix+mode+"|"+refSize+"|method:"+methodName+"|"+signature,
        units,seconds:(finished-started)/1000,at:finished});
    }
    state.historySamples=samples.sort((a,b)=>a.at-b.at);
    const known=new Set(state.serverSamples.map(sample=>sample.filename));
    const onDisk=new Set(state.results.map(entry=>entry.file.filename));
    const missing=samples.filter(sample=>onDisk.has(sample.filename)&&!known.has(sample.filename));
    if(missing.length){
      await Promise.allSettled(missing.map(sample=>post("/h3_studio/track",{
        filename:sample.filename,render_seconds:sample.seconds,units:sample.units,sample_key:sample.key,
      })));
      await loadLibrary(true);
    }
    renderEstimates();
  }catch{}
}
function saveSession(){
  try{
    sessionStorage.setItem(sessionKey,JSON.stringify({
      server:localStorage.getItem(baseKey)||location.origin,
      results:state.results.filter(e=>!e.kept&&isStudioOutputName(e.file?.filename)).map(e=>({file:e.file,meta:e.meta})),
      active:state.running?{id:state.running,started:state.started,renderStarted:state.renderStarted,meta:state.runMeta,
        uploads:state.uploads.slice(),estimated:state.estimated,lastStep:state.lastStep,
        stepAt:state.stepAt,stepDurations:state.stepDurations.slice(-8)}:null
    }));
  }catch{}
}
async function restoreSession(){
  const key=localStorage.getItem(baseKey)||location.origin;
  if(state.sessionRecoveredFor===key)return;
  let saved;
  try{saved=JSON.parse(sessionStorage.getItem(sessionKey)||"null");}catch{}
  if(!saved||saved.server!==key){state.sessionRecoveredFor=key;return;}
  try{
    const data=await post("/h3_studio/session_resume",{
      session:sessionId,client_ts:Date.now(),
      filenames:(saved.results||[]).map(entry=>entry.file?.filename).filter(isStudioOutputName),
    });
    state.sessionRecoveredFor=key;
    const recovered=new Set(data.recovered||[]);
    for(const entry of saved.results||[]){
      if(!isStudioOutputName(entry.file?.filename))continue;
      if(!recovered.has(entry.file?.filename))continue;
      if(state.results.some(e=>e.file.filename===entry.file.filename))continue;
      state.results.push({file:entry.file,meta:entry.meta||{},kept:false,thumb:null});
    }
    const active=saved.active;
    if(active?.id&&typeof active.id==="string"&&!state.running){
      state.running=active.id;
      state.started=Number(active.started)||Date.now();
      state.renderStarted=Number(active.renderStarted)||0;
      state.runMeta=active.meta||{};
      state.uploads=Array.isArray(active.uploads)?active.uploads.filter(x=>typeof x==="string"):[];
      state.estimated=active.estimated||null;
      state.lastStep=Number.isFinite(active.lastStep)?Math.max(0,Number(active.lastStep)):0;
      state.stepAt=Number.isFinite(active.stepAt)?Number(active.stepAt):0;
      state.stepDurations=Array.isArray(active.stepDurations)?active.stepDurations.filter(x=>Number.isFinite(x)&&x>0).slice(-8):[];
      setBusy(true);
      if(state.lastStep>0)samplerProgress(state.lastStep,Number(state.runMeta?.settings?.steps)||Number($("steps").value));
      else setProgress("Restoring queued render",4,null,true);
      $("stage").innerHTML="<div class='stage-empty'><b>Render in progress</b>Checking the server for your video.</div>";
      await Promise.allSettled([pollHistory(),pollQueue()]);
    }
    renderResults();
  }catch{}
}
async function checkConnection() {
  $("connection").className="connection";
  $("connectionText").textContent="Checking ComfyUI";
  try{
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),7000);
    try{
      const [stats,readiness]=await Promise.all([
        fetch(api("/system_stats"),{signal:controller.signal}),
        fetch(api("/h3_studio/readiness"),{signal:controller.signal}),
      ]);
      if(!stats.ok)throw Error("ComfyUI is not responding.");
      if(!readiness.ok)throw Error("Install or update H3 Higgsfield on this server.");
      const data=await stats.json(),ready=await readiness.json();
      state.modelsReady=ready.models||{};
      state.nodesReady=ready.nodes||null;
      await refreshLabCapabilities();
      state.ramLimit=Number(ready.system_ram_limit_gb)||null;
      if(!state.nodesReady)throw Error("Update H3 Higgsfield to check the installed ComfyUI nodes.");
      if(!ready.quality?.h3_vae_tile_fix)throw Error("Update ComfyUI: the H3 VAE quality fix is missing.");
      state.gpu=data.devices?.[0]?.name||"unknown";
      await loadLoras();
      const required=state.mode==="refs"?["ref2va","text_encoder","video_vae","audio_vae"]:["fl2va","text_encoder","video_vae","audio_vae"];
      const missing=required.filter(name=>!state.modelsReady[name]);
      const baseNodes=["UNETLoader","MiniMaxH3SigmaShift","CLIPLoader","VAELoader","ConditioningZeroOut","KSampler","H3ReleaseForDecode","VAEDecode","VAEDecodeAudio","CreateVideo","H3SaveVideo"];
      const modeNodes=state.mode==="refs"?["MiniMaxH3ReferenceToVideo"]:["MiniMaxH3ImageToVideo"];
      if(state.mode==="frames")modeNodes.push("LoadImage");
      if(state.mode==="refs"){
        if(state.refs.some(ref=>ref.kind==="image"))modeNodes.push("LoadImage");
        if(state.refs.some(ref=>ref.kind==="video"))modeNodes.push("LoadVideo","GetVideoComponents");
        if(state.refs.some(ref=>ref.kind==="audio"))modeNodes.push("LoadAudio");
      }
      if(state.loras.some(item=>item.enabled)||method()==="turbo")modeNodes.push("LoraLoaderModelOnly");
      if(method()==="spectrum")modeNodes.push("SpectrumApplyMiniMaxH3");
      if(method()==="motioncache")modeNodes.push("MiniMaxH3MotionCache");
      const missingNodes=[...new Set([...baseNodes,...modeNodes])].filter(name=>!state.nodesReady[name]);
      $("connection").className=missing.length||missingNodes.length?"connection down":"connection ready";
      $("connectionText").textContent=missing.length?"Missing models: "+missing.join(", "):missingNodes.length?"Missing nodes: "+missingNodes.join(", "):"Connected · "+state.gpu.replace(/^.*?:/,"").slice(0,46);
      if(state.labCapabilities?.inference_enabled===false){$("connectionText").textContent="V2 · Editing standby";$("status").textContent="V2 editing ready · Generation paused";}
      if(!missing.length&&!missingNodes.length&&!state.busy&&!state.running&&$("status").textContent==="Connect ComfyUI to begin")$("status").textContent="Ready to generate";
      updateCapabilities();
      renderEstimates();
      await Promise.allSettled([loadLibrary(),restoreSession(),loadHistorySamples()]);
      return !missing.length&&!missingNodes.length&&state.labCapabilities?.inference_enabled!==false;
    }finally{clearTimeout(timeout);}
  }catch(e){
    $("connection").className="connection down";
    $("connectionText").textContent=e.message||"Server offline";
    state.nodesReady=null;state.lorasLoaded=false;
    if(!state.busy&&method()!=="native"){$("renderMethod").value="native";$("steps").value="20";renderMethodInfo();renderEstimates();}
    return false;
  }
}
$("retry").onclick=checkConnection;
$("server").value=localStorage.getItem(baseKey)||"";
$("saveServer").onclick=()=>{
  if(state.busy)return;
  const v=$("server").value.trim().replace(/\/$/,"");
  if(v)localStorage.setItem(baseKey,v);else localStorage.removeItem(baseKey);
  state.libraryLoadedFor=null;state.sessionRecoveredFor=null;state.results=[];state.current=null;state.loras=[];state.lorasLoaded=false;state.modelsReady=null;state.nodesReady=null;
  renderResults();initProjectController();connectSocket();checkConnection();
};

async function generate() {
  if(state.busy)return;
  if(state.labCapabilities?.inference_enabled===false){info(state.labCapabilities.reason||"V2 generation is paused while production uses the shared GPU.",true);return;}
  if(state.projectLoading){info("Wait for project inputs to finish restoring.",true);return;}
  if(state.restoreMissing?.length){info("Reattach missing inputs and Save the project before rendering: "+state.restoreMissing.join(", "),true);return;}
  const epoch=++state.generationEpoch;
  let prompt,settings;
  try{
    state.labJobId=null;
    if(state.continuation){const c=state.continuation;
      if(c.source_canvas&&(c.source_canvas.width!==state.width||c.source_canvas.height!==state.height))throw Error("Continuation must use the source canvas.");
      if(c.type==="generated"&&c.source_model!==(state.mode==="refs"?modelRef:modelFL))throw Error("For another checkpoint, choose Video context and re-encode the source; direct latent switching has not been validated.");
    }
    prompt=resolvedPrompt();
    const aspectLead=$("prompt").value.slice(0,300);
    const asksLandscape=/\b16\s*[:x/]\s*9\b/i.test(aspectLead),asksPortrait=/\b9\s*[:x/]\s*16\b/i.test(aspectLead);
    if(asksLandscape&&!asksPortrait&&state.height>state.width)throw Error("The prompt asks for 16:9, but Output settings is set to Portrait. Select Landscape before generating.");
    if(asksPortrait&&!asksLandscape&&state.width>state.height)throw Error("The prompt asks for 9:16, but Output settings is set to Landscape. Select Portrait before generating.");
    const wantsSourceDialogue=$("prompt").value.split(/[.!?\n]+/).some(sentence=>
      !/\b(?:do not|don't|never|avoid)\s+(?:preserve|keep|repeat|reuse)\b/i.test(sentence)
      &&/\b(?:preserve|keep|repeat|reuse)\b.{0,60}\b(?:original|source|same)\b.{0,30}\b(?:dialogue|dialog|spoken words|speech)\b/i.test(sentence));
    if(state.mode==="refs"&&state.refs.some(ref=>ref.kind==="video"&&!ref.useAudio)
      &&wantsSourceDialogue)
      throw Error("The reference video's soundtrack is off, so H3 cannot know its original words. Write the exact dialogue and language in the prompt for a new voice. Turning on the soundtrack supplies audio guidance but may also carry the original voice; H3 cannot guarantee exact speech.");
    if(!syncDuration())throw Error("Enter a duration from 5 to 15.1 seconds.");
    const steps=Number($("steps").value),seed=Number($("seed").value);
    if(!Number.isInteger(steps)||steps<Number($("steps").min)||steps>Number($("steps").max))throw Error("Sampling steps must be between "+$("steps").min+" and "+$("steps").max+" for this render method.");
    if(!methodAvailable(method()))throw Error("The selected render method is not installed on this ComfyUI server.");
    if(method()==="turbo"&&state.loras.some(item=>item.enabled))throw Error("Turn off other LoRAs before Turbo. This combination is not verified yet.");
    if(!Number.isSafeInteger(seed)||seed<0)throw Error("Use a valid non-negative seed.");
    for(const item of state.loras.filter(x=>x.enabled)){
      if(!Number.isFinite(item.strength)||item.strength<0||item.strength>2)throw Error("LoRA strength must be between 0 and 2.");
      if(/^h3-realism-people-t2v-i2v-r2v\.safetensors$/i.test(item.name)&&!/(^|\W)r34l1sm(?=\W|$)/i.test($("prompt").value))
        throw Error("Include the Realism People trigger r34l1sm in your prompt before generating.");
      if(state.mode==="refs"&&/fl2v|fl2va|t2v/i.test(item.name)&&!/ref2v|r2v/i.test(item.name))
        throw Error("LoRA "+item.name+" appears to target FL2VA, not the Ref2VA checkpoint.");
      if(state.mode!=="refs"&&/ref2v|r2v/i.test(item.name)&&!/fl2v|t2v/i.test(item.name))
        throw Error("LoRA "+item.name+" appears to target Ref2VA, not the FL2VA checkpoint.");
    }
    if(state.mode==="frames"&&state.first&&!$("frameFit").value){
      const bitmap=await createImageBitmap(state.first.file);
      const ratio=bitmap.width/bitmap.height,target=state.width/state.height;
      bitmap.close();
      if(Math.abs(ratio/target-1)>.035)throw Error("The start frame aspect ratio differs from the canvas. H3 stretches the first frame; use a matching image to avoid distortion.");
    }
    if(state.mode==="refs"&&state.refs.filter(ref=>ref.kind==="audio"||ref.kind==="video"&&ref.useAudio).length>3)
      throw Error("H3 accepts at most three audio references, including video soundtracks.");
    if(state.mode==="refs"){
      const selected=state.refs.filter(ref=>ref.kind==="audio"||ref.kind==="video"&&ref.useAudio);
      const lengths=selected.map(ref=>Number(ref.kind==="video"&&ref.trimEnabled?ref.trimDuration:ref.meta?.duration||ref.localDuration));
      if(lengths.length&&lengths.every(value=>Number.isFinite(value)&&value>0)&&lengths.reduce((sum,value)=>sum+value,0)>15.1)
        throw Error("Selected audio references and video soundtracks exceed 15 seconds combined. Shorten or deselect one before generating.");
    }
    for(const ref of state.refs.filter(item=>item.kind==="video"||item.kind==="audio")){
      const source=Number(ref.meta?.source_duration||ref.localDuration||0);
      if(ref.trimEnabled){
        const start=Number(ref.trimStart),length=Number(ref.trimDuration);
        if(!Number.isFinite(start)||start<0||!Number.isFinite(length)||length<2||length>15)
          throw Error(`@${ref.alias}: trim start must be 0 or more and selected length 2–15 seconds.`);
        if(source&&start+length>source+.01)throw Error(`@${ref.alias}: selected range ends at ${(start+length).toFixed(2)} s, but the source ends at ${source.toFixed(2)} s. Shorten Use (s).`);
      }else if(source>15.1)throw Error(`@${ref.alias}: source is ${source.toFixed(2)} s. Enable Trim and select 2–15 seconds.`);
    }
    const selectedLoras=state.loras.filter(item=>item.enabled).map(item=>item.name);
    const selectedMethod=method();
    settings=captureSettings();
    if(!await checkConnection())throw Error(state.labCapabilities?.inference_enabled===false?state.labCapabilities.reason:"Start ComfyUI or install the missing H3 models first.");
    if(method()!==selectedMethod)throw Error("The selected render method is unavailable on this server. Review the method and try again.");
    if(selectedLoras.length&&!state.lorasLoaded)throw Error("Could not verify installed LoRAs. Refresh the list before generating.");
    const missingLora=selectedLoras.find(name=>!state.loras.some(item=>item.name===name&&item.enabled));
    if(missingLora)throw Error("Selected LoRA is no longer installed on this server: "+missingLora);
  }catch(e){info(e.message,true);return;}
  info("");uploadMessage("");setBusy(true);state.abortController=new AbortController();
  state.started=Date.now();state.stepDurations=[];state.lastStep=0;state.outputMissingSince=null;
  state.renderStarted=0;
  state.estimated=estimateFor();setProgress("Uploading inputs and preparing the model",2,(state.estimated.low+state.estimated.high)/2,true);
  showStageMessage("Generating your video","The first run may include model loading.");
  focusWorkspace();
  $("resultCard").classList.add("hidden");
  try{
    const uploads={first:null,last:null,refs:new Map(),guides:new Map()};
    const totalFiles=(state.mode==="frames"?Number(!!state.first)+Number(!!state.last):state.mode==="refs"?state.refs.length:0)+state.guides.length;
    let fileIndex=0;
    const sendFile=async(file,kind,label,ref=null)=>{
      const index=++fileIndex;
      const progress=(loaded,total)=>{
        const pct=Math.min(99,Math.floor(100*loaded/Math.max(total,1)));
        uploadMessage("Uploading "+index+" of "+totalFiles+" · "+label+" · "+fmtBytes(loaded)+" / "+fmtBytes(total)+" ("+pct+"%)",pct);
        setProgress("Uploading inputs",2+Math.floor(2*((index-1)+pct/100)/totalFiles),null,true);
        if(ref&&ref.uploadProgress!==pct){ref.uploadProgress=pct;renderRefs();}
      };
      progress(0,file.size);
      if(kind==="image"&&ref?.assetId){
        const frame=state.mode==="frames"||label.startsWith("Keyframe");const fit=frame?(ref.imageFit||$("frameFit").value):ref.imageFit||"preserve";
        const result=await projectCtrl.fetchJson(`/h3_studio/lab/assets/${encodeURIComponent(ref.assetId)}/resolve`,{method:"POST",body:JSON.stringify({fit,width:state.width,height:state.height})});
        state.uploads.push(result.filename);saveSession();uploadMessage("Linked server image · "+label,100);ref.uploadProgress=undefined;ref.uploadReady=true;
        return {...result,name:result.filename};
      }
      if(kind==="image" && (state.mode==="frames" || label.startsWith("Keyframe"))) file=await fitFrameImage(file,ref?.imageFit||null);
      if(kind==="image" && ref?.imageFit && ref.imageFit!=="preserve")file=await fitFrameImage(file,ref.imageFit);
      const options={resize:kind==="image"?(state.mode==="frames"?$("fitFrames").checked:$("fitImages").checked):kind==="video"&&ref?.fitVideo!==false,
        trimEnabled:(kind==="video"||kind==="audio")&&!!ref?.trimEnabled,trimStart:ref?.trimStart||0,trimDuration:ref?.trimDuration};
      const result=await uploadOne(file,kind,progress,()=>uploadMessage("Checking "+label+" on the server · converting to 24 fps if needed",100),options);
      uploadMessage("Uploaded "+index+" of "+totalFiles+" · "+label+" · "+fmtBytes(file.size)+" · checked on server",100);
      if(ref){ref.uploadProgress=undefined;ref.uploadReady=true;renderRefs();}
      return result;
    };
    if(state.mode==="frames"){
      if(state.first)uploads.first=(await sendFile(state.first.file,"image","Start frame",state.first)).name;
      if(state.last)uploads.last=(await sendFile(state.last.file,"image","End frame",state.last)).name;
    }else if(state.mode==="refs"){
      for(const ref of orderedRefs()){
        const result=await sendFile(ref.file,ref.kind,"@"+ref.alias,ref);
        ref.meta=result;
        if(ref.kind==="video"&&result.trim_adjusted){ref.trimDuration=result.trim_duration;renderRefs();saveDraft();}
        if(ref.kind==="video"&&ref.useAudio&&!result.has_audio){
          ref.useAudio=false;renderRefs();
          throw Error("Video @"+ref.alias+" has no soundtrack. Its audio option was turned off; retry to use its picture only.");
        }
        uploads.refs.set(ref,result.name);
      }
      const videoDuration=state.refs.filter(ref=>ref.kind==="video").reduce((sum,ref)=>sum+(ref.meta?.duration||0),0);
      const audioDuration=state.refs.filter(ref=>ref.kind==="audio"||ref.kind==="video"&&ref.useAudio).reduce((sum,ref)=>sum+(ref.meta?.duration||0),0);
      if(videoDuration>15.1)throw Error("Combined video references exceed 15 seconds.");
      if(audioDuration>15.1)throw Error("Combined audio references, including selected video soundtracks, exceed 15 seconds.");
      renderRefs();
    }
    for(const guide of state.guides){
      if(!state.labCapabilities?.add_guide)throw Error("This server has no native H3 AddGuide.");
      const kind=guide.kind||"image";const uploaded=await sendFile(guide.file,kind,"Keyframe at "+guide.frame_idx+"f",guide);
      guide.fps=uploaded.fps;guide.frame_count=uploaded.frame_count;guide.duration_frames=uploaded.frame_count;
      if(kind==="audio"&&guide.frame_idx+Math.ceil(uploaded.duration*24)>Number($("duration").value)-(state.continuation?.context_length||0))throw Error("Timed audio exceeds the remaining content. Trim the source or move it earlier.");
      if(kind==="video"&&guide.use_audio&&!uploaded.has_audio)throw Error("The timed AV guide has no soundtrack.");
      uploads.guides.set(guide,uploaded.name);
    }
    if(epoch!==state.generationEpoch)throw Error("Cancelled");
    if(state.continuation?.type==="imported"){
      if(!state.continuation.source_asset_id)throw Error("Continuation source asset is missing.");
      state.continuation.source_file=await projectCtrl.resolveAsset(state.continuation.source_asset_id);state.uploads.push(state.continuation.source_file);
    }
    const token=crypto.randomUUID().replace(/-/g,"").slice(0,12);
    const workflow=graph(prompt,uploads,token);
    const units=workUnits(),sample_key=sampleKey();
    await post("/h3_studio/register_job",{token,settings,units,sample_key});
    const draft=await persistProjectDraft();
    const submittedMeta={units:workUnits(),key:sampleKey(),mode:state.mode,size:sizeKey(),duration:settings.duration_seconds,prompt:$("prompt").value,settings,project_id:state.currentProject?.project_id,continuation:state.continuation?{...state.continuation}:null,draft};
    if(epoch!==state.generationEpoch)throw Error("Cancelled");
    state.runMeta=submittedMeta;
    const submission={request_id:token,render_spec:{...currentRenderSpec(),workflow,client_id:state.clientId},asset_leases:[...state.uploads],project_id:submittedMeta.project_id,take_id:token};
    state.runMeta.request_id=token;state.runMeta.submission=submission;state.running="request:"+token;saveSession();
    const r=await fetch(api("/h3_studio/lab/jobs"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(submission)});
    const response=await r.json();
    state.labJobId=response.job?.job_id||response.job_id||null;
    if(state.labJobId)state.runMeta.lab_job_id=state.labJobId;
    const body={...response,prompt_id:response.job?.prompt_id};
    if(response.job?.state==="cancelled"){state.running=null;state.labJobId=null;saveSession();throw new DOMException("Cancelled","AbortError");}
    if(!r.ok||!body.prompt_id){
      if(!r.ok&&r.status<500){state.running=null;state.labJobId=null;saveSession();}
      if(state.labJobId&&r.status>=500){state.running="lab:"+state.labJobId;saveSession();}
      throw Error(response.error||"The queue did not confirm submission. Its saved job will be reconciled before retrying.");
    }
    state.running=body.prompt_id;saveSession();
    if(epoch!==state.generationEpoch){
      let confirmed=false;
      try{confirmed=await cancelPromptId(body.prompt_id);}catch{}
      if(!confirmed){
        state.runMeta=submittedMeta;
        state.running=body.prompt_id;saveSession();
        throw Error("Cancellation could not be confirmed. The job is still being tracked; retry Cancel after checking the queue.");
      }
      state.running=null;state.labJobId=null;saveSession();
      throw Error("Cancelled");
    }
    state.runMeta=submittedMeta;
    state.running=body.prompt_id;
    saveSession();
    uploadMessage("");
    saveSession();
    setProgress("Queued or loading H3",4,(state.estimated.low+state.estimated.high)/2,true);
  }catch(e){
    if(state.running){setBusy(true);info(e.message,true);return;}
    await cleanupUploads();setBusy(false);
    const cancelled=epoch!==state.generationEpoch||e.name==="AbortError";
    setProgress(cancelled?"Cancelled":"Generation failed",0,null);
    showStageMessage(cancelled?"Render cancelled":"Generation failed",cancelled?"Your inputs are ready for another attempt.":"Check the message beside the prompt, then try again.");
    if(!cancelled)info(e.message,true);
    uploadMessage(cancelled?"Upload cancelled.":"Upload stopped: "+e.message);
  }finally{if(epoch===state.generationEpoch)state.abortController=null;}
}
$("generate").onclick=generate;
$("cancel").onclick=async()=>{
  ++state.generationEpoch;
  state.abortController?.abort();
  const id=state.running;
  state.running=null;
  if(!id){setProgress("Cancelling upload or queue request",0,null,true);return;}
  if(id){
    try{
      if(!await cancelPromptId(id)){
        state.running=id;await pollHistory();
        if(state.running){info("Job not in the queue yet; checking history before cancellation.",true);setBusy(true);saveSession();}
        return;
      }
    }catch(e){state.running=id;info("Could not confirm cancellation on the server. Check the queue before restarting.",true);setBusy(true);saveSession();return;}
  }
  await cleanupUploads();setBusy(false);info("");uploadMessage("");setProgress("Cancelled",0,null);showStageMessage("Render cancelled","Your inputs are ready for another attempt.");saveSession();pollQueue();
};

function videoURL(file) {
  const p=new URLSearchParams({filename:file.filename,subfolder:file.subfolder||"video",type:file.type||"output",_time:String(Date.now())});
  return api("/view?"+p);
}
function isStudioOutputName(name){return /^h3_studio_[0-9a-f]{12}_[0-9]+_\.mp4$/i.test(name||"");}
function findVideoOutput(output){
  for(const key of ["images","gifs","videos","video"]){
    const items=Array.isArray(output?.[key])?output[key]:output?.[key]?[output[key]]:[];
    const found=items.find(file=>file&&isStudioOutputName(file.filename)
      &&file.type==="output"&&(file.subfolder||"video")==="video");
    if(found)return found;
  }
  return null;
}
function thumbnailURL(file){return api("/h3_studio/thumbnail?"+new URLSearchParams({filename:file.filename}));}
function renderResults(){
  $("results").replaceChildren();
  $("videoCount").textContent="("+state.results.length+" on server)";
  $("wipe").classList.toggle("hidden",!state.results.some(e=>!e.kept));
  $("emptyHistory").classList.toggle("hidden",!!state.results.length);
  const remaining=Math.max(0,state.results.length-state.visibleResults);
  $("showMoreVideos").classList.toggle("hidden",remaining===0);
  $("showMoreVideos").textContent="Show "+Math.min(8,remaining)+" more · "+remaining+" remaining";
  state.results.slice(0,state.visibleResults).forEach((entry,index)=>{
    const card=document.createElement("div");card.className="result"+(entry.kept?" pinned":"");
    const b=document.createElement("button");b.className="result-open";b.type="button";
    const when=entry.meta.completedAt?new Date(entry.meta.completedAt).toLocaleString():"";
    const media=document.createElement("div");media.className="result-media";
    const img=document.createElement("img");img.src=thumbnailURL(entry.file);img.alt="Preview of video "+(index+1);img.loading="lazy";
    img.onerror=()=>{const fallback=document.createElement("span");fallback.className="result-fallback";fallback.textContent="▶";media.replaceChildren(fallback);};
    media.append(img);
    const label=document.createElement("div");label.className="result-label";
    label.textContent=(Number.isFinite(Number(entry.meta.duration))&&Number(entry.meta.duration)>0?Number(entry.meta.duration).toFixed(1)+"s video":"H3 video")+(entry.kept?" · Pinned":"");
    const detail=document.createElement("small");detail.textContent=(when||"Date unavailable")+(entry.meta.elapsed?" · Render "+fmt(entry.meta.elapsed):"");
    label.append(detail);b.append(media,label);
    b.setAttribute("aria-label",label.textContent);
    b.onclick=()=>showVideo(entry);
    const details=document.createElement("button");details.className="result-details";details.type="button";
    details.textContent="Details";details.setAttribute("aria-label","Settings for video "+(index+1));
    details.onclick=()=>openDetails(entry,details);
    card.append(b,details);$("results").append(card);
  });
}
$("showMoreVideos").onclick=()=>{state.visibleResults+=8;renderResults();};
let settingsTrigger=null;
function openDetails(entry,trigger){
  if(!entry)return;
  const content=$("settingsContent"),settings=entry.meta?.settings||entry.file?.settings||null;
  content.replaceChildren();
  const grid=document.createElement("dl");grid.className="settings-grid";
  const row=(key,value)=>{
    const dt=document.createElement("dt"),dd=document.createElement("dd");
    dt.textContent=key;dd.textContent=value===null||value===undefined||value===""?"—":String(value);
    grid.append(dt,dd);
  };
  row("Completed",entry.meta?.completedAt?new Date(entry.meta.completedAt).toLocaleString():"—");
  row("Video length",Number.isFinite(Number(entry.meta?.duration))&&Number(entry.meta.duration)>0?Number(entry.meta.duration).toFixed(1)+" s":"—");
  row("Render time",Number.isFinite(Number(entry.meta?.elapsed))&&Number(entry.meta.elapsed)>0?fmt(Number(entry.meta.elapsed)):"—");
  if(settings){
    if(settings.partial){
      const note=document.createElement("p");note.className="tip";
      note.textContent="Some settings were not recorded for this earlier video; only confirmed values are shown.";
      content.append(note);
    }
    const modeNames={text:"Text to video",frames:"Start / end frames",refs:"References"};
    const methodNames={native:"Original quality",spectrum:"Spectrum",motioncache:"MotionCache",turbo:"Turbo LoRA"};
    row("Mode",modeNames[settings.mode]||settings.mode);
    row("Model",settings.model);
    row("Canvas",[settings.quality,settings.canvas].filter(Boolean).join(" · "));
    row("Requested length",settings.duration_seconds?Number(settings.duration_seconds).toFixed(1)+" s · "+settings.frames+" frames":"—");
    row("Render method",methodNames[settings.render_method]||settings.render_method);
    row("Sampling steps",settings.steps);
    row("Seed",settings.seed);
    row("LoRAs",Array.isArray(settings.loras)?settings.loras.length?settings.loras.map(x=>x.name+" ("+x.strength+")").join(" · "):"None":"Not recorded");
    if(settings.reference_detail)row("Reference detail",settings.reference_detail);
    if(settings.start_frame)row("Start frame",settings.start_frame);
    if(settings.end_frame)row("End frame",settings.end_frame);
    if(Array.isArray(settings.references))row("References",settings.references.length?settings.references.map(x=>[x.name,x.type,x.use_as,x.file,x.video_soundtrack?"soundtrack on":""].filter(Boolean).join(" · ")).join("\n"):"None");
  }else{
    const note=document.createElement("p");note.className="tip";
    note.textContent="This video predates saved settings. Its file and timing remain available.";
    content.append(note);
  }
  content.append(grid);
  if(settings?.prompt){
    const heading=document.createElement("h3");heading.textContent="Prompt";
    const prompt=document.createElement("div");prompt.className="detail-prompt";prompt.textContent=settings.prompt;
    content.append(heading,prompt);
  }
  settingsTrigger=trigger||document.activeElement;
  $("settingsDialog").showModal();
  $("settingsClose").focus();
}
$("details").onclick=event=>openDetails(state.current,event.currentTarget);
$("settingsClose").onclick=()=>$("settingsDialog").close();
$("settingsDialog").addEventListener("click",event=>{if(event.target===$("settingsDialog"))$("settingsDialog").close();});
$("settingsDialog").addEventListener("close",()=>{if(settingsTrigger?.isConnected)settingsTrigger.focus();settingsTrigger=null;});
function showVideo(entry){
  state.current=entry;$("stage").replaceChildren();
  const v=document.createElement("video");
  v.src=videoURL(entry.file);v.controls=true;v.autoplay=true;v.loop=true;
  $("stage").append(v);$("resultCard").classList.remove("hidden");
  $("download").textContent="Download";
  $("keep").textContent=entry.kept?"Pinned":"Pin video";
  $("keep").disabled=entry.kept;
  const imported=entry.file.subfolder&&entry.file.subfolder!=="video";$("keep").hidden=!!imported;$("discard").hidden=!!imported;
}
async function complete(file){
  if(state.completing)return;state.completing=true;
  try{await completeImpl(file);}finally{state.completing=false;}
}
async function completeImpl(file){
  if(!state.running||!isStudioOutputName(file?.filename))return;
  try{
    const response=await fetch(api("/h3_studio/library"),{cache:"no-store"});
    if(!response.ok)throw Error("Library unavailable");
    const listed=((await response.json()).items||[]).some(item=>item.filename===file.filename);
    if(!listed){
      state.outputMissingSince??=Date.now();
      if(Date.now()-state.outputMissingSince>10000){
        info("ComfyUI reported a video, but the saved file is missing. No completed video was added. Check the Save Video node or server storage.",true);
        state.running=null;await cleanupUploads();setBusy(false);setProgress("Output missing",0,null);
        showStageMessage("Output missing","The server did not save the generated video.");saveSession();pollQueue();
      }else setProgress("Checking saved video",99,null);
      return;
    }
  }catch{return;}
  if(!state.running)return;
  state.outputMissingSince=null;
  state.running=null;
  state.labJobId=null;
  const completedEpoch=state.generationEpoch;
  const actual=(Date.now()-state.started)/1000;
  const renderActual=(Date.now()-(state.renderStarted||state.started))/1000;
  const meta={...(state.runMeta||{}),elapsed:actual,renderElapsed:renderActual,completedAt:Date.now()};
  const samples=readSamples();
  if(meta.key&&Number.isFinite(meta.units)&&meta.units>0)samples.push({filename:file.filename,key:meta.key,units:meta.units,seconds:renderActual,at:Date.now()});
  try{localStorage.setItem(storeKey,JSON.stringify(samples.slice(-80)));}catch{}
  const entry={file,meta,kept:false,thumb:null};
  const takeId = file.filename.replace(/^h3_studio_/, "").replace(/_[0-9]+_\.mp4$/, "");
  entry.takeId = takeId;
  await post("/h3_studio/track",{filename:file.filename,render_seconds:renderActual,
    units:meta.units,sample_key:meta.key,settings:meta.settings}).catch(()=>{});
  state.results.unshift(entry);
  if(projectCtrl && meta.project_id){
    try {
      const proj=await projectCtrl.fetchJson(`/h3_studio/lab/projects/${encodeURIComponent(meta.project_id)}`);
      const take={take_id:takeId,clip_id:state.currentClipId||("clip_"+takeId.slice(0,8)),
        output_file:"video/"+file.filename,effective_settings:meta.settings||{},draft:meta.draft,
        parent_take_id:meta.continuation?.source_take_id||null,overlap_frames:0,
        unique_frames:meta.settings?.frames||124,
        context_token:takeId,created_at:Date.now()/1000,status:"completed"};
      const saved=await projectCtrl.fetchJson(`/h3_studio/lab/projects/${encodeURIComponent(meta.project_id)}`,{
        method:"POST",body:JSON.stringify({project:{...proj,takes:[...(proj.takes||[]).filter(t=>t.take_id!==takeId),take]},expected_revision:proj.revision})});
      if(projectCtrl.currentProject?.project_id===meta.project_id){projectCtrl.currentProject=saved;updateProjectUI(saved);}
    }catch(e){info("Video saved; attaching its take to the project failed: "+e.message,true);}
  }
  const cleanup=cleanupUploads();
  setBusy(false);showVideo(entry);renderResults();
  focusWorkspace();
  saveSession();pollQueue();
  setProgress("Checking video and audio",99,null);
  try{
    const qa=await post("/h3_studio/verify_video",{filename:file.filename});
    if(!qa.ok&&completedEpoch===state.generationEpoch)info("Video saved, but media check found a problem: "+qa.message,true);
  }catch(e){if(completedEpoch===state.generationEpoch)info("Video saved; automatic audio check was unavailable. Play and listen before relying on this clip.",true);}
  await cleanup;
  if(completedEpoch===state.generationEpoch){setProgress("Completed",100,0);$("timer").textContent="Actual time: "+fmt(actual);}
  await loadLibrary(true);
  renderEstimates();
}
async function discardEntry(entry,includeSaved=false){
  if(!entry||entry.kept&&!includeSaved)return false;
  const result=await post(entry.kept?"/h3_studio/delete_saved":"/h3_studio/discard",{filename:entry.file.filename});
  if(!result.ok)throw Error("Server did not confirm deletion.");
  return true;
}
$("keep").onclick=async()=>{
  const entry=state.current;if(!entry)return;
  try{await post("/h3_studio/keep",{filename:entry.file.filename});entry.kept=true;showVideo(entry);renderResults();}catch(e){info("Could not keep the video on the server: "+e.message,true);}
};
$("download").onclick=()=>{
  const entry=state.current;if(!entry)return;
  const a=document.createElement("a");a.href=videoURL(entry.file);a.download=entry.file.filename;document.body.append(a);a.click();a.remove();
};
if($("extendVideo")) $("extendVideo").onclick=async()=>{
  const entry=state.current;if(!entry||state.busy)return;
  try{
    await refreshLabCapabilities();
    if(!state.labCapabilities?.continuation_ready)throw Error("Install the pinned continuation engine on the isolated server first. See Lab readiness.");
    const token=entry.takeId||entry.file.filename.match(/h3_studio_([a-f0-9]{12})/)?.[1];
    if(!token)throw Error("This older clip has no saved sampler context.");
    if($("extendSourceMethod").value==="imported"){
      const imported=await post("/h3_studio/lab/media/import_video",{filename:entry.file.filename,output_file:resultOutputPath(entry),project_id:state.activeProjectId});
      if(imported.metadata.width!==state.width||imported.metadata.height!==state.height)throw Error("Use the source video's canvas before extending it.");
      state.currentClipId="clip_"+crypto.randomUUID();
      state.continuation={type:"imported",source_take_id:entry.takeId,source_asset_id:imported.asset.asset_id,source_canvas:{width:imported.metadata.width,height:imported.metadata.height},context_length:39,audio_feather_ticks:8};
      $("durationSeconds").value="7.3";syncDuration();renderContinuation();$("prompt").value="";$("prompt").focus();updatePreviewLive();return;
    }
    const context=await projectCtrl.fetchJson(`/h3_studio/lab/contexts/${encodeURIComponent(token)}`);
    const expected=state.mode==="refs"?modelRef:modelFL;
    if(context.model_id!==expected||context.width!==state.width||context.height!==state.height)throw Error("Use the source checkpoint and canvas before extending this clip. Switching checkpoints has not been validated.");
    state.currentClipId="clip_"+crypto.randomUUID();
    state.continuation={type:"generated",source_take_id:entry.takeId,source_token:token,source_model:context.model_id,source_canvas:{width:context.width,height:context.height},context_length:39,audio_feather_ticks:8};
    $("durationSeconds").value="7.3";syncDuration();renderContinuation();
    $("prompt").value="";$("prompt").placeholder="Describe continuation motion and sound…";$("prompt").focus();
    updatePreviewLive();
  }catch(e){info(e.message,true);}
};
$("discard").onclick=async()=>{
  const entry=state.current;if(!entry)return;
  if(!confirm("Delete this video from the server? This cannot be undone."))return;
  try{await discardEntry(entry,true);}catch(e){info("Could not delete the video: "+e.message,true);return;}
  state.results=state.results.filter(e=>e!==entry);state.current=null;renderResults();
  $("resultCard").classList.add("hidden");$("stage").innerHTML="<div class='stage-empty'>Video deleted.</div>";
};
$("wipe").onclick=async()=>{
  const count=state.results.filter(e=>!e.kept).length;
  if(!count||!confirm("Delete "+count+" unpinned video(s) from this server? This cannot be undone."))return;
  const failed=[];
  await Promise.all(state.results.filter(e=>!e.kept).map(async entry=>{try{await discardEntry(entry);}catch{failed.push(entry);}}));
  state.results=state.results.filter(e=>e.kept||failed.includes(e));state.current=null;renderResults();
  if(failed.length)info(failed.length+" unpinned video(s) could not be deleted. Retry after reconnecting.",true);
  $("resultCard").classList.add("hidden");$("stage").innerHTML="<div class='stage-empty'>Selected videos deleted.</div>";
  await loadLibrary(true);
};

function showPreview(blob){
  if(!state.running)return;
  let img=$("stage").querySelector("img");
  if(!img){$("stage").replaceChildren();img=document.createElement("img");$("stage").append(img);}
  const old=img.src;img.src=URL.createObjectURL(blob);if(old.startsWith("blob:"))URL.revokeObjectURL(old);
}
function samplerProgress(value,max){
  const now=Date.now(),step=Math.round(value),total=Math.round(max);
  // ComfyUI starts each node at 0/1 before sampler steps are available.
  // Treating that node marker as the sampler count leaves the UI stuck at 0/1.
  if(!Number.isFinite(step)||!Number.isFinite(total)||total<2||step<0)return;
  if(step>state.lastStep&&state.stepAt&&state.lastStep>0){
    state.stepDurations.push((now-state.stepAt)/1000/(step-state.lastStep));
    state.stepDurations=state.stepDurations.slice(-8);
  }
  if(step>state.lastStep){state.lastStep=step;state.stepAt=now;}
  const avg=state.stepDurations.length?state.stepDurations.reduce((a,b)=>a+b,0)/state.stepDurations.length:null;
  const baseline=state.estimated?(state.estimated.low+state.estimated.high)/2:null;
  const remaining=avg?avg*(total-step)+Math.max(20,avg*total*.13):baseline?Math.max(0,baseline-(now-state.started)/1000):null;
  setProgress("H3 sampling · "+step+"/"+total,10+Math.min(80,80*step/total),remaining);
  saveSession();
}
async function pollServerProgress(id){
  try{
    const r=await fetch(api("/h3_studio/job_progress?prompt_id="+encodeURIComponent(id)),{cache:"no-store"});
    if(!r.ok)return;
    const data=await r.json();
    if(state.running!==id||data.prompt_id!==id||!Number.isFinite(data.step)||!Number.isFinite(data.total))return;
    if(data.step>=state.lastStep&&data.total>0)samplerProgress(data.step,data.total);
  }catch{}
}
function onSocket(event){
  if(typeof event.data!=="string"){
    if(!state.running||event.data.byteLength<8)return;
    const v=new DataView(event.data),kind=v.getUint32(0);
    if(kind===1)showPreview(new Blob([event.data.slice(8)],{type:v.getUint32(4)===2?"image/png":"image/jpeg"}));
    if(kind===4&&event.data.byteLength>=12){
      const count=v.getUint32(4);
      if(count<10000&&8+count<event.data.byteLength){
        try{const meta=JSON.parse(new TextDecoder().decode(new Uint8Array(event.data,8,count)));showPreview(new Blob([event.data.slice(8+count)],{type:meta.image_type||"image/jpeg"}));}catch{}
      }
    }
    return;
  }
  let message;try{message=JSON.parse(event.data);}catch{return;}
  const d=message.data||{};
  if(d.prompt_id!==state.running)return;
  if(!state.running)return;
  if(message.type==="execution_start"){
    state.renderStarted||=Date.now();saveSession();
  }else if(message.type==="progress_state"){
    const n=d.nodes?.["8"];if(n&&n.max)samplerProgress(n.value,n.max);
  }else if(message.type==="progress"&&d.node==="8"&&d.max)samplerProgress(d.value,d.max);
  else if(message.type==="executing"&&d.node){
    const phases={"1":["Loading model",5],"3":["Loading text encoder",7],"6":["Preparing prompt and references",9],"8":["Preparing sampler",10],"9":["Decoding video",91],"10":["Decoding audio",94],"11":["Muxing video and audio",96],"12":["Saving video",98]};
    if(phases[d.node]){
      const [label,pct]=phases[d.node];
      let remaining=null;
      if(pct>=91){
        const avg=state.stepDurations.length?state.stepDurations.reduce((a,b)=>a+b,0)/state.stepDurations.length:null;
        const midpoint=state.estimated?(state.estimated.low+state.estimated.high)/2:null;
        const tail=avg?Math.max(20,avg*Number($("steps").value)*.13):midpoint?midpoint*.1:null;
        if(tail)remaining=tail*(100-pct)/9;
      }
      setProgress(label,pct,remaining,pct<10);
    }
  }else if(message.type==="executed"&&d.node==="12"){
    const file=findVideoOutput(d.output);
    if(file)complete(file);
  }
  else if(message.type==="execution_error"||message.type==="execution_interrupted"){
    info(message.type==="execution_error"?(d.exception_message||"ComfyUI graph failed"):"Render interrupted",true);
    state.running=null;cleanupUploads();setBusy(false);setProgress("Render stopped",0,null);
    showStageMessage("Render stopped","Check the error message and retry when ready.");
    saveSession();pollQueue();
  }
}
function connectSocket(){
  const epoch=++state.socketEpoch;
  if(state.socket){try{state.socket.close();}catch{}}
  const base=localStorage.getItem(baseKey)||location.origin;
  const url=base.replace(/^http/,"ws").replace(/\/$/,"")+"/ws?clientId="+state.clientId;
  try{
    const ws=new WebSocket(url);state.socket=ws;ws.binaryType="arraybuffer";
    ws.onopen=()=>{state.reconnect=0;ws.send(JSON.stringify({type:"feature_flags",data:{supports_preview_metadata:true}}));};
    ws.onmessage=onSocket;
    ws.onclose=()=>{if(epoch===state.socketEpoch)setTimeout(()=>{if(epoch===state.socketEpoch)connectSocket();},Math.min(15000,1000*2**state.reconnect++));};
  }catch{setTimeout(connectSocket,5000);}
}
async function pollHistory(){
  if(state.pollingHistory)return;state.pollingHistory=true;
  try{await pollHistoryImpl();}finally{state.pollingHistory=false;}
}
async function pollHistoryImpl(){
  if(!state.running)return;
  if(String(state.running).startsWith("request:")&&state.runMeta?.submission){
    try{
      const lookup=await fetch(api(`/h3_studio/lab/jobs/by_request/${encodeURIComponent(state.runMeta.request_id)}`));
      let record;
      if(lookup.status===404){
        const retry=await fetch(api("/h3_studio/lab/jobs"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(state.runMeta.submission)});
        const response=await retry.json();record=response.job;
        if(!retry.ok&&retry.status<500){state.running=null;await cleanupUploads();setBusy(false);info(response.error||"Submission rejected",true);saveSession();return;}
      }else if(lookup.ok)record=await lookup.json();
      if(record?.job_id){state.labJobId=record.job_id;state.runMeta.lab_job_id=record.job_id;state.running=record.prompt_id||"lab:"+record.job_id;saveSession();}
    }catch{}
  }
  const jobId=state.labJobId||state.runMeta?.lab_job_id;
  if(jobId){try{const job=await projectCtrl.fetchJson(`/h3_studio/lab/jobs/${encodeURIComponent(jobId)}`);
    if(job.prompt_id){state.running=job.prompt_id;saveSession();}
    if(job.state==="completed"&&job.output){await complete(job.output);return;}
    if(job.state==="cancelled"||job.state==="failed"){state.running=null;await cleanupUploads();setBusy(false);setProgress(job.state,0,null);info(job.error?JSON.stringify(job.error).slice(0,400):"Job "+job.state,job.state==="failed");saveSession();return;}
  }catch{} }
  if(!state.running||/^(lab|request):/.test(String(state.running)))return;
  try{
    const r=await fetch(api("/history/"+state.running));if(!r.ok)return;
    const data=(await r.json())[state.running];if(!data)return;
    const file=findVideoOutput(data.outputs?.["12"]);
    if(file){await complete(file);return;}
    if(data.status?.status_str==="error"){
      const errors=(data.status?.messages||[]).filter(item=>item?.[0]==="execution_error");
      const detail=errors.at(-1)?.[1]?.exception_message;
      info(detail?"ComfyUI failed: "+String(detail).slice(0,500):"ComfyUI failed. Check the failing node message on the server.",true);
      state.running=null;await cleanupUploads();setBusy(false);setProgress("Failed",0,null);
      showStageMessage("Generation failed","Check the ComfyUI error and retry when ready.");
      saveSession();pollQueue();
    }
  }catch{}
}
async function pollQueue(){
  try{
    const r=await fetch(api("/queue"));if(!r.ok)throw Error("queue unavailable");
    if(state.serverMissingSince){state.serverMissingSince=null;if(state.running)setProgress("Connected · checking render progress",null,null,true);}
    const data=await r.json(),running=data.queue_running||[],pending=data.queue_pending||[];
    const id=state.running;
    if(/^(lab|request):/.test(String(id))){$("queueInfo").textContent="Queue: reconciling saved submission · inputs retained";await pollHistory();return;}
    const runningItem=running.find(item=>item[1]===id);
    const runningHere=!!runningItem;
    const position=pending.findIndex(item=>item[1]===id);
    const count=running.length+pending.length;
    $("queueInfo").textContent=id?(runningHere?"Queue: rendering now · "+count+" job(s) on server":position>=0?"Queue: position "+(position+1)+" of "+pending.length+" waiting · "+count+" total":"Queue: checking job history · "+count+" on server"):"Queue: "+count+" job(s) on server";
    if(id&&position>=0){
      state.phaseEtaAt=null;
      setProgress("Waiting in queue · position "+(position+1),4,null,true);
      $("remaining").textContent="Render estimate starts when your job runs";
    }
    if(id&&(runningHere||position>=0))state.queueMissingSince=null;
    if(id&&runningHere){
      // ComfyUI sends sampler updates only to the client ID that queued the job.
      // A restored tab may have a new ID, even while the queue still owns the render.
      const ownerId=runningItem[3]?.client_id;
      if(ownerId&&ownerId!==state.clientId){
        state.clientId=ownerId;
        sessionStorage.setItem("h3studio.client.id.v1",ownerId);
        connectSocket();
      }
      if(!state.renderStarted){state.renderStarted=Date.now();saveSession();}
      await pollServerProgress(id);
      if(!state.lastStep&&Date.now()-state.renderStarted>30000&&$("status").textContent==="Preparing sampler")
        setProgress("H3 sampling · detailed step updates unavailable",null,null,true);
      if(!state.lastStep&&state.phaseEtaAt===null&&["Restoring","Waiting in queue"].some(prefix=>$("status").textContent.startsWith(prefix))){
        const midpoint=state.estimated?(state.estimated.low+state.estimated.high)/2:null;
        setProgress("H3 rendering · waiting for step updates",8,midpoint,true);
      }
    }
    // History polling resolves jobs that finish while the browser is closed or the socket reconnects.
    if(id&&!runningHere&&position<0){
      await pollHistory();
      if(state.running){
        state.queueMissingSince??=Date.now();
        if(Date.now()-state.queueMissingSince>30000){
          info("This render is absent from both ComfyUI queue and history. The server may have restarted.",true);
          state.running=null;state.queueMissingSince=null;
          await cleanupUploads();setBusy(false);setProgress("Render unavailable",0,null);showStageMessage("Render unavailable","The server no longer lists this job. Check ComfyUI before trying again.");saveSession();
        }
      }
    }
  }catch{
    $("queueInfo").textContent="Queue: unavailable while disconnected";
    if(state.running){
      state.serverMissingSince??=Date.now();
      if(Date.now()-state.serverMissingSince>15000){
        state.phaseEtaAt=null;
        $("remaining").textContent="Time remaining: —";
        $("status").textContent="Server disconnected · render status unknown";
        showStageMessage("Server disconnected","The render may have stopped. Checking for the server to return.");
        info("Connection to ComfyUI was lost. If the server ran out of memory, the unfinished video cannot be resumed.",true);
      }
    }
  }
}
setInterval(pollHistory,5000);
setInterval(pollQueue,5000);
setInterval(()=>{if(document.visibilityState==="visible")loadLibrary(true);},20000);
setInterval(()=>{if(document.visibilityState==="visible")loadHistorySamples();},60000);
addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible")loadLibrary(true);});
setInterval(()=>{
  if(!state.busy)return;
  const elapsed=(Date.now()-state.started)/1000;
  $("timer").textContent="Elapsed: "+fmt(elapsed);
  if(state.phaseEtaAt){const left=(state.phaseEtaAt-Date.now())/1000;$("remaining").textContent=left<=0?"Estimate exceeded · still working":"Approx. time remaining: "+fmt(left);}
},1000);
addEventListener("pagehide",event=>{
  if(event.persisted)return;
  saveSession();
  // A refresh disconnects this client, but ComfyUI keeps the queued job and its uploaded inputs.
  if(!state.running)state.uploads.forEach(filename=>navigator.sendBeacon(api("/h3_studio/discard"),new Blob([JSON.stringify({filename})],{type:"application/json"})));
});
let projectCtrl = null;
async function initProjectController() {
  state.projectLoading=true;++state.hydrationEpoch;
  const apiBase=localStorage.getItem(baseKey)||"";
  let controller;controller=new H3ProjectController.ProjectController({apiBase,serverKey:apiBase||location.origin,onProjectChanged:p=>{if(controller===projectCtrl)updateProjectUI(p);}});
  projectCtrl=controller;
  try{
    const pid=localStorage.getItem(controller._storageKey("active_project_id"));
    let project;
    if(pid){try{project=await controller.loadProject(pid);}catch(e){if(e.status!==404)throw e;project=await controller.createProject("Default Project",{width:state.width,height:state.height});}}
    else project=await controller.createProject("Default Project",{width:state.width,height:state.height});
    if(controller!==projectCtrl)return;
    await hydrateProjectDraft(project.draft||{mode:"text",prompts:{text:$("prompt").value,frames:"",refs:""},refs:[],guides:[]});
    const json=sessionStorage.getItem("h3_lab.imported_handoff");
    if(json){const handoff=JSON.parse(json);if(handoff?.asset){await applyQwenHandoff(handoff.asset,handoff.as_role);sessionStorage.removeItem("h3_lab.imported_handoff");}}
  }catch(e){info("Project setup unavailable: "+e.message,true);}
  finally{if(controller===projectCtrl){state.projectLoading=false;refreshLabControls();}}
}

function updateProjectUI(proj) {
  if (!proj) return;
  state.currentProject = proj;
  state.activeProjectId = proj.project_id;
  if ($("projectNameDisplay")) $("projectNameDisplay").textContent = `${proj.name || 'Project'} (${proj.project_id.slice(0, 8)})`;
  if ($("projectRevision")) $("projectRevision").textContent = `Rev ${proj.revision || 1}`;
  appendProjectResults(proj);renderResults();renderSequenceCard();
  refreshProjectList().catch(e=>info("Project list unavailable: "+e.message,true));
}

async function applyQwenHandoff(asset, asRole) {
  try{
    const blob=await projectCtrl.mediaBlob(asset.asset_id);
    const file=new File([blob],asset.original_name||"qwen.png",{type:blob.type||"image/png"});
    const item={assetId:asset.asset_id,file,url:URL.createObjectURL(blob)};
    if(asRole==="start_frame"||asRole==="end_frame"){
      const which=asRole==="start_frame"?"first":"last";selectMode("frames",true);state[which]=item;renderFrameItem(which);
    }else{
      selectMode("refs",true);state.refs.push({...item,previewUrl:item.url,alias:aliasName(asset.alias||file.name),kind:"image",role:"custom",instruction:""});renderRefs();
    }
    info("Qwen image linked to H3. Save the project to retain this setup.");updatePreviewLive();saveDraft();
  }catch(e){info("Cannot restore Qwen image: "+e.message,true);throw e;}
}

function renderSequenceCard() {
  const card = $("sequenceCard");
  const strip = $("clipStrip");
  const durationSpan = $("sequenceDuration");
  if (!card || !strip) return;

  const proj = state.currentProject;
  const acceptedTakeIds = proj?.accepted_take_ids || [];
  const takes = proj?.takes || [];
  const acceptedTakes = acceptedTakeIds.map(id => takes.find(t => t.take_id === id)).filter(Boolean);

  if (!acceptedTakes.length && !state.results.length) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  strip.replaceChildren();

  let totalFrames = 0;
  acceptedTakes.forEach((take, idx) => {
    const chip = document.createElement("div");
    chip.className = "prompt-asset used";
    chip.style.display = "flex";
    chip.style.alignItems = "center";
    chip.style.gap = "8px";

    const frames = take.effective_settings?.frames || 124;
    totalFrames += take.unique_frames ?? Math.max(0,frames-(take.overlap_frames||0));

    chip.textContent = `Clip ${idx+1} · Take ${String(take.take_id).slice(0,6)} · ${take.unique_frames??frames}f`;
    const remove=document.createElement("button");remove.textContent="Remove";remove.className="button alt smallbtn";remove.disabled=state.busy;remove.onclick=()=>projectCtrl.saveCurrentProject({accepted_take_ids:acceptedTakeIds.filter(id=>id!==take.take_id)}).catch(e=>info(e.message,true));chip.append(remove);
    strip.append(chip);
  });

  const totalSec = (totalFrames / 24).toFixed(2);
  if (durationSpan) durationSpan.textContent = `${acceptedTakes.length} clip(s) · ${totalSec}s total`;
}

if ($("btnNewProject")) {
  $("btnNewProject").onclick = async () => {
    const name = prompt("Project name:", "New H3 Project");
    if (!name) return;
    try {
      await withProjectLoading(async()=>{await projectCtrl.createProject(name, { width: state.width, height: state.height });
      state.currentClipId=null;await hydrateProjectDraft({mode:"text",refs:[],guides:[],prompts:{text:"",frames:"",refs:""}});});
      info(`Created project "${name}".`);
    } catch (e) {
      info("Failed to create project: " + e.message, true);
    }
  };
}
if ($("btnSaveProject")) {
  $("btnSaveProject").onclick = async () => {
    if (!projectCtrl || !projectCtrl.currentProject) {
      await projectCtrl.createProject("Default Project", { width: state.width, height: state.height });
    }
    try {
      const currentTakeId = state.current?.takeId;
      const updates = {
        canvas: { width: state.width, height: state.height },
        fps: 24,
        draft:await persistProjectDraft(),
        assets:[...new Map([...(projectCtrl.currentProject.assets||[]),...allInputItems().filter(x=>x.assetId).map(x=>({asset_id:x.assetId,alias:x.alias,role:x.role}))].map(x=>[x.asset_id,x])).values()],
      };
      await projectCtrl.saveCurrentProject(updates);
      state.restoreMissing=[];info("Project saved successfully.");
    } catch (e) {
      info("Failed to save project: " + e.message, true);
    }
  };
}
if ($("btnExportProject")) {
  $("btnExportProject").onclick = async () => {
    try {
      const url = await projectCtrl.exportBundle();
      window.open(url, "_blank");
    } catch (e) {
      info("Export bundle failed: " + e.message, true);
    }
  };
}
if ($("btnAssembleSequence")) {
  $("btnAssembleSequence").onclick = async () => {
    const proj = state.currentProject;
    if (!proj || !proj.accepted_take_ids?.length) {
      info("No accepted takes in the current sequence to assemble.", true);
      return;
    }
    const status = $("sequenceExportStatus");
    if (status) status.textContent = "Assembling sequence with FFmpeg (single AAC encode)...";
    try {
      const res = await post(`/h3_studio/lab/projects/${encodeURIComponent(proj.project_id)}/assemble`, {
        accepted_take_ids: proj.accepted_take_ids
      });
      if (status) status.textContent = `Assembled ${res.assembly?.clips_joined || proj.accepted_take_ids.length} clips into ${res.output_file} (${res.assembly?.duration?.toFixed(2)}s).`;
      loadLibrary(true);
    } catch (e) {
      if (status) status.textContent = "Assembly failed: " + e.message;
      info("Sequence assembly error: " + e.message, true);
    }
  };
}

$("prompt").addEventListener("input", updatePreviewLive);

initLabUI();
const missingInputs=restoreDraft();
selectMode(state.mode);renderEstimates();
updatePreviewLive();
if(missingInputs)info("Draft restored. Reattach your frame or reference files before generating.");
checkConnection();connectSocket();
pollQueue();
initProjectController();
