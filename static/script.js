let projects=[], currentProject=null, config=null;
let isRunning=false, abortController=null;
let pipelineCost=0, pipelineTime=0, chatCalls=0;

let graphNodes=[], graphEdges=[];
let selectedNode=null, connectMode=false, connectFrom=null, deleteMode=false;
let dragNode=null, dragStart=null, prevToolsAgent='';
let graphStatus={};
let checkpoints=[];
let chatOrkMsg=null, chatAgentMsg=null;
let monitorState=null;
let asstHistory=[];
let importedSkills=[], builtinSkills=[];
let chatHistory=[];
let prevPage='/';

const NODE_W=146, NODE_H=46;
const PROJECT_NAME_RE=/^[a-zA-Z0-9_-]{1,64}$/;
let PROVIDERS={};
let MCP_PRESETS=[];

async function loadProviders(){
  try{
    const data=await api('GET','/api/settings/providers');
    const p={};
    (data||[]).forEach(pr=>{p[pr.key]=pr});
    PROVIDERS=p
  }catch(e){PROVIDERS={}}
}
async function loadMcpPresets(){
  try{
    const data=await api('GET','/api/settings/mcp_presets');
    MCP_PRESETS=data||[]
  }catch(e){MCP_PRESETS=[]}
}
function providerOptsHTML(selected){
  let h='<option value="">-- Select provider --</option>';
  for(const [k,v] of Object.entries(PROVIDERS)){h+='<option value="'+k+'"'+(selected===k?' selected':'')+'>'+v.label+'</option>'}
  return h
}
function applyProvider(providerKey,urlEl,modelEl){
  const p=PROVIDERS[providerKey];if(!p)return;
  if(urlEl)urlEl.value=p.url;
  if(modelEl)modelEl.value=p.model
}

// ===== ROUTING =====
function getHash(){const h=location.hash.slice(1)||'/';return h.startsWith('/')?h:'/'+h}
function navigate(hash){history.pushState(null,'','#'+hash);renderRoute()}
function renderRoute(){
  const hash=getHash();
  document.querySelectorAll('.view').forEach(v=>v.style.display='none');
  document.getElementById('header-actions').style.display='none';
  if(hash==='/'||hash===''){prevPage='/';document.getElementById('view-home').style.display='';renderHome()}
  else if(hash.startsWith('/graph/')){prevPage=hash;document.getElementById('view-graph').style.display='';loadGraph(decodeURIComponent(hash.slice(7).split('/')[0]))}
  else if(hash.startsWith('/chat/')){prevPage='/graph/'+encodeURIComponent(decodeURIComponent(hash.slice(6)));document.getElementById('view-chat').style.display='';loadChat(decodeURIComponent(hash.slice(6).split('/')[0]))}
  else if(hash==='/log'){document.getElementById('view-log').style.display='';loadLogView()}
  else navigate('/')
}
window.addEventListener('hashchange',renderRoute);
window.addEventListener('popstate',renderRoute);

async function loadLogView(){
  document.getElementById('header-badge').textContent='external calls';
  const container=document.getElementById('lv-entries');
  const count=document.getElementById('lv-count');
  const toolbar=document.getElementById('lv-toolbar');
  if(toolbar && !toolbar.dataset.built){
    toolbar.innerHTML=[
      ['all','All','#888'],['telegram','Telegram','#0088cc'],
      ['discord','Discord','#5865f2'],['whatsapp','WhatsApp','#25d366'],
      ['webhook','Webhook','#a855f7'],['mcp','MCP','#f59e0b']
    ].map(([k,l,c])=>`<button class="log-filter-btn" data-filter="${k}" style="padding:4px 10px;font-size:.7rem;border:1px solid ${c}55;background:${c}15;color:var(--text-primary);border-radius:999px;cursor:pointer">${l}</button>`).join('');
    toolbar.dataset.built='1';
    toolbar.querySelectorAll('.log-filter-btn').forEach(b=>b.addEventListener('click',()=>{
      toolbar.querySelectorAll('.log-filter-btn').forEach(x=>x.style.fontWeight='400');
      b.style.fontWeight='700';
      logFilter=b.dataset.filter;
      doRefresh()
    }))
  }
  let logFilter='all';
  const sourceMeta={
    telegram:{icon:'✈',color:'#0088cc',label:'Telegram'},
    discord:{icon:'▶',color:'#5865f2',label:'Discord'},
    whatsapp:{icon:'✉',color:'#25d366',label:'WhatsApp'},
    webhook:{icon:'🔗',color:'#a855f7',label:'Webhook'},
    mcp:{icon:'🧩',color:'#f59e0b',label:'MCP'},
    incoming:{icon:'←',color:'#3b82f6',label:'Incoming'}
  };
  const doRefresh=async()=>{
    try{
      const resp=await api('GET','/api/log');
      let entries=resp.log||[];
      if(logFilter!=='all') entries=entries.filter(e=>(e.source||'webhook')===logFilter);
      count.textContent=entries.length+' / '+resp.total+' calls';
      if(entries.length===0){
        container.innerHTML='<div style="text-align:center;padding:40px 20px;color:var(--text-muted)"><div style="font-size:2rem;margin-bottom:8px">📭</div><div style="font-size:.8rem">Nessuna chiamata esterna registrata.</div><div style="font-size:.68rem;margin-top:6px;opacity:.7">I messaggi ricevuti da Telegram, Discord, WhatsApp e i webhook appariranno qui in tempo reale.</div></div>';
        return
      }
      container.innerHTML=entries.map(e=>{
        const ts=new Date((e.ts||0)*1000).toLocaleTimeString();
        const src=e.source||'webhook';
        const meta=sourceMeta[src]||sourceMeta.incoming;
        const statusClass=e.status>=400?'log-error':(e.status>=200&&e.status<300?'log-ok':'log-info');
        const dir=e.direction==='out'?'→ out':'← in';
        const fromTxt=e.from?(' · da '+esc(e.from)):'';
        const projTxt=e.project?(' · '+esc(e.project)):'';
        const textPreview=e.text||e.payload||e.url||'';
        return `<div class="log-entry ${statusClass}" style="padding:8px 10px;border-bottom:1px solid var(--border);display:grid;grid-template-columns:auto auto 1fr auto;gap:8px;align-items:center">
          <span style="color:var(--text-muted);white-space:nowrap;font-size:.66rem">${ts}</span>
          <span style="display:inline-flex;align-items:center;gap:4px;background:${meta.color}22;color:${meta.color};padding:2px 8px;border-radius:999px;font-size:.62rem;font-weight:600;white-space:nowrap">${meta.icon} ${meta.label}</span>
          <div style="min-width:0">
            <div style="font-size:.66rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(dir)}${fromTxt}${projTxt}${e.channel_id?(' · '+esc(e.channel_id)):''}</div>
            <div style="font-size:.72rem;word-break:break-word;white-space:pre-wrap;margin-top:2px">${esc(textPreview).slice(0,200)||'<span style="opacity:.5">(no payload)</span>'}</div>
          </div>
          <span style="min-width:30px;text-align:right;font-weight:600;font-size:.68rem;flex-shrink:0">${e.status||'-'}</span>
        </div>`
      }).join('')
    }catch(err){
      container.innerHTML='<p style="color:var(--danger);text-align:center;padding:20px">Error: '+esc(err.message)+'</p>'
    }
  };
  document.getElementById('lv-refresh').onclick=doRefresh;
  document.getElementById('lv-clear').onclick=async()=>{
    await api('POST','/api/log/clear',{});
    doRefresh()
  };
  const arBtn=document.getElementById('lv-autorefresh');
  let arTimer=null;
  arBtn.onclick=()=>{
    if(arTimer){clearInterval(arTimer);arTimer=null;arBtn.textContent='⏳ Auto';arBtn.style.background=''}
    else{arTimer=setInterval(doRefresh,3000);arBtn.textContent='⏸ Stop';arBtn.style.background='var(--accent)';arBtn.style.color='#fff'}
  };
  await doRefresh()
}

// ===== API =====
async function api(method,url,body){
  const opts={method,headers:{}};
  if(body){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body)}
  const res=await fetch(url,opts);
  if(!res.ok){
    const t=await res.text();
    showToast(`Error ${res.status}: ${t.slice(0,200)}`,'error');
    throw new Error(`API ${res.status}: ${t.slice(0,200)}`)
  }
  try{return await res.json()}catch(e){
    const t=await res.text();
    throw new Error('Non-JSON response: '+t.slice(0,200))
  }
}
async function loadProjects(){projects=await api('GET','/api/projects');return projects}
async function loadProjectConfig(name){return await api('GET',`/api/projects/${encodeURIComponent(name)}/config`)}

// ===== HOME =====
let homeSearch='';
async function renderHome(){
  try {
  document.getElementById('header-badge').textContent='deepseek-v4-flash-free';
  const grid=document.getElementById('project-grid');grid.innerHTML='';
  await loadProjects();
  const filtered=homeSearch?projects.filter(p=>p.name.toLowerCase().includes(homeSearch.toLowerCase())):projects;
  if(filtered.length===0){grid.innerHTML='<p style="text-align:center;padding:60px;color:var(--text-muted);grid-column:1/-1">'+(homeSearch?'No projects match the search.':'No projects.')+'</p>';return}
  filtered.forEach(p=>{
    const isD=p.name==='default';
    const card=document.createElement('div');card.className='project-card';
    card.innerHTML=`<div class="project-card-header"><span class="project-card-icon">&#127916;</span><span class="project-card-name">${esc(p.name)}</span>${isD?'<span class="project-card-badge">default</span>':''}</div>
    <div class="project-card-info"><div class="pci-row"><span class="pci-label">Model:</span><span class="pci-val">${esc(p.model)}</span></div>
    <div class="pci-row"><span class="pci-label">API:</span><span class="pci-val">${esc(p.url)}</span></div></div>
    <div class="project-card-actions">
    <button class="btn btn-primary" data-name="${p.name}" data-action="open">Open graph</button>
    <button class="btn btn-secondary" data-name="${p.name}" data-action="chat">Chat</button>
    ${isD?'':`<button class="btn btn-secondary" data-name="${p.name}" data-action="rename">Rename</button>
     <button class="btn btn-secondary danger" data-name="${p.name}" data-action="delete">Delete</button>`}</div>`;
    grid.appendChild(card)
  });
  grid.querySelectorAll('[data-action="open"]').forEach(b=>b.addEventListener('click',()=>navigate(`/graph/${encodeURIComponent(b.dataset.name)}`)));
  grid.querySelectorAll('[data-action="chat"]').forEach(b=>b.addEventListener('click',()=>navigate(`/chat/${encodeURIComponent(b.dataset.name)}`)));
  grid.querySelectorAll('[data-action="rename"]').forEach(b=>b.addEventListener('click',async()=>{
    const n=prompt('New name:',b.dataset.name);
    if(n&&n.trim()&&n!==b.dataset.name){
      if(!PROJECT_NAME_RE.test(n.trim())){showToast('Invalid name','error');return}
      try{await api('PUT',`/api/projects/${encodeURIComponent(b.dataset.name)}`,{name:n.trim()});renderHome();showToast('Project renamed','success')}
      catch(e){showToast(e.message,'error')}
    }
  }));
  grid.querySelectorAll('[data-action="delete"]').forEach(b=>b.addEventListener('click',async()=>{
    if(!confirm(`Delete "${b.dataset.name}"?`))return;
    try{await api('DELETE',`/api/projects/${encodeURIComponent(b.dataset.name)}`);renderHome();showToast('Project deleted','success')}
    catch(e){showToast(e.message,'error')}
  }))
  } catch(e) {
    const grid=document.getElementById('project-grid');
    if(grid) grid.innerHTML='<p style="text-align:center;padding:60px;color:var(--danger)">Error: '+esc(e.message)+'</p>';
    showToast('Error loading projects: '+e.message,'error');
  }
}

// ===== GRAPH EDITOR =====
async function loadGraph(name){
  document.getElementById('header-actions').style.display='flex';
  document.getElementById('header-badge').textContent=name;
  document.getElementById('graph-project-title').textContent='\u25B6 '+esc(name);
  document.getElementById('header-project-name').textContent='@'+esc(name);
  document.querySelector('.logo-text').textContent=name;
  document.getElementById('chat-welcome-title').textContent=name;
  document.title='Stormo | '+esc(name);
  currentProject=await loadProjectConfig(name);config=currentProject;
  pipelineCost=0;pipelineTime=0;
  checkpoints=[];graphStatus={};
  document.getElementById('gt-status').textContent='Ready';
  document.getElementById('gbb-agents').textContent='0 agents';

  const g=config.graph||{nodes:[],edges:[]};
  graphNodes=(g.nodes||[]).map(n=>({...n}));
  graphEdges=(g.edges||[]).map(e=>({...e,type:e.type||'optional'}));

  // If no graph yet, build from agents
  if(graphNodes.length===0&&config.agents){
    const agents=Object.keys(config.agents).filter(a=>config.agents[a].enabled!==false);
    graphNodes=[{id:'orchestrator',type:'orchestrator',label:'Orchestrator',color:'#60a5fa',x:280,y:50}];
    agents.forEach((a,i)=>{const col=config.agents[a].color||'#888';graphNodes.push({id:a,type:'agent',label:'@'+a,color:col,x:80+(i%4)*130,y:160+Math.floor(i/4)*100})});
    graphEdges=agents.map(a=>({from:'orchestrator',to:a}));
    api('POST',`/api/projects/${encodeURIComponent(name)}/graph`,{nodes:graphNodes,edges:graphEdges}).catch(()=>{})
  }

  const chCount=graphNodes.filter(n=>n.type==='channel').length;
  document.getElementById('gbb-agents').textContent=Math.max(0,graphNodes.filter(n=>n.type==='agent').length)+' agents'+(chCount?', '+chCount+' channels':'');
  if(graphNodes.length===0)document.getElementById('graph-empty').style.display='';
  else document.getElementById('graph-empty').style.display='none';
  renderGraphSVG();
}

function renderGraphSVG(){
  const edgesEl=document.getElementById('graph-edges');
  const nodesEl=document.getElementById('graph-nodes');
  edgesEl.innerHTML='';
  // Edges
  graphEdges.forEach(e=>{
    const from=graphNodes.find(n=>n.id===e.from);
    const to=graphNodes.find(n=>n.id===e.to);
    if(!from||!to)return;
    const x1=from.x+NODE_W/2,y1=from.y+NODE_H,x2=to.x+NODE_W/2,y2=to.y;
    const s=graphStatus[e.from]||'idle';
    const cls=s==='active'||s==='streaming'?'active':s==='done'?'done':s==='error'?'error':'';
    const etype=e.type||'optional';
    const dx=x2-x1,dy=y2-y1;
    const cx1=x1+dx*0.15,cy1=y1+dy*0.4;
    const cx2=x2-dx*0.15,cy2=y2-dy*0.4;
    const d=`M${x1},${y1} C${cx1},${cy1} ${cx2},${cy2} ${x2},${y2}`;
    // Invisible hitbox (wider) behind the edge
    const hit=document.createElementNS('http://www.w3.org/2000/svg','path');
    hit.setAttribute('d',d);
    hit.setAttribute('class',deleteMode?'gedge-hit del':'gedge-hit');
    edgesEl.appendChild(hit);
    // Visible edge
    const p=document.createElementNS('http://www.w3.org/2000/svg','path');
    p.setAttribute('d',d);
    const clss='gedge'+(cls?' '+cls:'')+(etype==='required'?' required':'')+(e.loop?' loop':'');
    p.setAttribute('class',clss);
    p.setAttribute('style','pointer-events:none');
    edgesEl.appendChild(p);
    // Glow layer after the main edge (sibling selector)
    if(cls==='active'||cls==='streaming'){
      const glow=document.createElementNS('http://www.w3.org/2000/svg','path');
      glow.setAttribute('d',d);glow.setAttribute('class','gedge-glow');glow.setAttribute('style','pointer-events:none');
      edgesEl.appendChild(glow);
    }
    function onEdgeClick(ev){
      ev.stopPropagation();
      if(deleteMode){
        const idx=graphEdges.indexOf(e);
        if(idx>-1){graphEdges.splice(idx,1);deleteMode=false;document.getElementById('gt-remove-edges').innerHTML='\u274C Delete';document.getElementById('graph-svg').classList.remove('del-mode');queueGraphSave();renderGraphSVG()}
        return
      }
      if(ev.shiftKey){
        e.loop=!e.loop
      }else{
        e.type=e.type==='required'?'optional':'required'
      }
      queueGraphSave();renderGraphSVG()
    }
    hit.addEventListener('click',onEdgeClick);
    hit.addEventListener('contextmenu',ev=>{
      ev.preventDefault();ev.stopPropagation();
      const idx=graphEdges.indexOf(e);
      if(idx>-1){graphEdges.splice(idx,1);queueGraphSave();renderGraphSVG()}
    });
    // Show loop indicator as a label on the edge
    if(e.loop){
      const lbl=document.createElementNS('http://www.w3.org/2000/svg','text');
      const x=(x1+x2)/2,y=(y1+y2)/2;
      lbl.setAttribute('x',x);lbl.setAttribute('y',y-8);
      lbl.setAttribute('text-anchor','middle');lbl.setAttribute('font-size','9');
      lbl.setAttribute('fill','var(--success)');lbl.setAttribute('font-weight','bold');
      lbl.textContent='\u21BA loop';
      lbl.addEventListener('click',ev=>{ev.stopPropagation();e.loop=false;queueGraphSave();renderGraphSVG()});
      edgesEl.appendChild(lbl)
    }
    edgesEl.appendChild(p)
  });
  // Nodes
  nodesEl.innerHTML='';
  graphNodes.forEach(n=>{
    const g=document.createElementNS('http://www.w3.org/2000/svg','g');
    const st=graphStatus[n.id]||'idle';
    g.setAttribute('class','gnode'+(st!=='idle'?' '+st:'')+(selectedNode===n.id?' selected':''));
    g.setAttribute('transform',`translate(${n.x},${n.y})`);
    g.dataset.id=n.id;

    const r=document.createElementNS('http://www.w3.org/2000/svg','rect');
    r.setAttribute('width',NODE_W);r.setAttribute('height',NODE_H);r.setAttribute('rx','8');
    r.setAttribute('stroke',n.color||'var(--border)');
    g.appendChild(r);

    // Icon
    const icon=document.createElementNS('http://www.w3.org/2000/svg','text');
    icon.setAttribute('x','12');icon.setAttribute('y','28');icon.setAttribute('font-size','12');
    icon.setAttribute('pointer-events','none');
    icon.textContent=n.type==='orchestrator'?'\uD83E\uDDE0':n.type==='channel'?'\uD83D\uDCE1':'\uD83E\uDD16';
    g.appendChild(icon);

    // Label
    const lbl=document.createElementNS('http://www.w3.org/2000/svg','text');
    lbl.setAttribute('x','32');lbl.setAttribute('y','29');lbl.setAttribute('font-size','10');
    lbl.setAttribute('fill','var(--text-secondary)');lbl.setAttribute('font-weight','600');
    lbl.setAttribute('pointer-events','none');
    lbl.textContent=n.label||n.id;
    g.appendChild(lbl);

    // Status dot
    const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');
    dot.setAttribute('cx',NODE_W-14);dot.setAttribute('cy',NODE_H/2);dot.setAttribute('r','5');
    dot.setAttribute('class','gstatus-dot');
    const scMap={active:'var(--warning)',streaming:'var(--accent)',done:'var(--success)',error:'var(--danger)'};
    dot.setAttribute('fill',scMap[st]||'var(--text-muted)');
    dot.setAttribute('opacity',st==='idle'?'0.2':'1');
    g.appendChild(dot);

    // Output port
    const po=document.createElementNS('http://www.w3.org/2000/svg','circle');
    po.setAttribute('cx',NODE_W/2);po.setAttribute('cy',NODE_H);po.setAttribute('r','5');
    po.setAttribute('fill','var(--border-active)');po.setAttribute('stroke','var(--bg-primary)');po.setAttribute('stroke-width','2');
    po.style.cursor='crosshair';po.setAttribute('data-port','out');po.setAttribute('data-node',n.id);
    g.appendChild(po);
    // Input port
    if(n.id!=='orchestrator'){
      const pi=document.createElementNS('http://www.w3.org/2000/svg','circle');
      pi.setAttribute('cx',NODE_W/2);pi.setAttribute('cy','0');pi.setAttribute('r','5');
      pi.setAttribute('fill','var(--border-active)');pi.setAttribute('stroke','var(--bg-primary)');pi.setAttribute('stroke-width','2');
      pi.style.cursor='crosshair';pi.setAttribute('data-port','in');pi.setAttribute('data-node',n.id);
      g.appendChild(pi)
    }

    // Events
    g.addEventListener('mousedown',e=>{
      if(e.button!==0)return;
      e.preventDefault();
      const portAttr=e.target.getAttribute&&e.target.getAttribute('data-port');
      if(portAttr){
        const nid=e.target.getAttribute('data-node');
        if(portAttr==='out'){
          connectFrom=nid;document.getElementById('temp-edge').style.display=''
        }else if(portAttr==='in'&&connectFrom){
          if(nid!==connectFrom){
            graphEdges.push({from:connectFrom,to:n.id,type:e.shiftKey?'required':'optional'});
            if(connectMode){connectMode=false;document.getElementById('gt-add-edge').innerHTML='\u2194 Connect nodes'}
            connectFrom=null;document.getElementById('temp-edge').style.display='none';
            queueGraphSave();renderGraphSVG()
          }else{
            connectFrom=null;document.getElementById('temp-edge').style.display='none'
          }
        }
        return
      }
      if(connectMode&&connectFrom&&n.id!==connectFrom){
        graphEdges.push({from:connectFrom,to:n.id,type:e.shiftKey?'required':'optional'});connectMode=false;connectFrom=null;
        document.getElementById('gt-add-edge').innerHTML='\u2194 Connect nodes';
        queueGraphSave();renderGraphSVG();return
      }
      if(deleteMode&&n.id!=='orchestrator'){
        const idx=graphNodes.indexOf(n);
        if(idx>-1){
          graphEdges=graphEdges.filter(e=>e.from!==n.id&&e.to!==n.id);
          graphNodes.splice(idx,1);
          if(config.agents&&config.agents[n.id]){delete config.agents[n.id]}
          deleteMode=false;
          document.getElementById('gt-remove-edges').innerHTML='\u274C Delete';
          document.getElementById('graph-svg').classList.remove('del-mode');
          queueGraphSave();api('POST',`/api/projects/${encodeURIComponent(currentProject?.name)}/config`,config).catch(()=>{});renderGraphSVG()
        }
        return
      }
      selectedNode=n.id;
      document.querySelectorAll('.gnode').forEach(el=>el.classList.remove('selected'));
      g.classList.add('selected');
      dragNode=n.id;dragStart={x:e.clientX,y:e.clientY}
    });
    g.addEventListener('dblclick',()=>openNodeEditor(n.id));
    nodesEl.appendChild(g)
  });
}

// Mouse events
document.addEventListener('mousemove',e=>{
  if(dragNode){
    const n=graphNodes.find(x=>x.id===dragNode);
    if(n){n.x+=e.clientX-dragStart.x;n.y+=e.clientY-dragStart.y;dragStart={x:e.clientX,y:e.clientY};renderGraphSVG()}
  }
  if(connectFrom){
    const from=graphNodes.find(x=>x.id===connectFrom);
    if(from){const rect=document.getElementById('graph-svg').getBoundingClientRect()
      const x1=from.x+NODE_W/2,y1=from.y+NODE_H,x2=e.clientX-rect.left,y2=e.clientY-rect.top;
      const dx=x2-x1,dy=y2-y1;
      document.getElementById('temp-edge').setAttribute('d',`M${x1},${y1} C${x1+dx*0.15},${y1+dy*0.4} ${x2-dx*0.15},${y2-dy*0.4} ${x2},${y2}`)}
  }
});
document.addEventListener('mouseup',()=>{
  if(connectFrom&&!connectMode){
    connectFrom=null;document.getElementById('temp-edge').style.display='none'
  }
  if(dragNode){queueGraphSave();dragNode=null}
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if(deleteMode){
      deleteMode=false;
      document.getElementById('gt-remove-edges').innerHTML='\u274C Delete';
      document.getElementById('graph-svg').classList.remove('del-mode');
      renderGraphSVG()
    }
    if(connectMode){
      connectMode=false;
      document.getElementById('gt-add-edge').innerHTML='\u2194 Connect nodes';
      if(connectFrom){connectFrom=null;document.getElementById('temp-edge').style.display='none'}
    }
  }
});

let _saveTimer=null;
function queueGraphSave(){
  clearTimeout(_saveTimer);
  _saveTimer=setTimeout(()=>{const n=currentProject?.name;if(n)api('POST',`/api/projects/${encodeURIComponent(n)}/graph`,{nodes:graphNodes,edges:graphEdges}).catch(()=>{})},500)
}

// ===== NODE EDITOR =====
function openNodeEditor(nodeId){
  const node=graphNodes.find(n=>n.id===nodeId);
  if(!node)return;
  const isOrch=nodeId==='orchestrator';
  const cfg=isOrch?config.orchestrator:(config.agents[nodeId]||{});
  const isChannel=node.type==='channel';
  document.getElementById('node-modal-title').textContent=isOrch?'Orchestrator — '+esc(node.id):isChannel?'Channel — '+esc(node.id):'Agent — '+esc(node.id);

  let html=`
    <div class="form-group"><label>Node ID</label><input type="text" id="nm-id" value="${esc(node.id)}" ${isOrch?'readonly':''}></div>
    <div class="form-group"><label>Label</label><input type="text" id="nm-label" value="${esc(node.label||node.id)}"></div>
    <div class="form-row"><div class="form-group"><label>Color</label><input type="color" id="nm-color" value="${node.color||'#888888'}"></div>
    ${!isOrch?`<div class="form-group"><label>Enabled</label><label><input type="checkbox" id="nm-enabled" ${cfg.enabled!==false?'checked':''}> Active</label></div>`:''}</div>`;
  if(isOrch){
    html+=`<div class="form-group"><label>Provider</label><select id="nm-orch-provider" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem">${providerOptsHTML(cfg.provider||detectProvider(cfg.api_url,cfg.model))}</select></div>
    <div class="form-row"><div class="form-group"><label>Model</label><input type="text" id="nm-orch-model" value="${esc(cfg.model||'')}" placeholder="gpt-4o"></div>
    <div class="form-group"><label>Temp.</label><input type="number" id="nm-temp" value="${cfg.temperature??0.3}" step="0.05" min="0" max="2"></div></div>
    <div class="form-group"><label>API URL</label><input type="text" id="nm-orch-apiurl" value="${esc(cfg.api_url||'')}" placeholder="https://api.openai.com/v1"></div>
    <div class="form-group"><label>API Key</label><input type="password" id="nm-orch-apikey" value="${esc(cfg.api_key||'')}"></div>
    <div class="form-group"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="nm-orch-broadcast" ${cfg.broadcast?'checked':''}> Broadcast (invoke all agents together)</label></div>
    <details style="margin-top:8px;font-size:.72rem"><summary style="cursor:pointer;color:var(--text-muted)">&#128260; Agent loop</summary>
      <div style="margin-top:6px"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="nm-orch-loop" ${(cfg.loop_config&&cfg.loop_config.enabled)?'checked':''}> Repeat pipeline N times</label>
      <div style="display:flex;align-items:center;gap:6px;margin-top:4px"><label style="font-size:.7rem;white-space:nowrap">Max iterations:</label><input type="number" id="nm-orch-loop-max" value="${(cfg.loop_config&&cfg.loop_config.max_iterations)||3}" min="1" max="20" style="width:60px;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:4px 6px;font-size:.72rem">
      <label style="font-size:.7rem;white-space:nowrap">Stop agent:</label><input type="text" id="nm-orch-loop-condition" value="${esc((cfg.loop_config&&cfg.loop_config.condition_agent)||'')}" placeholder="e.g. revisor" style="width:100px;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:4px 6px;font-size:.72rem"></div></div>
    </details>
    <details style="margin-top:8px;font-size:.72rem"><summary style="cursor:pointer;color:var(--text-muted)">&#9881; System prompt</summary>
      <textarea id="nm-orch-sysprompt" rows="3" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px;font-size:.72rem;margin-top:6px;resize:vertical">${esc(cfg.system_prompt||'')}</textarea>
      <p style="font-size:.65rem;color:var(--text-muted);margin-top:2px">If empty, the default prompt is used.</p>
    </details>`
  } else if(isChannel){
    const chCfg=config.channels?.[node.id]||{type:node.channel_type||'telegram',config:{},enabled:true};
    html+=`<div class="form-row"><div class="form-group"><label>Channel type</label>
      <select id="nm-ch-type" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem">
        <option value="telegram" ${(chCfg.type||'telegram')==='telegram'?'selected':''}>Telegram ✅</option>
        <option value="discord" ${chCfg.type==='discord'?'selected':''}>Discord ✅</option>
        <option value="whatsapp" ${chCfg.type==='whatsapp'?'selected':''}>WhatsApp ✅</option>
      </select>
      <p style="font-size:.6rem;color:var(--text-muted);margin-top:2px">Telegram, Discord and WhatsApp support incoming webhook calls (see External Calls page).</p></div>
    <div class="form-group"><label>Enabled</label><label><input type="checkbox" id="nm-ch-enabled" ${chCfg.enabled!==false?'checked':''}> Active</label></div></div>
    <div id="nm-ch-config-area">
      <div id="nm-ch-f-telegram" style="display:${chCfg.type!=='telegram'?'none':''}">
        <div class="form-group"><label>Bot Token Telegram</label><input type="password" id="nm-ch-tg-token" value="${esc(chCfg.config?.bot_token||'')}" placeholder="123456:ABC..."></div>
        <div class="form-group"><label>Telegram Channel ID</label><input type="text" id="nm-ch-channel-id" value="${esc(chCfg.config?.channel_id||'')}" placeholder="@channelname or -1001234567890"></div>
      </div>
      <div id="nm-ch-f-discord" style="display:${chCfg.type==='discord'?'':'none'}">
        <div class="form-group"><label>Bot Token Discord</label><input type="password" id="nm-ch-dc-token" value="${esc(chCfg.config?.bot_token||'')}" placeholder="il_token_del_bot"></div>
        <div class="form-group"><label>Discord Channel ID</label><input type="text" id="nm-ch-discord-cid" value="${esc(chCfg.config?.channel_id||'')}" placeholder="123456789012345678"></div>
      </div>
      <div id="nm-ch-f-whatsapp" style="display:${chCfg.type==='whatsapp'?'':'none'}">
        <div class="form-group"><label>WhatsApp API Token</label><input type="password" id="nm-ch-wa-token" value="${esc(chCfg.config?.api_token||'')}" placeholder="EAAT..."></div>
        <div class="form-group"><label>Phone Number ID</label><input type="text" id="nm-ch-wa-pid" value="${esc(chCfg.config?.phone_number_id||'')}" placeholder="123456789012345"></div>
        <div class="form-group"><label>Verify Token (webhook handshake)</label><input type="text" id="nm-ch-wa-vtoken" value="${esc(chCfg.config?.verify_token||'')}" placeholder="your-secret-verify-token"></div>
      </div>
    <div class="form-row" style="gap:6px;margin-top:6px">
      <button class="btn btn-secondary" id="nm-ch-test-btn" style="padding:4px 10px;font-size:.7rem">&#9654;&#65039; Test (send message)</button>
      <button class="btn btn-secondary" id="nm-ch-webhook-btn" style="padding:4px 10px;font-size:.7rem">&#128279; Register webhook</button>
    </div>
    <div id="nm-ch-status" style="font-size:.65rem;color:var(--text-muted);margin-top:4px"></div>`
    // Channel type switch logic will be added after html injection
  } else {
    html+=`<div class="form-group"><label>Provider</label><select id="nm-provider" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem">${providerOptsHTML(cfg.provider||detectProvider(cfg.api_url,cfg.model))}</select></div>
    <div class="form-row"><div class="form-group"><label>Model</label><input type="text" id="nm-model" value="${esc(cfg.model||'')}" placeholder="gpt-4o"></div>
    <div class="form-group"><label>Temp.</label><input type="number" id="nm-temp" value="${cfg.temperature??0.3}" step="0.05" min="0" max="2"></div></div>
    <div class="form-group"><label>API URL</label><input type="text" id="nm-apiurl" value="${esc(cfg.api_url||'')}" placeholder="https://api.openai.com/v1"></div>
    <div class="form-group"><label>API Key</label><input type="password" id="nm-apikey" value="${esc(cfg.api_key||'')}"></div>
    <div class="form-group"><label>Prompt</label><textarea id="nm-prompt" rows="4">${esc(cfg.prompt||'')}</textarea>
      <div style="display:flex;gap:4px;margin-top:4px">
        <button class="btn btn-secondary" id="nm-test-btn" style="padding:3px 10px;font-size:.7rem">&#9654;&#65039; Test</button>
        <button class="btn btn-secondary" id="nm-history-btn" style="padding:3px 10px;font-size:.7rem">&#128337; History</button>
      </div>
    </div>
    <div class="form-group"><label>Webhook URL</label><input type="text" id="nm-webhook" value="${esc(cfg.webhook_url||'')}" placeholder="https://mio-server/webhook"></div>
    <details style="margin-top:8px;font-size:.72rem"><summary style="cursor:pointer;color:var(--text-muted)">&#128260; Auto-loop</summary>
      <div style="margin-top:6px"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="nm-loop-enabled" ${cfg.loop_config&&cfg.loop_config.enabled?'checked':''}> Ask the agent if another iteration is needed</label></div>
      <div style="display:flex;align-items:center;gap:6px;margin-top:4px"><label style="font-size:.7rem;white-space:nowrap">Safety limit:</label><input type="number" id="nm-loop-max" value="${(cfg.loop_config&&cfg.loop_config.max_iterations)||5}" min="1" max="20" style="width:60px;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:4px 6px;font-size:.72rem"></div>
      <p style="font-size:.65rem;color:var(--text-muted);margin-top:4px">Shift+click on an edge in the graph to toggle loop on that edge.</p>
    </details>
    <div class="form-group"><label>Agent interface</label>
      <select id="nm-ui-mode" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem">
        <option value="chat" ${(cfg.ui_mode||'chat')==='chat'?'selected':''}>&#128172; Chat (free text)</option>
        <option value="form" ${cfg.ui_mode==='form'?'selected':''}>&#128196; Structured form</option>
      </select>
    </div>
    <div id="nm-ui-config-area" style="display:${cfg.ui_mode==='form'?'':'none'};border:1px solid var(--border);border-radius:var(--radius);padding:10px;margin-bottom:8px">
      <div class="form-group"><label>Form title</label><input type="text" id="nm-ui-title" value="${esc((cfg.ui_config&&cfg.ui_config.title)||'')}" placeholder="e.g. Create Quote"></div>
      <div style="font-size:.7rem;color:var(--text-muted);margin-bottom:6px">Form fields:</div>
      <div id="nm-ui-fields"></div>
      <button class="btn btn-secondary" id="nm-ui-add-field" style="padding:3px 10px;font-size:.7rem;margin-top:4px">+ Add field</button>
    </div>
    <div class="form-group"><label>Tool (JSON)</label><textarea id="nm-tools" rows="3" readonly style="font-size:.68rem">${esc(JSON.stringify((cfg.tools||[]).map(t=>t.name),null,2))}</textarea>
    <div class="hint">Edit tools from the &#129504; button on the top right</div>
    <details style="margin-top:8px;font-size:.72rem"><summary style="cursor:pointer;color:var(--text-muted)">&#128214; RAG (Retrieval Augmented Generation)</summary>
      <div style="margin-top:6px"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="nm-rag-enabled" ${(cfg.rag_config&&cfg.rag_config.enabled)?'checked':''}> Enable RAG</label></div>
      <p style="font-size:.65rem;color:var(--text-muted);margin-top:4px">Documents uploaded in the RAG tab will be used as context for this agent.</p>
      <div id="nm-rag-docs" style="margin-top:4px"></div>
    </details>
    <details style="margin-top:8px;font-size:.72rem"><summary style="cursor:pointer;color:var(--text-muted)">&#128279; External triggers</summary>
      <div style="margin-top:6px">
        <p style="font-size:.65rem;color:var(--text-muted);margin-bottom:4px">Public webhook URL for this agent:</p>
        <input type="text" id="nm-trigger-url" readonly value="" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:4px 6px;font-size:.68rem">
        <p style="font-size:.65rem;color:var(--text-muted);margin-top:4px">Send a POST to this URL to trigger the agent from WhatsApp, Excel, external scripts, etc.</p>
        <div style="margin-top:4px"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="nm-trigger-multi" ${cfg.trigger_multi?'checked':''}> Accept triggers even during pipeline execution</label></div>
      </div>
    </details>
    <details style="margin-top:8px;font-size:.72rem" id="nm-mcp-test-section"><summary style="cursor:pointer;color:var(--text-muted)">&#129504; Test MCP tools</summary>
      <div style="margin-top:6px">
        <div id="nm-mcp-test-tools" style="font-size:.65rem;color:var(--text-muted)">Loading...</div>
      </div>
    </details>
    </div>`
  }
  document.getElementById('node-modal-body').innerHTML=html;

  // Channel type switch
  const nmChType=document.getElementById('nm-ch-type');
  if(nmChType){
    nmChType.addEventListener('change',function(){
      const v=this.value;
      ['telegram','discord','whatsapp'].forEach(t=>{
        document.querySelectorAll('#nm-ch-config-area [id^="nm-ch-f-'+t+'"]').forEach(el=>el.style.display=t===v?'':'none')
      })
    })
  }
  // Orchestrator provider auto-fill
  const nmOrchProv=document.getElementById('nm-orch-provider');
  if(nmOrchProv){
    nmOrchProv.addEventListener('change',function(){
      applyProvider(this.value,document.getElementById('nm-orch-apiurl'),document.getElementById('nm-orch-model'))
    })
  }

  // Channel test & webhook buttons
  if(isChannel){
    const chTestBtn=document.getElementById('nm-ch-test-btn');
    const chWebhookBtn=document.getElementById('nm-ch-webhook-btn');
    const chStatus=document.getElementById('nm-ch-status');
    if(chTestBtn){
      chTestBtn.addEventListener('click',async()=>{
        const pn=currentProject?.name;
        try{
          chStatus.textContent='Sending test...';
          const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/channels/'+encodeURIComponent(node.id)+'/test',{
            method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({text:'Test message from Stormo AI'})
          });
          const d=await resp.json();
          chStatus.textContent=d.status==='ok'?'&#9989; Test sent successfully':'&#10060; Error: '+(d.error||'');
          chStatus.style.color=d.status==='ok'?'var(--success)':'var(--danger)'
        }catch(e){
          chStatus.textContent='&#10060; Error: '+e.message;
          chStatus.style.color='var(--danger)'
        }
      })
    }
    if(chWebhookBtn){
      chWebhookBtn.addEventListener('click',async()=>{
        const pn=currentProject?.name;
        try{
          chStatus.textContent='Registering webhook...';
          const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/channels/'+encodeURIComponent(node.id)+'/register-webhook',{method:'POST'});
          const d=await resp.json();
          if(d.status==='ok'){
            chStatus.innerHTML='&#9989; Webhook registered: <code>'+esc(d.webhook_url)+'</code>';
            chStatus.style.color='var(--success)'
          }else{
            chStatus.textContent='&#10060; '+(d.error||'error');
            chStatus.style.color='var(--danger)'
          }
        }catch(e){
          chStatus.textContent='&#10060; '+e.message;
          chStatus.style.color='var(--danger)'
        }
      })
    }
  }

  if(!isOrch && !isChannel){
    // UI mode toggle
    document.getElementById('nm-ui-mode').addEventListener('change',function(){
      document.getElementById('nm-ui-config-area').style.display=this.value==='form'?'':'none'
    });
    // Render UI fields
    const uic=cfg.ui_config||{};
    renderUIFields(uic.fields||[]);
    document.getElementById('nm-ui-add-field').addEventListener('click',()=>{
      const fields=getUIFieldsFromDOM();
      fields.push({name:'',label:'',type:'text',required:false});
      renderUIFields(fields)
    });

    document.getElementById('nm-test-btn').addEventListener('click',async()=>{
      const inp=prompt('Enter test input for @'+node.id+':');
      if(!inp)return;
      const btn=document.getElementById('nm-test-btn');const orig=btn.textContent;
      btn.textContent='...';btn.disabled=true;
      try{
      const pn=currentProject?.name;
      const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/agents/'+encodeURIComponent(node.id)+'/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:node.id,input:inp})});
        const data=await resp.json();
        if(data.output)alert('Response from @'+node.id+':\n\n'+data.output.slice(0,2000));
        else alert('No response');
      }catch(e){alert('Error: '+e.message)}
      btn.textContent=orig;btn.disabled=false
    });
    document.getElementById('nm-history-btn').addEventListener('click',async()=>{
      const pn=currentProject?.name;
      try{
        const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/agents/'+encodeURIComponent(node.id)+'/history');
        const data=await resp.json();
        const hist=data.history||[];
        if(hist.length===0){alert('No prompt history for @'+node.id);return}
        let msg='Prompt history for @'+node.id+':\n';
        hist.forEach((h,i)=>{msg+='\n'+(i+1)+'. ['+new Date(h.timestamp*1000).toLocaleString()+']\n'+h.prompt.slice(0,200)+'...\n'});
        const idx=prompt(msg+'\nEnter number to restore (0 to cancel):');
        if(!idx||idx==='0')return;
        const restoreResp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/agents/'+encodeURIComponent(node.id)+'/restore-prompt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:parseInt(idx)-1})});
        const rd=await restoreResp.json();
        if(rd.status==='ok'){alert('Prompt restored!');openNodeEditor(node.id)}
        else alert('Error: '+(rd.error||''));
      }catch(e){alert('Error: '+e.message)}
    })
  }

  // RAG docs load when details opened
  setTimeout(()=>{
    const allDetails=document.querySelectorAll('#node-modal-body details');
    allDetails.forEach(d=>{
      d.addEventListener('toggle',async function(){
        const summary=this.querySelector('summary');
        if(summary&&summary.textContent.includes('RAG')){
          const ragDocs=document.getElementById('nm-rag-docs');
          if(!ragDocs)return;
          if(!this.open){ragDocs.innerHTML='';return}
          ragDocs.innerHTML='<span style="font-size:.65rem;color:var(--text-muted)">Loading...</span>';
          try{
            const pn=currentProject?.name;
            const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/rag/documents');
            const data=await resp.json();
            const docs=data.documents||[];
            if(docs.length===0){ragDocs.innerHTML='<span style="font-size:.65rem;color:var(--text-muted)">No documents uploaded. Go to RAG in project settings to upload documents.</span>';return}
            ragDocs.innerHTML=docs.map(d=>'<div style="font-size:.65rem;padding:2px 0">'+esc(d.filename)+' <span style="color:var(--text-muted)">('+d.source+')</span></div>').join('')
          }catch(e){ragDocs.innerHTML='<span style="font-size:.65rem;color:var(--danger)">Error: '+e.message+'</span>'}
        }
        if(summary&&summary.textContent.includes('Trigger')){
          const urlInput=document.getElementById('nm-trigger-url');
          if(!urlInput)return;
          if(!this.open){urlInput.value='';return}
          try{
            const pn=currentProject?.name;
            const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/webhook-url/'+encodeURIComponent(node.id));
            const data=await resp.json();
            urlInput.value=data.url||'Error retrieving URL'
          }catch(e){urlInput.value='Error: '+e.message}
        }
        if(summary&&summary.textContent.includes('Test MCP tools')){
          const container=document.getElementById('nm-mcp-test-tools');
          if(!container)return;
          if(!this.open){container.innerHTML='';return}
          const mcpTools=(config?.agents?.[node.id]?.mcp_tools||[]).map(t=>t.name);
          const projectTools=config?.mcp_tools||[];
          if(mcpTools.length===0){
            container.innerHTML='<span style="font-size:.65rem;color:var(--text-muted)">No MCP tools assigned to this agent. Go to MCP in project settings to assign them.</span>';
            return
          }
          const toolOpts=mcpTools.map(tn=>{
            const pt=projectTools.find(pt=>pt.name===tn);
            return '<option value="'+esc(tn)+'">'+esc(tn+(pt?.url?' ('+esc(pt.url)+')':''))+'</option>'
          }).join('');
          container.innerHTML='<div style="margin-bottom:4px"><label style="font-size:.65rem">Tool MCP:</label><select id="nm-mcp-test-select" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:4px 6px;font-size:.7rem">'+toolOpts+'</select></div>'+
            '<div style="margin-bottom:4px"><label style="font-size:.65rem">Parametri (JSON):</label><textarea id="nm-mcp-test-params" rows="3" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:4px 6px;font-size:.68rem" placeholder=\'{}\'></textarea></div>'+
            '<button class="btn btn-secondary" id="nm-mcp-test-run" style="padding:3px 10px;font-size:.7rem">&#9654;&#65039; Run</button>'+
            '<div id="nm-mcp-test-result" style="margin-top:6px;font-size:.68rem;white-space:pre-wrap;background:var(--bg-secondary);padding:6px;border-radius:4px;max-height:200px;overflow:auto"></div>';
          document.getElementById('nm-mcp-test-run').addEventListener('click',async()=>{
            const sel=document.getElementById('nm-mcp-test-select');
            const paramsEl=document.getElementById('nm-mcp-test-params');
            const resultEl=document.getElementById('nm-mcp-test-result');
            const toolName=sel?.value;
            if(!toolName)return;
            let params={};
            try{params=JSON.parse(paramsEl?.value||'{}')}catch(e){resultEl.textContent='ERROR: Invalid JSON parameters';return}
            resultEl.textContent='Running...';
            const pn=currentProject?.name;
            try{
              const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/tools/mcp-call',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool_name:toolName,params})});
              const data=await resp.json();
              resultEl.textContent=typeof data.result==='string'?data.result:JSON.stringify(data.result,null,2)
            }catch(e){            resultEl.textContent='ERROR: '+e.message}
          })
        }
      })
    })
  },50);

  document.getElementById('nm-delete').style.display=(isOrch?'none':'');
  document.getElementById('nm-delete').onclick=()=>{
    if(!confirm(`Delete node "${node.id}"?`))return;
    graphNodes=graphNodes.filter(x=>x.id!==node.id);
    graphEdges=graphEdges.filter(e=>e.from!==node.id&&e.to!==node.id);
    delete config.agents[node.id];
    if(config.channels)delete config.channels[node.id];
    const chDel=graphNodes.filter(x=>x.type==='channel').length;
    document.getElementById('gbb-agents').textContent=Math.max(0,graphNodes.filter(x=>x.type==='agent').length)+' agents'+(chDel?', '+chDel+' channels':'');
    if(graphNodes.length===0)document.getElementById('graph-empty').style.display='';
    else document.getElementById('graph-empty').style.display='none';
    queueGraphSave();document.getElementById('node-modal').classList.remove('open');renderGraphSVG()
  };

  document.getElementById('nm-save').onclick=()=>{
    node.label=document.getElementById('nm-label').value||node.id;node.color=document.getElementById('nm-color').value;
    const newId=document.getElementById('nm-id').value.trim();
    if(newId&&newId!==node.id&&!graphNodes.find(x=>x.id===newId)){
      const old=node.id;
      graphEdges.forEach(e=>{if(e.from===old)e.from=newId;if(e.to===old)e.to=newId});
      if(config.agents[old]){config.agents[newId]=config.agents[old];delete config.agents[old]}
      if(config.channels&&config.channels[old]){config.channels[newId]=config.channels[old];delete config.channels[old]}
      node.id=newId
    }
    if(isChannel){
      const chType=document.getElementById('nm-ch-type').value;
      node.channel_type=chType;
      if(!config.channels)config.channels={};
      const ch=config.channels[node.id]||{type:chType,config:{},enabled:true};
      ch.type=chType;
      ch.enabled=document.getElementById('nm-ch-enabled').checked;
      const bcfg={};
      if(chType==='telegram'){bcfg.bot_token=document.getElementById('nm-ch-tg-token')?.value||'';bcfg.channel_id=document.getElementById('nm-ch-channel-id')?.value||''}
      if(chType==='discord'){bcfg.bot_token=document.getElementById('nm-ch-dc-token')?.value||'';bcfg.channel_id=document.getElementById('nm-ch-discord-cid')?.value||''}
      if(chType==='whatsapp'){bcfg.api_token=document.getElementById('nm-ch-wa-token')?.value||'';bcfg.phone_number_id=document.getElementById('nm-ch-wa-pid')?.value||'';bcfg.verify_token=document.getElementById('nm-ch-wa-vtoken')?.value||''}
      ch.config=bcfg;
      config.channels[node.id]=ch
    } else if(!isOrch){
      const c=config.agents[node.id]||{};
      c.provider=document.getElementById('nm-provider').value;
      c.model=document.getElementById('nm-model').value;
      c.api_url=document.getElementById('nm-apiurl').value;
      c.api_key=document.getElementById('nm-apikey').value;
      c.temperature=parseFloat(document.getElementById('nm-temp').value)||0.3;
      c.prompt=document.getElementById('nm-prompt').value;c.enabled=document.getElementById('nm-enabled').checked;
      c.webhook_url=document.getElementById('nm-webhook').value;
      c.ui_mode=document.getElementById('nm-ui-mode').value;
      c.rag_config={enabled:document.getElementById('nm-rag-enabled')?.checked||false};
      c.loop_config={enabled:document.getElementById('nm-loop-enabled')?.checked||false,
        max_iterations:parseInt(document.getElementById('nm-loop-max')?.value)||3};
      c.trigger_multi=document.getElementById('nm-trigger-multi')?.checked||false;
      if(c.ui_mode==='form'){
        c.ui_config={title:document.getElementById('nm-ui-title').value,fields:getUIFieldsFromDOM()}
      }else{c.ui_config={}}
      c.color=node.color;config.agents[node.id]=c
    } else {
      const o=config.orchestrator;
      o.provider=document.getElementById('nm-orch-provider').value;
      o.model=document.getElementById('nm-orch-model').value;
      o.api_url=document.getElementById('nm-orch-apiurl').value;
      o.api_key=document.getElementById('nm-orch-apikey').value;
      o.temperature=parseFloat(document.getElementById('nm-temp').value)||0.3;
      o.broadcast=document.getElementById('nm-orch-broadcast')?.checked||false;
      o.loop_config={enabled:document.getElementById('nm-orch-loop')?.checked||false,
        max_iterations:parseInt(document.getElementById('nm-orch-loop-max')?.value)||3,
        condition_agent:document.getElementById('nm-orch-loop-condition')?.value||''};
      o.system_prompt=document.getElementById('nm-orch-sysprompt')?.value||''
    }
    config.graph={nodes:graphNodes,edges:graphEdges};
    api('POST',`/api/projects/${encodeURIComponent(currentProject?.name)}/config`,config).catch(e=>showToast('Save error: '+e.message,'error'));
    document.getElementById('node-modal').classList.remove('open');renderGraphSVG()
  };
  document.getElementById('node-modal').classList.add('open')
}

function showChannelTypePicker(onDone){
  const overlay=document.createElement('div');
  overlay.style.cssText='position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:10000;display:flex;align-items:center;justify-content:center';
  const box=document.createElement('div');
  box.style.cssText='background:var(--bg-secondary);border-radius:var(--radius);padding:20px;min-width:320px;box-shadow:0 8px 32px rgba(0,0,0,0.4)';
  box.innerHTML=`
    <h3 style="margin:0 0 12px;font-size:.9rem">New Channel</h3>
    <div style="margin-bottom:12px"><label style="font-size:.72rem;color:var(--text-muted)">Name</label>
    <input type="text" id="ch-name-input" value="channel_${graphNodes.filter(n=>n.type==='channel').length+1}" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem;margin-top:4px"></div>
    <div style="font-size:.72rem;color:var(--text-muted);margin-bottom:8px">Platform</div>
    <div style="display:flex;flex-direction:column;gap:6px" id="ch-type-options">
      <button class="ch-type-btn" data-type="telegram" style="display:flex;align-items:center;gap:8px;padding:10px 12px;border:2px solid transparent;border-radius:var(--radius);cursor:pointer;background:#0088cc15;color:var(--text-primary);font-size:.8rem;text-align:left"><span style="font-size:1.2rem">&#9906;</span> Telegram</button>
      <button class="ch-type-btn" data-type="whatsapp" style="display:flex;align-items:center;gap:8px;padding:10px 12px;border:2px solid transparent;border-radius:var(--radius);cursor:pointer;background:#25d36615;color:var(--text-primary);font-size:.8rem;text-align:left"><span style="font-size:1.2rem">&#9993;</span> WhatsApp</button>
      <button class="ch-type-btn" data-type="discord" style="display:flex;align-items:center;gap:8px;padding:10px 12px;border:2px solid transparent;border-radius:var(--radius);cursor:pointer;background:#5865f215;color:var(--text-primary);font-size:.8rem;text-align:left"><span style="font-size:1.2rem">&#9654;</span> Discord</button>
    </div>
    <div style="display:flex;gap:8px;margin-top:14px;justify-content:flex-end">
      <button class="btn btn-secondary" id="ch-picker-cancel">Cancel</button>
      <button class="btn btn-primary" id="ch-picker-confirm" disabled style="opacity:.5">Create</button>
    </div>`;
  overlay.appendChild(box);
  document.body.appendChild(overlay);

  let selectedType=null;
  const nameInput=document.getElementById('ch-name-input');
  const confirmBtn=document.getElementById('ch-picker-confirm');
  const cancelBtn=document.getElementById('ch-picker-cancel');

  box.querySelectorAll('.ch-type-btn').forEach(btn=>{
    btn.addEventListener('click',()=>{
      box.querySelectorAll('.ch-type-btn').forEach(b=>{b.style.borderColor='transparent'});
      const colors={'telegram':'#0088cc','whatsapp':'#25d366','discord':'#5865f2'};
      btn.style.borderColor=colors[btn.dataset.type];
      selectedType=btn.dataset.type;
      confirmBtn.disabled=false;confirmBtn.style.opacity='1'
    })
  });

  function close(result,chType){
    document.body.removeChild(overlay);
    if(result)onDone(result,chType)
    else onDone(null,null)
  }

  nameInput.addEventListener('keydown',e=>{
    if(e.key==='Enter'&&selectedType){close(nameInput.value.trim()||'channel_'+(graphNodes.filter(n=>n.type==='channel').length+1),selectedType)}
    if(e.key==='Escape')close(null,null)
  });
  confirmBtn.addEventListener('click',()=>{
    if(selectedType){
      const id=nameInput.value.trim()||('channel_'+(graphNodes.filter(n=>n.type==='channel').length+1));
      config.channels=config.channels||{};
      config.channels[id]=config.channels[id]||{type:selectedType,config:{},enabled:true};
      close(id,selectedType)
    }
  });
  cancelBtn.addEventListener('click',()=>close(null));
  setTimeout(()=>nameInput.focus(),100)
}

// ===== CHAT VIEW =====
async function loadChat(name){
  document.getElementById('header-actions').style.display='flex';
  document.getElementById('header-badge').textContent=name;
  document.getElementById('header-project-name').textContent='@'+esc(name);
  document.querySelector('.logo-text').textContent=name;
  document.getElementById('chat-welcome-title').textContent=name;
  document.title='Stormo | '+esc(name);
  currentProject=await loadProjectConfig(name);config=currentProject;
  pipelineCost=0;pipelineTime=0;chatCalls=0;
  document.getElementById('chat-agent-count').textContent=Object.keys(config.agents).length+' agents ready';
  document.getElementById('chat-stat-cost').textContent='$0.00';

  // Render agent list
  const list=document.getElementById('chat-agent-list');list.innerHTML='';
  Object.keys(config.agents).forEach(a=>{
    const col=config.agents[a].color||'#888';
    const item=document.createElement('div');item.className='agent-item idle';item.dataset.agent=a;
    item.innerHTML=`<span class="dot" style="background:${col}"></span><span class="name">@${a}</span>`;
    item.style.cursor='pointer';
    item.addEventListener('click',()=>showAgentForm(a));
    item.addEventListener('dblclick',()=>{
      loadGraph(name);setTimeout(()=>openNodeEditor(a),300)
    });
    list.appendChild(item)
  });

  // Clear chat messages
  document.getElementById('chat-messages').querySelectorAll('.message,.flow-divider,.pipeline-done,.message-error').forEach(el=>el.remove());
  const w=document.getElementById('chat-welcome');if(w)w.style.display='';
  document.getElementById('chat-brief').value='';
  chatOrkMsg=null;chatAgentMsg=null;chatHistory=[];
  // Hide any custom form
  document.getElementById('chat-agent-form')&&(document.getElementById('chat-agent-form').style.display='none');
  document.getElementById('chat-input-area').style.display='';
  updateChatStats()
}

function showAgentForm(agentId){
  const ag=config?.agents?.[agentId];
  if(!ag||ag.ui_mode!=='form'){navigate(`/graph/${encodeURIComponent(currentProject?.name)}`);return}
  const uic=ag.ui_config||{};
  const fields=uic.fields||[];
  if(fields.length===0){navigate(`/graph/${encodeURIComponent(currentProject?.name)}`);return}
  // Hide default input, show form
  document.getElementById('chat-input-area').style.display='none';
  let formEl=document.getElementById('chat-agent-form');
  if(!formEl){
    formEl=document.createElement('div');formEl.id='chat-agent-form';
    formEl.style.cssText='padding:10px 14px;border-top:1px solid var(--border);display:flex;flex-direction:column;gap:8px';
    document.getElementById('chat-input-area').parentNode.insertBefore(formEl,document.getElementById('chat-input-area').nextSibling)
  }
  formEl.style.display='';
  let html='<div style="font-size:.8rem;font-weight:600;color:var(--accent);margin-bottom:4px">&#128196; '+esc(uic.title||'@'+agentId)+'</div>';
  fields.forEach(f=>{
    const fname=f.name;const flabel=f.label||fname;const req=f.required?'required':'';
    if(f.type==='textarea'){
      html+=`<div class="form-group"><label>${esc(flabel)}</label><textarea id="af-${esc(fname)}" rows="3" ${req} style="background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem;font-family:var(--font);resize:vertical"></textarea></div>`
    }else if(f.type==='select'){
      html+=`<div class="form-group"><label>${esc(flabel)}</label><select id="af-${esc(fname)}" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem">`;
      (f.options||[]).forEach(o=>{html+=`<option value="${esc(o)}">${esc(o)}</option>`});
      html+=`</select></div>`
    }else if(f.type==='file'){
      html+=`<div class="form-group"><label>${esc(flabel)}</label><input type="file" id="af-${esc(fname)}" accept=".pdf,.doc,.docx,.txt,.png,.jpg" ${req} style="background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:4px;font-size:.78rem;width:100%"></div>`
    }else if(f.type==='number'){
      html+=`<div class="form-group"><label>${esc(flabel)}</label><input type="number" id="af-${esc(fname)}" ${req} style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem"></div>`
    }else{
      html+=`<div class="form-group"><label>${esc(flabel)}</label><input type="text" id="af-${esc(fname)}" ${req} style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem"></div>`
    }
  });
  html+=`<div style="display:flex;gap:8px;margin-top:4px">
    <button class="btn btn-primary" id="af-submit" style="flex:1">&#128640; Send to @${esc(agentId)}</button>
    <button class="btn btn-secondary" id="af-cancel">Cancel</button>
  </div>`;
  formEl.innerHTML=html;
  formEl.querySelector('#af-cancel').addEventListener('click',()=>{
    formEl.style.display='none';document.getElementById('chat-input-area').style.display=''
  });
  formEl.querySelector('#af-submit').addEventListener('click',async()=>{
    const btn=formEl.querySelector('#af-submit');const orig=btn.textContent;
    btn.textContent='...';btn.disabled=true;
    const formData={};
    fields.forEach(f=>{
      const el=document.getElementById('af-'+f.name);
      if(el){
        if(f.type==='file'){
          const file=el.files[0];
          formData[f.name]=file?file.name+'(file '+file.type+', '+Math.round(file.size/1024)+'KB)':''
        }else{formData[f.name]=el.value}
      }
    });
    try{
      const pn=currentProject?.name;
      // Add to chat
      const msgs=document.getElementById('chat-messages');
      const w=document.getElementById('chat-welcome');if(w)w.style.display='none';
      const userDiv=document.createElement('div');userDiv.className='message user';
      let summary='<div class="message-header">Form sent to @'+esc(agentId)+'</div><div class="message-body" style="font-size:.75rem">';
      fields.forEach(f=>{summary+='<b>'+esc(f.label||f.name)+':</b> '+esc(formData[f.name]||'(empty)')+'<br>'});
      summary+='</div>';userDiv.innerHTML=summary;msgs.appendChild(userDiv);
      const loadDiv=document.createElement('div');loadDiv.className='message agent body-loading';
      loadDiv.innerHTML='<div class="message-header"><span class="msg-avatar" style="background:'+(ag.color||'#888')+'22;color:'+(ag.color||'#888')+'">&#129302;</span>@'+esc(agentId)+'</div><div class="message-body"></div>';
      msgs.appendChild(loadDiv);msgs.scrollTop=msgs.scrollHeight;
      const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/agents/'+encodeURIComponent(agentId)+'/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({form:formData})});
      const data=await resp.json();
      loadDiv.classList.remove('body-loading');
      const body=loadDiv.querySelector('.message-body');
      if(data.output){body.textContent=data.output.slice(0,5000);formEl.style.display='none';document.getElementById('chat-input-area').style.display=''}
      else{body.textContent='ERROR: '+(data.error||'No response');body.style.color='var(--danger)'}
      msgs.scrollTop=msgs.scrollHeight
    }catch(e){
      const loadDiv=formEl.parentNode.querySelector('.message.agent.body-loading');
      if(loadDiv){loadDiv.classList.remove('body-loading');const b=loadDiv.querySelector('.message-body');if(b){b.textContent='Error: '+e.message;b.style.color='var(--danger)'}}
    }
    btn.textContent=orig;btn.disabled=false
  })
}

function updateChatStats(){
  document.getElementById('chat-stat-pipelines').textContent=chatCalls;
  document.getElementById('chat-stat-calls').textContent=chatCalls;
  const c=pipelineCost;
  document.getElementById('chat-stat-cost').textContent=c<0.001?'$'+(c*1000).toFixed(4)+'m':c<1?'$'+c.toFixed(6):'$'+c.toFixed(4)
}

function addChatMsg(type,agent,content,loading,color){
  const msgs=document.getElementById('chat-messages');const w=document.getElementById('chat-welcome');
  if(w)w.style.display='none';
  const div=document.createElement('div');div.className='message '+type+(loading?' body-loading':'');
  if(color)div.style.setProperty('--agent-color',color);
  if(type==='user'){
    div.innerHTML=`<div class="message-header"><span class="msg-avatar" style="background:var(--accent);color:#fff">&#128100;</span>You</div><div class="message-body">${esc(content)}</div>`
  } else if(type==='orchestrator'){
    div.innerHTML=`<div class="message-header"><span class="msg-avatar" style="background:rgba(96,165,250,0.15);color:#60a5fa">&#129504;</span>Orchestrator</div><div class="message-body">${loading?'':esc(content)}</div>`;
    chatOrkMsg=div
  } else {
    const c=color||'#888';
    div.innerHTML=`<div class="message-header"><span class="msg-avatar" style="background:${c}22;color:${c}">&#129302;</span>@${agent}</div><div class="message-body">${loading?'':esc(content)}</div>`;
    chatAgentMsg=div
  }
  msgs.appendChild(div);msgs.scrollTop=msgs.scrollHeight;return div
}

function addChatDivider(text){
  const d=document.createElement('div');d.className='flow-divider';d.textContent=text;
  document.getElementById('chat-messages').appendChild(d);d.scrollIntoView({behavior:'smooth'})
}

async function runChatPipeline(){
  if(isRunning)return;
  const brief=document.getElementById('chat-brief').value.trim();
  if(!brief)return;
  const name=currentProject?.name;if(!name)return;

  isRunning=true;abortController=new AbortController();
  chatCalls++;pipelineCost=0;pipelineTime=0;
  document.getElementById('chat-btn-send').style.display='none';
  document.getElementById('chat-btn-stop').style.display='inline-flex';
  document.getElementById('chat-brief').disabled=true;
  document.getElementById('chat-brief').value='';

  // Add user message to chat
  addChatMsg('user',null,brief);
  chatHistory.push({role:'user',content:brief});
  // Show loading message immediately
  chatOrkMsg=addChatMsg('orchestrator',null,'',true);
  let finalResponse='';let agentMsgs={};
  const chatMessages=document.getElementById('chat-messages');

  const chatHeaders={'Content-Type':'application/json'};
  try{
    const resp=await fetch(`/api/projects/${encodeURIComponent(name)}/chat`,{
      method:'POST',headers:chatHeaders,body:JSON.stringify({brief,history:chatHistory.slice(0,-1)}),signal:abortController.signal
    });
    if(!resp.ok)throw new Error(`Server ${resp.status}`);
    const reader=resp.body.getReader();const decoder=new TextDecoder();let buf='';
    while(true){
      const{done,value}=await reader.read();if(done)break;
      buf+=decoder.decode(value,{stream:true});
      const parts=buf.split('\n');buf=parts.pop()||'';
      for(const line of parts){
        const t=line.trim();
        if(t.startsWith('data: ')){
          const j=t.slice(6).trim();if(!j||j==='[DONE]')continue;
          try{const d=JSON.parse(j);
            if(d.type==='error'){
              addChatMsg('orchestrator',null,d.content,false,'var(--danger)');
              document.getElementById('chat-btn-send').style.display=''
            }
            else if(d.type==='graph_clear'){Object.keys(graphStatus).forEach(k=>graphStatus[k]='idle');graphStatus={}}
            else if(d.type==='graph_node_active'){if(d.node)graphStatus[d.node]='active';renderGraphSVG()}
            else if(d.type==='channel_output'){addChatMsg('orchestrator',null,d.content||'(channel)',false,'var(--success)')}
            else if(d.type==='graph_node_done'){if(d.node)graphStatus[d.node]='done';renderGraphSVG()}
            else if(d.type==='graph_node_error'){if(d.node)graphStatus[d.node]='error';renderGraphSVG()}
            else if(d.type==='graph_node_skip'){
              if(d.node)graphStatus[d.node]='done';renderGraphSVG();
              const agItem=document.querySelector(`.agent-item[data-agent="${d.node}"]`);
              if(agItem){agItem.classList.remove('active','streaming');agItem.classList.add('idle')}
            }
            else if(d.type==='orchestrator_start'){
              if(d.agents)d.agents.forEach(a=>{
                const agItem=document.querySelector(`.agent-item[data-agent="${a}"]`);
                if(agItem){agItem.classList.remove('idle','done','error');agItem.classList.add('active')}
              });
            }
            else if(d.type==='agent_start'){
              graphStatus[d.agent]='active';renderGraphSVG();
              const agItem=document.querySelector(`.agent-item[data-agent="${d.agent}"]`);
              if(agItem){agItem.classList.remove('idle','done','error');agItem.classList.add('active')}
              if(d.agent==='orchestrator'&&d.final){
                // Final orchestrator summary, show in chat
              }else if(d.agent!=='orchestrator'){
                // Agent started - don't create chat bubble, just track
                if(!agentMsgs[d.agent])agentMsgs[d.agent]=true;
              }
            }
            else if(d.type==='agent_chunk'){
              graphStatus[d.agent]='streaming';renderGraphSVG();
              const agItem=document.querySelector(`.agent-item[data-agent="${d.agent}"]`);
              if(agItem){agItem.classList.remove('active','idle');agItem.classList.add('streaming')}
              if(d.agent==='orchestrator'&&d.final){
                // Show final orchestrator response only
                let txt=d.content;
                txt=txt.replace(/ATTIVA\s*:.*/gi,'').trim();
                if(txt){
                  if(!chatOrkMsg){chatOrkMsg=addChatMsg('orchestrator',null,'',true)}
                  const b=chatOrkMsg.querySelector('.message-body');if(b)b.textContent+=txt;finalResponse+=txt;
                }
              }
            }
            else if(d.type==='agent_done'){
              graphStatus[d.agent]='done';renderGraphSVG();
              const agItem=document.querySelector(`.agent-item[data-agent="${d.agent}"]`);
              if(agItem){agItem.classList.remove('active','streaming','error');agItem.classList.add('done')}
              pipelineCost+=d.cost||0;pipelineTime+=d.timing||0;
              if(d.agent==='orchestrator'&&d.final){
                if(chatOrkMsg){chatOrkMsg.classList.remove('body-loading')}
                chatHistory.push({role:'assistant',content:finalResponse||d.content||''});
                document.getElementById('chat-btn-send').style.display=''
              }
            }
            else if(d.type==='agent_error'){
              graphStatus[d.agent]='error';renderGraphSVG();
              const agItem=document.querySelector(`.agent-item[data-agent="${d.agent}"]`);
              if(agItem){agItem.classList.remove('active','streaming');agItem.classList.add('error')}
if(d.agent==='orchestrator'){
                 addChatMsg('orchestrator',null,'Error: '+d.content,false,'var(--danger)');
               }
              document.getElementById('chat-btn-send').style.display=''
            }
            else if(d.type==='orchestrator_activate'){
              if(d.agents&&d.agents.length)d.agents.forEach(a=>{
                const agItem=document.querySelector(`.agent-item[data-agent="${a}"]`);
                if(agItem){agItem.classList.remove('idle','done','error');agItem.classList.add('active')}
              });
            }
            else if(d.type==='pipeline_failed'){
              if(d.error)addChatMsg('orchestrator',null,'Pipeline failed: '+d.error,false,'var(--danger)');
              document.getElementById('chat-btn-send').style.display='';
              if(d.node)graphStatus[d.node]='error';renderGraphSVG();
            }
            else if(d.type==='pipeline_done'){
              document.getElementById('chat-btn-send').style.display='';
              pipelineCost=d.total_cost||pipelineCost;pipelineTime=d.timing||pipelineTime;
              updateChatStats();
              document.querySelectorAll('.agent-item').forEach(i=>{i.classList.remove('active','streaming')});
            }
          }catch(e){}
        }
      }
    }
  }catch(err){
    if(err.name!=='AbortError')addChatMsg('orchestrator',null,'Error: '+err.message,false,'var(--danger)')
  }finally{
    isRunning=false;abortController=null;chatOrkMsg=null;
    document.getElementById('chat-btn-send').style.display='';
    document.getElementById('chat-btn-stop').style.display='none';
    document.getElementById('chat-brief').disabled=false;
    Object.keys(graphStatus).forEach(k=>graphStatus[k]='idle');renderGraphSVG();
    document.querySelectorAll('.agent-item').forEach(i=>i.classList.remove('active','streaming','error'));
    updateChatStats()
  }
}

// ===== TELEMETRY ANIMATION SYSTEM =====
const telemetry = {
  active: 0, total: 0, maxLatency: 0,
  _packets: [], _timeout: null,

  start() {
    this.active = 0; this.total = 0; this.maxLatency = 0;
    document.getElementById('telemetry-overlay').style.display = '';
    this._tick();
  },
  stop() {
    document.getElementById('telemetry-overlay').style.display = 'none';
    this.clear();
    if (this._timeout) { clearTimeout(this._timeout); this._timeout = null; }
  },
  clear() {
    document.querySelectorAll('.telemetry-packet,.telemetry-label').forEach(el => el.remove());
    this._packets = [];
  },
  _tick() {
    document.getElementById('tel-active').textContent = this.active;
    document.getElementById('tel-packets').textContent = this.total;
    this._timeout = setTimeout(() => this._tick(), 500);
  },

  // Packet traveling along an edge between two nodes
  spawnEdgePacket(fromId, toId, color, duration) {
    const from = graphNodes.find(n => n.id === fromId);
    const to = graphNodes.find(n => n.id === toId);
    if (!from || !to) return;
    this.total++;
    const edgesEl = document.getElementById('graph-edges');
    const x1 = from.x + NODE_W/2, y1 = from.y + NODE_H;
    const x2 = to.x + NODE_W/2, y2 = to.y;
    const dx = x2 - x1, dy = y2 - y1;
    const d = `M${x1},${y1} C${x1+dx*0.15},${y1+dy*0.4} ${x2-dx*0.15},${y2-dy*0.4} ${x2},${y2}`;
    const dur = duration || 1.2;
    const g = document.createElementNS('http://www.w3.org/2000/svg','g');
    g.setAttribute('class','telemetry-packet');
    const glow = document.createElementNS('http://www.w3.org/2000/svg','circle');
    glow.setAttribute('r','7'); glow.setAttribute('fill',color||'#007aff');
    glow.setAttribute('opacity','0.3'); glow.setAttribute('filter','url(#packetTrail)');
    const core = document.createElementNS('http://www.w3.org/2000/svg','circle');
    core.setAttribute('r','3.5'); core.setAttribute('fill',color||'#007aff');
    const m = document.createElementNS('http://www.w3.org/2000/svg','animateMotion');
    m.setAttribute('dur',dur+'s'); m.setAttribute('repeatCount','1');
    m.setAttribute('fill','freeze'); m.setAttribute('path',d);
    g.appendChild(glow); g.appendChild(core); g.appendChild(m);
    edgesEl.appendChild(g);
    setTimeout(() => { if(g.parentNode) g.parentNode.removeChild(g); }, dur*1000 + 200);
  },

  // Packet from agent to "external API" and back
  spawnToolPacket(agentId, color) {
    const agent = graphNodes.find(n => n.id === agentId);
    if (!agent) return;
    this.active++; this.total++;
    const edgesEl = document.getElementById('graph-edges');
    const cx = agent.x + NODE_W/2, cy = agent.y + NODE_H/2;
    const isLeft = agent.x < 400;
    const extX = cx + (isLeft ? 130 : -130);
    const extY = cy - 90;
    const colorC = color || '#ff9500';

    // Outgoing packet
    const pathOut = `M${cx},${cy} C${cx},${cy-50} ${extX},${extY-20} ${extX},${extY}`;
    const gOut = document.createElementNS('http://www.w3.org/2000/svg','g');
    gOut.setAttribute('class','telemetry-packet');
    const dotO = document.createElementNS('http://www.w3.org/2000/svg','circle');
    dotO.setAttribute('r','4'); dotO.setAttribute('fill',colorC);
    dotO.setAttribute('filter','url(#packetGlow)');
    const mO = document.createElementNS('http://www.w3.org/2000/svg','animateMotion');
    mO.setAttribute('dur','0.7s'); mO.setAttribute('repeatCount','1');
    mO.setAttribute('fill','freeze'); mO.setAttribute('path',pathOut);
    gOut.appendChild(dotO); gOut.appendChild(mO);
    edgesEl.appendChild(gOut);

    // Latency label
    const lbl = document.createElementNS('http://www.w3.org/2000/svg','text');
    lbl.setAttribute('x',extX); lbl.setAttribute('y',extY-14);
    lbl.setAttribute('text-anchor','middle'); lbl.setAttribute('fill',colorC);
    lbl.setAttribute('font-size','7'); lbl.setAttribute('class','telemetry-label');
    lbl.setAttribute('font-family','var(--font-mono)');
    lbl.textContent = '⚡ API';
    edgesEl.appendChild(lbl);

    setTimeout(() => {
      if(gOut.parentNode) gOut.parentNode.removeChild(gOut);
      // Return packet
      const pathRet = `M${extX},${extY} C${extX},${extY+20} ${cx},${cy+50} ${cx},${cy}`;
      const gRet = document.createElementNS('http://www.w3.org/2000/svg','g');
      gRet.setAttribute('class','telemetry-packet');
      const dotR = document.createElementNS('http://www.w3.org/2000/svg','circle');
      dotR.setAttribute('r','3.5'); dotR.setAttribute('fill','#34c759');
      dotR.setAttribute('filter','url(#packetGlow)');
      const glowR = document.createElementNS('http://www.w3.org/2000/svg','circle');
      glowR.setAttribute('r','6'); glowR.setAttribute('fill','#34c759');
      glowR.setAttribute('opacity','0.25');
      const mR = document.createElementNS('http://www.w3.org/2000/svg','animateMotion');
      mR.setAttribute('dur','0.7s'); mR.setAttribute('repeatCount','1');
      mR.setAttribute('fill','freeze'); mR.setAttribute('path',pathRet);
      gRet.appendChild(glowR); gRet.appendChild(dotR); gRet.appendChild(mR);
      edgesEl.appendChild(gRet);
      setTimeout(() => { this.active--; if(gRet.parentNode) gRet.parentNode.removeChild(gRet); if(lbl.parentNode) lbl.parentNode.removeChild(lbl); }, 900);
    }, 800);
  }
};

// ===== PIPELINE (from graph view) =====
async function runGraphPipeline(){
  if(isRunning)return;
  const brief=document.getElementById('graph-brief').value.trim();
  if(!brief)return;
  const name=currentProject?.name;if(!name)return;

  isRunning=true;abortController=new AbortController();pipelineCost=0;pipelineTime=0;checkpoints=[];
  Object.keys(graphStatus).forEach(k=>graphStatus[k]='idle');graphStatus={};
  initMonitorState();telemetry.start();
  document.getElementById('gt-status').textContent='Running...';
  document.getElementById('graph-brief').value='';
  document.getElementById('gt-run').style.display='none';
  document.getElementById('gt-stop').style.display='';
  document.getElementById('graph-empty').style.display='none';
  document.getElementById('gb-welcome')&&(document.getElementById('gb-welcome').style.display='none');
  document.getElementById('timeline-empty')&&(document.getElementById('timeline-empty').style.display='none');
  renderGraphSVG();
  // Auto-switch to monitor tab
  document.querySelectorAll('.gb-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.gb-panel').forEach(p=>p.classList.remove('active'));
  document.querySelector('.gb-tab[data-panel="monitor"]')?.classList.add('active');
  document.getElementById('gb-monitor')?.classList.add('active');
  const out=document.getElementById('gb-chat-messages');
  out.querySelectorAll('.message,.flow-divider,.pipeline-done,.message-error').forEach(el=>el.remove());

  const graphHeaders={'Content-Type':'application/json'};
  try{
    const resp=await fetch(`/api/projects/${encodeURIComponent(name)}/chat`,{
      method:'POST',headers:graphHeaders,body:JSON.stringify({brief}),signal:abortController.signal
    });
    if(!resp.ok)throw new Error(`Server ${resp.status}`);
    const reader=resp.body.getReader();const decoder=new TextDecoder();let buf='';
    while(true){
      const{done,value}=await reader.read();if(done)break;
      buf+=decoder.decode(value,{stream:true});
      const parts=buf.split('\n');buf=parts.pop()||'';
      for(const line of parts){
        const t=line.trim();
        if(t.startsWith('data: ')){
          const j=t.slice(6).trim();if(!j||j==='[DONE]')continue;
          try{const d=JSON.parse(j);
            if(d.type==='orchestrator_start'&&d.agents){monitorState.total=d.agents.length;d.agents.forEach(a=>{monitorState.agents[a]={status:'idle',color:'#888',tokens:0,cost:0,time:0}});renderMonitorDashboard()}
            else if(d.type==='agent_start'){graphStatus[d.agent]='active';addGraphOutput('message','@'+d.agent,'',true,d.color);if(monitorState.agents[d.agent]){monitorState.agents[d.agent].status='active';monitorState.agents[d.agent].color=d.color||'#888'};renderGraphSVG();renderMonitorDashboard();
              if(d.agent!=='orchestrator')telemetry.spawnEdgePacket('orchestrator',d.agent,d.color||'#007aff',1.5)}
            else if(d.type==='agent_chunk'){graphStatus[d.agent]='streaming';updateGraphOutput(d.agent,d.content);if(monitorState.agents[d.agent])monitorState.agents[d.agent].status='streaming';renderGraphSVG();renderMonitorDashboard()}
            else if(d.type==='agent_done'){graphStatus[d.agent]='done';updateGraphOutput(d.agent,d.content||'',false);pipelineCost+=d.cost||0;pipelineTime+=d.timing||0;
              document.getElementById('gt-status').textContent='Step ok ('+(d.timing||0)+'s)';
              checkpoints.push({node:d.agent,status:'done',cost:d.cost,timing:d.timing,output:d.content});renderTimeline();renderGraphSVG();
              if(monitorState.agents[d.agent]){const a=monitorState.agents[d.agent];a.status='done';a.cost=(a.cost||0)+(d.cost||0);a.time=(a.time||0)+(d.timing||0);a.tokens=(a.tokens||0)+Math.round((d.tokens||0))};monitorState.completed=Object.values(monitorState.agents).filter(x=>x.status==='done'||x.status==='error').length;monitorState.totalTokens+=Math.round((d.tokens||0));monitorState.totalCost+=d.cost||0;renderMonitorDashboard();
              if(d.agent!=='orchestrator')telemetry.spawnEdgePacket(d.agent,'orchestrator','#34c759',1.2)}
            else if(d.type==='agent_error'){graphStatus[d.agent]='error';updateGraphOutput(d.agent,'ERROR: '+d.content,false);
              document.getElementById('gt-status').textContent='Error on @'+d.agent;checkpoints.push({node:d.agent,status:'error',error:d.content});renderTimeline();renderGraphSVG();
              if(monitorState.agents[d.agent])monitorState.agents[d.agent].status='error';monitorState.completed=Object.values(monitorState.agents).filter(x=>x.status==='done'||x.status==='error').length;renderMonitorDashboard()}
            else if(d.type==='pipeline_done'){document.getElementById('gt-status').textContent='Completed';
              addGraphOutput('done','Production completed!');
              fetch('/api/observability/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:name,cost:pipelineCost,time:pipelineTime})}).catch(()=>{});
              renderMonitorDashboard()}
            else if(d.type==='tool_execute'){addGraphOutput('message','\u2699\uFE0F Tool: '+d.tool+' (parametri: '+JSON.stringify(d.params).slice(0,200)+')','',false,'var(--warning)');
              telemetry.spawnToolPacket(d.agent,d.color||'#ff9500')}
            else if(d.type==='tool_result'){addGraphOutput('message','\u2705 Tool '+d.tool+' executed: '+esc(d.result).slice(0,300),'',false,'var(--success)')}
            else if(d.type==='pipeline_failed'){document.getElementById('gt-status').textContent='Failed step '+d.step;if(d.error)addGraphOutput('error','Pipeline failed: '+d.error);else addGraphOutput('error','Pipeline failed at step '+d.step);if(d.node)graphStatus[d.node]='error';renderTimeline();renderGraphSVG();renderMonitorDashboard()}
            else if(d.type==='channel_output'){addGraphOutput('message','['+esc(d.node||'channel')+'] '+esc(d.content||''),'',false,'var(--success)')}
            else if(d.type==='graph_node_active'){if(d.node)graphStatus[d.node]='active';renderGraphSVG()}
            else if(d.type==='graph_node_done'){if(d.node)graphStatus[d.node]='done';renderGraphSVG()}
            else if(d.type==='graph_node_error'){if(d.node)graphStatus[d.node]='error';renderGraphSVG()}
            else if(d.type==='graph_clear'){Object.keys(graphStatus).forEach(k=>graphStatus[k]='idle');renderGraphSVG()}
          }catch(e){}
        }
      }
    }
  }catch(err){
    if(err.name!=='AbortError'){addGraphOutput('error','Error: '+err.message);document.getElementById('gt-status').textContent='Error'}
  }finally{
    isRunning=false;abortController=null;
    document.getElementById('gt-run').style.display='';document.getElementById('gt-stop').style.display='none';
    telemetry.stop()
  }
}

function addGraphOutput(type,content,loading,color){
  const out=document.getElementById('gb-chat-messages');
  if(type==='done'){const d=document.createElement('div');d.className='pipeline-done';d.innerHTML='<span class="big">&#9989;</span><span class="text">'+esc(content)+'</span>';out.appendChild(d);return}
  if(type==='error'){const d=document.createElement('div');d.className='message-error';d.innerHTML='&#9888;&#65039; '+esc(content);out.appendChild(d);return}
  const div=document.createElement('div');div.className='message agent'+(loading?' body-loading':'');
  if(color)div.style.setProperty('--agent-color',color);
  div.innerHTML=`<div class="message-header"><span class="msg-avatar" style="background:${color||'#888'}22;color:${color||'#888'}">&#129302;</span>${esc(content)}</div><div class="message-body"></div>`;
  out.appendChild(div);out.scrollTop=out.scrollHeight
}
function updateGraphOutput(agent,content,done){
  const out=document.getElementById('gb-chat-messages');
  const msgs=out.querySelectorAll('.message.agent');const last=msgs[msgs.length-1];
  if(last&&last.querySelector('.message-header')?.textContent.includes('@'+agent)){
    const body=last.querySelector('.message-body');
    if(body){
      if(content)body.textContent+=content;
      if(done===false)last.classList.remove('body-loading')
    }
  }
  out.scrollTop=out.scrollHeight
}

function renderTimeline(){
  const el=document.getElementById('timeline-content');
  if(checkpoints.length===0){el.innerHTML='<p style="color:var(--text-muted);text-align:center;padding:20px">No checkpoints.</p>';return}
  el.innerHTML=checkpoints.map((cp,i)=>`<div class="timeline-entry ${cp.status||'done'}" onclick="openTimeTravel(${i})">
    <div class="te-dot" style="background:${cp.status==='done'?'var(--success)':'var(--danger)'}"></div>
    <div class="te-info"><div class="te-node">Step ${i+1}: @${cp.node}</div><div class="te-meta">${cp.timing?cp.timing+'s':''}${cp.error?cp.error.slice(0,60):''}</div></div>
    <button class="te-action" onclick="event.stopPropagation();openTimeTravel(${i})">&#128337; Time Travel</button></div>`).join('')
}

function openTimeTravel(idx){
  const cp=checkpoints[idx];if(!cp)return;
  document.getElementById('tt-node-name').textContent='@'+cp.node;
  document.getElementById('tt-step').textContent=idx+1;
  document.getElementById('tt-time').textContent=cp.timing||'?';
  document.getElementById('tt-output').value=cp.output||'(no output)';
  const isOrch=cp.node==='orchestrator';
  const cfg=isOrch?config.orchestrator:(config.agents[cp.node]||{});
  document.getElementById('tt-prompt').value=cfg.prompt||'';
  document.getElementById('tt-restart').onclick=()=>{
    const p=document.getElementById('tt-prompt').value;
    if(isOrch)config.orchestrator.prompt=p;else if(config.agents[cp.node])config.agents[cp.node].prompt=p;
    api('POST',`/api/projects/${encodeURIComponent(currentProject?.name)}/config`,config).catch(()=>{});
    document.getElementById('tt-modal').classList.remove('open');
    document.getElementById('gt-status').textContent='Restart from step '+(idx+1)+'...';
    setTimeout(()=>runGraphPipeline(),300)
  };
  document.getElementById('tt-modal').classList.add('open')
}

// ===== INIT =====
async function init(){
  document.getElementById('app').style.display='';

  document.getElementById('logo-link').addEventListener('click',()=>navigate('/'));

  // New project
  const npm=document.getElementById('new-project-modal');
  document.getElementById('btn-new-project').addEventListener('click',()=>{npm.classList.add('open');setTimeout(()=>document.getElementById('np-name').focus(),100)});
  document.getElementById('np-close').addEventListener('click',()=>npm.classList.remove('open'));
  document.getElementById('np-cancel').addEventListener('click',()=>npm.classList.remove('open'));
  npm.addEventListener('click',e=>{if(e.target===e.currentTarget)npm.classList.remove('open')});
  document.getElementById('np-name').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('np-create').click()});
  document.getElementById('np-create').addEventListener('click',async()=>{
    const n=document.getElementById('np-name').value.trim();
    if(!n)return;
    if(!PROJECT_NAME_RE.test(n)){showToast('Invalid name: use only letters, numbers, - and _ (max 64)','error');return}
    try{const r=await api('POST','/api/projects',{name:n});if(r.status==='ok'){npm.classList.remove('open');document.getElementById('np-name').value='';navigate(`/graph/${encodeURIComponent(n)}`)}else showToast(r.error||'Error','error')}
    catch(e){showToast('Error: '+e.message,'error')}
  });

  // Import project
  document.getElementById('btn-import-project').addEventListener('click',()=>{
    const inp=document.createElement('input');inp.type='file';inp.accept='.json';
    inp.onchange=async()=>{
      const file=inp.files[0];if(!file)return;
      try{
        const text=await file.text();
        const data=JSON.parse(text);
        const r=await api('POST','/api/projects/import',{name:file.name.replace('.json',''),data});
        if(r.status==='ok'){alert('Project imported: '+r.project);navigate('/graph/'+encodeURIComponent(r.project))}
        else alert('Error: '+(r.error||''))
      }catch(e){alert('Error: '+e.message)}
    };
    inp.click()
  });

  // Project search
  document.getElementById('project-search')?.addEventListener('input',e=>{homeSearch=e.target.value;renderHome()});

  // Graph toolbar
  document.getElementById('lv-back').addEventListener('click',()=>navigate(prevPage||'/'));
  document.getElementById('gt-run').addEventListener('click',runGraphPipeline);
  document.getElementById('gt-stop').addEventListener('click',()=>{if(abortController){abortController.abort();abortController=null}});
  document.getElementById('gt-chat-btn').addEventListener('click',()=>{const n=currentProject?.name;if(n)navigate(`/chat/${encodeURIComponent(n)}`)});

  document.getElementById('gt-add-agent').addEventListener('click',()=>{
    document.getElementById('gt-add-menu').classList.remove('show');
    const count=graphNodes.filter(n=>n.type==='agent').length;
    const name=prompt('Name for the new agent:','agent_'+(count+1));
    if(!name||!name.trim())return;
    const colors=['#00d4aa','#7c3aed','#f59e0b','#06b6d4','#ec4899','#fb923c','#ef4444','#a855f7','#14b8a6','#f43f5e','#6366f1','#d946ef'];
    const color=colors[count%colors.length];
    const cols=Math.min(5,Math.max(3,Math.ceil(Math.sqrt(graphNodes.length+1))));
    const gapX=150,gapY=110;
    const ox=80+(count%cols)*gapX;
    const oy=160+Math.floor(count/cols)*gapY;
    graphNodes.push({id:name.trim(),type:'agent',label:'@'+name.trim(),color,x:ox,y:oy});
    graphEdges.push({from:'orchestrator',to:name.trim()});
    if(!config.agents[name.trim()])config.agents[name.trim()]={enabled:true,api_key:'',api_url:'',model:'',temperature:0.3,prompt:'You are a specialized assistant.',color,tools:[]};
    document.getElementById('gbb-agents').textContent=graphNodes.filter(n=>n.type==='agent').length+' agents'+(graphNodes.filter(n=>n.type==='channel').length>0?', '+graphNodes.filter(n=>n.type==='channel').length+' channels':'');
    document.getElementById('graph-empty').style.display='none';
    queueGraphSave();api('POST',`/api/projects/${encodeURIComponent(currentProject?.name)}/config`,config).catch(()=>{});renderGraphSVG();
    setTimeout(()=>openNodeEditor(name.trim()),100)
  });

  // Dropdown toggle
  document.getElementById('gt-add-btn').addEventListener('click',function(e){
    e.stopPropagation();
    document.getElementById('gt-add-menu').classList.toggle('show')
  });
  document.addEventListener('click',function(){
    document.getElementById('gt-add-menu').classList.remove('show')
  });

  document.getElementById('gt-add-channel').addEventListener('click',()=>{
    document.getElementById('gt-add-menu').classList.remove('show');
    showChannelTypePicker((name,channelType)=>{
      if(!name)return;
      const count=graphNodes.filter(n=>n.type==='channel').length;
      const colors={'telegram':'#0088cc','discord':'#5865f2','whatsapp':'#25d366'};
      const chType=channelType||'telegram';
      graphNodes.push({id:name,type:'channel',label:name,channel_type:chType,color:colors[chType]||'#0088cc',x:500,y:50+count*100});
      graphEdges.push({from:'orchestrator',to:name});
      if(!config.channels)config.channels={};
      if(!config.channels[name])config.channels[name]={type:chType,config:{},enabled:true};
document.getElementById('gbb-agents').textContent=graphNodes.filter(n=>n.type==='agent').length+' agents'+(graphNodes.filter(n=>n.type==='channel').length>0?', '+graphNodes.filter(n=>n.type==='channel').length+' channels':'');
      document.getElementById('graph-empty').style.display='none';
      queueGraphSave();api('POST',`/api/projects/${encodeURIComponent(currentProject?.name)}/config`,config).catch(()=>{});renderGraphSVG();
      setTimeout(()=>openNodeEditor(name),100)
    })
  });

  document.getElementById('gt-add-edge').addEventListener('click',()=>{
    document.getElementById('gt-add-menu').classList.remove('show');
    if(connectMode){connectMode=false;document.getElementById('gt-add-edge').innerHTML='\u2194 Connect nodes'}
    else{connectMode=true;document.getElementById('gt-add-edge').innerHTML='\u2716 Cancel';setTimeout(()=>{connectMode=false;document.getElementById('gt-add-edge').innerHTML='\u2194 Connect nodes'},8000)}
  });
  document.getElementById('gt-remove-edges').addEventListener('click',()=>{
    document.getElementById('gt-add-menu').classList.remove('show');
    if(graphNodes.length<=1&&graphEdges.length===0)return;
    deleteMode=!deleteMode;
    if(deleteMode){
      document.getElementById('gt-remove-edges').innerHTML='\u2716 Cancel delete';
      document.getElementById('graph-svg').classList.add('del-mode')
    }else{
      document.getElementById('gt-remove-edges').innerHTML='\u274C Delete';
      document.getElementById('graph-svg').classList.remove('del-mode')
    }
    renderGraphSVG()
  });

  document.getElementById('gt-monitor-btn').addEventListener('click',()=>{
    document.querySelectorAll('.gb-tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.gb-panel').forEach(p=>p.classList.remove('active'));
    document.querySelector('.gb-tab[data-panel="monitor"]')?.classList.add('active');
    document.getElementById('gb-monitor')?.classList.add('active');
    renderMonitorDashboard()
  });
  document.getElementById('gt-log-btn').addEventListener('click',()=>navigate('/log'));

  // Home dashboard mini-log panel (refresh + clear + entries preview)
  (function(){
    const refreshBtn=document.getElementById('gt-log-refresh');
    const clearBtn=document.getElementById('gt-log-clear');
    const entries=document.getElementById('log-entries');
    const cnt=document.getElementById('log-count');
    if(!refreshBtn||!entries) return;
    const srcColors={'telegram':'#0088cc','discord':'#5865f2','whatsapp':'#25d366','webhook':'#a855f7','mcp':'#f59e0b','incoming':'#3b82f6'};
    const doRefresh=async()=>{
      try{
        const r=await api('GET','/api/log');
        const list=r.log||[];
        if(cnt) cnt.textContent=list.length+' calls';
        if(!list.length){entries.innerHTML='<p style="color:var(--text-muted);text-align:center;padding:10px;font-family:inherit;font-size:.66rem">No external calls yet.</p>';return}
        entries.innerHTML=list.slice(0,30).map(e=>{
          const ts=new Date((e.ts||0)*1000).toLocaleTimeString();
          const src=e.source||'webhook';
          const c=srcColors[src]||'#888';
          const statusClass=e.status>=400?'log-error':(e.status>=200&&e.status<300?'log-ok':'log-info');
          return `<div class="log-entry ${statusClass}" style="padding:4px 6px;display:flex;gap:6px;align-items:center;border-bottom:1px solid var(--border)">
            <span style="color:var(--text-muted);font-size:.6rem;white-space:nowrap">${ts}</span>
            <span style="background:${c}22;color:${c};padding:1px 6px;border-radius:999px;font-size:.56rem;font-weight:600;text-transform:uppercase;white-space:nowrap">${src}</span>
            <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.64rem">${esc(e.text||e.payload||e.url||'')}</span>
          </div>`
        }).join('')
      }catch(err){entries.innerHTML='<p style="color:var(--danger);padding:8px;font-size:.66rem">Error: '+esc(err.message)+'</p>'}
    };
    refreshBtn.onclick=doRefresh;
    if(clearBtn) clearBtn.onclick=async()=>{await api('POST','/api/log/clear',{});doRefresh()};
    doRefresh();
    setInterval(doRefresh,5000);
  })();

  // Templates
  const tplModal=document.getElementById('templates-modal');
  document.getElementById('gt-add-template').addEventListener('click',async()=>{
    document.getElementById('gt-add-menu').classList.remove('show');
    tplModal.classList.add('open');
    try{
      const resp=await fetch('/api/templates');
      const data=await resp.json();
      const list=document.getElementById('templates-list');
      list.innerHTML='';
      (data.templates||[]).forEach(t=>{
        const card=document.createElement('div');card.className='template-card';
        card.innerHTML='<div class="template-card-color" style="background:'+t.color+'"></div><div class="template-card-info"><div class="template-card-name">'+esc(t.name)+'<span class="template-card-cat">'+esc(t.category)+'</span></div><div class="template-card-desc">'+esc(t.description)+'</div></div><button class="template-card-add">+ Create</button>';
        card.querySelector('.template-card-add').addEventListener('click',async(ev)=>{
          ev.stopPropagation();
          const aname=prompt('Name for the new agent (default: '+t.id+'):')||t.id;
          const pn=currentProject?.name;
          try{
            const r=await api('POST','/api/templates/apply',{project:pn,template_id:t.id,agent_name:aname});
            if(r.status==='ok'){alert('Agent @'+r.agent+' created!');const n=currentProject?.name;if(n)navigate('/graph/'+encodeURIComponent(n))}
            else alert('Error: '+(r.error||''))
          }catch(e){alert('Error: '+e.message)}
        });
        list.appendChild(card)
      })
    }catch(e){}
  });
  document.getElementById('templates-close').addEventListener('click',()=>tplModal.classList.remove('open'));
  tplModal.addEventListener('click',e=>{if(e.target===e.currentTarget)tplModal.classList.remove('open')});

  // Analytics
  const analyticsModal=document.getElementById('analytics-modal');
  document.getElementById('gt-analytics-btn').addEventListener('click',async()=>{
    analyticsModal.classList.add('open');
    document.getElementById('analytics-content').innerHTML='<p style="color:var(--text-muted);text-align:center;padding:20px">Loading...</p>';
    try{
      const resp=await fetch('/api/analytics');
      const d=await resp.json();
      let html='<div class="analytics-grid"><div class="analytics-card"><div class="analytics-card-value">'+d.total_projects+'</div><div class="analytics-card-label">Projects</div></div><div class="analytics-card"><div class="analytics-card-value">'+d.total_agents+'</div><div class="analytics-card-label">Agents</div></div><div class="analytics-card"><div class="analytics-card-value">'+d.total_pipelines+'</div><div class="analytics-card-label">Pipelines executed</div></div><div class="analytics-card"><div class="analytics-card-value">$'+d.total_cost.toFixed(4)+'</div><div class="analytics-card-label">Total cost</div></div></div>';
      html+='<h3 style="font-size:.8rem;margin-bottom:6px">Project details</h3><table class="analytics-table"><tr><th>Project</th><th>Agents</th><th>Pipeline</th><th>Cost</th></tr>';
      for(const [pname,info] of Object.entries(d.by_project||{})){
        html+='<tr><td>'+esc(pname)+'</td><td>'+info.agents+'</td><td>'+info.pipelines+'</td><td>$'+info.cost.toFixed(4)+'</td></tr>'
      }
      html+='</table>';
      document.getElementById('analytics-content').innerHTML=html
    }catch(e){document.getElementById('analytics-content').innerHTML='<p style="color:var(--danger);text-align:center;padding:20px">Error: '+e.message+'</p>'}
  });
  document.getElementById('analytics-close').addEventListener('click',()=>analyticsModal.classList.remove('open'));
  analyticsModal.addEventListener('click',e=>{if(e.target===e.currentTarget)analyticsModal.classList.remove('open')});

  // Share / Export
  document.getElementById('gt-share-btn').addEventListener('click',async()=>{
    const pn=currentProject?.name;
    if(!pn)return;
    try{
      const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/share',{method:'POST'});
      const d=await resp.json();
      const json=JSON.stringify(d.data||d,null,2);
      const blob=new Blob([json],{type:'application/json'});
      const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=pn+'-stormx-export.json';a.click();
      URL.revokeObjectURL(a.href);
      document.getElementById('gt-status').textContent='Project exported!'
    }catch(e){alert('Export error: '+e.message)}
  });

  document.getElementById('graph-brief').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();runGraphPipeline()}});

  // Bottom panel tabs
  document.querySelectorAll('.gb-tab').forEach(tab=>{tab.addEventListener('click',()=>{
    document.querySelectorAll('.gb-tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.gb-panel').forEach(p=>p.classList.remove('active'));
    tab.classList.add('active');document.getElementById('gb-'+tab.dataset.panel).classList.add('active')
  })});

  // Chat view
  document.getElementById('chat-btn-send').addEventListener('click',runChatPipeline);
  document.getElementById('chat-btn-stop').addEventListener('click',()=>{if(abortController){abortController.abort();abortController=null}});
  document.getElementById('chat-btn-clear').addEventListener('click',()=>{
    if(isRunning)return;
    document.getElementById('chat-messages').querySelectorAll('.message,.flow-divider,.pipeline-done,.message-error').forEach(el=>el.remove());
    const w=document.getElementById('chat-welcome');if(w)w.style.display='';document.getElementById('chat-brief').value='';chatHistory=[]
  });
  document.getElementById('chat-btn-graph').addEventListener('click',()=>{const n=currentProject?.name;if(n)navigate(`/graph/${encodeURIComponent(n)}`)});
  document.getElementById('chat-brief').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();runChatPipeline()}});
  document.querySelectorAll('#chat-welcome .hint-card').forEach(c=>c.addEventListener('click',()=>{
    document.getElementById('chat-brief').value=c.dataset.brief;document.getElementById('chat-brief').focus()
  }));

  // Node modal
  document.getElementById('nm-close').addEventListener('click',()=>document.getElementById('node-modal').classList.remove('open'));
  document.getElementById('node-modal').addEventListener('click',e=>{if(e.target===e.currentTarget)document.getElementById('node-modal').classList.remove('open')});

  // Time travel
  document.getElementById('tt-close').addEventListener('click',()=>document.getElementById('tt-modal').classList.remove('open'));
  document.getElementById('tt-cancel').addEventListener('click',()=>document.getElementById('tt-modal').classList.remove('open'));
  document.getElementById('tt-modal').addEventListener('click',e=>{if(e.target===e.currentTarget)document.getElementById('tt-modal').classList.remove('open')});

  // Observability
  document.getElementById('btn-obs').addEventListener('click',openObs);
  document.getElementById('obs-close').addEventListener('click',()=>document.getElementById('obs-modal').classList.remove('open'));
  document.getElementById('obs-modal').addEventListener('click',e=>{if(e.target===e.currentTarget)document.getElementById('obs-modal').classList.remove('open')});

  // Assistant
  const asstModal=document.getElementById('assistant-modal');
  document.getElementById('btn-assistant').addEventListener('click',()=>asstModal.classList.add('open'));
  document.getElementById('asst-close').addEventListener('click',()=>asstModal.classList.remove('open'));
  asstModal.addEventListener('click',e=>{if(e.target===e.currentTarget)asstModal.classList.remove('open')});
  document.getElementById('asst-clear').addEventListener('click',()=>{asstHistory=[];document.getElementById('asst-messages').innerHTML='<div class="asst-msg asst-bot"><div class="asst-msg-content">Hi! I\'m the Stormo AI assistant. I can help you configure your agents, write prompts, create tools, and optimize the pipeline. What would you like to do? &#128578;</div></div>'});
  document.getElementById('asst-send').addEventListener('click',sendAssistantMsg);
  document.getElementById('asst-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendAssistantMsg()}});

  // Export
  document.getElementById('export-close').addEventListener('click',()=>document.getElementById('export-modal').classList.remove('open'));
  document.getElementById('export-modal').addEventListener('click',e=>{if(e.target===e.currentTarget)document.getElementById('export-modal').classList.remove('open')});
  document.getElementById('export-download').addEventListener('click',()=>{
    const blob=new Blob([document.getElementById('export-content').textContent],{type:'text/plain'});
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='stormx-output.txt';a.click();URL.revokeObjectURL(a.href)
  });

  // Settings
  document.getElementById('btn-settings').addEventListener('click',openSettings);
  document.getElementById('settings-close').addEventListener('click',closeSettings);
  document.getElementById('settings-modal').addEventListener('click',e=>{if(e.target===e.currentTarget)closeSettings()});
  function autoSaveMcp(){
    const name=document.getElementById('mcp-new-name')?.value.trim();
    const url=document.getElementById('mcp-new-url')?.value.trim();
    const apikey=document.getElementById('mcp-new-apikey')?.value.trim();
    if(name&&url){
      const pn=currentProject?.name||'default';
      api('POST',`/api/projects/${encodeURIComponent(pn)}/mcp/register`,{name,url,description:document.getElementById('mcp-new-desc')?.value.trim()||'',api_key:apikey}).catch(()=>{})
    }
  }
  document.querySelectorAll('.settings-tab').forEach(tab=>{tab.addEventListener('click',()=>{
    document.querySelectorAll('.settings-tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.settings-content').forEach(c=>c.classList.remove('active'));
    tab.classList.add('active');document.getElementById('settings-'+tab.dataset.tab).classList.add('active');
    autoSaveSettings();
    autoSaveMcp();
    if(tab.dataset.tab==='mcp')renderMCP();
    if(tab.dataset.tab==='rag')renderRAG()
  })});
  document.getElementById('settings-reset').addEventListener('click',async()=>{
    const pn=currentProject?.name;if(!pn)return;
    config=await api('POST',`/api/projects/${encodeURIComponent(pn)}/config/reset`);
    closeSettings();const n=currentProject?.name;if(n)navigate(`/graph/${encodeURIComponent(n)}`)
  });
  document.getElementById('mcp-add').addEventListener('click',async()=>{
    const name=document.getElementById('mcp-new-name').value.trim();
    const url=document.getElementById('mcp-new-url').value.trim();
    const desc=document.getElementById('mcp-new-desc').value.trim();
    const apikey=document.getElementById('mcp-new-apikey').value.trim();
    if(!name||!url)return;
    const pn=currentProject?.name||'default';
    try{await api('POST',`/api/projects/${encodeURIComponent(pn)}/mcp/register`,{name,url,description:desc,api_key:apikey});
      document.getElementById('mcp-new-name').value='';document.getElementById('mcp-new-url').value='';document.getElementById('mcp-new-desc').value='';document.getElementById('mcp-new-apikey').value='';renderMCP()
    }catch(e){alert('Error: '+e.message)}
  });

  // Tools
  document.getElementById('btn-tools').addEventListener('click',openTools);
  document.getElementById('tools-close').addEventListener('click',closeTools);
  document.getElementById('tools-modal').addEventListener('click',e=>{if(e.target===e.currentTarget)closeTools()});
  document.getElementById('tools-agent-select').addEventListener('change',function(){if(prevToolsAgent)saveToolsFromUI(prevToolsAgent);prevToolsAgent=this.value;renderTools(this.value);populateTestSelect(this.value)});

  // Skills
  const skillsModal=document.getElementById('skills-modal');
  document.getElementById('btn-skills').addEventListener('click',async()=>{
    skillsModal.classList.add('open');
    try{
      const data=await api('GET','/api/skills');
      if(data&&data.skills){builtinSkills=data.skills;renderSkillsList('skills-builtin-list',builtinSkills,true)}
    }catch(e){}
  });
  document.getElementById('skills-builtin-search').addEventListener('input',function(){
    const q=this.value.trim().toLowerCase();
    if(!q){renderSkillsList('skills-builtin-list',builtinSkills,true);return}
    const keywords=q.split(/\s+/);
    const filtered=builtinSkills.filter(s=>{
      const name=(s.name||'').toLowerCase();
      const desc=(s.description||'').toLowerCase();
      const cat=(s.category||'').toLowerCase();
      const sid=(s.id||'').toLowerCase();
      return keywords.every(k=>name.includes(k)||desc.includes(k)||cat.includes(k)||sid.includes(k))
    });
    renderSkillsList('skills-builtin-list',filtered,true)
  });
  document.getElementById('skills-close').addEventListener('click',()=>skillsModal.classList.remove('open'));
  skillsModal.addEventListener('click',e=>{if(e.target===e.currentTarget)skillsModal.classList.remove('open')});
  document.querySelectorAll('.skills-tab').forEach(tab=>{tab.addEventListener('click',()=>{
    document.querySelectorAll('.skills-tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.skills-panel').forEach(p=>p.classList.remove('active'));
    tab.classList.add('active');document.getElementById('skills-'+tab.dataset.skillsPanel).classList.add('active')
  })});
  document.getElementById('skills-browse-btn').addEventListener('click',()=>{window.open('https://skills.sh/','_blank')});

  document.getElementById('skills-imported-search').addEventListener('input',function(){
    const q=this.value.trim().toLowerCase();
    if(!q){renderSkillsList('skills-imported-list',importedSkills,false);return}
    const keywords=q.split(/\s+/);
    const filtered=importedSkills.filter(s=>{
      const name=(s.name||'').toLowerCase();
      const desc=(s.description||'').toLowerCase();
      const cat=(s.category||'').toLowerCase();
      const src=(s.source||'').toLowerCase();
      const sid=(s.id||'').toLowerCase();
      return keywords.every(k=>name.includes(k)||desc.includes(k)||cat.includes(k)||src.includes(k)||sid.includes(k))
    });
    renderSkillsList('skills-imported-list',filtered,false)
  });
  document.getElementById('skills-import-btn').addEventListener('click',async()=>{
    const url=document.getElementById('skills-import-url').value.trim();
    if(!url)return;
    const btn=document.getElementById('skills-import-btn');
    const orig=btn.innerHTML;
    btn.innerHTML='\u23F3 Importing...';btn.disabled=true;
    const t0=Date.now();
    const ghMatch=url.match(/github\.com\/([^\/]+\/[^\/]+?)(?:\.git)?(?:\?|$)/);
    try{
      let data;
      if(ghMatch){data=await api('POST','/api/skills/github',{repo:ghMatch[1]})}
      else{data=await api('POST','/api/skills/import',{url})}
      const elapsed=Math.round((Date.now()-t0)/1000);
      if(data.skills&&data.skills.length){importedSkills=data.skills;renderSkillsList('skills-imported-list',importedSkills,false);document.querySelector('.skills-tab[data-skills-panel="imported"]').click();
        btn.innerHTML='\u2713 '+data.skills.length+' skill ('+elapsed+'s)';btn.disabled=false;
        setTimeout(()=>{btn.innerHTML=orig},3000)
      }else{alert('Error: '+(data.error||'No skills found'));btn.innerHTML=orig;btn.disabled=false}
    }catch(e){alert('Error: '+e.message);btn.innerHTML=orig;btn.disabled=false}
  });
  document.getElementById('tools-add').addEventListener('click',()=>{
    const sel=document.getElementById('tools-agent-select'),name=sel.value;
    if(!name||!config?.agents[name])return;
    const agent=config.agents[name];if(!agent.tools)agent.tools=[];
    agent.tools.push({name:'new_tool',description:'Describe the tool',code:'def run(params):\n    return str(params)'});
    renderTools(name);populateTestSelect(name)
  });
  document.getElementById('tools-save').addEventListener('click',async()=>{
    const sa=document.getElementById('tools-agent-select').value;saveToolsFromUI(sa);const pn=currentProject?.name;
      if(pn&&config){try{await api('POST',`/api/projects/${encodeURIComponent(pn)}/config`,config);showToast('Tools saved','success')}catch(e){showToast('Error saving: '+e.message,'error')}}
    document.getElementById('tools-modal').classList.remove('open')
  });
  document.getElementById('tools-test-run').addEventListener('click',async()=>{
    const sel=document.getElementById('tools-agent-select'),name=sel.value;
    if(!name||!config?.agents[name])return;saveToolsFromUI(name);
    const agent=config.agents[name],idx=parseInt(document.getElementById('tools-test-select').value);
    if(isNaN(idx)||!agent.tools[idx])return;
    const tool=agent.tools[idx];let params={};
    try{params=JSON.parse(document.getElementById('tools-test-params').value||'{}')}catch(e){document.getElementById('tools-test-result').textContent='ERRORE JSON: '+e.message;return}
    const btn=document.getElementById('tools-test-run');btn.disabled=true;btn.textContent='Running...';
    try{const pn=currentProject?.name||'default';
      const res=await fetch(`/api/projects/${encodeURIComponent(pn)}/tools/execute`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:tool.code,params})});
      const d=await res.json();document.getElementById('tools-test-result').textContent=d.result||'(no output)'
    }catch(e){document.getElementById('tools-test-result').textContent='ERRORE: '+e.message}
    finally{btn.disabled=false;btn.textContent='Run test'}
  });
  // SVG background click: deselect and exit delete mode (attached once)
  document.getElementById('graph-svg').addEventListener('mousedown',e=>{
    if(e.button!==0)return;
    const svg=document.getElementById('graph-svg');
    const edges=document.getElementById('graph-edges');
    const nodes=document.getElementById('graph-nodes');
    if(e.target===svg||e.target===edges||e.target===nodes){
      selectedNode=null;
      document.querySelectorAll('.gnode').forEach(el=>el.classList.remove('selected'));
      if(deleteMode&&!e.shiftKey){
        deleteMode=false;
        document.getElementById('gt-remove-edges').innerHTML='\u274C Elimina';
        svg.classList.remove('del-mode');
        renderGraphSVG()
      }
    }
  });

  await Promise.all([loadProviders(), loadMcpPresets()]);
  renderRoute();
  initTheme();

  // Confirm tab close while pipeline running
  window.addEventListener('beforeunload',e=>{
    if(isRunning){e.preventDefault();e.returnValue=''}
  });
}

function showToast(message, type='info'){
  let container=document.getElementById('toast-container');
  if(!container){
    container=document.createElement('div');container.id='toast-container';
    container.style.cssText='position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:10000;display:flex;flex-direction:column;gap:8px;pointer-events:none';
    document.body.appendChild(container)
  }
  const t=document.createElement('div');
  const color=type==='error'?'var(--danger)':type==='success'?'var(--success)':'var(--accent)';
  t.style.cssText='background:var(--bg-secondary);color:'+color+';border:1px solid var(--border);border-left:3px solid '+color+';padding:8px 14px;border-radius:8px;font-size:.78rem;box-shadow:var(--shadow);pointer-events:auto;animation:toastIn .2s ease';
  t.textContent=message;
  container.appendChild(t);
  setTimeout(()=>{t.style.opacity='0';t.style.transform='translateY(-8px)';setTimeout(()=>t.remove(),200)},3000)
}

// ===== SETTINGS =====
function detectProvider(url,model){
  if(!url&&!model)return '';
  for(const [k,v] of Object.entries(PROVIDERS)){
    if(url&&v.url){try{if(url.includes(new URL(v.url).hostname))return k}catch(e){}}
    if(model&&v.model&&model.startsWith(v.model.split('-')[0]))return k
  }
  if(model){
    if(model.includes('gpt')||model.includes('o1')||model.includes('o3'))return 'openai';
    if(model.includes('claude'))return 'anthropic';
    if(model.includes('gemini'))return 'google';
    if(model.includes('deepseek'))return 'deepseek';
    if(model.includes('openrouter'))return 'openrouter'
  }
  return ''
}
function renderRAG(){
  if(!config)return;
  const el=document.getElementById('settings-rag');
  let html='<p style="font-size:.75rem;color:var(--text-muted);margin-bottom:10px">Upload documents for Retrieval Augmented Generation. Content is indexed and made available to agents that have RAG enabled.</p>';
  // Query test
  html+='<div style="border:1px solid var(--border);border-radius:var(--radius);padding:10px;margin-bottom:12px">';
  html+='<div style="font-size:.78rem;font-weight:600;margin-bottom:6px">&#128270; Test query</div>';
html+='<div style="display:flex;gap:6px"><input type="text" id="rag-query-input" placeholder="Search documents..." style="flex:1;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.72rem">';
   html+='<button class="btn btn-secondary" id="rag-query-btn" style="padding:4px 10px;font-size:.72rem">Search</button></div>';
  html+='<div id="rag-query-results" style="margin-top:6px;font-size:.7rem"></div></div>';
  // Upload form
  html+='<div style="border:1px solid var(--border);border-radius:var(--radius);padding:10px;margin-bottom:12px">';
  html+='<div style="font-size:.78rem;font-weight:600;margin-bottom:6px">+ Upload document</div>';
  html+='<div class="form-group"><label>Upload file from PC (.txt, .md, .csv, .json)</label><input type="file" id="rag-file-input" accept=".txt,.md,.csv,.json,.html,.xml,.py,.js,.ts" style="width:100%;font-size:.72rem"></div>';
  html+='<div class="form-group"><label>Or paste content</label><textarea id="rag-new-content" rows="4" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px;font-size:.72rem;resize:vertical" placeholder="Paste document content..."></textarea></div>';
  html+='<button class="btn btn-primary" id="rag-upload-btn" style="margin-top:4px">&#128229; Upload</button></div>';
  // Document list
  html+='<div id="rag-doc-list"><p style="color:var(--text-muted);font-size:.75rem;text-align:center;padding:10px">Loading...</p></div>';
  el.innerHTML=html;
  // Query handler
  document.getElementById('rag-query-btn').addEventListener('click',async()=>{
    const q=document.getElementById('rag-query-input').value.trim();
    const resEl=document.getElementById('rag-query-results');
    if(!q){resEl.innerHTML='';return}
    const pn=currentProject?.name;if(!pn)return;
    resEl.innerHTML='<span style="color:var(--text-muted)">Searching...</span>';
    try{
      const r=await fetch('/api/projects/'+encodeURIComponent(pn)+'/rag/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q,top_k:5})});
      const d=await r.json();
      const results=d.results||[];
      if(results.length===0){resEl.innerHTML='<span style="color:var(--text-muted)">No results.</span>';return}
      resEl.innerHTML=results.map(r=>'<div style="padding:6px;border-bottom:1px solid var(--border);margin-bottom:4px">'+
        '<span style="color:var(--text-muted);font-size:.65rem">Score: '+r.score+'</span><br>'+
        '<span>'+esc(r.content.slice(0,300))+'...</span></div>').join('')
    }catch(e){resEl.innerHTML='<span style="color:var(--danger)">Error: '+e.message+'</span>'}
  });
  // File input auto-read
  document.getElementById('rag-file-input').addEventListener('change',function(){
    const file=this.files[0];
    if(!file)return;
    const reader=new FileReader();
    reader.onload=function(e){
      document.getElementById('rag-new-content').value=e.target.result
    };
    reader.readAsText(file)
  });
  // Upload handler
  document.getElementById('rag-upload-btn').addEventListener('click',async()=>{
    let content=document.getElementById('rag-new-content').value.trim();
    let filename='document.txt';
    const fileInput=document.getElementById('rag-file-input');
    if(fileInput.files.length>0){
      const file=fileInput.files[0];
      filename=file.name;
      if(!content){
        // Read file if textarea is empty
        content=await file.text()
      }
    }
    if(!content){alert('Select a file or write some content');return}
    const pn=currentProject?.name;if(!pn)return;
    try{
      const r=await fetch('/api/projects/'+encodeURIComponent(pn)+'/rag/ingest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,filename,source:'file'})});
      const d=await r.json();
      if(d.status==='ok'||d.note){document.getElementById('rag-new-content').value='';fileInput.value='';loadRAGDocs();alert('Document uploaded!')}
      else alert('Error: '+(d.error||''))
    }catch(e){alert('Error: '+e.message)}
  });
  loadRAGDocs()
}
async function loadRAGDocs(){
  const el=document.getElementById('rag-doc-list');if(!el)return;
  const pn=currentProject?.name;if(!pn){el.innerHTML='<p style="color:var(--text-muted);font-size:.75rem;text-align:center;padding:10px">No project open.</p>';return}
  try{
    const resp=await fetch('/api/projects/'+encodeURIComponent(pn)+'/rag/documents');
    const data=await resp.json();
    const docs=data.documents||[];
    if(docs.length===0){el.innerHTML='<p style="color:var(--text-muted);font-size:.75rem;text-align:center;padding:10px">No documents uploaded.</p>';return}
    el.innerHTML=docs.map(d=>'<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:var(--bg-primary);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:4px;font-size:.72rem">'+
      '<span style="font-weight:600;min-width:100px">'+esc(d.filename)+'</span>'+
      '<span style="color:var(--text-muted);flex:1">'+esc(d.source)+'</span>'+
      '<span style="color:var(--text-muted);font-size:.65rem">'+new Date(d.created*1000).toLocaleString()+'</span>'+
      '<button class="btn btn-secondary rag-delete" data-id="'+esc(d.id)+'" style="padding:2px 8px;font-size:.65rem;color:var(--danger)">Delete</button></div>'
    ).join('');
    el.querySelectorAll('.rag-delete').forEach(b=>{
      b.addEventListener('click',async()=>{
        const id=b.dataset.id;if(!confirm('Delete this document?'))return;
        try{
          await fetch('/api/projects/'+encodeURIComponent(pn)+'/rag/documents/'+encodeURIComponent(id),{method:'DELETE'});
          loadRAGDocs()
      }catch(e){alert('Error: '+e.message)}
      })
    })
  }catch(e){el.innerHTML='<p style="color:var(--danger);font-size:.75rem;text-align:center;padding:10px">Error: '+e.message+'</p>'}
}
function openSettings(){
  if(!config)return;
  const oc=config.orchestrator;
  document.getElementById('settings-orchestrator').innerHTML=`
    <div class="form-group"><label>Provider</label><select id="set-ork-provider" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px 8px;font-size:.78rem">${providerOptsHTML(oc.provider||detectProvider(oc.api_url,oc.model))}</select></div>
    <div class="form-group"><label>Model</label><input type="text" id="set-ork-model" value="${esc(oc.model)}"></div>
    <div class="form-group"><label>API URL</label><input type="text" id="set-ork-url" value="${esc(oc.api_url)}"></div>
    <div class="form-group"><label>API Key</label><input type="password" id="set-ork-key" value="${esc(oc.api_key)}"></div>
    <div class="form-group"><label>Temperature</label><input type="number" id="set-ork-temp" value="${oc.temperature}" step="0.05" min="0" max="2"></div>
    <div class="form-group"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="set-ork-broadcast" ${oc.broadcast?'checked':''}> Broadcast (invoke all agents together)</label></div>
    <div class="form-group"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="set-ork-loop" ${(oc.loop_config&&oc.loop_config.enabled)?'checked':''}> Agent loop (repeat pipeline N times)</label>
      <div style="display:flex;align-items:center;gap:6px;margin-top:4px"><label style="font-size:.7rem;white-space:nowrap">Iterazioni max:</label><input type="number" id="set-ork-loop-max" value="${(oc.loop_config&&oc.loop_config.max_iterations)||3}" min="1" max="20" style="width:60px;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:4px 6px;font-size:.72rem">
      <label style="font-size:.7rem;white-space:nowrap">Stop condition agent:</label><input type="text" id="set-ork-loop-condition" value="${esc((oc.loop_config&&oc.loop_config.condition_agent)||'')}" placeholder="e.g. revisor" style="width:100px;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:4px 6px;font-size:.72rem"></div></div>
    <details style="margin-top:8px;font-size:.72rem"><summary style="cursor:pointer;color:var(--text-muted)">&#9881; System prompt</summary>
      <textarea id="set-ork-sysprompt" rows="4" style="width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;color:var(--text-primary);padding:6px;font-size:.72rem;margin-top:6px;resize:vertical">${esc(oc.system_prompt||'')}</textarea>
      <p style="font-size:.65rem;color:var(--text-muted);margin-top:2px">If empty, the default orchestrator prompt is used.</p>
    </details>
    <p style="font-size:.7rem;color:var(--text-muted);margin-top:4px">Le modifiche vengono salvate automaticamente.</p>`;
  document.getElementById('settings-modal').classList.add('open');
  // Provider change -> auto-fill
  document.getElementById('set-ork-provider').addEventListener('change',function(){
    applyProvider(this.value,document.getElementById('set-ork-url'),document.getElementById('set-ork-model'));
    autoSaveSettings()
  });
  // Auto-save on any change
  ['set-ork-provider','set-ork-model','set-ork-url','set-ork-key','set-ork-temp','set-ork-broadcast','set-ork-loop','set-ork-loop-max','set-ork-loop-condition','set-ork-sysprompt'].forEach(id=>{
    const el=document.getElementById(id);
    if(el)el.addEventListener('input',autoSaveSettings);if(el&&el.type==='number')el.addEventListener('change',autoSaveSettings);
    if(el&&el.type==='checkbox')el.addEventListener('change',autoSaveSettings)
  });
  renderProvidersSettings();
  setTimeout(()=>renderMCP(),100)
}

function renderProvidersSettings(){
  const el=document.getElementById('settings-providers');
  el.innerHTML=`
    <p style="font-size:.75rem;color:var(--text-muted);margin-bottom:10px">Manage the available API providers. They are used to auto-complete URL and model in agents.</p>
    <div id="providers-list"></div>
    <details style="margin-top:10px;font-size:.72rem">
      <summary style="cursor:pointer;color:var(--accent);font-weight:500">+ Add provider</summary>
      <div style="margin-top:8px;border:1px solid var(--border);border-radius:var(--radius);padding:12px">
        <div class="form-group"><label>Key (unique identifier)</label><input type="text" id="set-prov-key" placeholder="e.g. mistral"></div>
        <div class="form-group"><label>Label</label><input type="text" id="set-prov-label" placeholder="es. Mistral AI"></div>
        <div class="form-group"><label>API URL</label><input type="text" id="set-prov-url" placeholder="https://api.mistral.ai/v1"></div>
        <div class="form-group"><label>Default model</label><input type="text" id="set-prov-model" placeholder="mistral-large"></div>
        <button class="btn btn-primary" id="set-prov-add" style="font-size:.72rem">+ Add provider</button>
      </div>
    </details>`;
  loadProvidersList();
  document.getElementById('set-prov-add').addEventListener('click',async()=>{
    const key=document.getElementById('set-prov-key').value.trim();
    const label=document.getElementById('set-prov-label').value.trim();
    const url=document.getElementById('set-prov-url').value.trim();
    const model=document.getElementById('set-prov-model').value.trim();
    if(!key||!label){showToast('Key and label required','error');return}
    try{
      await api('POST','/api/settings/providers',{key,label,url,model});
      document.getElementById('set-prov-key').value='';
      document.getElementById('set-prov-label').value='';
      document.getElementById('set-prov-url').value='';
      document.getElementById('set-prov-model').value='';
      await loadProviders();
      loadProvidersList();
      showToast('Provider added','success')
    }catch(e){showToast('Error: '+e.message,'error')}
  })
}
async function loadProvidersList(){
  try{
    const data=await api('GET','/api/settings/providers');
    const list=document.getElementById('providers-list');
    if(!data||data.length===0){list.innerHTML='<p style="font-size:.72rem;color:var(--text-muted);text-align:center;padding:10px">No providers configured.</p>';return}
    list.innerHTML='';
    data.forEach(p=>{
      const row=document.createElement('div');
      row.style.cssText='display:flex;align-items:center;gap:8px;padding:8px 10px;background:var(--bg-primary);border:1px solid var(--border);border-radius:var(--radius);margin-bottom:4px;font-size:.72rem';
      row.innerHTML=`
        <span style="font-weight:600;min-width:100px">${esc(p.label)}</span>
        <span style="color:var(--text-muted);font-family:var(--font-mono);font-size:.65rem;flex:1;overflow:hidden;text-overflow:ellipsis">${esc(p.url||'')}</span>
        <span style="color:var(--text-secondary);font-size:.65rem">${esc(p.model||'')}</span>
        <button class="btn btn-secondary" style="padding:2px 8px;font-size:.6rem;color:var(--danger)" data-key="${esc(p.key)}">Delete</button>`;
      row.querySelector('button').addEventListener('click',async()=>{
        if(!confirm(`Delete provider "${p.label}"?`))return;
        try{
          await api('DELETE',`/api/settings/providers/${encodeURIComponent(p.key)}`);
          await loadProviders();
          loadProvidersList();
          showToast('Provider deleted','success')
        }catch(e){showToast('Error: '+e.message,'error')}
      });
      list.appendChild(row)
    })
  }catch(e){}
}
function autoSaveSettings(){
  if(!config)return;
  const keyEl=document.getElementById('set-ork-key');
  if(!keyEl)return;
  const oc=config.orchestrator;
  oc.api_key=keyEl.value;oc.api_url=document.getElementById('set-ork-url').value;
  oc.model=document.getElementById('set-ork-model').value;oc.provider=document.getElementById('set-ork-provider').value;
  oc.temperature=parseFloat(document.getElementById('set-ork-temp').value)||0.3;
  oc.broadcast=document.getElementById('set-ork-broadcast')?.checked||false;
  oc.loop_config={enabled:document.getElementById('set-ork-loop')?.checked||false,
    max_iterations:parseInt(document.getElementById('set-ork-loop-max')?.value)||3,
    condition_agent:document.getElementById('set-ork-loop-condition')?.value||''};
  oc.system_prompt=document.getElementById('set-ork-sysprompt')?.value||'';
  const pn=currentProject?.name;
  if(pn)api('POST',`/api/projects/${encodeURIComponent(pn)}/config`,config).catch(()=>{})
}
function closeSettings(){document.getElementById('settings-modal').classList.remove('open')}


function renderMCPPresets(){
  const el=document.getElementById('mcp-presets');if(!el)return;el.innerHTML='';
  MCP_PRESETS.forEach(p=>{
    const btn=document.createElement('button');btn.className='btn btn-secondary';
    btn.style='font-size:.65rem;padding:3px 8px;display:flex;align-items:center;gap:4px';
    btn.textContent=p.name;
    btn.title=p.description;
    btn.addEventListener('click',()=>{
      document.getElementById('mcp-new-name').value=p.name;
      document.getElementById('mcp-new-url').value=p.url;
      document.getElementById('mcp-new-desc').value=p.description;
    });
    el.appendChild(btn)
  })
}

function renderMCP(){
  renderMCPPresets();
  const list=document.getElementById('mcp-tools-list');list.innerHTML='';
  const mcpTools=config?.mcp_tools||[];
  const agentNames=Object.keys(config?.agents||{});
  if(mcpTools.length===0){list.innerHTML='<p style="font-size:.75rem;color:var(--text-muted);padding:8px 0">No MCP tools registered. Use the presets above or add one manually.</p>';return}
  mcpTools.forEach(t=>{
    const row=document.createElement('div');row.className='mcp-row';
    row.style='display:flex;align-items:center;gap:6px;padding:6px 8px;background:var(--bg-primary);border:1px solid var(--border);border-radius:4px;margin-bottom:4px;font-size:.72rem;flex-wrap:wrap';
    const hasKey=t.api_key&&t.api_key.length>0;
    const keyBtn=`<button class="mcp-key-btn" data-tool="${esc(t.name)}" style="font-size:.6rem;padding:2px 6px;border-radius:3px;border:1px solid var(--border);cursor:pointer;background:${hasKey?'var(--success)':'var(--bg-primary)'};color:${hasKey?'#fff':'var(--text-muted)'}">${hasKey?'\u{1F511}':'Key'}</button>`;
    let agentBtns='';
    agentNames.forEach(an=>{
      const a=config.agents[an];
      const hasTool=a?.mcp_tools?.some(mt=>mt.name===t.name);
      agentBtns+=`<label style="font-size:.6rem;display:flex;align-items:center;gap:3px;cursor:pointer;padding:2px 4px;border-radius:3px;background:${hasTool?'var(--accent)':'transparent'};color:${hasTool?'#fff':'var(--text-muted)'}"><input type="checkbox" class="mcp-agent-cb" data-tool="${esc(t.name)}" data-agent="${esc(an)}" ${hasTool?'checked':''} style="accent-color:var(--accent)">${esc(an)}</label>`
    });
    const descText=t.description||'';
    row.innerHTML=`<span class="mcp-name" style="font-weight:600;min-width:90px">${esc(t.name)}</span>${keyBtn}<span class="mcp-url" style="color:var(--text-muted);flex:1;overflow:hidden;text-overflow:ellipsis;font-size:.65rem" title="${esc(descText)}">${esc(t.url||'')}</span><span class="mcp-desc" style="font-size:.6rem;color:var(--text-muted);width:100%;padding:2px 0 0 0">${esc(descText.slice(0,80))}</span><span style="display:flex;gap:4px;flex-wrap:wrap">${agentBtns}</span>`;
    list.appendChild(row)
  });
  list.querySelectorAll('.mcp-agent-cb').forEach(cb=>{
    cb.addEventListener('change',async()=>{
      const toolName=cb.dataset.tool,agentName=cb.dataset.agent;
      const has=cb.checked;
      const pn=currentProject?.name||'default';
      try{
        await api('POST',`/api/projects/${encodeURIComponent(pn)}/mcp/authorize`,{tool_name:toolName,agent_name:agentName,authorized:has});
        if(config.agents[agentName]){config.agents[agentName].mcp_tools=config.agents[agentName].mcp_tools||[]
          if(has)config.agents[agentName].mcp_tools.push({name:toolName});else config.agents[agentName].mcp_tools=config.agents[agentName].mcp_tools.filter(mt=>mt.name!==toolName)}
      }catch(e){alert('Error: '+e.message)}
    })
  });
  list.querySelectorAll('.mcp-key-btn').forEach(btn=>{
    btn.addEventListener('click',async()=>{
      const toolName=btn.dataset.tool;
      const t=mcpTools.find(mt=>mt.name===toolName);
      const curKey=t?.api_key||'';
      const newKey=prompt('Enter API Key / Bearer Token for "'+toolName+'":',curKey);
      if(newKey===null)return;
      const pn=currentProject?.name||'default';
      try{
        await api('POST',`/api/projects/${encodeURIComponent(pn)}/mcp/setkey`,{tool_name:toolName,api_key:newKey});
        if(t)t.api_key=newKey;
        renderMCP()
      }catch(e){alert('Error: '+e.message)}
    })
  })
}

// ===== TOOLS =====
function openTools(){if(!config)return;const names=Object.keys(config.agents);populateSelect('tools-agent-select',names);
  const sel=document.getElementById('tools-agent-select');prevToolsAgent=sel.value;
  if(sel.value){renderTools(sel.value);populateTestSelect(sel.value)}
  document.getElementById('tools-modal').classList.add('open')}
function closeTools(){saveToolsFromUI(document.getElementById('tools-agent-select').value);document.getElementById('tools-modal').classList.remove('open')}
function renderTools(an){if(!config)return;const a=config.agents[an];if(!a)return
  const c=document.getElementById('tools-list');const t=a.tools||[];c.innerHTML='';
  t.forEach((tool,i)=>{const card=document.createElement('div');card.className='tool-card';
    card.innerHTML=`<div class="tool-card-header"><input class="tool-name" value="${esc(tool.name)}" placeholder="name"><button class="tool-remove" data-idx="${i}">&times;</button></div>
    <input class="tool-desc" value="${esc(tool.description)}" placeholder="Description"><textarea class="tool-code" placeholder="def run(params):\n    return result">${esc(tool.code)}</textarea>`;
    c.appendChild(card)});
  c.querySelectorAll('.tool-remove').forEach(b=>{b.addEventListener('click',()=>{a.tools.splice(parseInt(b.dataset.idx),1);renderTools(an);populateTestSelect(an)})})
}
function saveToolsFromUI(agentName){if(!config)return;const n=agentName;if(!n||!config.agents[n])return
  const a=config.agents[n];const newTools=[];let hasErrors=false
  document.querySelectorAll('.tool-card').forEach(c=>{try{
    const n=c.querySelector('.tool-name')?.value.trim();if(!n)return;
    const code=c.querySelector('.tool-code')?.value||'';
    if(!code.includes('def run(')){hasErrors=true;c.style.border='1px solid var(--danger)'}
    else{c.style.border=''}
    newTools.push({name:n,description:c.querySelector('.tool-desc')?.value.trim()||'',code})
  }catch(e){}})
  a.tools=newTools
  if(hasErrors)showToast('Some tools are missing def run(params) — saved anyway','error')
}
function populateTestSelect(an){if(!config)return;const s=document.getElementById('tools-test-select');s.innerHTML=''
  ;(config.agents[an]?.tools||[]).forEach((t,i)=>{const o=document.createElement('option');o.value=i;o.textContent=t.name;s.appendChild(o)})}
function populateSelect(id,names){const s=document.getElementById(id);s.innerHTML='';names.forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent='@'+n;s.appendChild(o)});if(names.length>0)s.value=names[0]}

async function openObs(){
  document.getElementById('obs-body').innerHTML='<p style="color:var(--text-muted)">Loading...</p>';
  document.getElementById('obs-modal').classList.add('open');
  try{const data=await api('GET','/api/observability');
    let h=`<div class="stats"><div class="stat-row" style="font-weight:600;padding-bottom:6px;border-bottom:1px solid var(--border);margin-bottom:8px"><span class="stat-label">Global</span><span class="stat-value"></span></div>
    <div class="stat-row"><span class="stat-label">Total chats</span><span class="stat-value">${data.total_chats}</span></div>
    <div class="stat-row"><span class="stat-label">Total cost</span><span class="stat-value">$${(data.total_cost||0).toFixed(6)}</span></div>
    <div class="stat-row"><span class="stat-label">Total time</span><span class="stat-value">${Math.round(data.total_time)}s</span></div>`;
    for(const[pj,m]of Object.entries(data.by_project||{})){h+=`<div style="margin-top:12px;padding-top:8px;border-top:1px solid var(--border)">
      <div class="stat-row" style="font-weight:600;margin-bottom:4px"><span class="stat-label">${esc(pj)}</span><span class="stat-value"></span></div>
      <div class="stat-row"><span class="stat-label">Chats</span><span class="stat-value">${m.chats}</span></div>
      <div class="stat-row"><span class="stat-label">Cost</span><span class="stat-value">$${(m.cost||0).toFixed(6)}</span></div>
      <div class="stat-row"><span class="stat-label">Time</span><span class="stat-value">${Math.round(m.time)}s</span></div></div>`}
    h+='</div>';document.getElementById('obs-body').innerHTML=h
  }catch(e){document.getElementById('obs-body').innerHTML='<p style="color:var(--danger)">Error: '+esc(e.message)+'</p>'}
}

async function sendAssistantMsg(){
  const inp=document.getElementById('asst-input');const msg=inp.value.trim();
  if(!msg)return;inp.value='';const msgsEl=document.getElementById('asst-messages');
  const userDiv=document.createElement('div');userDiv.className='asst-msg asst-user';
  userDiv.innerHTML=`<div class="asst-msg-content">${esc(msg)}</div>`;msgsEl.appendChild(userDiv);
  const loadDiv=document.createElement('div');loadDiv.className='asst-msg asst-bot asst-loading';
  loadDiv.innerHTML='<div class="asst-msg-content"></div>';msgsEl.appendChild(loadDiv);
  msgsEl.scrollTop=msgsEl.scrollHeight;
  asstHistory.push({role:'user',content:msg});
  try{
    const pn=currentProject?.name||'default';
    const resp=await fetch('/api/assistant/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:pn,messages:asstHistory})});
    if(!resp.ok)throw new Error('Server error');
    const reader=resp.body.getReader();const decoder=new TextDecoder();let buf='';
    const body=loadDiv.querySelector('.asst-msg-content');body.textContent='';
    loadDiv.classList.remove('asst-loading');
    let fullResponse='';
    while(true){
      const{done,value}=await reader.read();if(done)break;
      buf+=decoder.decode(value,{stream:true});
      const parts=buf.split('\n');buf=parts.pop()||'';
      for(const line of parts){const t=line.trim();
        if(t.startsWith('data: ')){const j=t.slice(6).trim();if(!j||j==='[DONE]')continue;
          try{const d=JSON.parse(j);
            if(d.type==='chunk'){body.textContent+=d.content;fullResponse+=d.content}
            else if(d.type==='action'){
              body.textContent+='\n⚡ '+d.label;
              if(d.result&&d.result.message)body.textContent+=': '+d.result.message;
              body.textContent+=' ';
              if(currentProject&&d.action==='refresh'&&currentProject.name){
                loadProjectConfig(currentProject.name).then(newCfg=>{
                  config=newCfg;
                  if(document.getElementById('view-graph').style.display!=='none'){
                    const g=config.graph||{nodes:[],edges:[]};
                    graphNodes=(g.nodes||[]).map(n=>({...n}));
                    graphEdges=(g.edges||[]).map(e=>({...e,type:e.type||'optional'}));
                    const chC=graphNodes.filter(n=>n.type==='channel').length;
                    document.getElementById('gbb-agents').textContent=Math.max(0,graphNodes.filter(n=>n.type==='agent').length)+' agents'+(chC?', '+chC+' channels':'');
                    if(graphNodes.length===0)document.getElementById('graph-empty').style.display='';
                    else document.getElementById('graph-empty').style.display='none';
                    renderGraphSVG()
                  }
                  if(document.getElementById('view-chat').style.display!=='none'){
                    const list=document.getElementById('chat-agent-list');list.innerHTML='';
                    Object.keys(config.agents).forEach(a=>{
                      const col=config.agents[a].color||'#888';
                      const item=document.createElement('div');item.className='agent-item idle';item.dataset.agent=a;
                      item.innerHTML='<span class="dot" style="background:'+col+'"></span><span class="name">@'+a+'</span>';
                      list.appendChild(item)
                    });
                    document.getElementById('chat-agent-count').textContent=Object.keys(config.agents).length+' agents ready'
                  }
                })
              }
            }
            else if(d.type==='error')body.textContent+='\n❌ ERROR: '+d.content
          }catch(e){}}}
      msgsEl.scrollTop=msgsEl.scrollHeight
    }
    if(fullResponse)asstHistory.push({role:'assistant',content:fullResponse})
  }catch(e){loadDiv.querySelector('.asst-msg-content').textContent='ERROR: '+e.message;loadDiv.classList.remove('asst-loading')}
  msgsEl.scrollTop=msgsEl.scrollHeight
}

function renderSkillsList(containerId,skills,builtin){
  const el=document.getElementById(containerId);
  if(!skills||skills.length===0){el.innerHTML='<p style="color:var(--text-muted);text-align:center;padding:20px;font-size:.8rem">No skills available.</p>';return}
  el.innerHTML='';
  skills.forEach(s=>{
    const card=document.createElement('div');card.className='skill-card';
    card.innerHTML='<div class="skill-card-info"><div class="skill-card-name">'+esc(s.name)+'<span class="skill-card-category">'+esc(s.category||'General')+'</span></div><div class="skill-card-desc">'+esc(s.description||'')+'</div></div><button class="skill-card-install" data-skill="'+esc(s.id)+'" data-builtin="'+builtin+'">+ Install</button>';
    card.querySelector('.skill-card-install').addEventListener('click',async function(ev){
      ev.stopPropagation();
      const sid=this.dataset.skill;const agents=Object.keys(config?.agents||{});
      if(agents.length===0){alert('No agents available. Create an agent first.');return}
      // Pick agent via prompt
      const agentName=prompt('Install "'+s.name+'" on which agent?\nAvailable agents: '+agents.join(', '));
      if(!agentName||!agents.includes(agentName))return;
      const pn=currentProject?.name;
      try{
        const body={project:pn,agent:agentName,skill_id:sid};
        if(s.prompt)body.prompt=s.prompt;
        const data=await api('POST','/api/skills/install',body);
        if(data.status==='ok'){this.textContent='\u2713 Installed';this.classList.add('installed');
          // Refresh config
          const newCfg=await loadProjectConfig(pn);
          if(newCfg){config=newCfg;const cch=graphNodes.filter(n=>n.type==='channel').length;document.getElementById('gbb-agents').textContent=Math.max(0,graphNodes.filter(n=>n.type==='agent').length)+' agents'+(cch?', '+cch+' channels':'')}
          setTimeout(()=>{this.textContent='+ Install';this.classList.remove('installed')},3000)
        }else alert('Error: '+(data.error||'Unknown error'))
      }catch(e){alert('Error: '+e.message)}
    });
    el.appendChild(card)
  })
}

function renderUIFields(fields){
  const el=document.getElementById('nm-ui-fields');el.innerHTML='';
  fields.forEach((f,i)=>{
    const row=document.createElement('div');row.style.cssText='display:flex;gap:4px;align-items:center;margin-bottom:4px';
    row.innerHTML='<input type="text" value="'+esc(f.name||'')+'" placeholder="name" style="width:80px;background:var(--bg-primary);border:1px solid var(--border);border-radius:3px;color:var(--text-primary);padding:3px 5px;font-size:.7rem" data-idx="'+i+'" data-field="name">'+
      '<input type="text" value="'+esc(f.label||'')+'" placeholder="Label" style="width:120px;background:var(--bg-primary);border:1px solid var(--border);border-radius:3px;color:var(--text-primary);padding:3px 5px;font-size:.7rem" data-idx="'+i+'" data-field="label">'+
      '<select style="background:var(--bg-primary);border:1px solid var(--border);border-radius:3px;color:var(--text-primary);padding:3px 5px;font-size:.7rem" data-idx="'+i+'" data-field="type">'+
        '<option value="text" '+(f.type==='text'?'selected':'')+'>Text</option>'+
        '<option value="textarea" '+(f.type==='textarea'?'selected':'')+'>Long text</option>'+
        '<option value="number" '+(f.type==='number'?'selected':'')+'>Number</option>'+
        '<option value="select" '+(f.type==='select'?'selected':'')+'>Dropdown menu</option>'+
        '<option value="file" '+(f.type==='file'?'selected':'')+'>File (PDF)</option>'+
      '</select>'+
      '<label style="font-size:.7rem;color:var(--text-muted);white-space:nowrap"><input type="checkbox" '+(f.required?'checked':'')+' data-idx="'+i+'" data-field="required"> Required</label>'+
      '<button class="btn btn-secondary" data-idx="'+i+'" style="padding:2px 6px;font-size:.65rem;color:var(--danger)">&#10005;</button>';
    row.querySelector('button').addEventListener('click',()=>{
      const fields=getUIFieldsFromDOM();
      fields.splice(parseInt(row.querySelector('button').dataset.idx),1);
      renderUIFields(fields)
    });
    // Options for select type
    if(f.type==='select'){
      const optInput=document.createElement('input');
      optInput.type='text';optInput.value=esc((f.options||[]).join(', '));
      optInput.placeholder='options (comma separated)';
      optInput.style.cssText='width:100%;background:var(--bg-primary);border:1px solid var(--border);border-radius:3px;color:var(--text-primary);padding:3px 5px;font-size:.65rem;margin-top:2px';
      optInput.dataset.idx=i;optInput.dataset.field='options';
      row.appendChild(optInput)
    }
    el.appendChild(row)
  })
}
function getUIFieldsFromDOM(){
  const el=document.getElementById('nm-ui-fields');if(!el)return[];
  const fields=[];
  el.querySelectorAll('input[data-field="name"]').forEach(inp=>{
    const i=parseInt(inp.dataset.idx);
    const label=el.querySelector('input[data-field="label"][data-idx="'+i+'"]');
    const type=el.querySelector('select[data-field="type"][data-idx="'+i+'"]');
    const required=el.querySelector('input[data-field="required"][data-idx="'+i+'"]');
    const opts=el.querySelector('input[data-field="options"][data-idx="'+i+'"]');
    const f={name:inp.value,label:label?.value||'',type:type?.value||'text',required:!!required?.checked};
    if(opts&&opts.value)f.options=opts.value.split(',').map(s=>s.trim()).filter(Boolean);
    fields.push(f)
  });
  return fields
}

function initMonitorState(){
  monitorState={total:0,completed:0,current:null,agents:{},startTime:Date.now(),totalTokens:0,totalCost:0}
}
function renderMonitorDashboard(){
  const ms=monitorState;if(!ms)return;
  const el=document.getElementById('monitor-dashboard');const empty=document.getElementById('monitor-empty');
  if(ms.total===0){el.style.display='none';empty.style.display='';return}
  el.style.display='';empty.style.display='none';
  const pct=ms.total>0?Math.round(ms.completed/ms.total*100):0;
  document.getElementById('monitor-progress-fill').style.width=pct+'%';
  document.getElementById('monitor-completed').textContent=ms.completed;
  document.getElementById('monitor-total').textContent=ms.total;
  document.getElementById('monitor-time').textContent=Math.round((Date.now()-ms.startTime)/1000)+'s';
  document.getElementById('monitor-tokens').textContent=ms.totalTokens;
  document.getElementById('monitor-cost').textContent=ms.totalCost.toFixed(6);
  const container=document.getElementById('monitor-agents');
  container.innerHTML='';
  for(const [id,a] of Object.entries(ms.agents)){
    const card=document.createElement('div');card.className='monitor-agent'+(a.status==='active'||a.status==='streaming'?' active':'')+(a.status==='done'?' done':'')+(a.status==='error'?' error':'');
    card.innerHTML='<div class="monitor-agent-dot '+a.status+'"></div><div class="monitor-agent-info"><div class="monitor-agent-name">'+esc(id)+'</div><div class="monitor-agent-status">'+(a.status==='idle'?'Pending':a.status==='active'||a.status==='streaming'?'Running...':a.status==='done'?'Completed':a.status==='error'?'Error':'')+'</div><div class="monitor-agent-bar"><div class="monitor-agent-bar-fill '+(a.status==='done'?'done':a.status==='error'?'error':'')+'" style="width:'+(a.status==='done'?100:a.status==='active'||a.status==='streaming'?50:0)+'%"></div></div></div><div class="monitor-agent-meta">'+(a.time?Math.round(a.time)+'s ':'')+(a.tokens?Math.round(a.tokens)+' tok':'')+'</div>';
    container.appendChild(card)
  }
}

function esc(s){if(!s)return '';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

function initTheme(){
  const s=localStorage.getItem('stormx-theme')||'dark';
  document.documentElement.setAttribute('data-theme',s);
  const tt=document.getElementById('theme-toggle');
  if(tt){
    tt.addEventListener('click',()=>{
      const c=document.documentElement.getAttribute('data-theme'),n=c==='light'?'dark':'light';
      document.documentElement.setAttribute('data-theme',n);localStorage.setItem('stormx-theme',n)
    })
  }
}

document.addEventListener('DOMContentLoaded',init);
