// The ML layer: the 15 steps of the Sunday run with what each did, and the 7 models with every number behind them.
// Needs common.js. Data: /api/ml (see mlview.py).

const ML={sel:null};
const GROUPS=[['Data','wait pull market ledger'],['The 7 models','train mine community occasions funnel creators'],['Judge','judge'],['Deploy','package upload knowledge report']];
const ST={done:['ok','done'],running:['run','working'],failed:['bad','failed'],skipped:['','skipped'],pending:['','waiting']};
const gateIcon=s=>s==='pass'?'✓':s==='fail'?'✗':'⏳';
const gateCls=s=>s==='pass'?'ok':s==='fail'?'bad':'wait';

function renderMlFlow(el,M,onSelect){
  const byKey=Object.fromEntries(M.steps.map(s=>[s.key,s]));
  el.innerHTML=`<div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;align-items:start">`+GROUPS.map(([g,keys],gi)=>{
    const steps=keys.split(' ').map(x=>byKey[x]).filter(Boolean),done=steps.filter(s=>s.status==='done').length;
    return `<div><div class="meta" style="margin-bottom:6px;letter-spacing:.08em;text-transform:uppercase">${g} <span style="float:right">${done}/${steps.length}</span></div>`+steps.map(s=>{
      const [c,t]=ST[s.status]||['',s.status],on=ML.sel===s.key;
      return `<div class="mlstep ${s.status}" data-step="${s.key}" style="cursor:pointer;margin-bottom:8px;padding:9px 11px;border-radius:10px;background:#271416;border:${on?2:1.2}px solid ${on?'#f4b02e':s.status==='running'?'#f4b02e':s.status==='done'?'#3f8a60':s.status==='failed'?'#e0715f':'#3a1f21'};${s.status==='running'?'animation:blink 1.3s infinite':''}">
        <div class="row" style="gap:6px"><b style="font-size:13px">${esc(s.label)}</b><span class="grow"></span><span class="pill ${c}" style="font-size:10.5px;padding:0 8px">${t}</span></div>
        <div class="meta" style="margin-top:2px">${s.seconds!=null?ms(s.seconds*1000):''}${s.note?(s.seconds!=null?' · ':'')+esc(String(s.note).slice(0,70)):''}</div></div>`}).join('')+'</div>'}).join('')+'</div>';
  el.querySelectorAll('.mlstep').forEach(n=>n.onclick=()=>onSelect(n.dataset.step));
}
function stepHtml(M,key){
  const s=M.steps.find(x=>x.key===key);if(!s)return '<div class="empty">Click a step to see what it does and what it did in this run.</div>';
  const det=Object.entries(s.details||{});
  return `<h2>${esc(s.label)}</h2><div class="row"><span class="pill ${(ST[s.status]||[''])[0]}">${(ST[s.status]||['',s.status])[1]}</span><span class="meta">${s.seconds!=null?ms(s.seconds*1000):''}${s.started_at?' · started '+esc(s.started_at.slice(11,19))+' UTC':''}</span></div>
   <h3>What this step does</h3><div class="meta" style="font-size:13px;color:var(--ink)">${esc(s.desc)}</div>
   ${s.note?`<h3>Result</h3><div>${esc(s.note)}</div>`:''}
   <h3>What it recorded in this run</h3>${det.length?'<dl class="kv">'+det.map(([a,b])=>`<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join('')+'</dl>':'<div class="meta">nothing recorded yet</div>'}`;
}

function ciBar(v,ci,lo=0.4,hi=0.9){
  if(v==null)return '';const x=a=>Math.max(0,Math.min(100,(a-lo)/(hi-lo)*100));
  return `<div style="position:relative;height:10px;background:#0e0506;border-radius:5px;border:1px solid var(--line);width:180px;display:inline-block;vertical-align:middle">
   ${ci?`<i style="position:absolute;left:${x(ci[0])}%;width:${x(ci[1])-x(ci[0])}%;top:2px;bottom:2px;background:#7a5a3a;border-radius:3px"></i>`:''}
   <i style="position:absolute;left:${x(v)}%;top:-2px;bottom:-2px;width:3px;background:#f4b02e"></i><i style="position:absolute;left:${x(0.5)}%;top:0;bottom:0;width:1px;background:#a8987f"></i></div>`}

function renderMlModels(el,M,onOpen){
  el.innerHTML='<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px">'+M.models.map(m=>{
    const r=m.run,v=r&&r.verdict,t=r&&r.challenger&&r.challenger.test||{};
    const gates=(r&&r.gates)||[];
    return `<div class="card mlmodel" data-m="${esc(m.name)}" style="cursor:pointer;background:var(--card2)">
     <div class="row"><b>${esc(m.name.replace(/_/g,' '))}</b><span class="pill">${m.kind}</span><span class="grow"></span>${v?`<span class="pill ${v==='pass'?'ok':v==='fail'?'bad':''}">${esc(v)}</span>`:r?'<span class="pill">not judged yet</span>':'<span class="pill">no run yet</span>'}</div>
     <div class="meta" style="margin:6px 0">${esc(m.question||'')}</div>
     ${r?`<div class="meta">version <span class="mono">${esc(r.version||'')}</span>${r.split?` · trained on ${r.split.train??'?'}, tested on ${r.split.test??'?'}`:''}</div>
     ${t.balanced_accuracy!=null?`<div style="margin-top:6px"><span class="meta">${m.kind==='scorer'?'balanced accuracy':'quality'} </span><b>${t.balanced_accuracy}</b> ${ciBar(t.balanced_accuracy,t.balanced_accuracy_ci95)}</div>`:''}
     <div style="margin-top:8px">${gates.map(g=>`<span class="chip ${gateCls(g.status)}" title="${esc(g.detail)}">${gateIcon(g.status)} ${esc(g.gate)}</span>`).join('')}</div>`:''}
     <div class="meta" style="margin-top:6px">click for every number</div></div>`}).join('')+'</div>';
  el.querySelectorAll('.mlmodel').forEach(n=>n.onclick=()=>onOpen(n.dataset.m));
}

function confMatrix(c){
  if(!c)return '';const tot=c.flat().reduce((a,b)=>a+b,0)||1,cell=(v,good)=>`<td style="text-align:center;background:${good?'rgba(127,197,154,'+(.15+.6*v/tot)+')':'rgba(224,113,95,'+(.1+.6*v/tot)+')'};width:70px;height:46px"><b>${v}</b></td>`;
  return `<table style="width:auto"><tr><td></td><th>predicted no</th><th>predicted yes</th></tr><tr><th>really no</th>${cell(c[0][0],1)}${cell(c[0][1],0)}</tr><tr><th>really yes</th>${cell(c[1][0],0)}${cell(c[1][1],1)}</tr></table>`;
}
function metricRows(label,t){
  if(!t||!Object.keys(t).length)return '';
  return `<tr><td>${label}</td><td class="n">${t.n??''}</td><td class="n">${t.balanced_accuracy??''}${t.balanced_accuracy_ci95?` <span class="meta">(${t.balanced_accuracy_ci95.join(' to ')})</span>`:''}</td><td class="n">${t.roc_auc??''}</td><td class="n">${t.brier??''}</td></tr>`;
}
function modelHtml(d){
  const c=d.card,r=c.run||{},rep=d.report||{},ch=r.challenger||{},cp=r.champion||{};
  const h=[`<div class="row"><h1>${esc(c.name.replace(/_/g,' '))}</h1><span class="pill">${c.kind}</span>${r.verdict?`<span class="pill ${r.verdict==='pass'?'ok':'bad'}">${esc(r.verdict)}</span>`:''}<span class="grow"></span><button class="btn" id="mClose">close ✕</button></div>`,
   `<div class="meta" style="margin:6px 0 10px">${esc(c.question||'')}</div>`,
   `<dl class="kv">${[['reads',c.data],['label',c.label],['prediction time',c.prediction_time],['version',r.version],['warehouse pull',r.pull_id],['market snapshot',r.market_id],['trained',r.created_at],
     ['train / test posts',r.split?`${r.split.train} / ${r.split.test}${r.split.cutoff?' (test = posts after '+String(r.split.cutoff).slice(0,10)+')':''}`:null]].filter(([,v])=>v).map(([a,b])=>`<dt>${a}</dt><dd>${esc(b)}</dd>`).join('')}</dl>`];
  if((r.leaderboard||[]).length)h.push(`<h3>Candidates tried (cross-validated on the training posts)</h3><table><thead><tr><th>candidate</th><th>features</th><th>params</th><th class="n">cv balanced acc.</th><th class="n">threshold</th></tr></thead><tbody>`+r.leaderboard.map((x,i)=>`<tr><td>${i===0?'★ ':''}${esc(x.name)}</td><td>${esc(x.features)}</td><td class="mono meta">${esc(JSON.stringify(x.params))}</td><td class="n">${x['cv_balanced_accuracy@0.5']??''}</td><td class="n">${x.cv_threshold??''}</td></tr>`).join('')+'</tbody></table>');
  if(ch.test&&Object.keys(ch.test).length)h.push(`<h3>The new model against the current one, on posts neither has seen</h3><table><thead><tr><th></th><th class="n">posts</th><th class="n">balanced accuracy</th><th class="n">AUC</th><th class="n">Brier</th></tr></thead><tbody>${metricRows('new: '+(ch.model||''),ch.test)}${metricRows('current: '+(cp.model||''),cp.test)}</tbody></table>
    ${r.gain!=null?`<div class="meta" style="margin-top:6px">gain ${r.gain>0?'+':''}${r.gain}${r.gain_ci95?' (95% range '+r.gain_ci95.join(' to ')+')':''}</div>`:''}
    <div class="row" style="align-items:flex-start;margin-top:10px">${ch.test.confusion_matrix?`<div><div class="meta">what it got right and wrong (new model)</div>${confMatrix(ch.test.confusion_matrix)}</div>`:''}
    ${ch.test.per_platform?`<div><div class="meta">by platform</div><table style="width:auto"><tbody>`+Object.entries(ch.test.per_platform).map(([p,v])=>`<tr><td>${esc(p)}</td><td class="n">${v.n} posts</td><td class="n">${v.balanced_accuracy}</td></tr>`).join('')+'</tbody></table></div>':''}</div>`);
  if((r.gates||[]).length)h.push('<h3>The judge: every gate this model had to pass</h3><table><tbody>'+r.gates.map(g=>`<tr><td style="width:26px"><span class="chip ${gateCls(g.status)}">${gateIcon(g.status)}</span></td><td>${esc(g.gate)}</td><td class="meta">${esc(g.detail)}</td></tr>`).join('')+'</tbody></table>');
  if((r.top_complaints||[]).length)h.push('<h3>Top of its output</h3><div>'+r.top_complaints.map(x=>`<span class="chip">${esc(x)}</span>`).join('')+'</div>');
  if((d.insights_keys||[]).length)h.push(`<h3>What it produced (insights.json)</h3><div>${d.insights_keys.map(x=>`<span class="chip">${esc(x.key)} · ${k(x.size)} chars</span>`).join('')}</div><button class="btn" id="insBtn" style="margin-top:6px">open the output</button><div id="insBox"></div>`);
  if((d.files||[]).length)h.push(`<h3>Files of this run</h3><div class="meta">${d.files.map(esc).join(' · ')}</div>`);
  h.push('<div id="retrain" style="margin-top:16px"></div>');
  if(!d.report&&!r.version)h.push('<div class="empty">No run yet for this model.</div>');
  return h.join('');
}
async function openModel(name,demoCard){
  const m=document.createElement('div');m.className='modal';m.innerHTML='<div class="panel"><div class="empty">loading…</div></div>';document.body.appendChild(m);
  const close=()=>m.remove();m.onclick=e=>{if(e.target===m)close()};
  let d;try{d=demoCard?{card:demoCard,report:null,insights_keys:[],files:[]}:await api('/api/ml/model/'+name)}catch(e){m.firstChild.innerHTML='<div class="empty">could not load</div>';return}
  m.firstChild.innerHTML=modelHtml(d);m.querySelector('#mClose').onclick=close;
  if(!demoCard)try{const o=await api('/api/ops'),a=o.actions.find(x=>x.group==='model'&&x.models.includes(name));
    if(a){const box=m.querySelector('#retrain');box.innerHTML=`<button class="btn" id="rtBtn" ${o.studio_running||o.ml_running||(o.current&&o.current.state==='running')?'disabled':''}>Retrain ${esc(a.models.join(' and ').replace(/_/g,' '))} now</button> <span class="meta">a candidate only: nothing is uploaded or deployed (${esc(a.cost)})</span><div id="rtMsg" class="meta"></div>`;
      box.querySelector('#rtBtn').onclick=async()=>{if(!confirm(a.label+'\n\n'+a.what+'\n\nCost: '+a.cost+'. Start?'))return;try{await post('/api/ops/start',{action:a.id,confirm:true});box.querySelector('#rtMsg').textContent='started: watch it on the ML pipeline page'}catch(e){box.querySelector('#rtMsg').textContent=e.message}}}}catch(e){}
  const b=m.querySelector('#insBtn');if(b)b.onclick=async()=>{const t=await (await fetch(`/api/ml/model/${name}/insights`)).text();let v;try{v=JSON.parse(t)}catch(e){v=t}m.querySelector('#insBox').innerHTML=jbox(v)};
}
function mlHeader(M){
  const r=M.run,done=M.steps.filter(s=>s.status==='done').length;
  return {title:r.id?`ML pipeline · run ${r.id}`:'ML pipeline',state:r.running?'running':r.stalled?'cut off':(r.state||'no run yet'),cls:r.running?'run':r.state==='finished'?'ok':r.stalled||r.state==='failed'?'bad':'',percent:M.percent,done,total:M.steps.length,current:r.current_step};
}
