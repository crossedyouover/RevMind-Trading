"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="session"]').content;
let selected = null;
async function api(path, method="GET") {
  const r = await fetch(path, {method, headers:{"X-RevMind-Token":token, ...(method==="POST"?{"Content-Type":"application/json"}:{})}, ...(method==="POST"?{body:"{}"}:{})});
  if (!r.ok) throw new Error("The local service could not validate this request. Check that the launcher is still running, then refresh.");
  return r.json();
}
function node(tag, text, cls) {const n=document.createElement(tag); n.textContent=text; if(cls)n.className=cls; return n;}
function notice(text){$("notice").textContent=text;}
function chart(bars){
  $("chart").replaceChildren();
  if(!bars.length){$("chart").append(node("p","No selected bars."));return;}
  const svg=document.createElementNS("http://www.w3.org/2000/svg","svg"); svg.setAttribute("viewBox","0 0 600 180");svg.setAttribute("role","img");svg.setAttribute("aria-label","Synthetic closing prices over the captured bar history");
  const values=bars.map(b=>Number(b.bar.close)), low=Math.min(...values)-.5, high=Math.max(...values)+.5;
  function shape(tag, attrs, text){const e=document.createElementNS(svg.namespaceURI,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text)e.textContent=text;svg.append(e);return e;}
  for(let i=0;i<4;i++){const y=15+i*43;shape("line",{x1:40,x2:550,y1:y,y2:y,stroke:"#293a3e"});shape("text",{x:559,y:y+4},(high-(high-low)*i/3).toFixed(2));}
  const points=values.map((v,i)=>[40+i*510/Math.max(1,values.length-1),15+(high-v)/(high-low)*129]);
  shape("polyline",{points:points.map(p=>p.join(",")).join(" "),fill:"none",stroke:"#b5ebcb","stroke-width":2.5});
  points.forEach(([x,y],i)=>{shape("circle",{cx:x,cy:y,r:4,fill:"#b5ebcb"});shape("text",{x,y:173,"text-anchor":"middle"},bars[i].bar.timestamp.slice(11,16));});
  $("chart").append(svg);
}
async function select(key){
  try{selected=await api("/api/runs/"+encodeURIComponent(key));const r=selected.result;
    $("state").textContent=selected.state;$("run-id").textContent=selected.cycle_id;$("download").disabled=!r;
    const bars=r?r.research.request.history.bars:[];$("count").textContent=bars.length;chart(bars);
    const latest=r?.trend.snapshots.at(-1);$("trend").textContent=latest?(latest.regime||latest.status).replaceAll("_"," "):"Unavailable";
    $("bars").replaceChildren();for(const b of bars){const tr=node("tr","");for(const value of [b.bar.timestamp.replace("T"," "),b.bar.open,b.bar.high,b.bar.low,b.bar.close,b.bar.volume])tr.append(node("td",value));$("bars").append(tr);}
    $("evidence").replaceChildren();for(const s of (r?.research.setup_snapshots.at(-1)?.setups||[])){const row=node("div","","evidence-row");row.append(node("span",s.key.replaceAll("_"," ")),node("span",s.status.replaceAll("_"," ")));$("evidence").append(row);}
    $("audit").replaceChildren();for(const [stage,at]of selected.events){const row=node("div","");row.append(node("strong",stage.replaceAll("_"," ")+" · "),node("span",at));$("audit").append(row);}if(r){$("audit").append(node("p","Sealed evidence digest"),node("code",r.sealed_digest));}
    document.querySelectorAll(".run-item").forEach(b=>b.classList.toggle("selected",b.dataset.key===key));
    notice(r?"Completed offline research. These synthetic observations are not current market prices.":"This run is incomplete or blocked. No successful result is being claimed.");
  }catch(e){notice(e.message);}
}
async function refresh(preferred){try{const rows=await api("/api/runs");$("runs").replaceChildren();for(const row of rows){const b=node("button","","run-item");b.dataset.key=row.key;b.append(node("span",row.key==="existing"?"Your PowerShell demo":row.key.slice(0,8)+" · Offline demo"),node("span",row.state+" · "+row.bars+" bars"));b.onclick=()=>select(row.key);$("runs").append(b);}if(rows.length)await select(preferred||rows[0].key);else notice("Ready. Click Run offline demo to create your first research result.");}catch(e){notice(e.message);}}
$("run").onclick=async()=>{$("run").disabled=true;notice("Running the synthetic capture and research pipeline…");try{const result=await api("/api/demo","POST");await refresh(result.key);}catch(e){notice(e.message);}finally{$("run").disabled=false;}};
$("refresh").onclick=()=>refresh(selected?.key);
$("download").onclick=()=>{if(!selected?.result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(selected,null,2)],{type:"application/json"}));const a=node("a","");a.href=url;a.download="revmind-"+selected.cycle_id+".json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
refresh();
