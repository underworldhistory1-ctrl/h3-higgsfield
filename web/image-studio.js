"use strict";
const $=id=>document.getElementById(id);
const state={
  mode:"create",refs:[],profiles:{},nodes:{},ready:false,busy:false,
  clientId:crypto.randomUUID(),socket:null,activePromptId:localStorage.getItem("qwen.activePromptId")||null,
  started:0,lastStepAt:0,lastStep:0,stepTotal:0,stepTimes:[],uploaded:[],current:null,results:[],visible:8,timer:null,
  settings:null,abort:null,completing:false
};
const profileFiles={
  int8:{unet:"qwen_image_2.1_int8_convrot.safetensors",clip:"qwen3vl_8b_int8_convrot.safetensors"},
  bf16:{unet:"qwen_image_2.1_bf16.safetensors",clip:"qwen3vl_8b_bf16.safetensors"}
};
const vaeName="qwen_image_2.1_vae_bf16.safetensors";
const sizeMap={
  "1":{"1:1":[1024,1024],"16:9":[1344,768],"9:16":[768,1344],"4:3":[1152,896],"3:4":[896,1152],"3:2":[1216,832],"2:3":[832,1216]},
  "1.6":{"1:1":[1280,1280],"16:9":[1696,960],"9:16":[960,1696],"4:3":[1472,1088],"3:4":[1088,1472],"3:2":[1536,1024],"2:3":[1024,1536]},
  "4":{"1:1":[2048,2048],"16:9":[2752,1536],"9:16":[1536,2752],"4:3":[2400,1792],"3:4":[1792,2400],"3:2":[2528,1696],"2:3":[1696,2528]}
};
function api(path){return path;}
function wsUrl(){const url=new URL(location.href);url.protocol=url.protocol==="https:"?"wss:":"ws:";url.pathname="/ws";url.search="?clientId="+encodeURIComponent(state.clientId);return url.toString();}
function fmt(seconds){if(!Number.isFinite(seconds)||seconds<0)return "—";const total=Math.round(seconds),m=Math.floor(total/60),s=total%60;return m?`${m}m ${String(s).padStart(2,"0")}s`:`${s}s`;}
function fmtBytes(bytes){if(bytes<1024)return bytes+" B";if(bytes<1048576)return (bytes/1024).toFixed(1)+" KB";return (bytes/1048576).toFixed(1)+" MB";}
function showError(message=""){$("error").textContent=message;$("error").classList.toggle("hidden",!message);}
function setConnection(kind,text){$("connection").className="connection "+kind;$("connectionText").textContent=text;}
function showStage(title,copy){$("stage").innerHTML=`<div class="stage-empty"><b></b><span></span></div>`;$("stage").querySelector("b").textContent=title;$("stage").querySelector("span").textContent=copy;}
function setProgress(label,pct,remaining=null,indeterminate=false){
  $("status").textContent=label;$("percent").textContent=Number.isFinite(pct)?Math.round(pct)+"%":"—";
  $("progressFill").style.width=Math.max(0,Math.min(100,pct||0))+"%";$("progressBar").classList.toggle("busy",indeterminate);
  $("remaining").textContent="Time remaining: "+(remaining===null?"—":fmt(remaining));
}
function setBusy(value){state.busy=value;$("generate").classList.toggle("hidden",value);$("cancel").classList.toggle("hidden",!value);document.querySelectorAll(".left input,.left select,.left textarea,#addReferences").forEach(el=>{if(el.id!=="aspect"&&el.id!=="resolution")el.disabled=value;});updateSettings();}
function dimensions(){return sizeMap[$("resolution").value][$("aspect").value];}
function outputDimensions(){const first=state.refs[0];if(!first||$("customSize").checked||!first.width||!first.height)return dimensions();const scale=$("fitReferences").checked?Math.min(1,2048/Math.max(first.width,first.height)):1;return [Math.max(32,Math.round(first.width*scale/32)*32),Math.max(32,Math.round(first.height*scale/32)*32)];}
function estimateSeconds(){
  const [w,h]=outputDimensions(),steps=Math.max(1,Number($("steps").value)||40),profile=$("profile").value||"int8";
  const mode=state.refs.length?"edit":"create",key=`qwen.eta.${mode}.${profile}.${w}x${h}.${steps}`;let stored=[];try{stored=JSON.parse(localStorage.getItem(key)||"[]");}catch{}
  if(stored.length)return stored.reduce((a,b)=>a+b,0)/stored.length;
  const base=profile==="bf16"?1.25:1;return Math.max(20,(w*h/1048576)*steps*.8*base+(mode==="edit"?18:8));
}
function updateSettings(){
  const hasRefs=state.refs.length>0,sourceCanvas=hasRefs&&!$("customSize").checked;
  $("referenceOptions").hidden=!hasRefs;$("referenceCount").textContent=hasRefs?`${state.refs.length}/10 attached`:"Optional";
  $("aspect").disabled=state.busy||sourceCanvas;$("resolution").disabled=state.busy||sourceCanvas;$("transparentLabel").hidden=hasRefs;
  const [w,h]=outputDimensions();$("dimensions").textContent=sourceCanvas?(state.refs[0].width?`Output canvas from first reference · approximately ${w} × ${h}`:"Output canvas follows the first reference · reading dimensions…"):`${w} × ${h} · ${(w*h/1048576).toFixed(1)} MP target`;
  $("estimate").textContent=`Initial estimate: ${fmt(estimateSeconds())}. It will calibrate from completed images on this server.`;
}
function saveDraft(){
  localStorage.setItem("qwen.imageDraft",JSON.stringify({prompt:$("prompt").value,profile:$("profile").value,aspect:$("aspect").value,resolution:$("resolution").value,steps:$("steps").value,seed:$("seed").value,transparent:$("transparent").checked,referenceResolution:$("referenceResolution").value,customSize:$("customSize").checked,fitReferences:$("fitReferences").checked}));
}
function restoreDraft(){
  let draft={};try{draft=JSON.parse(localStorage.getItem("qwen.imageDraft")||"{}");}catch{}
  for(const id of ["prompt","aspect","resolution","steps","seed","referenceResolution"]){if(draft[id]!==undefined&&$(id))$(id).value=String(draft[id]);}
  $("transparent").checked=!!draft.transparent;$("customSize").checked=!!draft.customSize;$("fitReferences").checked=draft.fitReferences!==false;
}
function validName(name){return /^[\p{L}\p{N}_-]+$/u.test(name);}
function suggestedName(file){const base=file.name.replace(/\.[^.]+$/,""),clean=base.replace(/[^\p{L}\p{N}_-]+/gu,"_").replace(/^_+|_+$/g,"").slice(0,30)||"image";let name=clean,index=2;while(state.refs.some(ref=>ref.name.toLowerCase()===name.toLowerCase()))name=`${clean}_${index++}`;return name;}
function resolveMentions(prompt){const names=new Map(state.refs.map((ref,index)=>[ref.name.toLowerCase(),index+1]));return prompt.replace(/(^|[^\p{L}\p{N}_])@([\p{L}\p{N}_-]+)/gu,(full,prefix,name)=>{const index=names.get(name.toLowerCase());if(!index)throw Error(`@${name} has no attached reference. Add it or remove the mention.`);return `${prefix}<image${index}>`;});}
function insertMention(name){const field=$("prompt"),at=field.selectionStart,before=field.value.slice(0,at),match=before.match(/@([\p{L}\p{N}_-]*)$/u),start=match?at-match[0].length:at,token=`@${name} `;field.setRangeText(token,start,at,"end");field.focus();saveDraft();renderMentions();}
function renderMentions(){
  const field=$("prompt"),list=$("mentionList"),menu=$("mentionMenu");list.replaceChildren();menu.replaceChildren();
  state.refs.forEach((ref,index)=>{const chip=document.createElement("button");chip.type="button";chip.textContent=`@${ref.name} · image${index+1}`;chip.title=`Insert @${ref.name} into prompt`;chip.onclick=()=>insertMention(ref.name);list.append(chip);});
  const before=field.value.slice(0,field.selectionStart),match=before.match(/@([\p{L}\p{N}_-]*)$/u),matches=match?state.refs.filter(ref=>ref.name.toLowerCase().startsWith(match[1].toLowerCase())):[];
  menu.classList.toggle("is-hidden",!matches.length);matches.forEach(ref=>{const index=state.refs.indexOf(ref),button=document.createElement("button");button.type="button";button.role="option";button.textContent=`@${ref.name} · image${index+1}`;button.onmousedown=event=>event.preventDefault();button.onclick=()=>{insertMention(ref.name);menu.classList.add("is-hidden");};menu.append(button);});
}

function moveReference(index,direction){
  if(state.busy)return;
  const next=index+direction;
  if(next<0||next>=state.refs.length)return;
  [state.refs[index],state.refs[next]]=[state.refs[next],state.refs[index]];
  renderRefs();saveDraft();showError("");
}

function renderRefs(){
  $("referenceList").replaceChildren();state.refs.forEach((ref,index)=>{
    const row=document.createElement("div");row.className="ref";const image=document.createElement("img");image.src=ref.url;image.alt=`Preview of image ${index+1}`;
    const text=document.createElement("div");const title=document.createElement("strong");title.textContent=`image${index+1} · ${ref.file.name}`;const meta=document.createElement("small");meta.textContent=fmtBytes(ref.file.size)+(index===0?" · sets canvas size":" · reference");const nameLabel=document.createElement("label");nameLabel.textContent="MENTION NAME";const nameInput=document.createElement("input");nameInput.className="ref-name";nameInput.value=ref.name;nameInput.maxLength=32;nameInput.setAttribute("aria-label",`Mention name for image ${index+1}`);nameInput.onchange=()=>{const next=nameInput.value.trim();if(!validName(next)||state.refs.some(other=>other!==ref&&other.name.toLowerCase()===next.toLowerCase())){showError("Use a unique reference name with letters, numbers, _ or -.");nameInput.value=ref.name;return;}const old=ref.name;ref.name=next;if(old!==next){const escaped=old.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");$("prompt").value=$("prompt").value.replace(new RegExp(`(^|[^\\p{L}\\p{N}_])@${escaped}(?![\\p{L}\\p{N}_-])`,"gu"),(full,prefix)=>`${prefix}@${next}`);}showError("");renderMentions();saveDraft();};nameInput.oninput=()=>{if(validName(nameInput.value.trim())&&!state.refs.some(other=>other!==ref&&other.name.toLowerCase()===nameInput.value.trim().toLowerCase()))nameInput.onchange();};text.append(title,meta,nameLabel,nameInput);
    const remove=document.createElement("button");remove.className="button alt small";remove.type="button";remove.textContent="Remove";remove.onclick=()=>{if(state.busy)return;URL.revokeObjectURL(ref.url);state.refs.splice(index,1);renderRefs();saveDraft();};
    const aliasRow=document.createElement("div");aliasRow.className="ref-alias-row";aliasRow.append(nameLabel,nameInput);
    const insert=document.createElement("button");insert.type="button";insert.className="button alt small ref-insert";insert.textContent=`Insert @${ref.name} into prompt`;insert.title=insert.textContent;insert.disabled=state.busy;insert.onclick=()=>{insertMention(ref.name);$("prompt").scrollIntoView({behavior:"smooth",block:"center"});};
    nameInput.addEventListener("input",()=>{insert.textContent=`Insert @${ref.name} into prompt`;insert.title=insert.textContent;});
    const order=document.createElement("div");order.className="ref-order";
    const orderLabel=document.createElement("span");orderLabel.textContent=index===0?"Image 1 · output canvas / base":"Image "+(index+1)+" · additional reference";
    order.append(orderLabel);
    for(const [label,direction] of [["Move up",-1],["Move down",1]]){
      const button=document.createElement("button");button.type="button";button.className="button alt small";button.textContent=label;
      button.setAttribute("aria-label",`${label} ${ref.name}`);button.disabled=state.busy||index+direction<0||index+direction>=state.refs.length;
      button.onclick=()=>moveReference(index,direction);order.append(button);
    }
    row.append(image,text,remove,aliasRow,order,insert);$("referenceList").append(row);
  });renderMentions();updateSettings();
}
$("addReferences").onclick=()=>{if(!state.busy)$("referenceFiles").click();};
$("referenceFiles").onchange=()=>{
  const incoming=[...$("referenceFiles").files];for(const file of incoming){
    if(state.refs.length>=10)break;
    if(!/\.(png|jpe?g|webp)$/i.test(file.name)||!file.size||file.size>25*1024*1024){showError("Use non-empty PNG, JPG or WebP images under 25 MB.");continue;}
    const ref={file,url:URL.createObjectURL(file),name:suggestedName(file),width:0,height:0};state.refs.push(ref);createImageBitmap(file).then(bitmap=>{ref.width=bitmap.width;ref.height=bitmap.height;bitmap.close();updateSettings();}).catch(()=>{});
  }
  $("referenceFiles").value="";renderRefs();saveDraft();
};

async function checkConnection(){
  try{
    const response=await fetch(api("/h3_studio/image_readiness"),{cache:"no-store"});if(!response.ok)throw Error("Image extension unavailable");
    const data=await response.json();state.profiles=data.profiles||{};state.nodes=data.nodes||{};state.ready=!!data.ready;
    for(const option of $("profile").options){option.disabled=!state.profiles[option.value]?.ready;}
    if(!state.profiles[$("profile").value]?.ready){const first=[...$("profile").options].find(option=>!option.disabled);if(first)$("profile").value=first.value;}
    const readyProfiles=Object.entries(state.profiles).filter(([,item])=>item.ready).map(([key])=>key.toUpperCase());
    const missingNodes=Object.entries(state.nodes).filter(([,ready])=>!ready).map(([name])=>name);
    if(!state.ready){setConnection("down",missingNodes.length?"Missing nodes: "+missingNodes.join(", "):"Qwen weights are not installed");$("profileNote").innerHTML="<strong>Qwen Image is unavailable</strong><br>Install one complete model profile before generating.";return false;}
    setConnection("ready","Connected · "+readyProfiles.join(" + "));$("profileNote").innerHTML=$("profile").value==="bf16"?"<strong>Maximum precision</strong><br>Full BF16 weights. More model loading and memory use.":"<strong>Fast / efficient</strong><br>INT8 ConvRot reduces storage and VRAM with a possible small quality difference.";openSocket();return true;
  }catch(error){state.ready=false;setConnection("down",error.message);return false;}finally{setBusy(state.busy);updateSettings();}
}
$("retry").onclick=checkConnection;$("profile").onchange=()=>{checkConnection();saveDraft();};
function openSocket(){
  if(state.socket&&state.socket.readyState<=1)return;try{state.socket=new WebSocket(wsUrl());}catch{return;}
  state.socket.onmessage=event=>{let msg;try{msg=JSON.parse(event.data);}catch{return;}handleSocket(msg);};state.socket.onclose=()=>{state.socket=null;if(state.busy)setTimeout(openSocket,1500);};
}
function handleSocket(message){
  const data=message.data||{};if(data.prompt_id&&state.activePromptId&&data.prompt_id!==state.activePromptId)return;
  if(message.type==="progress"&&state.busy){
    const value=Number(data.value||0),max=Math.max(1,Number(data.max||1)),now=Date.now();if(state.lastStepAt&&value>state.lastStep)state.stepTimes.push((now-state.lastStepAt)/1000/(value-state.lastStep));if(value>state.lastStep)state.lastStepAt=now;state.lastStep=value;state.stepTotal=max;saveActiveJob();
    const pct=12+Math.round(78*value/max),avg=state.stepTimes.length?state.stepTimes.slice(-8).reduce((a,b)=>a+b,0)/Math.min(8,state.stepTimes.length):0;setProgress(`Sampling · ${value}/${max}`,pct,avg?avg*(max-value):null);
  }else if(message.type==="executing"&&state.busy){
    if(data.node===null){pollHistory(true);}else if(data.node==="save")setProgress("Saving PNG and settings",96,2);else if(data.node==="decode")setProgress("Decoding image",91,5);else if(!state.lastStepAt)setProgress("Loading Qwen Image",8,estimateSeconds(),true);
  }else if(message.type==="execution_error"&&state.busy){failGeneration(data.exception_message||"ComfyUI graph failed");}
}
function saveActiveJob(){if(!state.activePromptId)return;localStorage.setItem("qwen.activeJob",JSON.stringify({promptId:state.activePromptId,settings:state.settings,started:state.started,uploaded:state.uploaded,lastStep:state.lastStep,stepTotal:state.stepTotal,stepTimes:state.stepTimes.slice(-8)}));}
async function pollImageProgress(){const id=state.activePromptId;if(!id)return;try{const response=await fetch(api("/h3_studio/job_progress?prompt_id="+encodeURIComponent(id)),{cache:"no-store"});if(!response.ok)return;const data=await response.json();if(id!==state.activePromptId||!Number.isFinite(data.step)||!Number.isFinite(data.total)||data.total<=0||data.step<state.lastStep)return;state.lastStep=data.step;state.stepTotal=data.total;state.lastStepAt=Date.now();const avg=state.stepTimes.length?state.stepTimes.reduce((a,b)=>a+b,0)/state.stepTimes.length:0;setProgress(`Sampling · ${data.step}/${data.total}`,12+Math.round(78*data.step/data.total),avg?avg*(data.total-data.step):null);saveActiveJob();}catch{}}

function buildGraph(settings,uploads,token){
  const files=profileFiles[settings.profile];if(!files)throw Error("Selected model profile is invalid.");
  const prompt=settings.transparent&&settings.mode==="create"?`This is an RGBA format image with transparency. ${settings.prompt.replace(/\.+$/,'')}. The image has an alpha channel and a transparent background.`:settings.prompt;
  const metadata={kind:"image",...settings,prompt,references:uploads.map((name,index)=>({name:settings.referenceNames?.[index]||`image${index+1}`,file:name.split(/[\\/]/).pop()})),submitted_at:Date.now()/1000,token};
  const graph={
    unet:{class_type:"UNETLoader",inputs:{unet_name:files.unet,weight_dtype:"default"}},
    clip:{class_type:"CLIPLoader",inputs:{clip_name:files.clip,type:"qwen_image",device:"default"}},
    vae:{class_type:"VAELoader",inputs:{vae_name:vaeName}},
    enc:{class_type:"TextEncodeQwenImage21",inputs:{clip:["clip",0],prompt,negative_prompt:"",resolution:settings.referenceResolution}},
    decode:{class_type:"VAEDecode",inputs:{samples:["sampler",0],vae:["vae",0]}},
    save:{class_type:"QwenStudioSaveImage",inputs:{images:["decode",0],token,metadata_json:JSON.stringify(metadata)}}
  };
  let model=["unet",0],latent;
  if(settings.mode==="create"){
    graph.latent={class_type:"EmptyLatentImage",inputs:{width:settings.width,height:settings.height,batch_size:1}};latent=["latent",0];
  }else{
    graph.enc.inputs.vae=["vae",0];uploads.forEach((name,index)=>{const n=index+1,id="load"+n;graph[id]={class_type:"LoadImage",inputs:{image:name}};let link=[id,0];if(n===1&&settings.customSize){graph.scale_primary={class_type:"ImageScale",inputs:{image:link,upscale_method:"lanczos",width:settings.width,height:settings.height,crop:"center"}};link=["scale_primary",0];}graph.enc.inputs["images.image_"+n]=link;});
    graph.cache={class_type:"QwenImage21Cache",inputs:{model:["unet",0],device:"auto",dtype:"default"}};model=["cache",0];latent=["enc",2];
  }
  graph.sampler={class_type:"KSampler",inputs:{model,seed:settings.seed,steps:settings.steps,cfg:1,sampler_name:"euler",scheduler:"simple",positive:["enc",0],negative:["enc",1],latent_image:latent,denoise:1}};
  return graph;
}
async function uploadOne(file,index){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();state.abort?.signal.addEventListener("abort",()=>xhr.abort(),{once:true});xhr.open("POST",api("/h3_studio/upload_ref?kind=image"+($("fitReferences").checked?"&resize=1":"")));
    xhr.upload.onprogress=event=>{if(event.lengthComputable)setProgress(`Uploading image ${index+1}/${state.refs.length}`,2+Math.round(6*event.loaded/event.total),null);};
    xhr.onload=()=>{let data={};try{data=JSON.parse(xhr.responseText);}catch{}if(xhr.status>=200&&xhr.status<300&&data.name)resolve(data.name);else reject(Error(data.error||"Upload failed"));};xhr.onerror=()=>reject(Error("Upload failed"));xhr.onabort=()=>reject(new DOMException("Cancelled","AbortError"));const form=new FormData();form.append("file",file,file.name);xhr.send(form);
  });
}
async function cleanupUploads(){const names=state.uploaded.splice(0);await Promise.allSettled(names.map(filename=>fetch(api("/h3_studio/discard"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename})})));}
function captureSettings(){
  const [width,height]=outputDimensions(),sourcePrompt=$("prompt").value.trim(),steps=Number($("steps").value),seed=Number($("seed").value),profile=$("profile").value,mode=state.refs.length?"edit":"create";
  if(!sourcePrompt)throw Error("Write a prompt.");if(!state.profiles[profile]?.ready)throw Error("The selected precision profile is not installed.");if(!Number.isInteger(steps)||steps<1||steps>100)throw Error("Steps must be between 1 and 100.");if(!Number.isSafeInteger(seed)||seed<0)throw Error("Seed must be a positive safe integer.");if(state.refs.length>10)throw Error("Qwen Image supports up to 10 images.");
  if(mode==="edit"&&!$("customSize").checked){if(!state.refs[0].width||!state.refs[0].height)throw Error("Still reading the first reference's dimensions. Try again in a moment.");if(width<256||height<256)throw Error("First reference is too small for the output canvas. Select 'Fit first image to selected output size'.");if(width>2752||height>2752)throw Error("First reference exceeds the supported output canvas. Select 'Fit first image to selected output size'.");}
  const prompt=resolveMentions(sourcePrompt);
  return {mode,profile,prompt,sourcePrompt,referenceNames:state.refs.map(ref=>ref.name),width,height,steps,seed,transparent:mode==="create"&&$("transparent").checked,referenceResolution:Number($("referenceResolution").value),customSize:mode==="edit"&&$("customSize").checked};
}
async function generate(){
  showError("");let settings;try{settings=captureSettings();if(!await checkConnection())throw Error("Qwen Image is not ready on this server.");}catch(error){showError(error.message);return;}
  state.settings=settings;state.abort=new AbortController();state.uploaded=[];state.started=Date.now();state.lastStepAt=0;state.lastStep=0;state.stepTotal=0;state.stepTimes=[];setBusy(true);setProgress("Preparing inputs",2,estimateSeconds(),true);showStage("Generating your image","The first run can include model loading.");
  try{
    if(settings.mode==="edit")for(let i=0;i<state.refs.length;i++){const name=await uploadOne(state.refs[i].file,i);state.uploaded.push(name);}
    const token=crypto.randomUUID().replace(/-/g,"").slice(0,12),prompt=buildGraph(settings,state.uploaded,token);openSocket();
    const response=await fetch(api("/prompt"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt,client_id:state.clientId}),signal:state.abort.signal});const data=await response.json();if(!response.ok||!data.prompt_id)throw Error(nodeError(data));
    state.activePromptId=data.prompt_id;localStorage.setItem("qwen.activePromptId",data.prompt_id);saveActiveJob();setProgress("Queued or loading Qwen Image",8,estimateSeconds(),true);pollQueue();pollHistory(false);
  }catch(error){if(error.name==="AbortError"){await finishFailure("Cancelled",true);}else await finishFailure(error.message,false);}
}
function nodeError(data){const errors=[];if(data.error?.message)errors.push(data.error.message);for(const [id,node] of Object.entries(data.node_errors||{}))for(const error of node.errors||[])errors.push(`${id}: ${error.message||""} ${error.details||""}`.trim());return (errors.join(" · ")||"ComfyUI rejected the image graph").slice(0,700);}
async function finishFailure(message,cancelled){state.activePromptId=null;state.completing=false;localStorage.removeItem("qwen.activePromptId");localStorage.removeItem("qwen.activeJob");await cleanupUploads();setBusy(false);setProgress(cancelled?"Cancelled":"Generation failed",0,null);showStage(cancelled?"Generation cancelled":"Generation failed",cancelled?"Your prompt and references are still here.":message);if(!cancelled)showError(message);}
function failGeneration(message){finishFailure(message,false);}
$("generate").onclick=generate;
$("cancel").onclick=async()=>{
  state.abort?.abort();const id=state.activePromptId;if(id){try{const queue=await (await fetch(api("/queue"))).json();const running=(queue.queue_running||[]).some(item=>item[1]===id);await fetch(api(running?"/interrupt":"/queue"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(running?{}:{delete:[id]})});}catch{showError("Could not confirm cancellation. The job remains tracked.");return;}}
  await finishFailure("Cancelled",true);
};
async function pollQueue(){
  if(!state.busy)return;try{const data=await (await fetch(api("/queue"),{cache:"no-store"})).json();const running=data.queue_running||[],pending=data.queue_pending||[];const position=pending.findIndex(item=>item[1]===state.activePromptId);$("queueInfo").textContent=running.some(item=>item[1]===state.activePromptId)?`Queue: rendering now · ${pending.length} waiting`:position>=0?`Queue: position ${position+1} · ${pending.length} waiting`:`Queue: ${running.length} active · ${pending.length} waiting`;if(running.some(item=>item[1]===state.activePromptId))await pollImageProgress();}catch{}setTimeout(pollQueue,2500);
}
function findOutput(history){for(const output of Object.values(history?.outputs||{})){for(const image of output.images||[]){if(/^qwen_studio_[A-Za-z0-9_-]+_[0-9]{5}\.png$/.test(image.filename||""))return image;}}return null;}
async function pollHistory(force=false){
  if(!state.activePromptId)return;try{const response=await fetch(api("/history/"+encodeURIComponent(state.activePromptId)),{cache:"no-store"});const all=await response.json();const history=all[state.activePromptId];if(history){const file=findOutput(history);if(file){await completeGeneration(file);return;}const status=history.status||{};if(status.status_str==="error"||status.completed===false)throw Error("The image graph failed. Check the ComfyUI error details.");}}catch(error){if(force){await finishFailure(error.message,false);return;}}setTimeout(()=>pollHistory(false),2000);
}
async function completeGeneration(file){
  if(state.completing)return;state.completing=true;
  const elapsed=(Date.now()-state.started)/1000;const filename=file.filename,settings=state.settings||{};await fetch(api("/h3_studio/image_details"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename,render_seconds:elapsed,settings})});
  if(settings.mode&&settings.profile&&settings.width&&settings.height&&settings.steps){const key=`qwen.eta.${settings.mode}.${settings.profile}.${settings.width}x${settings.height}.${settings.steps}`;let samples=[];try{samples=JSON.parse(localStorage.getItem(key)||"[]");}catch{}samples.push(elapsed);localStorage.setItem(key,JSON.stringify(samples.slice(-8)));}
  state.activePromptId=null;localStorage.removeItem("qwen.activePromptId");localStorage.removeItem("qwen.activeJob");await cleanupUploads();setBusy(false);setProgress("Completed",100,0);$("timer").textContent="Actual time: "+fmt(elapsed);await loadLibrary(true);const entry=state.results.find(item=>item.filename===filename)||{filename,settings};showImage(entry);showError("");state.completing=false;
}
function imageUrl(entry){return api("/h3_studio/image_file?"+new URLSearchParams({filename:entry.filename,_time:String(entry.modified||Date.now())}));}
function showImage(entry){state.current=entry;const image=document.createElement("img");image.src=imageUrl(entry);image.alt="Generated Qwen image";$("stage").replaceChildren(image);$("currentActions").classList.remove("hidden");}
async function loadLibrary(force=false){
  try{const response=await fetch(api("/h3_studio/image_library"),{cache:"no-store"});if(!response.ok)throw Error();state.results=(await response.json()).items||[];renderLibrary();}catch{if(force)showError("Could not refresh the generated image library.");}
}
function renderLibrary(){
  $("results").replaceChildren();$("imageCount").textContent=`(${state.results.length} on server)`;$("emptyHistory").classList.toggle("hidden",!!state.results.length);const remaining=Math.max(0,state.results.length-state.visible);$("showMore").classList.toggle("hidden",!remaining);$("showMore").textContent=`Show ${Math.min(8,remaining)} more · ${remaining} remaining`;
  state.results.slice(0,state.visible).forEach((entry,index)=>{const card=document.createElement("div");card.className="result";const open=document.createElement("button");open.className="result-open";open.type="button";const media=document.createElement("div");media.className="result-media";const image=document.createElement("img");image.src=imageUrl(entry);image.loading="lazy";image.alt=`Generated image ${index+1}`;media.append(image);const label=document.createElement("div");label.className="result-label";label.textContent=`${entry.width} × ${entry.height} · ${(entry.settings?.profile||"Qwen").toUpperCase()}`;const small=document.createElement("small");small.textContent=new Date(entry.modified*1000).toLocaleString()+(entry.render_seconds?" · "+fmt(entry.render_seconds):"");label.append(small);open.append(media,label);open.onclick=()=>showImage(entry);const actions=document.createElement("div");actions.className="result-card-actions";const details=document.createElement("button");details.type="button";details.textContent="Details";details.onclick=()=>openDetails(entry,details);const remove=document.createElement("button");remove.type="button";remove.textContent="Delete";remove.setAttribute("aria-label",`Delete generated image ${index+1}`);remove.onclick=()=>deleteEntry(entry);actions.append(details,remove);card.append(open,actions);$("results").append(card);});
}
function openDetails(entry,trigger){const data=entry.settings||{};const content=$("detailsContent");content.replaceChildren();const grid=document.createElement("dl");grid.className="settings";const row=(key,value)=>{const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=key;dd.textContent=value===undefined||value===null||value===""?"—":String(value);grid.append(dt,dd);};row("Inputs",data.mode==="edit"?"Prompt + references":"Prompt only");row("Precision",data.profile?.toUpperCase());row("Canvas",`${entry.width||data.width} × ${entry.height||data.height}`);row("Steps",data.steps);row("Seed",data.seed);row("Transparent",data.transparent?"Yes":"No");row("References",Array.isArray(data.referenceNames)?data.referenceNames.map((name,index)=>`@${name} → image${index+1}`).join(", "):Array.isArray(data.references)?data.references.length:0);row("Render time",entry.render_seconds?fmt(entry.render_seconds):"—");row("Prompt",data.sourcePrompt||data.prompt);content.append(grid);$("detailsDialog").showModal();$("detailsDialog").dataset.returnId=trigger?.id||"";}
$("closeDetails").onclick=()=>$("detailsDialog").close();$("detailsDialog").onclick=event=>{if(event.target===$("detailsDialog"))$("detailsDialog").close();};
$("details").onclick=event=>state.current&&openDetails(state.current,event.currentTarget);
$("download").onclick=()=>{if(!state.current)return;const a=document.createElement("a");a.href=imageUrl(state.current);a.download=state.current.filename;a.click();};
async function deleteEntry(entry){if(!entry||!confirm("Delete this generated image from the server? This cannot be undone."))return;try{const response=await fetch(api("/h3_studio/delete_image"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({filename:entry.filename})});if(!response.ok)throw Error();if(state.current?.filename===entry.filename){state.current=null;$("currentActions").classList.add("hidden");showStage("Image deleted","Generate another image or select one from the library.");}state.results=state.results.filter(item=>item.filename!==entry.filename);renderLibrary();showError("");await loadLibrary(false);}catch{showError("The image could not be deleted. Retry after reconnecting.");}}
$("deleteCurrent").onclick=()=>deleteEntry(state.current);$("refreshLibrary").onclick=()=>loadLibrary(true);$("showMore").onclick=()=>{state.visible+=8;renderLibrary();};
$("randomSeed").onclick=()=>{$("seed").value=Math.floor(Math.random()*2**48);saveDraft();};
for(const id of ["prompt","aspect","resolution","steps","seed","transparent","referenceResolution","customSize","fitReferences"]){$(id).addEventListener("input",()=>{updateSettings();saveDraft();if(id==="prompt")renderMentions();});}
window.addEventListener("focus",()=>loadLibrary(false));
state.timer=setInterval(()=>{if(state.busy&&state.started){const elapsed=(Date.now()-state.started)/1000;$("timer").textContent="Elapsed: "+fmt(elapsed);if(!state.lastStepAt)$("remaining").textContent="Time remaining: "+fmt(Math.max(0,estimateSeconds()-elapsed));}},1000);
async function resume(){if(!state.activePromptId)return;let job={};try{job=JSON.parse(localStorage.getItem("qwen.activeJob")||"{}");}catch{}if(job.promptId===state.activePromptId){state.settings=job.settings||null;state.started=Number(job.started)||Date.now();state.uploaded=Array.isArray(job.uploaded)?job.uploaded:[];state.lastStep=Number(job.lastStep)||0;state.stepTotal=Number(job.stepTotal)||0;state.stepTimes=Array.isArray(job.stepTimes)?job.stepTimes:[];}else{state.started=Date.now();}state.busy=true;setBusy(true);setProgress("Restoring active generation",5,null,true);if(state.lastStep&&state.stepTotal)setProgress(`Sampling · ${state.lastStep}/${state.stepTotal}`,12+Math.round(78*state.lastStep/state.stepTotal),null);openSocket();pollQueue();pollHistory(false);}
restoreDraft();renderRefs();updateSettings();loadLibrary(false);checkConnection().then(resume);
