"use strict";
const $ = selector => document.querySelector(selector);
const esc = text => String(text ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const steps = ["剧本分镜","角色设定","场景环境","首尾关键帧","分镜视频","成片导出"];
const labels = {scene:"场景与剧情",cast:"出镜角色",dialogue:"独立对白",profile:"角色设定卡",environment:"环境设定",
  sheet:"角色三视图",plate:"无人环境参考图",first:"首帧",last:"尾帧",video:"镜头视频",film:"最终成片"};
const textKinds = ["scene","cast","dialogue","profile","environment"];
const edits = new Map();
const notes = new Map();
let p, jobs = [], step = 0, timer, pending = false, config;
const kindOf = key => key.split(".")[1];
const current = key => p.nodes[key]?.versions.at(-1);
const activeJob = () => jobs.find(j => ["排队中","制作中"].includes(j.status));
const button = (label,action,key="",primary=false) =>
  `<button data-a="${action}" data-k="${esc(key)}" class="${primary?"primary":""}">${label}</button>`;
const input = (key,field,label,v,rows=0) => `<label>${label}${rows ?
  `<textarea data-key="${esc(key)}" data-field="${field}" rows="${rows}">${esc(v)}</textarea>` :
  `<input data-key="${esc(key)}" data-field="${field}" value="${esc(v)}">`}</label>`;
function message(s,error=false) {
  $("#message").textContent=s;
  $("#message").classList.toggle("error",error);
}
async function api(url, options) {
  const r=await fetch(url,options), result=await r.json();
  if (!r.ok) throw Error(result.error || `HTTP ${r.status}`);
  return result;
}
const post = body => api("/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
function setState(data) {
  p=data.project;
  jobs=data.jobs;
  p.scene_sharing_available=Boolean(p.scene_owners&&p.scene_candidates&&p.scene_groups);
  const owners=p.scene_owners||{},candidates=p.scene_candidates||{};
  p.scene_owners=Object.fromEntries(p.shots.map(s=>[s,owners[s]||s]));
  p.scene_candidates=Object.fromEntries(p.shots.map(s=>[s,candidates[s]||[s]]));
  p.scene_groups={};
  for(const shot of p.shots){
    const owner=p.scene_owners[shot];
    (p.scene_groups[owner]??=[]).push(shot);
  }
  localStorage.setItem("paper-studio-project",p.id);
}
const send = async (op,other={}) => setState(await post({op,id:p.id,revision:p.revision,...other}));
const localValue = key => edits.has(key)?edits.get(key):current(key)?.value;
function media(name) {
  if (!/^[a-f0-9]{64}\.(png|jpg|webp|mp4)$/.test(name)) return "";
  return name.endsWith(".mp4")?`<video class="preview" src="/media/${name}" controls playsinline preload="metadata"></video>`:
    `<img class="preview" src="/media/${name}" alt="生成的参考图" loading="lazy">`;
}
function editor(key) {
  const kind=kindOf(key),v=localValue(key);
  if (kind==="scene") return input(key,"place","地点",v.place)+input(key,"time","时段（白天 / 黄昏 / 夜晚）",v.time)+
    input(key,"seconds","时长（秒）",v.seconds)+input(key,"plot","动作与运镜",v.plot,3)+
    input(key,"first","开始时刻",v.first,2)+input(key,"last","结束时刻",v.last,2);
  if (kind==="cast") return input(key,"cast","角色名，每行一位",Array.isArray(v)?v.join("\n"):v,3);
  if (kind==="dialogue") return (Array.isArray(v)&&v.some(x=>x.speaker==="待确认")?
    '<p class="hint">部分台词的说话人待确认，请核对原文并修改后审核。</p>':"")+
    input(key,"dialogue","每行一句「说话人：台词」",Array.isArray(v)?v.map(x=>x.speaker+"："+x.text).join("\n"):v,4);
  return input(key,"description","描述",v || "",4);
}
function panel(key) {
  const kind=kindOf(key), node=p.nodes[key], v=current(key), textual=textKinds.includes(kind);
  const content=textual?editor(key):(v?media(v.value):"");
  const feedback=`<label class="feedback">修改建议<input class="notes" data-note="${key}" value="${esc(notes.get(key)||"")}" placeholder="写下要调整的细节"></label>`;
  const actions=[];
  if(textual) actions.push(button("保存编辑","edit",key));
  if(kind==="profile"||kind==="environment") {
    if(v) actions.push(button("按建议修改","generate",key));
  }else if(!["scene","cast","dialogue"].includes(kind)) actions.push(button(v?"按当前输入重做":"生成"+labels[kind],"generate",key,!v));
  else actions.push(button("按建议修改","generate",key));
  if(v) actions.push(button("审核通过","approve",key),button("退回修改","reject",key),button("历史版本 · "+node.versions.length,"versions",key));
  return `<section class="card ${kind}"><div class="caption"><h3>${labels[kind]}</h3><span class="badge">${esc(node?.status||"待制作")}</span></div>
  ${kind==="profile"?`<div class="actions">${button(v?"重新自动生成":"自动生成初版","generate",key,true)}</div><p class="hint">根据该角色出场的已确认分镜生成描述，可继续手动编辑。</p>`:""}
  ${kind==="environment"?`<div class="actions">${button(v?"重新自动生成初稿":"自动生成初稿","generate",key,true)}</div><p class="hint">根据本镜已确认的场景生成地点、时段、布局和光线描述，可编辑并审核后生成环境参考图。</p>`:""}
  ${content}${feedback}<div class="actions">${actions.join("")}</div></section>`;
}
function render(){
  clearTimeout(timer);
  $("#title").value=edits.get("title")??p.title;
  $("#steps").innerHTML=steps.map((s,i)=>`<button data-a="step" data-i="${i}" class="${step===i?"active":""}" aria-current="${step===i?"step":"false"}"><span class="num">0${i+1}</span>${s}</button>`).join("");
  const sec=p.shots.reduce((sum,s)=>sum+current(s+".scene").value.seconds,0);
  $("#count").textContent=`${p.shots.length} 镜 · ${sec} 秒 · 9:16`;
  const head=s=>`<h2>分镜 ${s.slice(1)} · ${esc(current(s+".scene").value.place)}</h2>`;
  let content="";
  if(step===0){
    content=`<section class="card"><h2>风格设定</h2><textarea id="style" aria-label="风格设定" rows="3" placeholder="例如：水彩绘本风格，柔和低饱和配色，手绘纸张质感……">${esc(edits.get("style")??p.style??"漫画插画")}</textarea>
      <p class="hint">用于角色三视图、场景图、首尾关键帧和视频。修改风格后，相关已有素材需重新制作。</p><div class="actions">${button("保存风格","save-style")}</div></section>
      <section class="card"><h2>剧本原文</h2><textarea class="story" id="story" aria-label="剧本原文" ${p.shots.length?"readonly":""} placeholder="粘贴故事或小说原文……">${esc(edits.get("story")??p.story)}</textarea>
      <div class="actions">${p.shots.length?`<a class="button" download="storyboard.json" href="/storyboard?id=${p.id}">下载分镜与对白 JSON</a>`:button("拆分为分镜","generate","plan",true)}
      <small>每镜 10–15 秒</small></div></section>`+
      p.shots.map(s=>`<article class="card">${head(s)}<div class="group">${["scene","cast","dialogue"].map(f=>panel(s+"."+f)).join("")}</div></article>`).join("");
  }else if(!p.shots.length){
    content='<div class="empty">先在「剧本分镜」输入故事并拆镜。</div>';
  }else if(step===1){
    content=Object.entries(p.characters).map(([name,id])=>`<article><h2>${esc(name)}</h2><div class="group">${panel(id+".profile")}${panel(id+".sheet")}</div></article>`).join("") || '<div class="empty">当前分镜均为空镜。</div>';
  }else if(step===2){
    content=(p.scene_sharing_available?`<section class="card"><h2>场景复用</h2><p class="hint">同地点、同时间段可共用一份环境设定和参考图。需要不同布置的分镜可选择「本镜环境」。</p>
      ${button("按地点和时间合并场景","group-scenes","",true)}
      ${p.shots.map(s=>`<label>分镜 ${s.slice(1)} 的环境来源<select data-scene-source="${s}">${p.scene_candidates[s].map(t=>`<option value="${t}" ${p.scene_owners[s]===t?"selected":""}>${t===s?"本镜环境":"共用分镜 "+t.slice(1)+" 的环境"} · ${esc(current(t+".scene").value.place)} · ${esc(current(t+".scene").value.time)}</option>`).join("")}</select></label>`).join("")}</section>`:
      '<section class="card"><p>当前服务使用逐镜环境。重启工作台服务后可启用场景复用。</p></section>')+
      Object.entries(p.scene_groups).map(([owner,members])=>`<article><h2>${esc(current(owner+".scene").value.place)} · ${esc(current(owner+".scene").value.time)}</h2>
        <p class="hint">使用分镜 ${members.map(s=>s.slice(1)).join("、")} · 环境来源 ${owner}</p>
        <div class="frame-pair">${panel(owner+".environment")}${panel(owner+".plate")}</div></article>`).join("");
  }else if(step===5){
    const count=p.shots.filter(s=>p.nodes[s+".video"]?.status==="已确认").length;
    content=`<section class="card"><h2>剪辑顺序</h2><ol>${p.shots.map(s=>`<li>${esc(current(s+".scene").value.place)} · ${current(s+".scene").value.seconds} 秒 · ${p.nodes[s+".video"]?.status||"待制作"}</li>`).join("")}</ol>
    <p>已确认 ${count} / ${p.shots.length} 个镜头 · 输出 MP4，720 × 1280</p></section>`+panel("final.film");
    const film=current("final.film");
    if(film && p.nodes["final.film"].status!=="上游已变更")
      content+=`<a class="button primary" download="漫剧成片.mp4" href="/media/${film.value}">下载成片 MP4</a>`;
  }else{
    const kinds=step===3?["first","last"]:["video"];
    content=p.shots.map(s=>`<article><header class="card"><div>${head(s)}<p>${esc(current(s+".scene").value.plot)}</p></div></header>
      ${step===3?`<p class="hint">环境来源 · ${esc(p.scene_owners[s])}</p>`:""}
      <div class="${kinds.length===2?"frame-pair":""}">${kinds.map(k=>panel(s+"."+k)).join("")}</div></article>`).join("");
  }
  $("#work").innerHTML=`<h1>${steps[step]}</h1>${content}`;
  if(jobs.length) $("#work").insertAdjacentHTML("beforeend",`<details><summary>制作任务记录</summary>${jobs.map(j=>`<p>${esc(j.target)} · ${esc(j.status)} ${esc(j.message)} ${j.remote_id?" · 远端 "+esc(j.remote_id):""}</p>`).join("")}</details>`);
  const job=activeJob();
  if(job){
    message(`${job.target} · ${job.status}，可切换步骤查看。`);
    $("#work").querySelectorAll("input,textarea,button").forEach(el=>el.disabled=true);
    $("#title").disabled=true;
    timer=setTimeout(poll,1500);
  }else $("#title").disabled=false;
}
async function poll(){
  const id=p.id;
  try{
    const data=await api("/project?id="+id);
    if(id!==p.id)return;
    setState(data);
    if(!activeJob()){
      const j=jobs[0];
      message(j.status==="完成"?"制作完成，可以审核当前版本。":j.message,j.status!=="完成");
    }
    render();
  }catch(e){message(e.message,true);timer=setTimeout(poll,3000);}
}
function fieldValue(key,v){
  if(kindOf(key)==="cast" && typeof v==="string")return v.split(/[\n、，,]/).map(n=>n.trim()).filter(Boolean);
  if(kindOf(key)==="dialogue" && typeof v==="string")return v.split("\n").filter(l=>l.trim()).map(line=>{
    const m=line.match(/^([^：:]+)[：:](.+)$/);
    if(!m)throw Error("每句对白都需要说话人和台词，中间用冒号分隔");
    return {speaker:m[1].trim(),text:m[2].trim()};
  });
  return v;
}
async function flush() {
  if(edits.has("title")||edits.has("story")||edits.has("style")){
    await send("save",{title:edits.get("title")??p.title,story:edits.get("story")??p.story,style:edits.get("style")??p.style??"漫画插画"});
    edits.delete("title");edits.delete("story");edits.delete("style");
  }
  for(const [key,v] of [...edits]){
    await send("edit",{target:key,value:fieldValue(key,v),note:notes.get(key)||""});
    edits.delete(key);
  }
}
function modal(html){
  $("#panel").innerHTML=html;
  if(!$("#dialog").open)$("#dialog").showModal();
}
function versions(key){
  const vs=p.nodes[key].versions;
  modal(`<h2>${labels[kindOf(key)]} · 历史版本</h2><p class="hint">回退后会新增一条待审核记录。历史素材保留生成时的上游引用及风格，可据此核对需要还原的设定。</p>`+vs.map((v,i)=>`<div class="history"><b>版本 ${i+1} · ${esc(v.review)}</b>
    <p><time>${esc(v.at)}</time> · ${esc(v.source)}</p>${textKinds.includes(kindOf(key))?`<pre>${esc(typeof v.value==="string"?v.value:JSON.stringify(v.value,null,2))}</pre>`:media(v.value)}
    ${v.restored_from?`<p>从版本 ${esc(v.restored_from)} 回退</p>`:""}${v.style?`<p>风格 · ${esc(v.style)}</p>`:""}
    ${Object.keys(v.inputs).length?`<details><summary>使用的上游版本</summary>${Object.entries(v.inputs).map(([dep,number])=>`<p>${esc(dep)} · ${esc(labels[kindOf(dep)])} · 版本 ${number}</p>`).join("")}</details>`:""}
    <p>${esc(v.note)}</p>${v.reviews.map(r=>`<p>${esc(r.state)} · ${esc(r.note)} <time>${esc(r.at)}</time></p>`).join("")}
    ${i<vs.length-1?`<button class="primary" data-a="restore" data-k="${key}" data-i="${i}">回退到版本 ${i+1}</button>`:'<span class="badge">当前版本</span>'}</div>`).reverse().join(""));
}
const serviceNames={text:"文本模型",image:"图片模型",video:"视频模型"};
const localNames={text:"本地 Qwen · Ollama",image:"本地 MiniMax H3 Image",video:"本地 MiniMax H3"};
function settingsPanel(){
  modal("<h2>连接设置</h2>"+Object.entries(config).map(([kind,row])=>{
    const mode=row.mode, d=row[mode], remote=mode==="api", editable=remote||kind==="text";
    const apiName={text:"Chat Completions API",image:"OpenAI Images / Muse API",video:"MiniMax H3 V2 API"}[kind];
    return `<fieldset><legend>${serviceNames[kind]}</legend><label>来源<select data-mode="${kind}"><option value="local" ${remote?"":"selected"}>${localNames[kind]}</option>
    <option value="api" ${remote?"selected":""}>${apiName}</option></select></label>
    ${editable?["url","model",...(remote?["key"]:[])].map(f=>`<label>${{url:"基础地址",model:"模型名称",key:"API Key"}[f]}<input type="${f==="key"?"password":"text"}" data-cfg="${kind}" data-f="${f}" value="${esc(d[f]||"")}" placeholder="${f==="key"&&d.has_key?"已配置，留空沿用":""}"></label>`).join(""):"<p>使用本机已安装的 MLX 运行时和权重。</p>"}</fieldset>`;
  }).join("")+button("保存连接","config-save","",true));
}
async function projects(){
  const list=await api("/projects");
  modal("<h2>历史项目</h2>"+button("新建项目","new")+list.map(row=>`<div class="history"><b>${esc(row.title)}</b>
    <p>${row.shots} 镜 · ${esc(row.updated)}</p><button data-a="open" data-k="${row.id}">打开</button>
    <a class="button" href="/package?id=${row.id}" download>下载项目包</a></div>`).join(""));
}
document.addEventListener("input",e=>{
  const el=e.target,{key,field,note,cfg,f}=el.dataset;
  if(el.id==="story"||el.id==="title"||el.id==="style")edits.set(el.id,el.value);
  if(key){
    if(kindOf(key)==="scene")edits.set(key,{...localValue(key),[field]:field==="seconds"?Number(el.value):el.value});
    else edits.set(key,el.value);
  }
  if(note)notes.set(note,el.value);
  if(cfg)config[cfg][config[cfg].mode][f]=el.value;
});
document.addEventListener("change",async e=>{
  const kind=e.target.dataset.mode;
  if(kind){config[kind].mode=e.target.value;settingsPanel();}
  const shot=e.target.dataset.sceneSource;
  if(shot&&!pending){
    pending=true;
    try{await flush();await send("scene-source",{target:shot,source:e.target.value});message("环境来源已保存，共用该环境的分镜会使用同一张参考图。");}
    catch(err){message(err.message,true);}
    finally{pending=false;render();}
  }
});
async function handle(el){
  const a=el.dataset.a,k=el.dataset.k, note=notes.get(k)||"";
  if(a==="step"){step=Number(el.dataset.i);render();return;}
  if(a==="versions"){versions(k);return;}
  if(a==="settings"){config=await api("/settings");settingsPanel();return;}
  if(a==="config-save"){await post({op:"settings",settings:config});$("#dialog").close();message("连接设置已保存");return;}
  if(a==="import"){await flush();$("#upload").click();return;}
  if(a==="projects"){await flush();await projects();return;}
  if(a==="open"||a==="new"){
    await flush();
    setState(a==="new"?await post({op:"new"}):await api("/project?id="+k));
    notes.clear();step=0;$("#dialog").close();message("");render();return;
  }
  if(a==="edit"&&!edits.has(k)){message("请先编辑描述或分镜内容");return;}
  await flush();
  if(a==="group-scenes"){
    if(p.scene_sharing_available){await send("group-scenes");message("已合并同地点、同时间段的场景，优先选用已有环境图。");}
    else message("请重启工作台服务以启用场景复用。",true);
  }else if(a==="save-style"){
    message("风格设定已保存，后续图片和视频将使用此风格。");
  }else if(a==="save"){
    const link=document.createElement("a");link.href="/package?id="+p.id;link.download="comic-project.zip";link.click();
    message("项目已保存，正在下载包含历史素材的项目包。");
  }else if(a==="generate"){
    const progress={profile:"正在根据分镜生成该角色的描述初稿……",environment:"正在根据当前分镜生成场景环境初稿……"};
    message(k==="plan"?"正在提交拆镜任务，等待文本模型返回……":progress[kindOf(k)]||"正在提交制作任务……");
    await send("generate",{target:k,note});
    if(!activeJob() && jobs[0]?.status==="失败")message(jobs[0].message,true);
  }
  else if(a==="approve"||a==="reject"){await send("review",{target:k,note,approve:a==="approve"});message("审核意见已保存");}
  else if(a==="restore"){
    await send("restore",{target:k,index:Number(el.dataset.i),note});$("#dialog").close();
    message(p.nodes[k].status==="上游已变更"?"已回退历史版本；其上游或风格与当前设定有差异，请核对历史记录中的上游版本。":"已回退到所选内容，请审核确认。之后可继续使用与该版本内容匹配的下游素材。");
  }
  else if(a==="edit"){message("编辑已保存为新版本");}
  if(k)notes.delete(k);
  render();
}
document.addEventListener("click",async e=>{
  const el=e.target.closest("[data-a]");
  if(!el||pending)return;
  pending=true;
  try{await handle(el);}catch(err){message(err.message,true);if($("#dialog").open)alert(err.message);}
  finally{pending=false;}
});
$("#upload").addEventListener("change",async e=>{
  const file=e.target.files[0];if(!file)return;
  try{setState(await api("/import",{method:"POST",headers:{"Content-Type":"application/zip"},body:file}));
    step=0;edits.clear();notes.clear();message("项目已导入为独立副本");render();
  }catch(err){message(err.message,true);}finally{e.target.value="";}
});
window.addEventListener("beforeunload",e=>{if(edits.size){e.preventDefault();e.returnValue="";}});
function initFlowSidebar(){
  const panel=$("#flow-panel"),handle=$("#flow-handle"),desktop=window.matchMedia("(min-width:901px)");
  let drag=null,top;
  function place(y){
    if(!desktop.matches)return;
    const height=Math.min(panel.scrollHeight,Math.max(0,window.innerHeight-24));
    top=Math.max(12,Math.min(y,Math.max(12,window.innerHeight-height-12)));
    panel.style.setProperty("--flow-top",top+"px");
  }
  function endDrag(e){
    if(!drag||drag.id!==e.pointerId)return;
    drag=null;
    panel.classList.remove("dragging");
    if(handle.hasPointerCapture(e.pointerId))handle.releasePointerCapture(e.pointerId);
  }
  handle.addEventListener("pointerdown",e=>{
    if(e.button!==0||!desktop.matches)return;
    e.preventDefault();
    drag={id:e.pointerId,y:e.clientY,top:panel.getBoundingClientRect().top};
    handle.setPointerCapture(e.pointerId);
    panel.classList.add("dragging");
  });
  handle.addEventListener("pointermove",e=>{
    if(drag?.id===e.pointerId)place(drag.top+e.clientY-drag.y);
  });
  for(const event of ["pointerup","pointercancel","lostpointercapture"])handle.addEventListener(event,endDrag);
  handle.addEventListener("keydown",e=>{
    if(!desktop.matches||!["ArrowUp","ArrowDown","Home","End"].includes(e.key))return;
    e.preventDefault();
    if(e.key==="Home")place(12);
    else if(e.key==="End")place(window.innerHeight);
    else place(panel.getBoundingClientRect().top+(e.key==="ArrowUp"?-20:20));
  });
  function layout(){
    if(!desktop.matches&&drag)endDrag({pointerId:drag.id});
    place(top??Math.max(12,$("body > header").getBoundingClientRect().bottom+33));
  }
  window.addEventListener("resize",layout);
  desktop.addEventListener("change",layout);
  layout();
}
initFlowSidebar();
(async()=>{
  try{
    const list=await api("/projects"),saved=localStorage.getItem("paper-studio-project");
    const id=list.find(row=>row.id===saved)?.id||list[0]?.id;
    setState(id?await api("/project?id="+id):await post({op:"new"}));render();
    if(jobs[0]?.status==="失败"||jobs[0]?.status==="已中断")message(jobs[0].message,true);
  }catch(err){message(err.message,true);}
})();
