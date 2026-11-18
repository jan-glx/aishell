'use strict';
const holder = document.getElementById('history');
const status = document.getElementById('status');
let lastPayload = '';
let loaded = [];
let nextBefore = null;
const olderButton = document.getElementById('older');
const colors = ['#252525','#ef6868','#7bc48a','#e6c17c','#78baf2','#c49ff2','#6ac7c5','#ecf0f3',
                '#8895a7','#ff9292','#9be3a3','#f8d39c','#9ed5ff','#e0b8ff','#87e3de','#ffffff'];

function appendAnsi(parent, value) {
  // Interpret harmless SGR color sequences; never interpret command/output as HTML.
  const rx = /\x1b\[([0-9;]*)m|\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g;
  let at = 0, match, fg = '', bold = false;
  function emit(raw) {
    if (!raw) return;
    const span = document.createElement('span');
    span.textContent = raw.replace(/\r(?!\n)/g, '\n').replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '');
    if (fg) span.style.color = fg;
    if (bold) span.style.fontWeight = 'bold';
    parent.appendChild(span);
  }
  while ((match = rx.exec(value)) !== null) {
    emit(value.slice(at, match.index));
    at = rx.lastIndex;
    if (match[1] === undefined) continue;
    const codes = (match[1] || '0').split(';').map(Number);
    for (let i = 0; i < codes.length; i++) {
      let code = codes[i];
      if (code === 0) {fg = '';bold = false;}
      else if (code === 1) bold = true;
      else if (code === 22) bold = false;
      else if (code === 39) fg = '';
      else if (code >= 30 && code <= 37) fg = colors[code - 30];
      else if (code >= 90 && code <= 97) fg = colors[8 + code - 90];
      else if (code === 38 && codes[i+1] === 2 && i+4 < codes.length) {
        const [r,g,b] = codes.slice(i+2,i+5).map(n=>Math.min(255,Math.max(0,n)));
        fg = `rgb(${r},${g},${b})`;i += 4;
      } else if (code === 38 && codes[i+1] === 5 && i+2 < codes.length) {
        const n = codes[i+2];
        if (n < 16) fg=colors[n];
        else if (n >= 232) {const v = 8 + (n-232)*10;fg=`rgb(${v},${v},${v})`;}
        else {const m=n-16;const q=k=>k===0?0:55+40*k;
          fg=`rgb(${q(Math.floor(m/36))},${q(Math.floor(m/6)%6)},${q(m%6)})`;}
        i+=2;
      }
    }
  }
  emit(value.slice(at));
}

function render(commands) {
  const nearBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 130;
  const frag = document.createDocumentFragment();
  if (!commands.length) {
    const empty = document.createElement('p'); empty.className='empty';
    empty.textContent='No runCommand calls logged yet.'; frag.appendChild(empty);
  }
  for (const c of commands) {
    const card = document.createElement('section');card.className='command';
    const meta = document.createElement('div');meta.className='meta';
    const stamp=document.createElement('span');stamp.textContent=new Date(c.started*1000).toLocaleString();
    const id=document.createElement('span');id.textContent='#'+c.id+' · '+c.unix_user;
    const state=document.createElement('span');state.className='state';
    if (c.ended === null) {state.textContent='● Running';state.classList.add('running');}
    else {state.textContent=c.error ? c.error : c.timed_out ? 'Timed out' : c.truncated ? 'Output limit' : 'Exit '+c.exit_code;
      if (c.error || c.exit_code !== 0) state.classList.add('failure');}
    meta.append(stamp,id,state);card.appendChild(meta);
    const pre=document.createElement('pre');pre.className='terminal';
    const prompt=document.createElement('span');prompt.className='prompt';prompt.textContent=c.unix_user+'@aishell $ ';
    const cmd=document.createElement('span');cmd.className='cmd';cmd.textContent=c.command+'\n';
    pre.append(prompt,cmd);
    for (const chunk of c.chunks) {
      const part=document.createElement('span');if(chunk.stream==='stderr')part.className='stderr';
      appendAnsi(part,chunk.content);pre.appendChild(part);
    }
    card.appendChild(pre);
    if(c.display_clipped) {const cut=document.createElement('div');cut.className='cut';
      cut.textContent='Only the latest 10,000 characters of output are displayed.';card.appendChild(cut);}
    frag.appendChild(card);
  }
  holder.replaceChildren(frag);
  if (nearBottom) window.scrollTo(0,document.documentElement.scrollHeight);
}

async function poll() {
  try {
    const response=await fetch('/command-log/events',{credentials:'same-origin',cache:'no-store'});
    if(response.status===401){window.location.href='/command-log';return;}
    if(!response.ok)throw Error('HTTP '+response.status);
    const data=await response.json();
    const snapshot=JSON.stringify(data.commands);
    if(snapshot!==lastPayload){
      lastPayload=snapshot;
      const newestIds = new Set(data.commands.map(x=>x.id));
      loaded = [...data.commands, ...loaded.filter(x=>!newestIds.has(x.id))];
      loaded.sort((a,b)=>a.id-b.id);
      render(loaded);
      if(nextBefore===null) nextBefore=data.next_before_id;
      olderButton.hidden=nextBefore===null;
    }
    status.textContent='● Live';
  } catch(err) {status.textContent='● Disconnected ('+err.message+')';}
  setTimeout(poll,1000);
}
poll();

olderButton.addEventListener('click', async () => {
  if(nextBefore===null) return;
  olderButton.disabled=true;
  try {
    const r=await fetch('/command-log/events?before_id='+encodeURIComponent(nextBefore),{credentials:'same-origin',cache:'no-store'});
    if(!r.ok) throw Error('HTTP '+r.status);
    const data=await r.json();
    const ids=new Set(loaded.map(x=>x.id));
    loaded=[...data.commands.filter(x=>!ids.has(x.id)),...loaded];
    nextBefore=data.next_before_id;
    render(loaded);
    olderButton.hidden=nextBefore===null;
  } catch(err) {status.textContent='● Older history failed ('+err.message+')';}
  finally {olderButton.disabled=false;}
});
