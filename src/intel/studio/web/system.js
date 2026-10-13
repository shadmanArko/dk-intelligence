// The agent system: a map of every layer (orchestrator, agents, MCP servers, tools, ports) with live numbers, and a
// drawer that opens any node or call in full. Needs common.js. Data: /api/system (see trace.py).

const SYS={sel:null,callId:null,callData:null,filter:'all',text:''};

function sysModel(S){
  const T=S.topology,L=S.layers,tools=T.servers.map(s=>({...s,tools:[...s.tools]}));
  L.tools.forEach(t=>{let sv=tools.find(s=>s.name===t.server);if(!sv){sv={name:t.server,tools:[]};tools.push(sv)}if(!sv.tools.includes(t.tool))sv.tools.push(t.tool)});
  const trow=(s,t)=>L.tools.find(x=>x.server===s&&x.tool===t)||{by_agents:0,by_code:0,errors:0,ms:0,ms_max:0};
  const fromId=e=>e.from==='orchestrator'?'orch':e.from;
  const edge=(a,b)=>(L.edges.find(e=>fromId(e)===a&&e.to===b)||{calls:0}).calls;
  const openTool=new Set(S.calls.filter(c=>c.type==='tool'&&c.status==='working').map(c=>c.who));
  const openAgent=new Set(Object.entries(L.agents).filter(([,v])=>v.running>0).map(([k])=>k));
  return {T,L,servers:tools,trow,edge,openTool,openAgent};
}

function renderSystemMap(el,S,onSelect){
  const M=sysModel(S),W=1260,TOP=44,AH=66,AG=10;
  const agents=M.T.agents,AY=i=>TOP+i*(AH+AG);
  // servers stacked, each tall enough for its tools
  let y=TOP;const SV=M.servers.map(s=>{const h=Math.max(78,34+s.tools.length*26);const r={...s,y,h};y+=h+14;return r});
  const portY=y+6,PH=64,RAGY=portY+2*(PH+10),H=Math.max(AY(agents.length),RAGY+78)+20;
  const OX=18,OW=210,AX=330,AW=230,SX=690,SW=230,TX=1010,TW=230;
  const nodes={};  // id -> {x,y,w,h}
  nodes['orch']={x:OX,y:TOP,w:OW,h:H-TOP-30};
  agents.forEach((a,i)=>nodes['agent:'+a.name]={x:AX,y:AY(i),w:AW,h:AH});
  SV.forEach(s=>{nodes['server:'+s.name]={x:SX,y:s.y,w:SW,h:s.h};s.tools.forEach((t,j)=>nodes['tool:'+s.name+'.'+t]={x:TX,y:s.y+30+j*26,w:TW,h:22})});
  M.T.ports.forEach((p,i)=>nodes['port:'+p.id]={x:SX,y:portY+i*(PH+10),w:SW,h:PH});
  nodes['rag']={x:SX,y:RAGY,w:SW,h:68};
  M.T.phases.forEach((p,i)=>nodes['phase:'+p.id]={x:OX+10,y:TOP+36+i*54,w:OW-20,h:46});
  const side=(id,right)=>{const n=nodes[id];return {x:right?n.x+n.w:n.x,y:n.y+n.h/2}};
  const E=[];const add=(a,b,live,fromRight=true)=>{if(!nodes[a]||!nodes[b])return;const c=live?M.edge(a,b):0;E.push({a,b,calls:c})};
  agents.forEach(a=>add('orch','agent:'+a.name,true));
  agents.forEach(a=>{[...new Set(a.tools.map(t=>t.server))].forEach(s=>add('agent:'+a.name,'server:'+s,true))});
  SV.forEach(s=>{add('orch','server:'+s.name,true);s.tools.forEach(t=>add('server:'+s.name,'tool:'+s.name+'.'+t,true))});
  M.T.ports.forEach(p=>add('orch','port:'+p.id,true));
  const RAGT=['grounding_pack','search_posts','search_brand','search_reviews','verify_citations','get_sources','brand_rules','get_facts'];
  const ragCalls=s=>RAGT.reduce((a,t)=>{const r=M.trow(s,t);return a+r.by_agents+r.by_code},0);
  ['dk_intel_planner','dk_intel_writer'].forEach(s=>{if(nodes['server:'+s])E.push({a:'server:'+s,b:'rag',calls:ragCalls(s)})});
  // live-only agent->server edges not in the static allowlists
  M.L.edges.forEach(e=>{const f=e.from==='orchestrator'?'orch':e.from;if(!E.find(x=>x.a===f&&x.b===e.to)&&nodes[f]&&nodes[e.to])E.push({a:f,b:e.to,calls:e.calls})});
  const running=S.running;let svg=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="agent system map">`;
  const heads=[['Orchestrator (code)',OX],['Agents',AX],['MCP servers · ports',SX],['Tools',TX]];
  heads.forEach(([t,x])=>svg+=`<text x="${x}" y="22" fill="#a8987f" font-size="11" letter-spacing="1.2">${t.toUpperCase()}</text>`);
  const selected=SYS.sel;
  const edgeLive=e=>{if(!running)return false;const ag=e.a.startsWith('agent:')?e.a.slice(6):(e.b.startsWith('agent:')?e.b.slice(6):null);
    if(ag&&M.openAgent.has(ag))return true;if(e.b.startsWith('tool:'))return M.openTool.has(e.b.slice(5));if(e.b==='port:renderer')return S.calls.some(c=>c.type==='render'&&c.status==='working');return false};
  E.forEach(e=>{const A=side(e.a,true),B=side(e.b,false),mx=(A.x+B.x)/2,hot=selected&&(e.a===selected||e.b===selected);
    const w=e.calls?1.2+Math.min(5,Math.log2(e.calls+1)):1,col=e.calls?(hot?'#f4b02e':'#7a5a3a'):'#2c1a1c';
    svg+=`<path d="M${A.x},${A.y} C${mx},${A.y} ${mx},${B.y} ${B.x},${B.y}" fill="none" stroke="${col}" stroke-width="${w}" ${e.calls?'':'stroke-dasharray="3 5"'} opacity="${e.calls?.95:.8}"/>`;
    if(edgeLive(e))svg+=`<path d="M${A.x},${A.y} C${mx},${A.y} ${mx},${B.y} ${B.x},${B.y}" fill="none" stroke="#f4b02e" stroke-width="2" stroke-dasharray="5 9" style="animation:dash .8s linear infinite"/>`;
    if(e.calls&&!e.b.startsWith('tool:'))svg+=`<text x="${mx}" y="${(A.y+B.y)/2-3}" fill="#a8987f" font-size="10" text-anchor="middle">${e.calls}</text>`});
  const box=(id,inner,extra='')=>{const n=nodes[id];const on=selected===id;
    return `<g class="nd" data-id="${id}" style="cursor:pointer"><rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="10" fill="#271416" stroke="${on?'#f4b02e':'#3a1f21'}" stroke-width="${on?2:1.2}" ${extra}/>${inner}</g>`};
  // orchestrator with its phases
  const ph=Object.fromEntries(M.L.phases.map(p=>[p.phase,p]));
  svg+=`<g class="nd" data-id="orch" style="cursor:pointer"><rect x="${OX}" y="${TOP}" width="${OW}" height="${nodes.orch.h}" rx="12" fill="#1b0d0f" stroke="${selected==='orch'?'#f4b02e':'#3a1f21'}" stroke-width="${selected==='orch'?2:1.2}"/>
    <text x="${OX+12}" y="${TOP+22}" fill="#f3e9d8" font-size="14" font-weight="600">Orchestrator</text></g>`;
  M.T.phases.forEach(p=>{const n=nodes['phase:'+p.id],r=ph[p.id]||{},st=r.status||'pending',col=st==='end'?'#7fc59a':st==='start'?'#f4b02e':'#3a1f21';
    svg+=`<g class="nd" data-id="phase:${p.id}" style="cursor:pointer"><rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="8" fill="#271416" stroke="${selected==='phase:'+p.id?'#f4b02e':col}" stroke-width="${st==='start'?2:1.2}" ${st==='start'?'style="animation:blink 1.2s infinite"':''}/>
      <text x="${n.x+10}" y="${n.y+19}" fill="#f3e9d8" font-size="12" font-weight="600">${p.label}</text>
      <text x="${n.x+10}" y="${n.y+35}" fill="#a8987f" font-size="10.5">${st==='pending'?'waiting':st==='start'?'working':'done'}${r.seconds!=null?' · '+mmss(r.seconds):''}</text></g>`});
  // agents
  agents.forEach(a=>{const v=M.L.agents[a.name]||{},n=v.calls||0,col=COLOR[a.name]||'#ccc';
    svg+=box('agent:'+a.name,`<rect x="${nodes['agent:'+a.name].x}" y="${nodes['agent:'+a.name].y}" width="5" height="${AH}" rx="2" fill="${col}"/>
      <text x="${AX+16}" y="${nodes['agent:'+a.name].y+22}" fill="#f3e9d8" font-size="13.5" font-weight="600" opacity="${n?1:.5}">${a.name}</text>
      <text x="${AX+16}" y="${nodes['agent:'+a.name].y+39}" fill="#a8987f" font-size="11">${a.model} · ${n} call${n===1?'':'s'}${v.retries?` · ${v.retries} retr${v.retries>1?'ies':'y'}`:''}</text>
      <text x="${AX+16}" y="${nodes['agent:'+a.name].y+55}" fill="#a8987f" font-size="11">${k((v.tokens_in||0)+(v.tokens_out||0))} tokens · ${v.tool_calls||0} tool call${v.tool_calls===1?'':'s'}</text>
      ${M.openAgent.has(a.name)?`<circle cx="${AX+AW-16}" cy="${nodes['agent:'+a.name].y+16}" r="5" fill="#f4b02e" style="animation:blink 1s infinite"/>`:''}`)});
  // servers and tools
  SV.forEach(s=>{const n=nodes['server:'+s.name],tot=s.tools.reduce((a,t)=>{const r=M.trow(s.name,t);return a+r.by_agents+r.by_code},0),ms_=s.tools.reduce((a,t)=>a+M.trow(s.name,t).ms,0);
    svg+=box('server:'+s.name,`<text x="${n.x+12}" y="${n.y+20}" fill="#f3e9d8" font-size="12.5" font-weight="600">${s.name}</text>
      <text x="${n.x+12}" y="${n.y+36}" fill="#a8987f" font-size="10.5">${tot} call${tot===1?'':'s'}${tot?' · avg '+ms(ms_/tot):''}</text>`);
    s.tools.forEach(t=>{const tn=nodes['tool:'+s.name+'.'+t],r=M.trow(s.name,t),c=r.by_agents+r.by_code,hot=selected==='tool:'+s.name+'.'+t;
      svg+=`<g class="nd" data-id="tool:${s.name}.${t}" style="cursor:pointer"><rect x="${tn.x}" y="${tn.y}" width="${tn.w}" height="${tn.h}" rx="6" fill="#1b0d0f" stroke="${hot?'#f4b02e':'#3a1f21'}"/>
        <text x="${tn.x+9}" y="${tn.y+15}" fill="${c?'#f3e9d8':'#7a6a58'}" font-size="11">${t}</text>
        <text x="${tn.x+tn.w-9}" y="${tn.y+15}" fill="${r.errors?'#e0715f':'#a8987f'}" font-size="10.5" text-anchor="end">${c?c+' × '+ms(r.ms/c):'not used yet'}</text></g>`});
    if(!s.tools.length)svg+=`<text x="${TX}" y="${n.y+48}" fill="#7a6a58" font-size="10.5">no tool used by this studio yet</text>`});
  // the knowledge pack (RAG)
  {const n=nodes.rag,R=S.rag||{},pk=R.pack,tot=pk?Object.values(pk.collections).reduce((a,b)=>a+b,0):0;
   svg+=box('rag',`<text x="${n.x+12}" y="${n.y+22}" fill="#f3e9d8" font-size="12.5" font-weight="600">Knowledge pack (RAG)</text>
    <text x="${n.x+12}" y="${n.y+40}" fill="#a8987f" font-size="10.5">${pk?k(tot)+' chunks · '+Object.keys(pk.collections).length+' collections':'no pack loaded'}</text>
    <text x="${n.x+12}" y="${n.y+56}" fill="#7a6a58" font-size="10">${pk?String(pk.id).slice(0,30):''}</text>`,'stroke-dasharray="0"')}
  // ports
  M.T.ports.forEach(p=>{const n=nodes['port:'+p.id];let line='';
    if(p.id==='publisher'){const calls=Object.values(M.L.publisher).reduce((a,x)=>a+x.calls,0);line=`${calls} call${calls===1?'':'s'} · ${M.L.uploads} upload${M.L.uploads===1?'':'s'}`}
    else line=`${M.L.renderer.renders} render${M.L.renderer.renders===1?'':'s'}${M.L.renderer.renders?' · avg '+(M.L.renderer.seconds/M.L.renderer.renders).toFixed(0)+' s':''}`;
    svg+=box('port:'+p.id,`<text x="${n.x+12}" y="${n.y+22}" fill="#f3e9d8" font-size="12.5" font-weight="600">${p.label}</text><text x="${n.x+12}" y="${n.y+40}" fill="#a8987f" font-size="10.5">${line}</text><text x="${n.x+12}" y="${n.y+55}" fill="#7a6a58" font-size="10">${p.what.slice(0,38)}</text>`)});
  svg+='</svg>';el.innerHTML=svg;
  el.querySelectorAll('.nd').forEach(g=>g.onclick=()=>onSelect(g.dataset.id));
}

// ---------- the drawer: one node, or one call, in full ----------
const statusChip=s=>`<span class="chip ${s==='done'?'ok':s==='failed'?'bad':'wait'}">${esc(s)}</span>`;
function callsTable(list,sel){
  if(!list.length)return '<div class="meta">no calls yet</div>';
  return `<table><thead><tr><th>#</th><th>who</th><th>about</th><th class="n">time</th><th class="n">tokens</th><th></th></tr></thead><tbody>`+list.map((c,i)=>
    `<tr class="click callrow ${sel===c.id?'sel':''}" data-call="${esc(c.id)}"><td class="meta">${i+1}</td><td><span class="dot" style="background:${COLOR[c.who]||COLOR[c.type]||'#888'}"></span>${esc(c.who)}</td>
    <td class="meta">${esc((c.node||'').replace(/^.*[:]/,''))}</td><td class="n">${c.end==null?'…':ms(c.end-c.start)}</td><td class="n">${c.tokens?k(c.tokens):''}</td><td>${statusChip(c.status)}</td></tr>`).join('')+'</tbody></table>';
}
function callView(d){
  if(!d)return '';
  const h=[`<div class="row"><b>${esc(d.who)}</b>${statusChip(d.status)}<span class="meta">${esc(d.node||'')}</span><span class="grow"></span><button class="btn" id="backBtn">← back</button></div>`];
  const kv=[['kind',d.type],['took',d.end!=null?ms(d.end-d.start):'working'],['model',d.model],['turns',d.turns],
    ['tokens in / out / cached',d.tokens_in!=null?`${d.tokens_in} / ${d.tokens_out} / ${d.tokens_cached}`:null],['tools allowed',(d.tools_allowed||[]).map(t=>String(t).replace('mcp__','').replace('__','.')).join(', ')||null],
    ['tools hidden from it',d.tools_hidden],['retries',(d.retries||[]).join('; ')||null],['error',d.error],['method',d.method],['what',d.what]].filter(([,v])=>v!=null&&v!=='');
  h.push('<dl class="kv" style="margin-top:8px">'+kv.map(([a,b])=>`<dt>${a}</dt><dd>${esc(b)}</dd>`).join('')+'</dl>');
  if(d.task!=null)h.push('<h3>What it was given</h3>'+jbox(d.task));
  if(d.input!=null)h.push('<h3>Input</h3>'+jbox(d.input));
  if((d.tool_calls||[]).length)h.push('<h3>Tools it used</h3>'+d.tool_calls.map(t=>`<div style="margin-bottom:8px"><div class="row"><span class="chip">${esc(t.server)}.${esc(t.tool)}</span><span class="meta">${ms(t.took_ms)}${t.error?' · error':''}</span></div>
     <div class="meta">input</div>${jbox(t.input)}<div class="meta" style="margin-top:4px">result</div>${jbox(t.output)}</div>`).join(''));
  if((d.thoughts||[]).length)h.push('<h3>What it said</h3>'+d.thoughts.map(x=>`<div class="jv">${esc(x)}</div>`).join(''));
  if(d.output!=null)h.push('<h3>'+(d.type==='agent'?'Its answer':'Result')+'</h3>'+jbox(d.output));
  return h.join('');
}
function drawerHtml(S,sel){
  const M=sysModel(S),L=M.L,calls=S.calls;
  if(!sel)return `<div class="empty">Click any box on the map to open it: an agent, a server, a tool, a phase, a port.<br>Then click a call to see exactly what went in and what came out.</div>`;
  const [type,id]=sel==='orch'?['orch','']:sel.split(/:(.*)/s);
  if(type==='orch'){const tot=Object.values(L.agents).reduce((a,v)=>a+(v.tokens_in||0)+(v.tokens_out||0),0);
    return `<h2>Orchestrator</h2><div class="meta">Plain code. It starts every phase, calls MCP servers itself (0 tokens), runs the agents one judgment at a time, checks and repairs, renders and hands over. An agent never decides what happens next.</div>
      <dl class="kv" style="margin-top:8px"><dt>phases</dt><dd>${M.T.phases.length}</dd><dt>agent calls</dt><dd>${Object.values(L.agents).reduce((a,v)=>a+v.calls,0)}</dd><dt>code calls to MCP</dt><dd>${L.tools.reduce((a,t)=>a+t.by_code,0)}</dd><dt>tokens</dt><dd>${k(tot)}</dd><dt>renders</dt><dd>${L.renderer.renders}</dd></dl>
      <h3>Everything it did, in order</h3>`+callsTable(calls.filter(c=>c.type!=='tool'),SYS.callId)}
  if(type==='phase'){const p=M.T.phases.find(x=>x.id===id),r=L.phases.find(x=>x.phase===id)||{};
    const mine=calls.filter(c=>r.start_ms!=null&&c.start>=r.start_ms&&(r.end_ms==null||c.start<=r.end_ms)&&c.type!=='tool');
    return `<h2>Phase: ${esc(p.label)}</h2><div class="meta">${esc(p.what)}</div><dl class="kv" style="margin-top:8px"><dt>status</dt><dd>${esc(r.status||'pending')}</dd><dt>time</dt><dd>${r.seconds!=null?mmss(r.seconds):'–'}</dd><dt>calls inside</dt><dd>${mine.length}</dd></dl><h3>Calls in this phase</h3>`+callsTable(mine,SYS.callId)}
  if(type==='agent'){const a=M.T.agents.find(x=>x.name===id),v=L.agents[id]||{},mine=calls.filter(c=>c.type==='agent'&&c.who===id);
    return `<h2><span class="dot" style="background:${COLOR[id]}"></span>Agent: ${esc(id)}</h2><dl class="kv"><dt>model</dt><dd>${esc(a.model)}</dd><dt>may use</dt><dd>${a.tools.map(t=>`<span class="chip">${esc(t.server)}.${esc(t.tool)}</span>`).join('')||'<span class="meta">no tools: a pure judgment</span>'}${a.builtin.map(t=>`<span class="chip">built-in ${esc(t)}</span>`).join('')}</dd>
      <dt>calls</dt><dd>${v.calls||0}${v.running?' ('+v.running+' working)':''}</dd><dt>tokens</dt><dd>${k((v.tokens_in||0)+(v.tokens_out||0))} (cached ${k(v.tokens_cached||0)})</dd><dt>avg time</dt><dd>${v.calls?ms(v.seconds*1000/v.calls):'–'}</dd><dt>retries · failed</dt><dd>${v.retries||0} · ${v.failed||0}</dd><dt>system prompt</dt><dd>${a.prompt_chars} characters <button class="btn" id="promptBtn" data-agent="${esc(id)}">show</button></dd></dl><div id="promptBox"></div>
      <h3>Its calls</h3>`+callsTable(mine,SYS.callId)}
  if(type==='server'){const sv=M.servers.find(s=>s.name===id)||{tools:[]};
    return `<h2>MCP server: ${esc(id)}</h2><div class="meta">A tool server the agents and the orchestrator talk to over stdio. Agents may only use the tools on their own list; the rest are hidden from them.</div>
      <h3>Tools</h3><table><thead><tr><th>tool</th><th class="n">agents</th><th class="n">code</th><th class="n">avg</th><th class="n">max</th><th class="n">errors</th></tr></thead><tbody>`+sv.tools.map(t=>{const r=M.trow(id,t),c=r.by_agents+r.by_code;return `<tr class="click toolrow" data-tool="tool:${esc(id)}.${esc(t)}"><td>${esc(t)}</td><td class="n">${r.by_agents}</td><td class="n">${r.by_code}</td><td class="n">${c?ms(r.ms/c):'–'}</td><td class="n">${ms(r.ms_max||null)}</td><td class="n">${r.errors}</td></tr>`}).join('')+'</tbody></table><h3>Calls</h3>'+callsTable(calls.filter(c=>(c.type==='code'||c.type==='tool')&&c.who.startsWith(id+'.')),SYS.callId)}
  if(type==='tool'){const r=M.trow(id.split('.')[0],id.split('.').slice(1).join('.')),mine=calls.filter(c=>(c.type==='code'||c.type==='tool')&&c.who===id);
    return `<h2>Tool: ${esc(id)}</h2><dl class="kv"><dt>called by agents</dt><dd>${r.by_agents}</dd><dt>called by code</dt><dd>${r.by_code}</dd><dt>total time</dt><dd>${ms(r.ms)}</dd><dt>slowest</dt><dd>${ms(r.ms_max||null)}</dd><dt>errors</dt><dd>${r.errors}</dd></dl><h3>Every call</h3>`+callsTable(mine,SYS.callId)}
  if(sel==='rag')return ragDrawer(S);
  if(type==='port'){const mine=calls.filter(c=>id==='publisher'?(c.type==='publisher'||c.type==='upload'):c.type==='render');
    return `<h2>${id==='publisher'?'Publisher API':'Reels renderer'}</h2><div class="meta">${id==='publisher'?'The only place drafts leave this Mac. It makes drafts, never posts.':'Turns a spec into a video or pictures with the reels engine, on this Mac, one at a time.'}</div>`+
      (id==='publisher'?`<h3>By method</h3><table><tbody>`+Object.entries(L.publisher).map(([m,v])=>`<tr><td>${esc(m)}</td><td class="n">${v.calls}×</td><td class="n">${ms(v.ms/Math.max(1,v.calls))} avg</td></tr>`).join('')+'</tbody></table>':'')+'<h3>Calls</h3>'+callsTable(mine,SYS.callId)}
  return '';
}
function wireDrawer(el,S,ctx){
  el.querySelectorAll('.callrow').forEach(r=>r.onclick=()=>ctx.openCall(r.dataset.call));
  el.querySelectorAll('.toolrow').forEach(r=>r.onclick=()=>ctx.select(r.dataset.tool));
  const pb=el.querySelector('#promptBtn');if(pb)pb.onclick=async()=>{const t=await (await fetch('/api/system/agent/'+pb.dataset.agent+'/prompt')).text();el.querySelector('#promptBox').innerHTML=`<div class="jv" style="white-space:pre-wrap">${esc(t)}</div>`};
  const bb=el.querySelector('#backBtn');if(bb)bb.onclick=()=>ctx.openCall(null);
}
function renderAllCalls(el,S,ctx){
  const types=['all','agent','tool','code','publisher','render','upload'];
  let list=S.calls.filter(c=>SYS.filter==='all'||c.type===SYS.filter);const t=SYS.text.toLowerCase();if(t)list=list.filter(c=>(c.who+' '+c.node).toLowerCase().includes(t));
  el.innerHTML=`<div class="tabs">${types.map(x=>`<button class="tab ${SYS.filter===x?'on':''}" data-f="${x}">${x}${x==='all'?' ('+S.calls.length+')':' ('+S.calls.filter(c=>c.type===x).length+')'}</button>`).join('')}<input id="callSearch" placeholder="filter…" value="${esc(SYS.text)}" style="background:#0e0506;border:1px solid var(--line);border-radius:8px;color:var(--ink);padding:3px 10px;font:inherit;font-size:12px"></div>
   <div style="max-height:380px;overflow:auto">${callsTable(list.slice(-300),SYS.callId)}</div>${list.length>300?'<div class="meta">showing the latest 300</div>':''}`;
  el.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{SYS.filter=b.dataset.f;ctx.redraw()});
  const s=el.querySelector('#callSearch');s.oninput=()=>{SYS.text=s.value;clearTimeout(SYS._t);SYS._t=setTimeout(ctx.redraw,200)};
  el.querySelectorAll('.callrow').forEach(r=>r.onclick=()=>ctx.openCall(r.dataset.call));
}

// ---------- RAG: where the agent system uses retrieval ----------
function ragDrawer(S){
  const R=S.rag||{},pk=R.pack;
  return `<h2>Knowledge pack (RAG)</h2><div class="meta">What the studio retrieves its evidence from. Built every Sunday by the ML run, searched by the dk_intel MCP servers. See the section "Where the agents use RAG" on this page.</div>`+(pk?
   `<dl class="kv" style="margin-top:8px"><dt>pack</dt><dd class="mono">${esc(pk.id)}</dd><dt>built</dt><dd>${esc(String(pk.built_at||'').slice(0,16).replace('T',' '))} UTC</dd><dt>from pull / market</dt><dd class="mono">${esc(pk.pull)} / ${esc(pk.market)}</dd>
    <dt>embedder</dt><dd>${esc(pk.embedder)} (${pk.dim} dimensions)</dd><dt>search</dt><dd>hybrid (vector + words), re-read by a cross-encoder reranker</dd><dt>duplicates collapsed</dt><dd>${pk.duplicates_collapsed??'–'}</dd></dl>
    <h3>Collections</h3><table><tbody>${Object.entries(pk.collections).map(([a,b])=>`<tr><td>${esc(a)}</td><td class="n">${b.toLocaleString()}</td></tr>`).join('')}</tbody></table>`:'<div class="empty">No knowledge pack is active on this Mac.</div>');
}
function renderRag(el,S){
  const R=S.rag||{},pk=R.pack,rt=R.retrieval,us=R.use;
  const box=(n,title,body,on)=>`<div style="flex:1 1 210px;min-width:200px;background:#271416;border:1.5px solid ${on?'#3f8a60':'#3a1f21'};border-radius:12px;padding:11px 13px"><div class="meta" style="letter-spacing:.08em;text-transform:uppercase">${n}</div><b>${title}</b><div style="margin-top:6px;font-size:12.5px">${body}</div></div>`;
  const arrow='<div style="align-self:center;color:#7a5a3a;font-size:22px">→</div>';
  const cols=pk?Object.entries(pk.collections).map(([a,b])=>`<span class="chip">${esc(a)} · ${b.toLocaleString()}</span>`).join(''):'<span class="meta">no pack</span>';
  const parts=rt?Object.entries(rt.parts).map(([a,b])=>`<tr><td>${esc(a)}</td><td class="n">${b.n}</td><td class="meta">${esc(b.from)}</td></tr>`).join(''):'';
  el.innerHTML=`<div class="meta" style="margin-bottom:10px;font-size:13px">The agents do <b>not</b> search the knowledge pack themselves. The orchestrator's code retrieves once per run, the agents get the evidence with citation ids inside their task, and code throws away any citation that is not in the evidence.</div>
  <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:stretch">
   ${box('1 · Build','Knowledge pack (weekly)',`Step "Rebuild the knowledge pack" of the Sunday ML run: clean, mask personal data, chunk, embed, gate.<div style="margin-top:6px">${cols}</div>${pk?`<div class="meta" style="margin-top:6px">${esc(pk.embedder)} · ${esc(String(pk.id).slice(3,18))}</div>`:''}`,!!pk)}${arrow}
   ${box('2 · Retrieve','Orchestrator → grounding_pack',rt?`One call to <span class="mono">${esc(rt.tool)}</span> in the inputs phase${rt.took_ms?` (${ms(rt.took_ms)})`:''}: hybrid search over the pack, reranked.<table style="margin-top:6px"><tbody>${parts}</tbody></table><div class="meta">${rt.cite_ids} citation ids come back</div>`:'Happens in the inputs phase of a run (not yet, or no run).',!!rt)}${arrow}
   ${box('3 · Agents read','Evidence inside the task',`<b>strategist</b> gets the whole pack and writes each angle with the <span class="mono">sources</span> it rests on.<br><b>copywriter</b> gets the cited quotes and posts, plus our best posts as voice examples.<br><b>adapter</b> and <b>designer</b> get the same cited evidence with the angle and the brand rules.`,!!us&&us.angled>0)}${arrow}
   ${box('4 · Code checks','Citations and facts',`Only citations that exist in the pack survive (<span class="mono">normalise</span>). Prices, hours and claims come from <span class="mono">get_facts</span>; every caption goes through <span class="mono">evaluate_drafts</span>.${us?`<div style="margin-top:6px"><b>${us.with_sources} of ${us.angled}</b> angles cite a source · ${us.citations} citations · ${us.distinct} distinct</div>`:''}`,!!us&&us.with_sources>0)}
  </div>`;
}
