// Shared helpers for the operations pages (no dependencies, no network except the console's own /api).
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const k=n=>n>=1e6?(n/1e6).toFixed(2)+'M':n>=1e3?Math.round(n/1e3)+'k':String(Math.round(n||0));
const mmss=s=>{s=Math.max(0,Math.round(s));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
const ms=m=>m==null?'–':m<1000?Math.round(m)+' ms':(m/1000).toFixed(m<10000?1:0)+' s';
const COLOR={strategist:'#c792ea',director:'#82aaff',copywriter:'#f4b02e',adapter:'#ffcb6b',designer:'#7fc59a',curator:'#89ddff',analyst:'#f78c6c',
  render:'#f78c6c',upload:'#89ddff',code:'#a8987f',publisher:'#89ddff',tool:'#c792ea'};
const q=new URLSearchParams(location.search);
async function api(path){const r=await fetch(path);if(!r.ok)throw new Error(path+' '+r.status);return r.json()}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||('failed '+r.status));return j}

// A collapsible JSON viewer: objects and arrays fold, long text wraps. Returns HTML.
function jv(v,depth=0){
  if(v===null||v===undefined)return '<span class="b">null</span>';
  if(typeof v==='string'){return v.length>400&&depth>0?`<details><summary>${esc(v.slice(0,90))}… (${v.length} chars)</summary><span class="s">${esc(v)}</span></details>`:`<span class="s">${esc(v)}</span>`}
  if(typeof v==='number')return `<span class="n">${v}</span>`;
  if(typeof v==='boolean')return `<span class="b">${v}</span>`;
  const isArr=Array.isArray(v),keys=isArr?v.map((_,i)=>i):Object.keys(v);
  if(!keys.length)return isArr?'[]':'{}';
  const body=keys.map(key=>`<div>${isArr?'':`<span class="k">${esc(key)}</span>: `}${jv(v[key],depth+1)}</div>`).join('');
  return `<details ${depth<1?'open':''}><summary>${isArr?'['+keys.length+']':'{'+keys.length+'}'}</summary>${body}</details>`}
const jbox=v=>`<div class="jv">${jv(v)}</div>`;

// Poll a url on an interval; demo pages pass a tick counter. Returns a stop function.
function every(ms_,fn){let on=true;(async function loop(){if(on){try{await fn()}catch(e){console.warn(e)}setTimeout(loop,ms_)}})();return()=>{on=false}}
