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
  if (kind==="dialogue") return input(key,"dialogue","每行一句「说话人：台词」",Array.isArray(v)?v.map(x=>x.speaker+"："+x.text).join("\n"):v,4);
  return input(key,"description","描述",v || "",4);
}
function panel(key) {
  const kind=kindOf(key), node=p.nodes[key], v=current(key), textual=textKinds.includes(kind);
  const content=textual?editor(key):(v?media(v.value):"");
  const feedback=`<label class="feedback">修改建议<input class="notes" data-note="${key}" value="${esc(notes.get(key)||"")}" placeholder="写下要调整的细节"></label>`;
  const actions=[];
  if(textual) actions.push(button("保存编辑","edit",key));
  if(!["scene","cast","dialogue"].includes(kind)) actions.push(button(v?"按当前输入重做":"生成"+labels[kind],"generate",key,!v));
  else actions.push(button("按建议修改","generate",key));
  if(v) actions.push(button("审核通过","approve",key),button("退回修改","reject",key),button("历史 · "+node.versions.length,"versions",key));
  return `<section class="card ${kind}"><div class="caption"><h3>${labels[kind]}</h3><span class="badge">${esc(node?.status||"待制作")}</span></div>
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
    content=`<section class="card"><h2>剧本原文</h2><textarea class="story" id="story" aria-label="剧本原文" ${p.shots.length?"readonly":""} placeholder="粘贴故事或小说原文……">${esc(edits.get("story")??p.story)}</textarea>
      <div class="actions">${p.shots.length?`<a class="button" download="storyboard.json" href="/storyboard?id=${p.id}">下载分镜与对白 JSON</a>`:button("拆分为分镜","generate","plan",true)}
      <small>每镜 10–15 秒</small></div></section>`+
      p.shots.map(s=>`<article class="card">${head(s)}<div class="group">${["scene","cast","dialogue"].map(f=>panel(s+"."+f)).join("")}</div></article>`).join("");
  }else if(!p.shots.length){
    content='<div class="empty">先在「剧本分镜」输入故事并拆镜。</div>';
  }else if(step===1){
    content=Object.entries(p.characters).map(([name,id])=>`<article><h2>${esc(name)}</h2><div class="group">${panel(id+".profile")}${panel(id+".sheet")}</div></article>`).join("") || '<div class="empty">当前分镜均为空镜。</div>';
  }else if(step===5){
    const count=p.shots.filter(s=>p.nodes[s+".video"]?.status==="已确认").length;
    content=`<section class="card"><h2>剪辑顺序</h2><ol>${p.shots.map(s=>`<li>${esc(current(s+".scene").value.place)} · ${current(s+".scene").value.seconds} 秒 · ${p.nodes[s+".video"]?.status||"待制作"}</li>`).join("")}</ol>
    <p>已确认 ${count} / ${p.shots.length} 个镜头 · 输出 MP4，720 × 1280</p></section>`+panel("final.film");
    const film=current("final.film");
    if(film && p.nodes["final.film"].status!=="上游已变更")
      content+=`<a class="button primary" download="漫剧成片.mp4" href="/media/${film.value}">下载成片 MP4</a>`;
  }else{
    const kinds=step===2?["environment","plate"]:step===3?["first","last"]:["video"];
    content=p.shots.map(s=>`<article><header class="card"><div>${head(s)}<p>${esc(current(s+".scene").value.plot)}</p></div></header>
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
  if(edits.has("title")||edits.has("story")){
    await send("save",{title:edits.get("title")??p.title,story:edits.get("story")??p.story});
    edits.delete("title");edits.delete("story");
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
  modal(`<h2>${labels[kindOf(key)]} · 版本记录</h2>`+vs.map((v,i)=>`<div class="history"><b>版本 ${i+1} · ${esc(v.review)}</b>
    <p><time>${esc(v.at)}</time> · ${esc(v.source)}</p>${textKinds.includes(kindOf(key))?`<pre>${esc(typeof v.value==="string"?v.value:JSON.stringify(v.value,null,2))}</pre>`:media(v.value)}
    <p>${esc(v.note)}</p>${v.reviews.map(r=>`<p>${esc(r.state)} · ${esc(r.note)} <time>${esc(r.at)}</time></p>`).join("")}
    ${i<vs.length-1?`<button data-a="restore" data-k="${key}" data-i="${i}">恢复为新版本</button>`:""}</div>`).reverse().join(""));
}
const serviceNames={text:"文本模型",image:"图片模型",video:"视频模型"};
const localNames={text:"本地 Qwen · Ollama",image:"本地 MiniMax H3 Image",video:"本地 MiniMax H3"};
function settingsPanel(){
  modal("<h2>连接设置</h2>"+Object.entries(config).map(([kind,row])=>{
    const mode=row.mode, d=row[mode], remote=mode==="api", editable=remote||kind==="text";
    const apiName={text:"Chat Completions API",image:"OpenAI Images API",video:"MiniMax H3 V2 API"}[kind];
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
  if(el.id==="story"||el.id==="title")edits.set(el.id,el.value);
  if(key){
    if(kindOf(key)==="scene")edits.set(key,{...localValue(key),[field]:field==="seconds"?Number(el.value):el.value});
    else edits.set(key,el.value);
  }
  if(note)notes.set(note,el.value);
  if(cfg)config[cfg][config[cfg].mode][f]=el.value;
});
document.addEventListener("change",e=>{
  const kind=e.target.dataset.mode;
  if(kind){config[kind].mode=e.target.value;settingsPanel();}
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
  if(a==="save"){
    const link=document.createElement("a");link.href="/package?id="+p.id;link.download="comic-project.zip";link.click();
    message("项目已保存，正在下载包含历史素材的项目包。");
  }else if(a==="generate"){
    message(k==="plan"?"正在提交拆镜任务，等待文本模型返回……":"正在提交制作任务……");
    await send("generate",{target:k,note});
    if(!activeJob() && jobs[0]?.status==="失败")message(jobs[0].message,true);
  }
  else if(a==="approve"||a==="reject"){await send("review",{target:k,note,approve:a==="approve"});message("审核意见已保存");}
  else if(a==="restore"){await send("restore",{target:k,index:Number(el.dataset.i),note});$("#dialog").close();message("已恢复为待审核的新版本");}
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
(async()=>{
  try{
    const list=await api("/projects"),saved=localStorage.getItem("paper-studio-project");
    const id=list.find(row=>row.id===saved)?.id||list[0]?.id;
    setState(id?await api("/project?id="+id):await post({op:"new"}));render();
    if(jobs[0]?.status==="失败"||jobs[0]?.status==="已中断")message(jobs[0].message,true);
  }catch(err){message(err.message,true);}
})();
