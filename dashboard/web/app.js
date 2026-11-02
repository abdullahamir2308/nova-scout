/* The page's script. Generated into index.html by build_page.py; the pieces marked @verbatim are copied from
   dashboard/design/reference.html by the build, so they cannot drift from the design. Everything else is the
   reference's render code with its sample DATA replaced by /api/data (adapt() makes the reference's DATA shape
   out of the server's answer). The only place a number is made is the server (server.py): this file lays out,
   labels and rounds for display, nothing more. */

/*@verbatim helpers*/

let DATA = null, OPTIONS = [], SEL = "all", LAST = "";

/* ---- presentation lookups (the reference's labels and series colours) ---- */
const SHORT = { cro:"CRO", research_site:"Research sites", none:"Unassigned" };
const COLOR = { cro:"var(--s-cro)", research_site:"var(--s-site)", none:"var(--s-other)" };
const COLOR_NEW = "#4a627d";   // a niche added later: neutral until the design gives it a colour
const CAME = { reply:"Replies", "opt-out":"Opt-outs", "out-of-office":"Out of office", bounce:"Bounces", unmatched:"Unmatched" };
const HELD = { "linkedin":"LinkedIn, sent by hand", "operator hold":"Operator hold", "no-address":"No email address",
  "auto-approve-off":"Auto-approve off", "blocklisted":"Blocklisted", "claim-check":"Claim check", "first draft":"First draft",
  "low-context":"Low context", "none":"No hold reason", "no-send-clock":"No send clock", "retired":"Retired",
  "rule-tags":"Rule tags", "smtp-rejected":"Mail server refused", "unverified-address":"Unverified address" };
const upper1 = s=>s.charAt(0).toUpperCase()+s.slice(1);
const cut = (s,n)=>{ s=String(s||"").trim(); return s.length>n ? s.slice(0,n-1).trimEnd()+"…" : s; };
const heldLabel = c=>c.startsWith("db-guard:") ? (/overture/.test(c)?"Overture source hold":"Source hold") : (HELD[c]||upper1(c));
const tot = x=>DATA.niches.reduce((s,n)=>s+(x.n[n.key]||0),0);

/* The server's answer -> the reference's DATA shape. */
function adapt(a){
  const label = k=>SHORT[k] || (a.options.find(o=>o.key===k)||{}).label || k;
  const color = k=>COLOR[k] || COLOR_NEW;
  const p = n=>String(n).padStart(2,"0");
  const when = ts=>{ const t=new Date(ts*1000); return `${t.getFullYear()}-${p(t.getMonth()+1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}`; };
  const f = a.funnel;
  return {
    options: a.options.filter(o=>o.key!=="none").map(o=>({ key:o.key, label:label(o.key) })),
    niches: a.selected.map(k=>({ key:k, label:label(k), color:color(k) })),
    today: a.today,
    days: a.days.map(x=>({ d:x.iso.slice(5,7)+"/"+x.iso.slice(8,10), n:x.n })),
    queue: a.queue,
    bounce: Object.assign({}, a.bounce, { scaleMax:a.bounce.brakeAt*1.5 }),
    brake: a.brake, spam: a.spam, replies: a.replies,
    came: a.came.map(r=>[ CAME[r.type]||r.type, r.last7, r.total,
      r.type==="bounce" && r.permanent+r.delay ? `${r.permanent} permanent, ${r.delay} ${r.delay===1?"delay":"delays"}` : "" ]),
    latest: a.latest.map(r=>({ when:when(r.ts), company:r.company_name, country:r.country, niche:label(r.niche),
      arm:r.arm ? "Arm "+r.arm : "before A/B", type:r.type==="opt-out" ? "Opt-out" : "Reply",
      outcome:r.outcome ? upper1(r.outcome) : null, subject:cut(r.subject,60), excerpt:cut(r.excerpt,120) })),
    ab: a.ab.map(n=>({ niche:label(n.niche), color:color(n.niche), reached:n.reached, arms:n.arms })),
    need: a.need,
    funnel: [["Leads",f.leads],["Enriched",f.enriched],["Scored",f.scored],["Contact found",f.contact],["Drafted",f.drafted],["Approved",f.approved],["Sent",f.sent]],
    disqualified: f.disqualified,
    held: a.held.map(h=>[heldLabel(h.cls), h.held, h.needs])
  };
}

/*@verbatim ringSVG*/

function barsSVG(){
  const W=760,H=318,L=34,R=8,T=14,B=28, max=Math.max(DATA.today.cap, ...DATA.days.map(tot));
  const iw=W-L-R, ih=H-T-B, n=DATA.days.length, slot=iw/n, bw=Math.min(26, slot*.58);
  const y=v=>T+ih-(v/max)*ih;
  const step=[1,2,5,10,25,50,100,250,500,1000,2500,5000].find(s=>max/s<=2) || max, ticks=[];
  for(let v=0; v<=max; v+=step) ticks.push(v);
  let g="";
  ticks.forEach(v=>{ g+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(120,200,255,${v===0?.28:.1})"/><text class="axis" x="${L-8}" y="${y(v)+4}" text-anchor="end">${v}</text>`; });
  let bars="", hits="";
  DATA.days.forEach((d,i)=>{
    const cx=L+slot*i+slot/2, x=cx-bw/2; let base=0;
    DATA.niches.forEach(nc=>{
      const v=d.n[nc.key]; if(!v) return;
      const h=(v/max)*ih, top=y(base+v);
      bars+=`<rect class="bar" x="${x}" y="${top}" width="${bw}" height="${Math.max(h-2,1)}" rx="3" fill="${nc.color}" style="transition-delay:${i*45}ms"/>`;
      base+=v;
    });
    hits+=`<rect class="hit" tabindex="0" x="${L+slot*i}" y="${T}" width="${slot}" height="${ih}" data-i="${i}" aria-label="${d.d}: ${tot(d)} sent"/>`;
    if(i%2===0||i===n-1) g+=`<text class="axis" x="${cx}" y="${H-6}" text-anchor="middle">${d.d}</text>`;
  });
  const cap=y(DATA.today.cap);
  g+=`<line x1="${L}" x2="${W-R}" y1="${cap}" y2="${cap}" stroke="#ffb347" stroke-width="1.4" stroke-dasharray="6 5"/><text class="axis" x="${W-R}" y="${cap-6}" text-anchor="end" fill="#ffb347" style="fill:#ffb347">Daily cap ${DATA.today.cap}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Emails sent per day over the last 14 days, by niche">${g}${bars}${hits}</svg>`;
}

/*@verbatim gaugeSVG*/

function renderSeg(){
  $("#seg").innerHTML=[{ key:"all", label:"All niches" }].concat(OPTIONS).map(o=>`<button data-k="${esc(o.key)}" aria-pressed="${o.key===SEL}">${esc(o.label)}</button>`).join("");
}

function render(){
  const wasOpen=!!$("#tbl") && !$("#tbl").hidden;
  const d=DATA, pct=Math.min(d.today.sent/d.today.cap*100,100), g=gaugeSVG(), b=d.bounce;
  const lvlText={ok:"Well under the brake",warn:"Close to the brake",crit:"Over the brake"}[g.level];
  const legend=d.niches.map(n=>`<span><i style="background:${n.color}"></i>${esc(n.label)}</span>`).join("");
  const days=d.days, tableRows=days.map(x=>`<tr><td>${x.d}</td>${d.niches.map(n=>`<td>${x.n[n.key]||0}</td>`).join("")}<td>${tot(x)}</td></tr>`).join("");
  const tableHead=`<th>Day</th>${d.niches.map(n=>`<th>${esc(n.label)}</th>`).join("")}<th>Total</th>`;
  const fmax=d.funnel[0][1]||1;
  const funnel=d.funnel.map(([k,v],i)=>`<div class="frow"><span>${k}</span><div class="track"><span style="background:${i===d.funnel.length-1?"var(--accent)":"#1d86ad"}" data-w="${v/fmax*100}"></span></div><span class="v">${fmt(v)}</span></div>`).join("")
    + `<div class="frow dim"><span>Disqualified</span><div class="track"><span style="background:#4a627d" data-w="${d.disqualified/fmax*100}"></span></div><span class="v">${fmt(d.disqualified)}</span></div>`;
  const heldMax=Math.max(1,...d.held.map(h=>h[1])), heldTotal=d.held.reduce((s,h)=>s+h[1],0), heldNeed=d.held.reduce((s,h)=>s+h[2],0);
  const held=d.held.map(([k,v])=>`<div class="frow"><span>${esc(k)}</span><div class="track"><span style="background:#4a627d" data-w="${v/heldMax*100}"></span></div><span class="v">${fmt(v)}</span></div>`).join("");
  const came=d.came.map(([k,a,t,n])=>`<tr><td>${k}${n?` <span class="muted">(${n})</span>`:""}</td><td>${a}</td><td>${t}</td></tr>`).join("");
  const latest=d.latest.length ? d.latest.map(r=>`<div class="reply"><div class="top"><strong>${esc(r.company)}</strong><span class="muted">${esc(r.country)}</span><span class="tag">${esc(r.niche)}</span><span class="tag">${esc(r.arm)}</span><span class="tag">${esc(r.type)}</span><span class="tag ${r.outcome?"":"warn"}">${r.outcome?esc(r.outcome):"Not tagged"}</span></div><span class="muted" style="font-size:12.5px">${esc(r.when)}</span><p><b style="font-weight:600;color:var(--ink)">${esc(r.subject)}</b> ${esc(r.excerpt)}</p></div>`).join("") : `<p class="sub empty">No replies yet.</p>`;
  const ab=d.ab.map(n=>`<div class="abn"><h3><i style="background:${n.color}"></i>${esc(n.niche)}</h3>${n.arms.map(a=>`<div class="arm"><div class="rail"><b>${a.a}</b><div class="track"><span style="background:${n.color}" data-w="${Math.min(a.n/d.need*100,100)}"></span></div><em>${a.n} / ${d.need}</em></div><div class="meta"><span>${a.fu} follow-ups</span><span>${a.rep} replies</span></div></div>`).join("")}</div>`).join("");
  const reached=d.ab.filter(n=>n.reached);
  const abNote=!d.ab.length ? `<p class="sub empty">No template emails sent yet.</p>`
    : `<p class="note">${!reached.length ? `Collecting. No winner is shown before ${d.need} first touches in each arm.`
      : reached.length===d.ab.length ? `Sample reached in both arms. No winner is computed on this page.`
      : `Sample reached in both arms for ${esc(reached.map(n=>n.niche).join(", "))}; collecting for the rest. No winner is computed on this page.`}</p>`;
  const paused=d.brake.paused, sc=paused?"#ff5c70":"#34dba0";
  const statusIcon=`<svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true"><circle cx="15" cy="15" r="13" fill="none" stroke="${sc}" stroke-width="1.8"/><path d="${paused?"M10 10l10 10M20 10L10 20":"M9 15.5l4 4 8-9"}" fill="none" stroke="${sc}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const left=d.queue.daysLeft;
  const daysLeft=left==null ? "–" : left>=10 ? Math.round(left) : left;

  document.body.classList.remove("on");
  $("#tip").style.opacity=0;
  $("#app").innerHTML=`
  <section class="frame core c4" aria-label="Sent today"><div class="ringbox">${ringSVG(pct)}<div class="ringtext"><span class="num">${d.today.sent}</span><span class="sub">of ${d.today.cap} sent today</span><span><span class="chip">${Math.max(d.today.cap-d.today.sent,0)} left</span></span></div></div></section>

  <section class="open c5"><h2>Sent per day<small>last 14 days</small></h2>
    <div class="chart"><div class="legend">${legend}</div>${barsSVG()}
      <div style="margin-top:8px"><button class="linkbtn" id="tblbtn" aria-expanded="false">Show as table</button></div>
      <div class="tbl-wrap" id="tbl" hidden><table><thead><tr>${tableHead}</tr></thead><tbody>${tableRows}</tbody></table></div>
    </div></section>

  <section class="open queue c3"><h2>Approved queue</h2>
    <span class="num">${fmt(d.queue.approved)}</span>
    <p class="sub" style="margin:10px 0 0">emails ready to send</p>
    <div class="qrow"><div><b>${daysLeft}</b><span class="muted">days left</span></div><div><b>${d.queue.perDay}</b><span class="muted">sent a day, last 7 days</span></div></div></section>

  <section class="open c4"><h2>Bounces<small>last ${b.windowDays} days</small></h2>
    <div style="display:grid; justify-items:start; gap:6px">${g.svg}<div class="gnum" style="margin-top:-60px; margin-left:68px">${b.pct.toFixed(1)}<span style="font-size:22px; color:var(--mute)">%</span></div></div>
    <div style="display:flex; align-items:center; gap:12px; margin-top:12px"><span class="chip ${g.level}">${lvlText}</span><span class="sub">${b.bounces} of ${b.sends} sends bounced</span></div></section>

  <section class="open c4"><h2>Send brake</h2>
    <div class="status${paused?" crit":""}" role="status">${statusIcon}<strong>${paused?"Sending paused":"Sending: no pause"}</strong></div>
    ${paused&&d.brake.reasons?`<p class="sub" style="margin:12px 0 0">${esc(d.brake.reasons)}</p>`:""}
    <p class="sub" style="margin:12px 0 0">Pauses on more than ${b.brakeAt}% permanent bounces in ${b.windowDays} days, or any spam complaint.</p></section>

  <section class="open c4"><h2>Spam complaints</h2>
    <div class="pair"><div><span class="num" style="color:var(${d.spam.counted?"--crit":"--ok"})">${d.spam.counted}</span><div class="sub" style="margin-top:8px">counted by the brake</div></div><div><span class="num" style="color:var(--ink-2)">${d.spam.ever}</span><div class="sub" style="margin-top:8px">all time</div></div></div></section>

  <section class="open c4"><h2>Replies</h2>
    <div class="tiles"><div class="tile"><span class="num">${d.replies.total}</span><span class="sub">replies</span></div><div class="tile"><span class="num">${d.replies.positive}</span><span class="sub">positive</span></div><div class="tile"><span class="num">${d.replies.meetings}</span><span class="sub">meetings booked</span></div><div class="tile ${d.replies.untagged?"nudge":""}"><span class="num">${d.replies.untagged}</span><span class="sub">not tagged yet</span></div></div></section>

  <section class="open c4"><h2>What came back</h2>
    <table><thead><tr><th>Type</th><th>Last 7 days</th><th>All time</th></tr></thead><tbody>${came}</tbody></table></section>

  <section class="open c4"><h2>Latest replies</h2>${latest}</section>

  <section class="open c7"><h2>A/B test by niche<small>each arm needs ${d.need} first touches</small></h2><div class="ab">${ab}</div>
    ${abNote}</section>

  <section class="open c5"><h2>Funnel</h2><div class="fun">${funnel}</div>
    <div class="held"><h2>Held drafts<small>${fmt(heldTotal)} total, ${heldNeed?`${fmt(heldNeed)} need you`:"none need you"}</small></h2><div class="fun">${held}</div></div></section>`;

  if(wasOpen){ $("#tbl").hidden=false; const t=$("#tblbtn"); t.setAttribute("aria-expanded",true); t.textContent="Hide table"; }
  OPTIONS=d.options; renderSeg();

  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    document.body.classList.add("on");
    $("#prog").style.strokeDasharray=`${$("#prog").dataset.pct} 100`;
    const gf=$("#gfill"); if(gf) gf.style.strokeDasharray=`${gf.dataset.v} 100`;
    document.querySelectorAll("[data-w]").forEach(el=>el.style.width=el.dataset.w+"%");
  }));
  wire();
}

function wire(){
  const tip=$("#tip"), chart=$(".chart");
  const show=(el)=>{
    const x=DATA.days[+el.dataset.i], r=el.getBoundingClientRect(), cr=chart.getBoundingClientRect();
    tip.innerHTML=`<b>${x.d}, ${tot(x)} sent</b>`+DATA.niches.map(n=>`<div class="row"><span><i style="background:${n.color}"></i>${esc(n.label)}</span><span>${x.n[n.key]||0}</span></div>`).join("");
    tip.style.left=(r.left+r.width/2)+"px"; tip.style.top=(r.top-8+window.scrollY)+"px"; tip.style.position="absolute"; tip.style.opacity=1;
  };
  document.querySelectorAll(".hit").forEach(h=>{
    h.addEventListener("mouseenter",()=>show(h)); h.addEventListener("focus",()=>show(h));
    h.addEventListener("mouseleave",()=>tip.style.opacity=0); h.addEventListener("blur",()=>tip.style.opacity=0);
  });
  const btn=$("#tblbtn"); btn.addEventListener("click",()=>{ const t=$("#tbl"); const open=t.hidden; t.hidden=!open; btn.setAttribute("aria-expanded",open); btn.textContent=open?"Hide table":"Show as table"; });
}

/* ---- live data: fetched on load, on a niche click, and every minute ---- */
function live(ok){ $("#live").classList.toggle("stale",!ok); $("#fresh").textContent=ok?"Live":"Offline"; }

async function load(){
  const asked=SEL;
  try{
    const r=await fetch("/api/data?niche="+encodeURIComponent(asked),{ cache:"no-store" });
    if(!r.ok) throw new Error("HTTP "+r.status);
    const a=await r.json();
    if(asked!==SEL) return;                       // the selection moved on while this was in flight
    live(true);
    const key=JSON.stringify(a,(k,v)=>k==="asOf"?undefined:v);
    if(key!==LAST){ LAST=key; DATA=adapt(a); render(); }   // unchanged numbers: leave the page (and its animations) alone
  }catch(e){
    live(false);
    if(!DATA) $("#app").innerHTML=`<p class="sub empty">The data service is not answering. Trying again every minute.</p>`;
  }
}

$("#seg").addEventListener("click",e=>{
  const b=e.target.closest("button");
  if(!b||b.dataset.k===SEL) return;
  SEL=b.dataset.k; renderSeg(); load();
});

/*@verbatim tick*/
tick(); setInterval(tick,30000);
load(); setInterval(load,60000);
