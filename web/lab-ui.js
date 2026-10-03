"use strict";
// Server-owned project inputs, native keyframes and review actions.
function allInputItems(){return [state.first,state.last,...state.refs,...state.guides].filter(Boolean);}
async function refreshLabCapabilities(){
  try{state.labCapabilities=await (await fetch(api("/h3_studio/lab/capabilities"))).json();}
  catch{state.labCapabilities={add_guide:false,continuation_ready:false,missing_reasons:["Lab readiness unavailable"]};}
  try{const response=await fetch(api("/object_info/MiniMaxH3AddGuide"));if(response.ok){const schemas=await response.json();const input=schemas.MiniMaxH3AddGuide?.input||{};const fields={...input.required,...input.optional};state.labCapabilities.guide_audio=!!fields.audio;state.labCapabilities.guide_video=!!fields.image&&!!state.nodesReady?.LoadVideo&&!!state.nodesReady?.GetVideoComponents;}}
  catch{}
  refreshLabControls();
}
function refreshLabControls(){
  const paused=state.labCapabilities?.inference_enabled===false;
  $("generate").disabled=state.busy||state.projectLoading||paused;
  let banner=$("v2StandbyBanner");
  if(!banner){banner=document.createElement("div");banner.id="v2StandbyBanner";banner.setAttribute("role","status");banner.style.cssText="padding:12px 20px;background:#352d12;color:#ffe69a;border-bottom:1px solid #695922;font-size:13px";document.querySelector(".top").after(banner);}
  banner.hidden=!paused;
  banner.textContent=paused?"V2 · Editing and project saving are available. Generation is paused to protect your active production render.":"";
  if($("addGuide"))$("addGuide").disabled=state.busy||!state.labCapabilities?.add_guide;
  if($("guideKind")){for(const option of $("guideKind").options)if(option.value!=="image")option.disabled=!state.labCapabilities?.["guide_"+option.value];if($("guideKind").selectedOptions[0]?.disabled)$("guideKind").value="image";}
  if($("guideStatus"))$("guideStatus").textContent=state.labCapabilities?.add_guide?"Times use the new content timeline when extending.":"Native AddGuide is not available on this server.";
  if($("extendVideo")){$("extendVideo").disabled=state.busy||!state.labCapabilities?.continuation_ready;$("extendVideo").title=(state.labCapabilities?.missing_reasons||[]).join("\n");}
}
function renderContinuation(){
  $("continuationPanel").classList.toggle("hidden",!state.continuation);
  if(state.continuation){const frames=Number($("duration").value)-state.continuation.context_length;$("continuationStatus").textContent=`Source ${state.continuation.source_take_id||state.continuation.source_token} (${state.continuation.type}) · ${state.continuation.context_length} context frames · +${frames} new frames / ${(frames/24).toFixed(2)}s`;}
  renderGuides();
}
function renderGuides(){
  const wrap=$("guideList");if(!wrap)return;wrap.replaceChildren();
  state.guides.forEach((guide,index)=>{
    const card=document.createElement("div");card.className="ref";
    const img=document.createElement(guide.kind==="audio"?"audio":guide.kind==="video"?"video":"img");img.src=guide.url;img.alt="Guide "+(index+1);if(guide.kind==="audio"||guide.kind==="video")img.controls=true;img.style.cssText="width:100%;max-height:100px;object-fit:contain";
    const label=document.createElement("label");label.textContent=state.continuation?"TIME IN NEW CONTENT (seconds)":"TIME (seconds)";
    const time=document.createElement("input");time.type="number";time.min=0;time.step=1/24;time.max=(Number($("duration").value)-(state.continuation?.context_length||0)-1)/24;time.value=guide.frame_idx/24;time.disabled=state.busy;
    time.onchange=()=>{const frame=Math.round(Number(time.value)*24);if(!Number.isFinite(frame)||frame<0||frame>Number(time.max)*24){info("Keyframe time is outside the generated content.",true);return;}guide.frame_idx=frame;time.value=frame/24;updatePreviewLive();};
    const remove=document.createElement("button");remove.className="button alt smallbtn";remove.textContent="Remove";remove.disabled=state.busy;remove.onclick=()=>{state.guides.splice(index,1);URL.revokeObjectURL(guide.url);renderGuides();updatePreviewLive();};
    card.append(img,label,time,remove);
    if(!guide.kind||guide.kind==="image"){const fit=document.createElement("select");for(const value of ["crop","contain"]){const o=document.createElement("option");o.value=value;o.textContent=value==="crop"?"Crop to canvas":"Contain on canvas";fit.append(o);}fit.value=guide.imageFit||"crop";fit.disabled=state.busy;fit.onchange=()=>guide.imageFit=fit.value;card.append(fit);}
    if(guide.kind==="video"){const label=document.createElement("label"),check=document.createElement("input");check.type="checkbox";check.checked=!!guide.use_audio;check.disabled=state.busy||!state.labCapabilities?.guide_audio;check.onchange=()=>guide.use_audio=check.checked;label.append(check,document.createTextNode(" Guide the same clip's audio too"));card.append(label);}
    if(guide.kind&&guide.kind!=="image"){
      const label=document.createElement("label"),check=document.createElement("input");check.type="checkbox";check.checked=!!guide.trimEnabled;check.disabled=state.busy;check.onchange=()=>{guide.trimEnabled=check.checked;renderGuides();};label.append(check,document.createTextNode(" Trim source range"));card.append(label);
      if(guide.trimEnabled)for(const [key,title,min,max] of [["trimStart","Source start (s)",0,3600],["trimDuration","Source use (s)",2,15]]){const l=document.createElement("label"),input=document.createElement("input");l.textContent=title;input.type="number";input.min=min;input.max=max;input.step=".01";input.value=guide[key]??(key==="trimStart"?0:2);input.disabled=state.busy;input.onchange=()=>guide[key]=Number(input.value);card.append(l,input);}
    }
    if(guide.kind&&guide.kind!=="image"){const tip=document.createElement("div");tip.className="tip";tip.textContent="Native video guides crop to 5 + 17k frames. Audio is timed conditioning; exact PCM reuse or lip sync is not guaranteed.";card.append(tip);}
    wrap.append(card);
  });
}
async function fitFrameImage(file,fitOverride=null){
  const bitmap=await createImageBitmap(file);const canvas=document.createElement("canvas");canvas.width=state.width;canvas.height=state.height;
  const ctx=canvas.getContext("2d");ctx.fillStyle="#000";ctx.fillRect(0,0,canvas.width,canvas.height);
  const fit=fitOverride||$("frameFit").value;const scale=(fit==="contain"?Math.min:Math.max)(canvas.width/bitmap.width,canvas.height/bitmap.height);
  const w=bitmap.width*scale,h=bitmap.height*scale;ctx.drawImage(bitmap,(canvas.width-w)/2,(canvas.height-h)/2,w,h);bitmap.close();
  const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));if(!blob)throw Error("Could not fit frame image");
  return new File([blob],file.name.replace(/\.[^.]+$/,"_fit.png"),{type:"image/png"});
}
async function renderFrameItem(which){
  const item=state[which],box=$(which==="first"?"firstBox":"lastBox");box.replaceChildren();
  if(!item){box.textContent=which==="first"?"+ Add start frame":"+ Add end frame";$(which==="first"?"clearFirst":"clearLast").classList.add("hidden");return;}
  const fitted=await fitFrameImage(item.file);if(state[which]!==item)return;
  const img=document.createElement("img");img.src=URL.createObjectURL(fitted);img.alt=which+" frame fit preview";img.onload=()=>URL.revokeObjectURL(img.src);box.append(img);$(which==="first"?"clearFirst":"clearLast").classList.remove("hidden");
}
async function persistProjectDraft(){
  if(!projectCtrl?.currentProject)throw Error("Open or create a project before saving inputs.");
  for(const item of allInputItems())if(!item.assetId){const rec=await projectCtrl.uploadAsset(item.file);item.assetId=rec.asset_id;}
  const refreshed=await projectCtrl.fetchJson(`/h3_studio/lab/projects/${encodeURIComponent(projectCtrl.currentProject.project_id)}`);projectCtrl.currentProject=refreshed;updateProjectUI(refreshed);
  const descriptor=item=>item?Object.fromEntries(Object.entries(item).filter(([key])=>!["file","url","previewUrl","meta","uploadProgress","uploadReady","originalPreviewUrl"].includes(key))):null;
  return {version:1,mode:state.mode,prompts:{...state.prompts,[state.mode]:$("prompt").value},canvas:{width:state.width,height:state.height},
    promptMode:$("promptMode").value,frameFit:$("frameFit").value,first:descriptor(state.first),last:descriptor(state.last),
    refs:state.refs.map(descriptor),guides:state.guides.map(descriptor),continuation:state.continuation,
    settings:{duration:$("duration").value,durationSeconds:$("durationSeconds").value,steps:$("steps").value,seed:$("seed").value,refSize:$("refSize").value,renderMethod:method()},loras:state.loras.filter(x=>x.enabled)};
}
async function hydrateProjectDraft(draft){
  if(!draft)return;
  const epoch=++state.hydrationEpoch;const missing=[];
  async function item(desc){if(!desc)return null;try{if(!desc.assetId)throw Error("Missing asset identifier");const rec=await projectCtrl.getAsset(desc.assetId);const blob=await projectCtrl.mediaBlob(desc.assetId);return {...desc,file:new File([blob],rec.original_name||"asset.png",{type:blob.type}),url:URL.createObjectURL(blob),previewUrl:URL.createObjectURL(blob)};}catch{missing.push(desc.alias||desc.assetId||"frame");return null;}}
  const first=await item(draft.first),last=await item(draft.last),refs=await Promise.all((draft.refs||[]).map(item)),guides=await Promise.all((draft.guides||[]).map(item));
  if(epoch!==state.hydrationEpoch)return;
  state.first=first;state.last=last;state.refs=refs.filter(Boolean);state.guides=guides.filter(Boolean);state.continuation=draft.continuation||null;
  if(draft.canvas){state.width=draft.canvas.width;state.height=draft.canvas.height;}
  state.prompts={text:"",frames:"",refs:"",...draft.prompts};state.promptMode=draft.promptMode||"guided";$("promptMode").value=state.promptMode;$("frameFit").value=draft.frameFit||"crop";
  for(const [key,value] of Object.entries(draft.settings||{}))if($(key))$(key).value=value;
  state.desiredLoras=draft.loras||[];
  state.aspectFormat=Object.keys(aspectPresets).find(k=>aspectPresets[k].some(([w,h])=>w===state.width&&h===state.height))||"16:9";
  state.mode=draft.mode||"text";$("prompt").value=state.prompts[state.mode]||"";selectMode(state.mode,true);state.loras=state.loras.map(x=>({...x,enabled:!!draft.loras?.some(l=>l.name===x.name),strength:draft.loras?.find(l=>l.name===x.name)?.strength??x.strength}));
  await Promise.all([renderFrameItem("first"),renderFrameItem("last")]);renderRefs();renderGuides();renderContinuation();updatePreviewLive();
  state.restoreMissing=missing;
  if(missing.length)info("Project has unavailable inputs: "+missing.join(", ")+". Reattach them and Save before rendering.",true);
}
async function refreshProjectList(){
  if(!projectCtrl)return;const response=await projectCtrl.listProjects();const select=$("projectSelect");select.replaceChildren();
  const placeholder=document.createElement("option");placeholder.value="";placeholder.textContent="Choose project…";select.append(placeholder);
  for(const project of response.projects||[]){const option=document.createElement("option");option.value=project.project_id;option.textContent=project.name;select.append(option);}select.value=state.activeProjectId||"";
}
function initLabUI(){
  state.hydrationEpoch=0;
  $("refreshContextStorage").onclick=async()=>{try{const data=await projectCtrl.fetchJson("/h3_studio/lab/contexts/usage");$("contextStorage").textContent=`${data.count} contexts · ${data.total_mb} MB. Accepted/active contexts are protected.`;}catch(e){info(e.message,true);}};
  $("refreshProjects").onclick=()=>refreshProjectList().catch(e=>info(e.message,true));
  $("projectSelect").onchange=async()=>{
    if(state.busy||state.projectLoading||!$("projectSelect").value)return;
    state.projectLoading=true;const controller=projectCtrl;const id=$("projectSelect").value;
    const controls=["projectSelect","btnNewProject","btnSaveProject","btnExportProject","importProject"];controls.forEach(k=>$(k).disabled=true);
    try{const p=await controller.loadProject(id);if(controller!==projectCtrl)return;state.currentClipId=null;await hydrateProjectDraft(p.draft||{mode:"text",refs:[],guides:[]});}
    catch(e){info(e.message,true);}finally{state.projectLoading=false;controls.forEach(k=>$(k).disabled=state.busy);}
  };
  $("importProject").onclick=()=>$("projectBundleFile").click();$("projectBundleFile").onchange=async()=>{try{await withProjectLoading(async()=>{const p=await projectCtrl.importBundle($("projectBundleFile").files[0]);await hydrateProjectDraft(p.draft);await refreshProjectList();});}catch(e){info(e.message,true);}finally{$("projectBundleFile").value="";}};
  $("promptMode").onchange=()=>{state.promptMode=$("promptMode").value;updatePreviewLive();};
  $("frameFit").onchange=()=>Promise.all([renderFrameItem("first"),renderFrameItem("last")]);
  $("addGuide").onclick=()=>{const kind=$("guideKind").value;$("guideFile").accept=kind==="image"?"image/png,image/jpeg,image/webp":kind==="audio"?".wav,.mp3,.m4a,.flac":".mp4,.mov,.webm";$("guideFile").click();};$("guideFile").onchange=()=>{const file=$("guideFile").files[0];if(file&&!state.busy){state.guides.push({asset_id:crypto.randomUUID(),kind:$("guideKind").value,file,url:URL.createObjectURL(file),frame_idx:0});renderGuides();}$("guideFile").value="";};
  $("clearContinuation").onclick=()=>{state.continuation=null;renderContinuation();updatePreviewLive();};
  $("durationSeconds").addEventListener("change",renderContinuation);
  for(const which of ["first","last"])$(which+"File").addEventListener("change",()=>setTimeout(()=>renderFrameItem(which),0));
  $("purgeContext").onclick=async()=>{if(state.busy||!state.current?.takeId)return;try{await post(`/h3_studio/lab/contexts/${encodeURIComponent(state.current.takeId)}/purge`,{});info("Extend data removed; the video is retained.");$("refreshContextStorage").click();}catch(e){info("Cannot remove protected extend data: "+e.message,true);}};
  $("acceptTake").onclick=async()=>{if(!state.current||state.busy)return;try{const proj=projectCtrl.currentProject;if(!proj?.takes?.some(t=>t.take_id===state.current.takeId))throw Error("This result has no take in the current project.");const take=proj.takes.find(t=>t.take_id===state.current.takeId),accepted=proj.accepted_take_ids||[];
      if(take.parent_take_id&&!accepted.includes(take.parent_take_id))throw Error("Accept the source take before accepting its continuation.");
      const replaced=proj.takes.find(t=>t.clip_id===take.clip_id&&t.take_id!==take.take_id&&accepted.includes(t.take_id));
      const invalid=new Set(replaced?[replaced.take_id]:[]);let changed=true;while(changed){changed=false;for(const t of proj.takes)if(t.parent_take_id&&invalid.has(t.parent_take_id)&&!invalid.has(t.take_id)){invalid.add(t.take_id);changed=true;}}
      const position=replaced?accepted.indexOf(replaced.take_id):accepted.length;const next=accepted.filter(id=>!invalid.has(id)&&id!==take.take_id);next.splice(Math.min(position,next.length),0,take.take_id);
      await projectCtrl.saveCurrentProject({accepted_take_ids:next,takes:proj.takes.map(t=>invalid.has(t.take_id)?{...t,invalidated_by:take.take_id}:t)});info("Take accepted into the sequence.");}catch(e){info(e.message,true);}};
  $("useResult").onclick=async()=>{const entry=state.current;if(!entry||state.busy)return;try{const role=$("resultRole").value;if(role==="video"){const response=await fetch(videoURL(entry.file));if(!response.ok)throw Error("Result unavailable");const blob=await response.blob();selectMode("refs");state.refs.push({file:new File([blob],entry.file.filename,{type:blob.type}),kind:"video",alias:aliasName(entry.file.filename),role:"custom",previewUrl:URL.createObjectURL(blob),useAudio:false,trimEnabled:true,trimStart:0,trimDuration:15});renderRefs();}else{const index=Number($("resultFrameIndex").value);const result=await post("/h3_studio/lab/media/frame",{filename:entry.file.filename,output_file:resultOutputPath(entry),frame_index:index,project_id:state.activeProjectId});await applyQwenHandoff(result.asset,role);} }catch(e){info(e.message,true);}};
  $("exportMedia").onclick=async()=>{if(!state.current||state.busy)return;try{const result=await post("/h3_studio/lab/media/export",{filename:state.current.file.filename,output_file:resultOutputPath(state.current),format:$("mediaFormat").value});const a=document.createElement("a");a.href=api(result.download_url);a.download=result.filename;a.click();}catch(e){info(e.message,true);}};
  $("restoreTake").onclick=async()=>{if(state.busy)return;const t=state.currentProject?.takes?.find(t=>t.take_id===state.current?.takeId);if(!t?.draft){info("Original inputs are not stored for this older result.",true);return;}await withProjectLoading(async()=>{state.currentClipId=t.clip_id;await hydrateProjectDraft(t.draft);});info("Original take settings and inputs restored. Change the seed for a retry.");};
  refreshLabControls();renderGuides();
}

function resultOutputPath(entry){return [entry.file.subfolder||"video",entry.file.filename].join("/");}
async function updateRefFitPreview(ref){
  if(!ref.originalPreviewUrl)ref.originalPreviewUrl=ref.previewUrl;
  if((ref.imageFit||"preserve")==="preserve"){if(ref.previewUrl!==ref.originalPreviewUrl)URL.revokeObjectURL(ref.previewUrl);ref.previewUrl=ref.originalPreviewUrl;renderRefs();return;}
  const fitted=await fitFrameImage(ref.file,ref.imageFit);if(!state.refs.includes(ref))return;
  if(ref.previewUrl!==ref.originalPreviewUrl)URL.revokeObjectURL(ref.previewUrl);ref.previewUrl=URL.createObjectURL(fitted);renderRefs();
}

function appendProjectResults(project){
  for(const take of project?.takes||[]){
    if(!take.output_file||take.status!=="completed")continue;
    const parts=take.output_file.split("/"),filename=parts.pop(),subfolder=parts.join("/");
    if(!state.results.some(e=>e.file.filename===filename&&e.file.subfolder===subfolder))state.results.push({takeId:take.take_id,file:{filename,subfolder,type:"output"},meta:{settings:take.effective_settings,duration:take.unique_frames/24,completedAt:take.created_at*1000},kept:false});
  }
}

async function withProjectLoading(action){
  if(state.busy||state.projectLoading)throw Error("Wait for the current operation to finish.");
  state.projectLoading=true;const controls=["generate","projectSelect","btnNewProject","btnSaveProject","btnExportProject","importProject","restoreTake","useResult","extendVideo","server","saveServer"].map(id=>$(id)).filter(Boolean);const previous=controls.map(x=>x.disabled);controls.forEach(x=>x.disabled=true);
  try{return await action();}finally{state.projectLoading=false;controls.forEach((x,i)=>x.disabled=state.busy||previous[i]);refreshLabControls();}
}
