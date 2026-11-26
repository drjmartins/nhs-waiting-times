/* Shared code for the waiting-times dashboards (Cancer, RTT, A&E).

   Loaded by each dashboard page BEFORE its own inline script, as a classic script, so
   everything here is a global the page can use. The functions below read page state
   (META, CURRENT, CURRENT_TYPE, PTYPE, RANGE, SHOW_ENGLAND, CSV_DATA ...) and call
   page functions (render, renderBig, exportName, csvFilter, buildExportSVG, showTip ...)
   only when they run, never at load time, so the page may define those after this file.

   Hooks a page MAY define (each is optional; a missing hook does nothing):
     closeExtraPanels()    close the page's own dropdowns (Escape in a search box, closing
                           the expand modal)
     onOrgSwitch()         reset page-level slices when the organisation changes
     ensureSlice()         async: load whatever the current slice needs before render()
     submissionNoteHTML()  extra HTML for the organisation note ("did not submit")
   Constants a page MUST define: BG (chart box), EXPORT_HEADER_H (export header height).

   Moved here from the two pages on 2026-11-25 without changing behaviour (DECISIONS). */

const _hook = (name, ...args) => (typeof window[name] === "function" ? window[name](...args) : undefined);

/* ---------- data fetching ---------- */
const DATA = "data/";
// Cache-bust: every data file (except meta.json itself) is fetched with a build-version
// token derived from meta.built_at, so a new index.html can never pair with a browser-
// or CDN-cached index.json from a previous build. meta.json is fetched no-store so the
// token always reflects the latest deployed build (it's tiny). Set in each page's init().
let DV="";
const durl = path => DATA+path+(DV?(path.includes("?")?"&":"?")+"v="+DV:"");
const NATIONAL_CODE = "__NATIONAL__";

/* ---------- months and dates ---------- */
const ABBR=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const _MN=["January","February","March","April","May","June","July","August","September","October","November","December"];
const monthLabel = m => {const[y,mm]=m.split("-");return ABBR[+mm-1]+" "+y.slice(2);};
const tipMonth   = m => {const[y,mm]=m.split("-");return ABBR[+mm-1]+" "+y;};
const fullMonth  = m => {const[y,mm]=m.split("-");return _MN[+mm-1]+" "+y;};
const isoDate = iso => {const[y,m,d]=iso.slice(0,10).split("-");return (+d)+" "+_MN[+m-1]+" "+y;};
// "2024-07" (or "2024-07-01") -> "July 2024"; "" for a missing date.
const longDate = iso => { if(!iso) return ""; const[y,m]=iso.split("-"); return _MN[+m-1]+" "+y; };

/* ---------- numbers ---------- */
const fmtPct = p => (p*100).toFixed(1)+"%";
const commas = v => (+v).toLocaleString();
// Compact count: 7,093,502 -> "7.09M"; 97,388 -> "97.4k"; 154 -> "154".
function fmtCompact(v){
  v=+v;
  if(v>=1e6) return (v/1e6).toFixed(2)+"M";
  if(v>=1e4) return (v/1e3).toFixed(1)+"k";
  if(v>=1e3) return (v/1e3).toFixed(2)+"k";
  return commas(v);
}
// Axis tick label: 1.2k, 2k, 2.5M. One decimal where needed, so neighbouring ticks never
// read the same (until 2026-11-25 RTT rounded 1,200 and 1,600 to "1k" and "2k").
function fmtAxis(v){
  const f=(x,u)=> (x%1===0 ? x : +x.toFixed(1))+u;
  if(v>=1e6) return f(v/1e6,"M");
  if(v>=1e3) return f(v/1e3,"k");
  return ""+v;
}
// "Nice" top tick at 1/2/5 x 10^k >= max.
function niceTop(max){
  if(max<=0) return 1;
  const exp=Math.floor(Math.log10(max)), base=max/Math.pow(10,exp);
  const mult = base<=1?1 : base<=2?2 : base<=5?5 : 10;
  return mult*Math.pow(10,exp);
}
// Index of the last non-null value (-1 if none).
const lastIdx = arr => { for(let i=arr.length-1;i>=0;i--) if(arr[i]!=null) return i; return -1; };

/* ---------- time range ---------- */
// A FIXED rolling window (last-N months) so the chart stays a constant width as NHS
// publishes more months. "all" = the full series. Display-only: the cards' sparklines
// are unaffected. Each page holds RANGE (default "all", user decision 2026-11-20).
const RANGE_MONTHS={"3y":36,"12m":12,"all":Infinity};
function firstVisibleMonth(months){
  const n=RANGE_MONTHS[RANGE];
  return (!isFinite(n) || months.length<=n) ? months[0] : months[months.length-n];
}
// Clip parallel arrays to months >= minMonth (string compare on "YYYY-MM").
function clipObj(s, minMonth){
  if(!s||!s.months||!s.months.length||minMonth==null) return s;
  const i0=s.months.findIndex(m=>m>=minMonth);
  if(i0<=0) return s;
  const out=Object.assign({},s);
  for(const k of Object.keys(s)) if(Array.isArray(s[k])) out[k]=s[k].slice(i0);
  return out;
}
// Month labels along the x axis of a page's chart (BG = the page's chart box).
function xAxisSVG(xs, x){
  const {H}=BG; const n=xs.length;
  let xl=""; const lstep=Math.max(1,Math.ceil(n/8)), guard=Math.ceil(lstep*0.75);   // guard: the last two labels crowded in the "All" view
  for(let i=0;i<n-guard;i+=lstep) xl+=`<text x="${x(i).toFixed(1)}" y="${H-8}" font-size="10" text-anchor="middle" fill="var(--ink-soft)">${monthLabel(xs[i])}</text>`;
  xl+=`<text x="${x(n-1).toFixed(1)}" y="${H-8}" font-size="10" text-anchor="end" fill="var(--ink-soft)">${monthLabel(xs[n-1])}</text>`;
  return xl;
}
function setRange(r){ if(RANGE===r || !(r in RANGE_MONTHS)) return; RANGE=r; renderBig(); }
function setShowEngland(on){ if(SHOW_ENGLAND===on) return; SHOW_ENGLAND=on; renderBig(); }

/* ---------- organisation picker ---------- */
const ptypeOf = o => o.ptype==="independent" ? "independent" : "trust";  // absent => NHS trust

// Org type: National (no dropdown) / Providers / Commissioners. Switching to a
// provider/ICB type defaults to the first org alphabetically so the chart is
// never blank. `code` optionally selects a specific org (deep link).
async function setType(type, code){
  CURRENT_TYPE=type;
  document.querySelectorAll("#typebtns .typebtn").forEach(b=>{ const on=b.dataset.type===type;
    b.classList.toggle("active", on); b.setAttribute("aria-pressed", on?"true":"false"); });
  closeOrgPanel();
  const drop=document.getElementById("orgdrop");
  if(type==="national"){
    document.getElementById("ptyperow").hidden=true;
    drop.hidden=true;
    document.getElementById("orglabel").textContent="England — all-England";
    await loadNational(); return;
  }
  // Provider-type filter shows for Providers only. DEEP-LINK SAFETY: when a specific
  // org is requested, the filter FOLLOWS that org's type so the link is never hidden;
  // otherwise default to NHS Trusts.
  if(type==="provider"){
    const e=code && window._idx.find(o=>o.code===code);
    PTYPE = e ? ptypeOf(e) : "trust";
    document.getElementById("ptyperow").hidden=false;
    updatePtypeButtons();
  } else {
    document.getElementById("ptyperow").hidden=true;
  }
  drop.hidden=false;
  const list=orgListFor(type);
  // A direct ?org= link may target a HIDDEN org (negligible activity, out of the
  // picker but still reachable): resolve `code` against the FULL index, falling
  // back to the first visible org so the chart never blanks.
  const full=window._idx.filter(o=>o.level===type);
  const pick=(code && full.find(o=>o.code===code)) || list[0];
  if(!pick) return;
  document.getElementById("orglabel").textContent=`${pick.name} (${pick.code})`;
  buildOrgOptions("");
  await loadOrg(pick.code);
}
// Provider-type sub-filter: re-scope the dropdown to the chosen type, then select
// its first org so the chart never blanks.
function setPtype(pt){
  if(pt===PTYPE || CURRENT_TYPE!=="provider") return;
  PTYPE=pt; updatePtypeButtons(); closeOrgPanel();
  const list=orgListFor("provider"); buildOrgOptions("");
  if(list.length){ document.getElementById("orglabel").textContent=`${list[0].name} (${list[0].code})`; loadOrg(list[0].code); }
}
function updatePtypeButtons(){
  document.querySelectorAll("#ptypebtns .ptypebtn").forEach(b=>{ const on=b.dataset.ptype===PTYPE;
    b.classList.toggle("active", on); b.setAttribute("aria-pressed", on?"true":"false"); });
}
function orgListFor(type){
  let list=window._idx.filter(o=>o.level===type && !o.hidden);
  if(type==="provider") list=list.filter(o=>ptypeOf(o)===PTYPE);   // provider-type filter (ODS role)
  return list.sort((a,b)=>a.name.localeCompare(b.name));
}
// Commissioner sub-grouping off the org NAME: ICB vs Other (the national hubs).
// (Providers are scoped by the ODS-role provider-type FILTER, so they need no
// name-based NHS-Trust/Independent grouping inside the list.)
function orgGroupOf(o){
  if(o.former) return "Former organisations";   // closed/superseded per ODS — own group, still selectable
  if(o.stopped) return "Stopped reporting";      // A&E: figures end early (merged/closed, not flagged by ODS)
  const u=(o.name||"").toUpperCase();
  if(o.level==="icb") return u.includes("INTEGRATED CARE BOARD") ? "ICB" : "Other";
  return "Current";
}
function buildOrgOptions(q){
  const host=document.getElementById("orgoptions"); host.innerHTML="";
  q=(q||"").trim().toLowerCase();
  const list=orgListFor(CURRENT_TYPE).filter(o=> !q || o.name.toLowerCase().includes(q) || o.code.toLowerCase().includes(q));
  if(!list.length){ host.innerHTML='<div class="fgroup first">No matches</div>'; return; }
  // Providers: the type filter already scopes the list, so just split current vs
  // Former (no redundant type header). Commissioners keep ICB / Other / Former.
  // Former orgs stay SELECTABLE — their history is still viewable after a merger.
  const groups = CURRENT_TYPE==="icb"
    ? [["ICB","ICB"],["Other","Other"],["Former organisations","Former organisations"]]
    : [["Current",null],["Former organisations","Former organisations"],["Stopped reporting","Stopped reporting"]];
  let first=true;
  for(const [key,label] of groups){
    const items = key==="Current" ? list.filter(o=>!o.former && !o.stopped) : list.filter(o=>orgGroupOf(o)===key);
    if(!items.length) continue;
    if(label){ const h=document.createElement("div"); h.className="fgroup"+(first?" first":""); h.textContent=label; host.appendChild(h); }
    first=false;
    items.forEach(o=>{ const b=document.createElement("button");
      const on=CURRENT && CURRENT.code===o.code;
      b.className="fopt"+(on?" active":"");
      b.setAttribute("role","option"); b.setAttribute("aria-selected", on?"true":"false");
      b.textContent=`${o.name} (${o.code})`;
      b.onclick=()=>selectOrg(o); host.appendChild(b); });
  }
}
function selectOrg(o){
  document.getElementById("orglabel").textContent=`${o.name} (${o.code})`;
  closeOrgPanel(); loadOrg(o.code);
}
function toggleOrgPanel(){ const p=document.getElementById("orgpanel"); if(p && !p.hidden) closeOrgPanel(); else openOrgPanel(); }
function openOrgPanel(){
  if(CURRENT_TYPE==="national") return;
  const p=document.getElementById("orgpanel"), s=document.getElementById("orgsearch");
  s.value=""; buildOrgOptions("");
  p.hidden=false; document.getElementById("orgbtn").setAttribute("aria-expanded","true");
  // Reopen on an already-selected org: scroll it into view (it carries .active),
  // rather than starting from the top of a long list. Focus without scrolling so
  // the search box doesn't yank the panel back up.
  setTimeout(()=>{ const act=p.querySelector(".fopt.active"); if(act) act.scrollIntoView({block:"nearest"}); s.focus({preventScroll:true}); },0);
}
function closeOrgPanel(){ const p=document.getElementById("orgpanel"); if(p)p.hidden=true;
  const b=document.getElementById("orgbtn"); if(b)b.setAttribute("aria-expanded","false"); }
// Shared keyboard handler for in-panel search inputs: Enter picks first match, Esc closes.
function dropSearchKey(e){
  if(e.key==="Enter"){ const f=e.target.parentElement.querySelector(".fopt"); if(f){e.preventDefault();f.click();} }
  else if(e.key==="Escape"){ closeOrgPanel(); _hook("closeExtraPanels"); }
}

/* ---------- loading an organisation ---------- */
async function loadNational(){
  // national.json carries only the figures; wrap it with identifying fields.
  CURRENT = Object.assign({code:NATIONAL_CODE, name:"England", level:"national", region:"England", isNational:true}, NATIONAL);
  BREAKDOWN=null; _hook("onOrgSwitch");
  renderOrgNote(null);
  await _hook("ensureSlice");
  render();
}
async function loadOrg(code){
  CURRENT = await (await fetch(durl("org/"+code+".json"))).json();
  CURRENT.isNational=false;
  BREAKDOWN=null; _hook("onOrgSwitch");      // breakdown is per-org; refetch lazily
  renderOrgNote(code);
  await _hook("ensureSlice");
  render();
}
// Jump to a related org (a successor / predecessor from the ODS change note).
function goToOrg(code){
  const e=(window._idx||[]).find(o=>o.code===code);
  if(e) setType(e.level, e.code);
}
// Generic, auto-generated lifecycle note (no hand-written per-merger text), built
// from the index entry's ODS succession fields. Former → "Closed … — see related:";
// newly-formed → "Formed … — see related:". A page's submissionNoteHTML() hook adds
// its "did not submit" note. Hidden when there is nothing to say.
function renderOrgNote(code){
  const box=document.getElementById("orgnote");
  const e=code ? (window._idx||[]).find(o=>o.code===code) : null;
  const sub=code ? (_hook("submissionNoteHTML")||"") : "";
  if(!e || (!e.former && e.reltype!=="formed")){
    box.hidden=!sub; box.innerHTML=sub; return; }
  const rel=(e.related||[]).map(r=>{
    const known=(window._idx||[]).some(o=>o.code===r.code);
    return known ? `<button type="button" class="rellink" onclick="goToOrg('${r.code}')">${r.name}</button>`
                 : `${r.name} (${r.code})`;
  });
  let msg;
  if(e.former){
    const when=longDate(e.closed);
    msg=`<b>Closed${when?" "+when:""}.</b> This organisation was reorganised`+
        (rel.length?` — see related organisation${rel.length>1?"s":""}: ${rel.join(", ")}.`:".")+
        ` Its figures up to closure remain viewable here.`;
  } else {
    const when=longDate(e.opened);
    msg=`<b>Formed${when?" "+when:""}.</b> This is a new organisation`+
        (rel.length?`, drawing on the area${rel.length>1?"s":""} of: ${rel.join(", ")}.`:".")+
        ` Earlier months are not available under this code.`;
  }
  box.innerHTML=msg+(sub?"<br>"+sub:""); box.hidden=false;
}

/* ---------- legend and tooltips ---------- */
function legendSwatch(lineAttrs, markAttrs, square){
  const mark = !markAttrs ? ``
    : square ? `<rect x="10.5" y="2.5" width="7" height="7" ${markAttrs}/>`
             : `<circle cx="14" cy="6" r="3" ${markAttrs}/>`;
  return `<svg width="28" height="12" style="vertical-align:middle;margin-right:5px"><line x1="1" y1="6" x2="27" y2="6" fill="none" ${lineAttrs}/>${mark}</svg>`;
}
// `kind` is passed through to the page's showTip (RTT: "rate" | "count").
function attachTips(s, kind){
  const box=document.getElementById("chartbox");
  box.querySelectorAll(".pt").forEach(c=> c.addEventListener("mouseenter",()=>showTip(+c.dataset.i, s, kind)));
  // Keyboard parity: focusable hit targets show the same tooltip on focus.
  box.querySelectorAll(".pt.hit").forEach(c=>{
    c.addEventListener("focus",()=>showTip(+c.dataset.i, s, kind));
    c.addEventListener("blur",hideTip);
  });
  box.addEventListener("mouseleave",hideTip);
}
function hideTip(){const t=document.getElementById("tip"); if(t)t.style.display="none";}

/* ---------- expand modal ----------
   We MOVE the live .bigpanel into the overlay rather than cloning it, so the
   dropdowns, tooltips, legend and download control are the same nodes and keep
   working — no duplicated IDs, no re-wiring. On close we put it back where it was.
   The SVG is width:100% so it simply scales up in the wider modal; a renderBig()
   refresh keeps tooltips/markers consistent in the new box. */
let EXPANDED=false, _panelHome=null;
function setExpandLabel(){ const b=document.getElementById("expandbtn");
  if(b) b.innerHTML = EXPANDED ? "&#10529; Exit full screen" : "&#10530; Expand"; }
function modalFocusables(){
  const inner=document.querySelector("#chartmodal .modal-inner"); if(!inner) return [];
  return [...inner.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])')]
    .filter(el=>el.getClientRects().length);
}
function modalTrap(e){
  if(!EXPANDED || e.key!=="Tab") return;
  const f=modalFocusables(); if(!f.length) return;
  const first=f[0], last=f[f.length-1];
  if(e.shiftKey && document.activeElement===first){ e.preventDefault(); last.focus(); }
  else if(!e.shiftKey && document.activeElement===last){ e.preventDefault(); first.focus(); }
}
function openModal(){
  if(EXPANDED) return;
  const panel=document.getElementById("bigpanel");
  _panelHome={parent:panel.parentNode, next:panel.nextSibling};
  document.getElementById("modalhost").appendChild(panel);
  document.getElementById("chartmodal").hidden=false;
  document.body.style.overflow="hidden";
  // Hide + de-tab the rest of the page (the dialog content lives outside .wrap).
  document.querySelectorAll(".wrap,.sitebar,.sitefoot").forEach(el=>{ el.setAttribute("aria-hidden","true"); el.inert=true; });
  document.addEventListener("keydown", modalTrap, true);
  EXPANDED=true; setExpandLabel(); renderBig();
  setTimeout(()=>{ const c=document.getElementById("modalclose"); if(c) c.focus(); },0);
}
function closeModal(){
  if(!EXPANDED) return;
  const panel=document.getElementById("bigpanel");
  _hook("closeExtraPanels"); closeDlPanel();
  if(_panelHome) _panelHome.parent.insertBefore(panel, _panelHome.next);
  document.getElementById("chartmodal").hidden=true;
  document.body.style.overflow="";
  document.querySelectorAll(".wrap,.sitebar,.sitefoot").forEach(el=>{ el.removeAttribute("aria-hidden"); el.inert=false; });
  document.removeEventListener("keydown", modalTrap, true);
  EXPANDED=false; setExpandLabel(); renderBig();
  const eb=document.getElementById("expandbtn"); if(eb) eb.focus();   // return focus to the trigger
}
function toggleExpand(){ EXPANDED ? closeModal() : openModal(); }

/* ---------- small helpers ---------- */
// Resolve var(--x) → literal value from :root (canvas rasterisation ignores CSS vars).
function resolvePalette(str){
  const cs=getComputedStyle(document.documentElement);
  return str.replace(/var\((--[a-z-]+)\)/g, (m,k)=>{ const v=cs.getPropertyValue(k).trim(); return v||m; });
}
// XML-escape text node content (titles/labels may contain & < >).
function xmlEsc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
// Escape for use inside a double-quoted HTML/SVG attribute (adds the quote).
function escAttr(s){ return xmlEsc(s).replace(/"/g,"&quot;"); }
// Polite screen-reader announcement (chart updated / empty state). Re-set even to
// the same text by clearing first, so repeated identical updates are re-announced.
function announce(msg){ const l=document.getElementById("live"); if(!l) return;
  l.textContent=""; setTimeout(()=>{ l.textContent=msg; },30); }
// NHS org names are ALL-CAPS in source; title-case them for export titles, filenames
// and notes while preserving common acronyms.
function humanName(name){
  const keep=new Set(["NHS","FT","ICB","UHB","CCG","HMP","STH","MSK"]), small=new Set(["AND","OF","THE","FOR"]);
  return name.split(/\s+/).map(w=>{ const u=w.toUpperCase();
    return keep.has(u)?u : small.has(u)?w.toLowerCase() : w.charAt(0).toUpperCase()+w.slice(1).toLowerCase(); }).join(" ");
}

/* ---------- downloads: PNG, SVG and CSV ---------- */
function closeDlPanel(){ const p=document.getElementById("dlpanel"); if(p)p.hidden=true;
  const b=document.getElementById("dlbtn"); if(b)b.setAttribute("aria-expanded","false"); }
function toggleDlPanel(){ const p=document.getElementById("dlpanel");
  if(p && !p.hidden){ closeDlPanel(); return; }
  if(p){ document.getElementById("dlcsv").disabled=!CSV_DATA;
    p.hidden=false; document.getElementById("dlbtn").setAttribute("aria-expanded","true"); } }
/* CSV download of the figures in the current chart. Each data-table builder also records
   CSV_DATA from the SAME clipped series, so the download always matches what is on screen
   (organisation, filters, time range). Numbers are raw (no thousands separators, % to 2 dp)
   so they work in a spreadsheet. The page supplies csvFilter() and exportName(). */
let CSV_DATA=null;
const pct2 = p => p==null ? "" : +(p*100).toFixed(2);
function csvCell(v){ if(v==null) return ""; const t=String(v); return /[",\r\n]/.test(t) ? '"'+t.replace(/"/g,'""')+'"' : t; }
function downloadCSV(){
  if(!CSV_DATA) return;
  const ctx=[CURRENT.isNational?"England":humanName(CURRENT.name), CURRENT.isNational?"":CURRENT.code,
             document.getElementById("bigttl").textContent, csvFilter()];
  const head=["Organisation","Code","Chart","Selection",...CSV_DATA.header];
  const lines=[head, ...CSV_DATA.rows.map(r=>[...ctx,...r])].map(r=>r.map(csvCell).join(","));
  lines.push("", csvCell(`Source: NHS England, via ${location.origin}${location.pathname}. Downloaded ${new Date().toISOString().slice(0,10)}.`));
  // BOM so Excel opens accented names correctly.
  saveBlob(new Blob(["﻿"+lines.join("\r\n")+"\r\n"],{type:"text/csv;charset=utf-8"}), exportName("csv"));
}
function saveBlob(blob, name){
  const url=URL.createObjectURL(blob), a=document.createElement("a");
  a.href=url; a.download=name; document.body.appendChild(a); a.click();
  a.remove(); setTimeout(()=>URL.revokeObjectURL(url),1500);
}
// The page's buildExportSVG() returns the chart with its header as one SVG string.
function downloadChart(kind){
  const svg=buildExportSVG(); if(!svg) return;
  if(kind==="svg"){ saveBlob(new Blob([svg],{type:"image/svg+xml;charset=utf-8"}), exportName("svg")); return; }
  // PNG: rasterise the export SVG to a 2× canvas (white background already painted).
  const scale=2, {W,H}=BG, headerH=EXPORT_HEADER_H, w=W*scale, h=(H+headerH)*scale;
  const img=new Image();
  const url=URL.createObjectURL(new Blob([svg],{type:"image/svg+xml;charset=utf-8"}));
  img.onload=()=>{ const c=document.createElement("canvas"); c.width=w; c.height=h;
    const ctx=c.getContext("2d"); ctx.scale(scale,scale); ctx.drawImage(img,0,0);
    URL.revokeObjectURL(url); c.toBlob(b=>{ if(b) saveBlob(b, exportName("png")); }, "image/png"); };
  img.onerror=()=>URL.revokeObjectURL(url);
  img.src=url;
}
