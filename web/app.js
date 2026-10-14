/* Skrift front end: no build step, plain JS. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");
let cleanup = []; // functions to run when leaving a view

function clock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
function ago(t) {
  const d = (Date.now() / 1000 - t);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} hr ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: d > 300 * 86400 ? "numeric" : undefined });
}
function eta(sec) {
  if (!sec) return "";
  if (sec < 60) return "under a minute left";
  if (sec < 3600) return `about ${Math.round(sec / 60)} min left`;
  return `about ${(sec / 3600).toFixed(1)} hr left`;
}
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: opts.body && typeof opts.body === "string" ? { "Content-Type": "application/json" } : {}, ...opts });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}
let toastTimer;
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}
const ICON = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="4" width="5" height="16" rx="1.5"/><rect x="14" y="4" width="5" height="16" rx="1.5"/></svg>',
};

/* ------------------------------------------------------------------ router */
function go(path, replace = false) {
  if (replace) history.replaceState({}, "", path); else history.pushState({}, "", path);
  route();
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a || a.target === "_blank" || e.metaKey || e.ctrlKey || a.hasAttribute("download")) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.preventDefault();
  go(url.pathname + url.search);
});
window.addEventListener("popstate", route);

function route() {
  cleanup.forEach((f) => f());
  cleanup = [];
  const p = location.pathname;
  $$("nav a").forEach((a) => a.classList.toggle("on", (a.dataset.nav === "home" && (p === "/" || p.startsWith("/t/"))) || (a.dataset.nav !== "home" && p.startsWith("/" + a.dataset.nav))));
  window.scrollTo(0, 0);
  if (p.startsWith("/t/")) return transcriptView(p.split("/")[2]);
  if (p.startsWith("/search")) return searchView();
  if (p.startsWith("/settings")) return settingsView();
  return homeView();
}

/* ------------------------------------------------------------------ home */
const uploads = new Map(); // temp id -> {name, pct}

function jobCard(j) {
  const m = j.meta || {};
  const thumb = j.thumb ? `style="background-image:url('/api/jobs/${j.id}/thumb')"` : "";
  const icon = j.thumb ? "" : (j.source === "file" ? "🎞" : "▶");
  const metaBits = [m.channel && esc(m.channel), m.duration ? clock(m.duration) : "", ago(j.created)].filter(Boolean).join(" · ");
  let side = "", extra = "";
  if (j.status === "done") {
    const n = j.stats?.reviews ?? 0;
    side = n ? `<span class="pill warn">${n} to check</span>` :
      j.stats?.second_engine ? `<span class="pill ok">✓ Checked</span>` : `<span class="pill">Single engine</span>`;
  } else if (j.status === "error") {
    side = `<span class="pill err">Failed</span><button class="btn sm" data-retry="${j.id}">Retry</button>`;
    extra = `<div class="err">${esc(j.error || "")}</div>`;
  } else if (j.status === "cancelled") {
    side = `<span class="pill">Cancelled</span><button class="btn sm" data-retry="${j.id}">Restart</button>`;
  } else {
    side = `<button class="btn sm ghost" data-cancel="${j.id}">Cancel</button>`;
    extra = `<div class="meta"><span>${esc(j.stage || "")}</span>${j.eta ? `<span class="faint">${eta(j.eta)}</span>` : ""}</div>
      <div class="bar"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div>`;
  }
  const tag = j.status === "done" ? "a" : "div";
  return `<${tag} class="card job" ${tag === "a" ? `href="/t/${j.id}"` : ""} data-id="${j.id}">
    <div class="thumb" ${thumb}>${icon}</div>
    <div><h3>${esc(m.title || "Untitled")}</h3><div class="meta">${metaBits}</div>${extra}</div>
    <div class="side">${side}${j.status !== "done" && !["queued", "downloading", "converting", "transcribing", "checking", "uploading"].includes(j.status) ? `<button class="btn sm ghost" data-del="${j.id}" title="Delete">✕</button>` : ""}</div>
  </${tag}>`;
}

async function homeView() {
  document.title = "Skrift";
  const s = await api("/api/settings").catch(() => ({}));
  view.innerHTML = `
    <section class="card hero">
      <h1>New transcript</h1>
      <p class="sub">Paste YouTube or Twitch links (one per line), or drop a video or audio file.</p>
      <div class="drop" id="drop">
        <textarea id="url" rows="2" placeholder="https://www.youtube.com/watch?v=…   or   https://www.twitch.tv/videos/…" spellcheck="false"></textarea>
        <label class="btn sm pick">Choose file<input type="file" id="file" accept="video/*,audio/*,.mkv,.flv,.ts" multiple hidden></label>
      </div>
      <div class="opts">
        <div>
          <label class="field">Accuracy</label>
          <div class="seg" id="mode">
            <button data-v="accurate" title="Two engines transcribe independently; every disagreement is flagged">Cross-checked</button>
            <button data-v="fast" title="One engine, about 3× faster, no cross-check">Fast</button>
          </div>
        </div>
        <div>
          <label class="field">Language</label>
          <div class="seg" id="lang"><button data-v="en" class="on">English</button><button data-v="auto">Other</button></div>
        </div>
        <div class="grow">
          <label class="field">Names &amp; terms in this video <span class="faint">(optional)</span></label>
          <input type="text" id="vocab" placeholder="e.g. Elden Ring, Nightreign, Kojima" />
        </div>
        <div class="go"><label class="field">&nbsp;</label><button class="btn primary" id="start">Transcribe</button></div>
      </div>
    </section>
    <div id="setup"></div>
    <div id="active"></div>
    <div class="section-h"><h2>Your transcripts</h2><span class="faint" id="count"></span></div>
    <div class="jobs" id="done"></div>`;

  let mode = s.default_mode || "accurate", lang = "en";
  const setSeg = (id, v) => $$(`#${id} button`).forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  setSeg("mode", mode);
  $("#mode").onclick = (e) => { if (e.target.dataset.v) { mode = e.target.dataset.v; setSeg("mode", mode); } };
  $("#lang").onclick = (e) => { if (e.target.dataset.v) { lang = e.target.dataset.v; setSeg("lang", lang); } };

  const start = async () => {
    const url = $("#url").value.trim();
    if (!url) return toast("Paste a link first, or drop a file.");
    $("#start").disabled = true;
    try {
      const r = await api("/api/jobs", { method: "POST", body: JSON.stringify({ url, mode, language: lang, vocab: $("#vocab").value }) });
      $("#url").value = "";
      toast(r.jobs.length > 1 ? `${r.jobs.length} videos added` : "Added. Working on it…");
      refresh();
    } catch (e) { toast(e.message, 4000); }
    $("#start").disabled = false;
  };
  $("#start").onclick = start;
  $("#url").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); start(); } });

  const sendFiles = (files) => [...files].forEach((f) => uploadFile(f, mode, lang, $("#vocab").value, refresh));
  $("#file").onchange = (e) => { sendFiles(e.target.files); e.target.value = ""; };
  const drop = $("#drop");
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) sendFiles(e.dataTransfer.files); else if (e.dataTransfer.getData("text")) $("#url").value = e.dataTransfer.getData("text"); });

  const onJobBtn = async (e) => {
    const b = e.target.closest("button[data-retry],button[data-cancel],button[data-del]");
    if (!b) return;
    e.preventDefault();
    e.stopPropagation();
    if (b.dataset.retry) await api(`/api/jobs/${b.dataset.retry}/retry`, { method: "POST" });
    if (b.dataset.cancel && confirm("Stop this transcription?")) await api(`/api/jobs/${b.dataset.cancel}/cancel`, { method: "POST" });
    if (b.dataset.del && confirm("Delete this transcript? This can't be undone.")) await api(`/api/jobs/${b.dataset.del}`, { method: "DELETE" });
    refresh();
  };
  view.addEventListener("click", onJobBtn);
  cleanup.push(() => view.removeEventListener("click", onJobBtn));

  // first run: the engines and models download once (several GB); show it so nobody thinks it's stuck
  let setupTimer = null;
  async function refreshSetup() {
    let st;
    try { st = await api("/api/setup"); } catch { setupTimer = setTimeout(refreshSetup, 4000); return; }
    const box = $("#setup");
    if (!box) return;
    if (st.ready) { box.innerHTML = ""; return; }
    const gb = (b) => (b / 1e9).toFixed(1);
    const done = st.items.reduce((a, i) => a + (i.done || 0), 0), total = st.items.reduce((a, i) => a + (i.total || 0), 0);
    box.innerHTML = `<section class="card" style="padding:18px;margin-bottom:18px">
      <h2 style="font-size:17px;margin-bottom:4px">Getting Skrift ready <span class="faint" style="font-weight:600">(first time only)</span></h2>
      <p class="muted" style="margin:0 0 10px;font-size:14px">${st.error ? `<span style="color:var(--danger)">${esc(st.error)}</span>` :
        "Downloading the speech engines and AI model. They stay on this computer, so this only happens once. You can paste links now; they'll start as soon as this finishes."}</p>
      ${total ? `<div class="bar"><i style="width:${Math.round((done / total) * 100)}%"></i></div><p class="faint" style="font-size:13px;margin:6px 0 0">${gb(done)} of ${gb(total)} GB</p>` : ""}
      <div style="display:grid;gap:3px;margin-top:10px;font-size:13.5px">${st.items.map((i) => `<div>${i.ready ? "✓" : "…"} ${esc(i.label)}</div>`).join("")}</div>
    </section>`;
    setupTimer = setTimeout(refreshSetup, 1500);
  }
  refreshSetup();
  cleanup.push(() => clearTimeout(setupTimer));

  let timer = null;
  async function refresh() {
    clearTimeout(timer);
    let data;
    try { data = await api("/api/jobs"); } catch { timer = setTimeout(refresh, 4000); return; }
    if (!$("#active")) return;
    const active = data.jobs.filter((j) => ["queued", "downloading", "converting", "transcribing", "checking", "uploading"].includes(j.status));
    const rest = data.jobs.filter((j) => !active.includes(j));
    const up = [...uploads.values()].map((u) => `<div class="card job"><div class="thumb">⬆</div><div><h3>${esc(u.name)}</h3><div class="meta">Uploading… ${u.pct}%</div><div class="bar"><i style="width:${u.pct}%"></i></div></div><div></div></div>`).join("");
    $("#active").innerHTML = active.length || uploads.size ? `<div class="section-h"><h2>In progress</h2></div><div class="jobs">${up}${active.map(jobCard).join("")}</div>` : "";
    $("#done").innerHTML = rest.length ? rest.map(jobCard).join("") : `<div class="empty">Nothing here yet. Paste a link above to make your first transcript.</div>`;
    $("#count").textContent = rest.length ? `${rest.length}` : "";
    if (active.length || uploads.size) timer = setTimeout(refresh, 1500);
  }
  refresh();
  const onFocus = () => refresh();
  window.addEventListener("focus", onFocus);
  cleanup.push(() => { clearTimeout(timer); window.removeEventListener("focus", onFocus); });
  homeView.refresh = refresh;
}

function uploadFile(file, mode, lang, vocab, refresh) {
  const tmp = Math.random().toString(36).slice(2);
  uploads.set(tmp, { name: file.name, pct: 0 });
  const xhr = new XMLHttpRequest();
  const q = new URLSearchParams({ name: file.name, mode, language: lang, vocab });
  xhr.open("POST", `/api/upload?${q}`);
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) uploads.get(tmp).pct = Math.round((e.loaded / e.total) * 100);
    const card = [...$$(".job h3")].find((h) => h.textContent === file.name);
    if (card) { const m = card.parentElement; $(".meta", m).textContent = `Uploading… ${uploads.get(tmp).pct}%`; $(".bar i", m).style.width = uploads.get(tmp).pct + "%"; }
  };
  xhr.onloadend = () => {
    uploads.delete(tmp);
    if (xhr.status !== 200) toast("Upload failed: " + (xhr.responseText || xhr.status), 4000);
    refresh();
  };
  xhr.send(file);
  refresh();
}

/* ------------------------------------------------------------------ transcript */
const PH = {
  differ: "Engines disagree",
  missing: "Only the second engine heard this",
  spelling: "Spelling fixed from your names list / video description",
  single: "Only one engine heard this",
  unsure: "Low confidence",
  voted: "Settled by vote",
  ai: "AI picked by meaning",
  lonely: "Only one engine heard this",
};

async function transcriptView(id) {
  view.innerHTML = `<div class="empty">Loading…</div>`;
  let job;
  try { job = await api(`/api/jobs/${id}`); } catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  if (job.status !== "done") return pendingView(job);
  document.title = (job.meta.title || "Transcript") + " · Skrift";

  const T = { job, words: job.transcript.words, reviews: job.transcript.reviews, version: job.transcript.version || 1 };
  const byId = new Map(T.reviews.map((r) => [r.id, r]));
  const undo = [];
  let sel = null; // {type:'review', id} | {type:'word', i}
  let showSingle = false, follow = true, stopAt = null, saveTimer = null, saving = Promise.resolve(), dirty = false;
  let spans = [], gaps = new Map(), lastScroll = 0, nowIdx = -1;

  const m = job.meta;
  const crossChecked = !!job.stats?.second_engine; // what actually ran, not what was picked
  const third = job.stats?.third_engine === "youtube-captions" ? "YouTube" : job.stats?.third_engine ? "Whisper large-v3" : null;
  view.innerHTML = `
    <div class="tv-head">
      <div class="thumb" ${job.thumb ? `style="background-image:url('/api/jobs/${id}/thumb')"` : ""}>${job.thumb ? "" : "🎞"}</div>
      <div>
        <h1 id="title" title="Click to rename">${esc(m.title || "Untitled")}</h1>
        <div class="meta">
          ${m.channel ? `<span>${esc(m.channel)}</span>` : ""}
          ${m.duration ? `<span>${clock(m.duration)}</span>` : ""}
          ${m.upload_date ? `<span>${esc(m.upload_date)}</span>` : ""}
          ${m.webpage_url ? `<a href="${esc(m.webpage_url)}" target="_blank" rel="noopener">Open original ↗</a>` : ""}
        </div>
        <div class="stats" id="stats"></div>
      </div>
    </div>
    <div class="toolbar">
      <div class="reviewnav" id="rnav">
        <button class="btn sm" id="prev" title="Previous (P)">‹</button>
        <span class="count" id="rcount"></span>
        <button class="btn sm" id="next" title="Next spot to check (N)">Next ›</button>
      </div>
      <button class="btn sm" id="findBtn" title="Find & replace (⌘F)">Find &amp; replace</button>
      <button class="btn sm" id="namesBtn" title="Every unusual word and name, for a quick spelling scan">Names</button>
      <span class="saving" id="saving"></span>
      <span class="spacer"></span>
      <div class="menu" id="moreMenu">
        <button class="btn sm ghost" id="moreBtn">⋯</button>
        <div class="pop hidden" id="morePop">
          <button class="item" id="toggleSingle">Show words only one engine heard</button>
          <button class="item" id="copyAll">Copy all text</button>
          <button class="item" id="rerun">Transcribe again…</button>
          <button class="item" id="delJob" style="color:var(--danger)">Delete transcript</button>
        </div>
      </div>
      <div class="menu" id="expMenu">
        <button class="btn sm primary" id="expBtn">Export ▾</button>
        <div class="pop hidden" id="expPop">
          ${[["srt", "Subtitles"], ["vtt", "Web subtitles"], ["docx", "Word document"], ["pdf", "PDF"], ["txt", "Plain text"], ["md", "Markdown"], ["json", "JSON with word timings"]]
            .map(([f, n]) => `<a href="/api/jobs/${id}/export/${f}" data-fmt="${f}" download><span>${n}</span><span class="ext">.${f}</span></a>`).join("")}
          <label class="opt"><input type="checkbox" id="stampOpt" checked> Timestamps in Word / PDF / text</label>
          ${job.transcript.sounds?.length ? `<label class="opt" style="border-top:0;margin-top:0;padding-top:2px"><input type="checkbox" id="soundOpt" checked> Sound tags in subtitles ([laughter], [music])</label>` : ""}
          <label class="opt" style="border-top:0;margin-top:0;padding-top:2px" title="Writes swears as f***, s***. YouTube's own captions bleep them too."><input type="checkbox" id="bleepOpt"> Bleep swear words</label>
        </div>
      </div>
    </div>
    <div class="legend" id="legend">
      ${crossChecked ? `<span><i class="sw differ"></i>Engines disagree</span><span><i class="sw gap"></i>Possibly missed words</span>`
        : job.stats?.primary_engine?.startsWith("whisper") ? `<span><i class="sw differ"></i>Low confidence</span><span class="faint">One engine (${esc(job.stats.language || "auto")}), not cross-checked</span>`
        : `<span class="faint">Fast mode: one engine, not cross-checked</span>`}
      <span><i class="sw spell"></i>Auto-fixed spelling</span>
      ${third ? `<span title="Two of three engines agreed, so it was settled automatically. Click to see or undo."><i class="sw voted"></i>Settled by vote</span>` : ""}
      ${job.stats?.meaning ? `<span title="Where the engines disagreed, a local AI read the conversation and picked the version that makes sense"><i class="sw ai"></i>AI picked by meaning</span>` : ""}
      <span class="faint">Click any word to hear it or fix it.</span>
    </div>
    <article class="doc" id="doc"></article>
    <div class="dock">
      <div id="rcard"></div>
      <div class="player">
        <button class="play" id="play" title="Play / pause (space)">${ICON.play}</button>
        <button class="btn sm ghost hide-sm" id="back5" title="Back 5 s (←)">−5</button>
        <span class="time" id="cur">0:00</span>
        <input type="range" id="seek" min="0" max="1000" value="0" step="1">
        <span class="time hide-sm" id="dur">${clock(m.duration)}</span>
        <select id="speed" title="Playback speed"><option>1</option><option>1.25</option><option>1.5</option><option>1.75</option><option>2</option></select>
        <button class="btn sm ghost hide-sm" id="followBtn" title="Scroll along with the audio">Follow ✓</button>
      </div>
    </div>
    <div class="panel hidden" id="panel"></div>
    <audio id="audio" src="/api/jobs/${id}/audio" preload="metadata"></audio>`;

  const audio = $("#audio"), doc = $("#doc");

  /* ---------- review bookkeeping */
  function index() {
    const where = new Map();
    gaps = new Map();
    T.words.forEach((w, i) => {
      if (w.r != null && !where.has(w.r)) where.set(w.r, i);
      if (w.g != null) gaps.set(w.g, i);
    });
    for (const [rid, i] of gaps) if (!where.has(rid)) where.set(rid, i);
    return where;
  }
  function openReviews() {
    const where = index();
    return T.reviews.filter((r) => !r.done && r.kind !== "single" && r.kind !== "voted" && where.has(r.id))
      .map((r) => ({ r, at: where.get(r.id) })).sort((a, b) => a.at - b.at);
  }
  function rangeOf(rid) {
    let a = -1, b = -1;
    T.words.forEach((w, i) => { if (w.r === rid) { if (a < 0) a = i; b = i + 1; } });
    return a < 0 ? null : [a, b];
  }
  function cls(w) {
    const r = w.r != null ? byId.get(w.r) : null;
    let c = "w";
    if (r && !r.done) {
      if (r.kind === "differ" || r.kind === "unsure" || r.kind === "lonely") c += " differ";
      else if (r.kind === "ai") c += " ai";
      else if (r.kind === "voted") c += " voted";
      else if (r.kind === "spelling") c += " spell";
      else if (r.kind === "single" && showSingle) c += " single";
    } else if (w.u) c += " fixed";
    return c;
  }

  /* ---------- rendering */
  function paraBounds() {
    const out = [];
    let start = 0;
    T.words.forEach((w, i) => { if (i > 0 && w.p) { out.push([start, i]); start = i; } });
    if (T.words.length) out.push([start, T.words.length]);
    return out;
  }
  function paraHTML(a, b, k) {
    let h = "";
    for (let i = a; i < b; i++) {
      const w = T.words[i];
      if (w.g != null) {
        const r = byId.get(w.g);
        if (r && !r.done) h += `<span class="gapmark" data-gap="${r.id}" title="The second engine heard: ${esc(r.alt)}">+</span>`;
      }
      h += `<span class="${cls(w)}">${esc(w.t)}</span> `;
    }
    return `<div class="para" data-k="${k}"><div class="stamp" data-s="${T.words[a].s}">${clock(T.words[a].s)}<span class="edit" data-edit="${k}">edit</span></div><div class="txt">${h}</div></div>`;
  }
  function render() {
    const bounds = paraBounds();
    doc.innerHTML = bounds.map(([a, b], k) => paraHTML(a, b, k)).join("") || `<div class="empty">No speech was found in this audio.</div>`;
    spans = $$(".w", doc);
    nowIdx = -1;
    updateStats();
    markSel();
  }
  function rerenderPara(i) {
    const bounds = paraBounds();
    const k = bounds.findIndex(([a, b]) => i >= a && i < b);
    const el = $$(".para", doc)[k];
    if (k < 0 || !el || bounds.length !== $$(".para", doc).length) return render();
    const tmp = document.createElement("div");
    tmp.innerHTML = paraHTML(bounds[k][0], bounds[k][1], k);
    el.replaceWith(tmp.firstChild);
    spans = $$(".w", doc);
    updateStats();
    markSel();
  }
  function updateStats() {
    const open = openReviews();
    const n = open.length;
    $("#rcount").textContent = n ? `${n} to check` : crossChecked ? "All checked ✓" : "Not cross-checked";
    $("#rnav").classList.toggle("clear", !n);
    $("#prev").classList.toggle("hidden", !n);
    $("#next").classList.toggle("hidden", !n);
    const st = job.stats || {};
    const conf = st.confirmed != null ? `<span class="pill ok" title="Words at least two engines heard identically">${(st.confirmed * 100).toFixed(1)}% confirmed by ${third ? "2+ engines" : "both engines"}</span>` : "";
    $("#stats").innerHTML = `<span class="pill">${T.words.length.toLocaleString()} words</span>${conf}` +
      (st.spelling_fixes ? `<span class="pill" title="Names corrected using your names list and the video description">${st.spelling_fixes} names auto-spelled</span>` : "") +
      (third ? `<span class="pill" title="Wherever Parakeet and Whisper disagreed, ${third} cast the deciding vote">3 engines · ${third} as tie-breaker</span>` : "") +
      (st.vote ? `<span class="pill ok" title="Disagreements settled automatically because two of three engines agreed">${(st.vote.fixed || 0) + (st.vote.inserted || 0) + (st.vote.kept || 0)} settled by vote</span>` : "") +
      (st.meaning?.asked ? `<span class="pill" title="A local AI read each remaining disagreement in context">AI read ${st.meaning.asked} spots, changed ${st.meaning.changed}</span>` : "");
  }

  /* ---------- selection + review card */
  function markSel() {
    $$(".w.sel,.gapmark.sel", doc).forEach((e) => e.classList.remove("sel"));
    if (!sel) return;
    if (sel.type === "word") spans[sel.i]?.classList.add("sel");
    else {
      const rg = rangeOf(sel.id);
      if (rg) for (let i = rg[0]; i < rg[1]; i++) spans[i]?.classList.add("sel");
      $(`.gapmark[data-gap="${sel.id}"]`, doc)?.classList.add("sel");
    }
  }
  function ctxHTML(a, b, insert) {
    const L = Math.max(0, a - 8), R = Math.min(T.words.length, b + 8);
    const before = T.words.slice(L, a).map((w) => esc(w.t)).join(" ");
    const mid = insert != null ? `<b style="background:rgba(255,95,193,.25)">+ ${esc(insert)}</b>` : `<b>${T.words.slice(a, b).map((w) => esc(w.t)).join(" ")}</b>`;
    const after = T.words.slice(b, R).map((w) => esc(w.t)).join(" ");
    return `${L > 0 ? "… " : ""}${before} ${mid} ${after}${R < T.words.length ? " …" : ""}`;
  }
  function select(s, { play = false, scroll = true } = {}) {
    sel = s;
    markSel();
    const card = $("#rcard");
    if (!s) { card.innerHTML = ""; return; }
    let a, b, html;
    if (s.type === "review") {
      const r = byId.get(s.id);
      index();
      const rg = rangeOf(r.id);
      if (!rg && r.kind !== "missing") { sel = null; card.innerHTML = ""; return; }
      const at = r.kind === "missing" ? gaps.get(r.id) : rg[0];
      a = rg ? rg[0] : at; b = rg ? rg[1] : at;
      const cur = T.words.slice(a, b).map((w) => w.t).join(" ");
      const open = openReviews(), pos = open.findIndex((o) => o.r.id === r.id);
      const counter = pos >= 0 ? `<span class="faint" style="float:right;margin-right:34px">${pos + 1} of ${open.length}</span>` : "";
      if (r.kind === "differ") {
        html = `<div class="kind">${PH.differ} · ${clock(T.words[a].s)} ${counter}</div>
          <div class="ctx">${ctxHTML(a, b)}</div>
          <div class="choices">
            <button class="choice" data-act="keep"><small><span>Parakeet (in transcript)</span><kbd class="kbd">Enter</kbd></small><span class="t">${esc(cur)}</span></button>
            ${r.alt ? `<button class="choice" data-act="alt"><small><span>${r.alt_from ? esc(r.alt_from) : "Whisper"} heard</span><kbd class="kbd">A</kbd></small><span class="t">${esc(r.alt)}</span></button>` : ""}
            ${r.alt2 ? `<button class="choice" data-act="alt2"><small><span>${third} heard</span><kbd class="kbd">Y</kbd></small><span class="t">${esc(r.alt2)}</span></button>` : ""}
          </div>`;
      } else if (r.kind === "missing") {
        html = `<div class="kind gap">${r.src === "youtube" ? `Only ${third || "YouTube"} heard this` : PH.missing} · ${clock(r.s)} ${counter}</div>
          <div class="ctx">${ctxHTML(a, a, r.alt)}</div>
          <div class="choices">
            <button class="choice" data-act="keep"><small><span>Leave it out</span><kbd class="kbd">Enter</kbd></small><span class="t">Nothing was said there</span></button>
            <button class="choice" data-act="alt"><small><span>Add it</span><kbd class="kbd">A</kbd></small><span class="t">${esc(r.alt)}</span></button>
          </div>`;
      } else if (r.kind === "spelling") {
        html = `<div class="kind spell">${PH.spelling} · ${clock(T.words[a].s)} ${counter}</div>
          <div class="ctx">${ctxHTML(a, b)}</div>
          <div class="choices">
            <button class="choice" data-act="keep"><small><span>Keep the fix</span><kbd class="kbd">Enter</kbd></small><span class="t">${esc(cur)}</span></button>
            <button class="choice" data-act="alt"><small><span>Put back what was heard</span><kbd class="kbd">A</kbd></small><span class="t">${esc(r.alt)}</span></button>
          </div>`;
      } else if (r.kind === "ai") {
        const agreed = r.ai_from === "Parakeet" || r.ai_from === "Parakeet + Whisper";
        html = `<div class="kind ai">${r.suggest ? "AI suggestion" : PH.ai} · ${clock(T.words[a].s)} ${counter}</div>
          <div class="ctx">${ctxHTML(a, b)}</div>
          <p class="muted" style="margin:-4px 0 10px;font-size:13.5px">${r.suggest
            ? `Parakeet and Whisper heard “${esc(cur)}”, ${esc(third || "the third engine")} spelled it “${esc(r.alt)}”. The AI thinks “${esc(r.alt)}” may fit better, but names are easy to get wrong, so the transcript wasn't changed. Your call.`
            : r.homophone
            ? `Parakeet and Whisper heard “${esc(r.ai_from === "Parakeet + Whisper" ? cur : r.alt)}”, ${esc(third || "the third engine")} heard a word that sounds the same. The AI read the conversation and picked what makes sense.`
            : agreed ? "The engines disagreed here. The AI read the conversation and thinks the transcript already has it right."
            : "The engines disagreed here. The AI read the conversation and picked the version that makes sense."}${r.suggest ? "" : " It's right most of the time, not always, so give it a listen."}</p>
          <div class="choices">
            <button class="choice" data-act="keep"><small><span>${r.suggest ? "Keep · " : "AI pick · "}${esc(r.ai_from || "")}</span><kbd class="kbd">Enter</kbd></small><span class="t">${esc(cur)}</span></button>
            ${r.alt ? `<button class="choice" data-act="alt"><small><span>${r.suggest ? "AI suggests · " : ""}${esc(r.alt_from || "Other")}${r.suggest ? "" : " heard"}</span><kbd class="kbd">A</kbd></small><span class="t">${esc(r.alt)}</span></button>` : ""}
            ${r.alt2 ? `<button class="choice" data-act="alt2"><small><span>${esc(r.alt2_from || "Other")} heard</span><kbd class="kbd">Y</kbd></small><span class="t">${esc(r.alt2)}</span></button>` : ""}
          </div>`;
      } else if (r.kind === "voted") {
        const added = r.was === "missing";
        html = `<div class="kind vote">${PH.voted} · ${clock(T.words[a].s)}</div>
          <div class="ctx">${ctxHTML(a, b)}</div>
          <p class="muted" style="margin:-4px 0 10px;font-size:13.5px">${added ? `Whisper and ${third} both heard these words, but Parakeet missed them, so they were added.`
            : `Whisper and ${third} both heard this, so it replaced what Parakeet heard.`}</p>
          <div class="choices">
            <button class="choice" data-act="keep"><small><span>Keep it</span><kbd class="kbd">Enter</kbd></small><span class="t">${esc(cur)}</span></button>
            <button class="choice" data-act="alt"><small><span>${added ? "Remove it" : "Put back Parakeet's version"}</span><kbd class="kbd">A</kbd></small><span class="t">${added ? "(nothing)" : esc(r.alt)}</span></button>
          </div>`;
      } else if (r.kind === "lonely") {
        html = `<div class="kind">${PH.lonely} · ${clock(T.words[a].s)} ${counter}</div>
          <div class="ctx">${ctxHTML(a, b)}</div>
          <p class="muted" style="margin:-4px 0 10px;font-size:13.5px">Parakeet heard this, but Whisper and ${third} heard nothing here. It may be background noise.</p>
          <div class="choices">
            <button class="choice" data-act="keep"><small><span>Keep it</span><kbd class="kbd">Enter</kbd></small><span class="t">${esc(cur)}</span></button>
            <button class="choice" data-act="alt"><small><span>Remove it</span><kbd class="kbd">A</kbd></small><span class="t">(nothing)</span></button>
          </div>`;
      } else if (r.kind === "unsure") {
        html = `<div class="kind">${PH.unsure} · ${clock(T.words[a].s)} ${counter}</div><div class="ctx">${ctxHTML(a, b)}</div>
          <div class="choices"><button class="choice" data-act="keep"><small><span>Looks right</span><kbd class="kbd">Enter</kbd></small><span class="t">${esc(cur)}</span></button></div>`;
      } else {
        html = `<div class="kind">${PH.single} · ${clock(T.words[a].s)}</div><div class="ctx">${ctxHTML(a, b)}</div>
          <div class="choices"><button class="choice" data-act="keep"><small><span>Looks right</span><kbd class="kbd">Enter</kbd></small><span class="t">${esc(cur)}</span></button></div>`;
      }
      html += `<div class="row"><input type="text" id="fix" value="${esc(r.kind === "missing" ? r.alt : cur)}" placeholder="Type the correct words">
        <button class="btn sm" data-act="save">Save edit</button><button class="btn sm ghost" data-act="replay" title="Replay (R)">▶ Replay <kbd>R</kbd></button>
        <button class="btn sm ghost" data-act="skip" title="Next (N)">Skip <kbd>N</kbd></button></div>`;
    } else {
      a = s.i; b = s.i + 1;
      const w = T.words[s.i];
      html = `<div class="kind word">Word at ${clock(w.s)}${w.ok ? " · both engines agree" : ""}</div>
        <div class="row"><input type="text" id="fix" value="${esc(w.t)}">
        <button class="btn sm" data-act="save">Save</button>
        <button class="btn sm ghost" data-act="replay">▶ Replay <kbd>R</kbd></button>
        <button class="btn sm ghost" data-act="playfrom">Play from here</button>
        <button class="btn sm ghost danger" data-act="delete">Delete word</button></div>`;
    }
    card.innerHTML = `<div class="rcard"><button class="btn sm ghost x" data-act="close" title="Close (Esc)">✕</button>${html}</div>`;
    const target = spans[a] || spans[Math.min(a, spans.length - 1)];
    if (scroll && target) ensureVisible(target, true);
    if (play) replay();
  }
  // keep a word between the sticky toolbar and the bottom dock (their heights differ on phones)
  // (off-screen paragraphs skip layout for speed, so positions settle only after they render:
  //  jump, then re-measure for a couple of frames and correct)
  function ensureVisible(el, center, tries = 3) {
    const r = el.getBoundingClientRect();
    const top = $(".toolbar").getBoundingClientRect().bottom + 12;
    const bottom = $(".dock").getBoundingClientRect().top - 12;
    if (r.top >= top && r.bottom <= bottom) return;
    const want = center ? top + Math.max(0, (bottom - top) / 2 - r.height / 2) : top + (bottom - top) * 0.3;
    window.scrollTo({ top: window.scrollY + r.top - want, behavior: "instant" });
    if (tries > 1) requestAnimationFrame(() => requestAnimationFrame(() => ensureVisible(el, center, tries - 1)));
  }
  function selRange() {
    if (!sel) return null;
    if (sel.type === "word") return [sel.i, sel.i + 1];
    const r = byId.get(sel.id);
    if (r.kind === "missing") { const g = gaps.get(r.id); return [g, g]; }
    return rangeOf(r.id);
  }
  function replay() {
    const rg = selRange();
    if (!rg) return;
    const [a, b] = rg;
    const s = T.words[Math.max(0, Math.min(a, T.words.length - 1))].s;
    const e = b > a ? T.words[b - 1].e : (T.words[a]?.s ?? s);
    const start = Math.max(0, (a > 0 && b === a ? T.words[a - 1].s : s) - 1.2);
    playRange(start, e + 0.9);
  }
  function playRange(s, e) {
    audio.currentTime = s;
    stopAt = e;
    audio.play().catch(() => {});
  }
  function nextReview(dir = 1, fromAt = null) {
    const open = openReviews();
    if (!open.length) { select(null); toast("Nothing left to check ✓"); return; }
    let k = sel?.type === "review" && fromAt == null ? open.findIndex((o) => o.r.id === sel.id) : -1;
    if (k >= 0) k += dir;
    else {
      const p = fromAt ?? (sel?.type === "word" ? sel.i : Math.max(0, wordIndexAt(audio.currentTime - 0.5)));
      k = dir > 0 ? open.findIndex((o) => o.at >= p) : open.findLastIndex((o) => o.at < p);
      if (k < 0) k = dir > 0 ? 0 : open.length - 1;
    }
    k = (k + open.length) % open.length;
    select({ type: "review", id: open[k].r.id }, { play: true });
  }

  /* ---------- editing */
  const key = (t) => t.normalize("NFKC").toLowerCase().replace(/[^a-z0-9]/g, "");
  function tokens(text) { return text.trim().split(/\s+/).filter(Boolean); }
  function retime(old, texts, S, E) {
    // keep timings (and flags) for words that didn't change; spread new words over the gaps
    const n = old.length, m2 = texts.length;
    const match = new Array(m2).fill(-1);
    if (n * m2 <= 400000) {
      const L = Array.from({ length: n + 1 }, () => new Int32Array(m2 + 1));
      for (let i = n - 1; i >= 0; i--) for (let j = m2 - 1; j >= 0; j--)
        L[i][j] = key(old[i].t) === key(texts[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
      let i = 0, j = 0;
      while (i < n && j < m2) {
        if (key(old[i].t) === key(texts[j])) { match[j] = i; i++; j++; } else if (L[i + 1][j] >= L[i][j + 1]) i++; else j++;
      }
    }
    const out = texts.map((t, j) => {
      if (match[j] >= 0) {
        const o = old[match[j]];
        const w = { ...o, t };
        if (o.t !== t) w.u = 1;
        delete w.p;
        return w;
      }
      return { t, s: 0, e: 0, c: 1, u: 1 };
    });
    // fill unmatched runs between anchors
    let j = 0;
    while (j < out.length) {
      if (match[j] >= 0) { j++; continue; }
      let k = j;
      while (k < out.length && match[k] < 0) k++;
      const t0 = j > 0 ? out[j - 1].e : S, t1 = k < out.length ? out[k].s : E;
      const chars = out.slice(j, k).reduce((acc, w) => acc + w.t.length + 1, 0);
      let t = t0;
      for (let x = j; x < k; x++) {
        const d = Math.max(0, t1 - t0) * ((out[x].t.length + 1) / chars);
        out[x].s = +t.toFixed(2); out[x].e = +(t + d).toFixed(2); t += d;
      }
      j = k;
    }
    return out;
  }
  function replaceRange(a, b, texts, group, { paraBreaks = null } = {}) {
    const old = T.words.slice(a, b).map((w) => ({ ...w }));
    const next = T.words[b];
    const nextP = !!next?.p;
    const S = b > a ? old[0].s : (a > 0 ? T.words[a - 1].e : (next?.s ?? 0));
    const E = b > a ? old[old.length - 1].e : (next?.s ?? S);
    const neu = retime(old, texts, S, E);
    // the new words take over a paragraph start at this spot
    const startsPara = a === 0 || (b > a ? !!old[0].p : nextP);
    if (paraBreaks) paraBreaks.forEach((k) => { if (neu[k]) neu[k].p = 1; });
    T.words.splice(a, b - a, ...neu);
    const after = T.words[a + neu.length];
    if (startsPara) {
      if (neu.length) { neu[0].p = 1; if (b === a && after) delete after.p; }
      else if (after) after.p = 1;
    }
    group.push({ op: "splice", a, removed: old, count: neu.length, nextP });
  }
  function resolve(r, group) {
    group.push({ op: "review", id: r.id, prev: r.done });
    r.done = true;
  }
  function commit(group, rerenderAt = null) {
    if (!group.length) return;
    undo.push(group);
    if (undo.length > 200) undo.shift();
    // redraw just the touched paragraph when possible (a full redraw of a 3-hour stream takes ~0.3 s)
    const splices = group.filter((g) => g.op === "splice");
    if (splices.length > 1) render();
    else if (splices.length === 1) rerenderPara(Math.min(splices[0].a, T.words.length - 1));
    else if (rerenderAt == null) render();
    else rerenderPara(rerenderAt);
    scheduleSave();
  }
  function doUndo() {
    const group = undo.pop();
    if (!group) return toast("Nothing to undo");
    for (const g of [...group].reverse()) {
      if (g.op === "splice") {
        T.words.splice(g.a, g.count, ...g.removed);
        const after = T.words[g.a + g.removed.length];
        if (after) { if (g.nextP) after.p = 1; else delete after.p; }
      }
      if (g.op === "review") byId.get(g.id).done = g.prev;
    }
    select(null);
    render();
    scheduleSave();
    toast("Undone");
  }
  function act(what) {
    if (!sel) return;
    const group = [];
    if (sel.type === "word") {
      const i = sel.i;
      if (what === "save") {
        const txt = $("#fix").value;
        if (txt.trim() === T.words[i].t) return select(null);
        replaceRange(i, i + 1, tokens(txt), group);
        commit(group);
        select(null);
      } else if (what === "delete") {
        replaceRange(i, i + 1, [], group);
        commit(group);
        select(null);
      } else if (what === "replay") replay();
      else if (what === "playfrom") { stopAt = null; audio.currentTime = T.words[i].s; audio.play().catch(() => {}); }
      else if (what === "close") select(null);
      return;
    }
    const r = byId.get(sel.id);
    if (what === "replay") return replay();
    if (what === "close") return select(null);
    if (what === "skip") return nextReview(1);
    const rg = selRange();
    if (what === "keep") {
      resolve(r, group);
      commit(group, rg[0]);
    } else if (what === "alt" || what === "alt2" || what === "save") {
      if (what === "alt2" && !r.alt2) return;
      const txt = what === "alt" ? (r.alt || "") : what === "alt2" ? r.alt2 : $("#fix").value;
      resolve(r, group);
      if (r.kind === "missing") {
        if (tokens(txt).length) replaceRange(rg[0], rg[0], tokens(txt), group);
      } else {
        replaceRange(rg[0], rg[1], tokens(txt), group);
      }
      commit(group);
    }
    if (r.kind === "voted") return select(null);  // not part of the queue, so don't jump ahead
    if (openReviews().length) nextReview(1, rg[0]); else { select(null); toast("All spots checked ✓"); }
  }
  $("#rcard").addEventListener("click", (e) => { const b = e.target.closest("[data-act]"); if (b) act(b.dataset.act); });
  $("#rcard").addEventListener("keydown", (e) => {
    if (e.target.id !== "fix") return;
    if (e.key === "Enter") { e.preventDefault(); act("save"); }
    if (e.key === "Escape") { e.target.blur(); }
  });

  // paragraph editing
  function editPara(k) {
    const bounds = paraBounds();
    const [a, b] = bounds[k];
    const el = $(`.para[data-k="${k}"] .txt`, doc);
    const text = T.words.slice(a, b).map((w) => w.t).join(" ");
    el.innerHTML = `<textarea class="pedit">${esc(text)}</textarea><div class="pedit-actions"><span class="faint" style="margin-right:auto;font-size:12.5px">Tip: leave a blank line to split into two paragraphs.</span><button class="btn sm ghost" data-pe="cancel">Cancel</button><button class="btn sm primary" data-pe="save">Save</button></div>`;
    const ta = $("textarea", el);
    ta.style.height = Math.max(120, ta.scrollHeight + 10) + "px";
    ta.focus();
    el.onclick = (e) => {
      const b2 = e.target.closest("[data-pe]");
      if (!b2) return;
      e.stopPropagation();
      if (b2.dataset.pe === "cancel") return render();
      const chunks = ta.value.split(/\n\s*\n/);
      const texts = [], breaks = [];
      chunks.forEach((c) => { const t = tokens(c); if (t.length) { breaks.push(texts.length); texts.push(...t); } });
      const group = [];
      replaceRange(a, b, texts, group, { paraBreaks: breaks });
      commit(group);
    };
    ta.onkeydown = (e) => { if (e.key === "Escape") render(); if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $('[data-pe="save"]', el).click(); };
  }

  /* ---------- doc clicks */
  doc.addEventListener("click", (e) => {
    const ed = e.target.closest("[data-edit]");
    if (ed) { e.stopPropagation(); return editPara(+ed.dataset.edit); }
    const st = e.target.closest(".stamp");
    if (st) { stopAt = null; audio.currentTime = +st.dataset.s; audio.play().catch(() => {}); return; }
    const g = e.target.closest(".gapmark");
    if (g) return select({ type: "review", id: +g.dataset.gap }, { play: true, scroll: false });
    const w = e.target.closest(".w");
    if (!w) return;
    const i = spans.indexOf(w), word = T.words[i];
    if (!word) return;
    const r = word.r != null ? byId.get(word.r) : null;
    if (r && !r.done && (r.kind !== "single" || showSingle)) select({ type: "review", id: r.id }, { play: true, scroll: false });
    else select({ type: "word", i }, { play: true, scroll: false });
  });

  /* ---------- saving */
  function scheduleSave() {
    dirty = true;
    $("#saving").textContent = "Unsaved changes…";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 700);
  }
  function save() {
    clearTimeout(saveTimer);
    if (!dirty) return saving;
    dirty = false;
    $("#saving").textContent = "Saving…";
    saving = saving.then(() => api(`/api/jobs/${id}/transcript`, { method: "PUT", body: JSON.stringify({ version: T.version, words: T.words, reviews: T.reviews }) })
      .then((r) => { T.version = r.version; if (!dirty) $("#saving").textContent = "Saved"; })
      .catch((e) => { dirty = true; $("#saving").textContent = "Not saved: " + e.message; toast("Couldn't save: " + e.message, 5000); }));
    return saving;
  }
  const beforeUnload = (e) => { if (dirty) { save(); e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", beforeUnload);
  cleanup.push(() => { window.removeEventListener("beforeunload", beforeUnload); if (dirty) save(); });

  /* ---------- toolbar */
  $("#next").onclick = () => nextReview(1);
  $("#prev").onclick = () => nextReview(-1);
  $("#title").onclick = () => {
    const t = prompt("Rename transcript", job.meta.title || "");
    if (t && t.trim()) api(`/api/jobs/${id}`, { method: "PATCH", body: JSON.stringify({ title: t.trim() }) }).then(() => { job.meta.title = t.trim(); $("#title").textContent = t.trim(); document.title = t.trim() + " · Skrift"; });
  };
  const menus = [["#expBtn", "#expPop"], ["#moreBtn", "#morePop"]];
  menus.forEach(([b, p]) => { $(b).onclick = (e) => { e.stopPropagation(); menus.forEach(([, q]) => q !== p && $(q).classList.add("hidden")); $(p).classList.toggle("hidden"); }; });
  const closeMenus = (e) => { if (!e.target.closest(".menu")) menus.forEach(([, p]) => $(p)?.classList.add("hidden")); };
  document.addEventListener("click", closeMenus);
  cleanup.push(() => document.removeEventListener("click", closeMenus));
  $("#expPop").addEventListener("click", async (e) => {
    const a = e.target.closest("a[data-fmt]");
    if (!a) return;
    e.preventDefault();
    await save();
    const stamps = $("#stampOpt").checked ? 1 : 0;
    const link = document.createElement("a");
    const snd = $("#soundOpt") ? ($("#soundOpt").checked ? 1 : 0) : 1;
    const blp = $("#bleepOpt").checked ? 1 : 0;
    link.href = `/api/jobs/${id}/export/${a.dataset.fmt}?timestamps=${stamps}&sounds=${snd}&bleep=${blp}`;
    link.download = "";
    document.body.appendChild(link);
    link.click();
    link.remove();
    $("#expPop").classList.add("hidden");
    toast(`Downloading .${a.dataset.fmt}`);
  });
  $("#toggleSingle").onclick = () => { showSingle = !showSingle; $("#toggleSingle").textContent = (showSingle ? "Hide" : "Show") + " words only one engine heard"; render(); };
  $("#copyAll").onclick = async () => {
    const text = paraBounds().map(([a, b]) => T.words.slice(a, b).map((w) => w.t).join(" ")).join("\n\n");
    await navigator.clipboard.writeText(text);
    toast("Copied the whole transcript");
  };
  $("#rerun").onclick = async () => {
    if (!confirm("Transcribe this again from scratch? It uses your current names list. Any edits you've made here will be replaced.")) return;
    await api(`/api/jobs/${id}/recheck`, { method: "POST" });
    go("/");
  };
  $("#delJob").onclick = async () => {
    if (!confirm("Delete this transcript? This can't be undone.")) return;
    await api(`/api/jobs/${id}`, { method: "DELETE" });
    go("/");
  };

  /* ---------- find & replace + names */
  const panel = $("#panel");
  function openFind(prefill = "") {
    panel.classList.remove("hidden");
    panel.innerHTML = `<h3>Find &amp; replace <button class="btn sm ghost" id="pclose">✕</button></h3>
      <div class="grid2"><input type="search" id="fq" placeholder="Find…" value="${esc(prefill)}"><input type="text" id="fr" placeholder="Replace with…"></div>
      <div class="checks"><label><input type="checkbox" id="fcase"> Match case</label><label><input type="checkbox" id="fwhole" checked> Whole words</label><label title="Future videos will spell it this way too"><input type="checkbox" id="fremember" checked> Remember spelling</label></div>
      <div class="row" style="display:flex;gap:8px;align-items:center"><button class="btn sm primary" id="frall">Replace all</button><span class="faint" id="fcount"></span></div>
      <div class="hits" id="fhits"></div>`;
    $("#pclose").onclick = () => panel.classList.add("hidden");
    const run = () => findHits();
    ["#fq", "#fcase", "#fwhole"].forEach((s) => $(s).addEventListener("input", run));
    $("#frall").onclick = replaceAll;
    $("#fq").focus();
    $("#fq").select();
    run();
  }
  function buildIndex() {
    let pos = 0;
    const starts = [];
    const text = T.words.map((w) => { starts.push(pos); pos += w.t.length + 1; return w.t; }).join(" ");
    return { text, starts };
  }
  function findRegex() {
    const q = $("#fq").value;
    if (!q.trim()) return null;
    const body = q.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
    const whole = $("#fwhole").checked;
    return new RegExp((whole ? "(?<![\\p{L}\\p{N}])" : "") + body + (whole ? "(?![\\p{L}\\p{N}])" : ""), "gu" + ($("#fcase").checked ? "" : "i"));
  }
  function wordAt(starts, pos) {
    let lo = 0, hi = starts.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= pos) lo = mid; else hi = mid - 1; }
    return lo;
  }
  function findHits() {
    const rx = findRegex();
    const box = $("#fhits");
    if (!rx) { box.innerHTML = ""; $("#fcount").textContent = ""; return []; }
    const { text, starts } = buildIndex();
    const hits = [...text.matchAll(rx)].map((mm) => ({ i: wordAt(starts, mm.index), j: wordAt(starts, mm.index + mm[0].length - 1), at: mm.index, len: mm[0].length }));
    $("#fcount").textContent = `${hits.length} match${hits.length === 1 ? "" : "es"}`;
    box.innerHTML = hits.slice(0, 150).map((h, n) => {
      const a = Math.max(0, h.i - 6), b = Math.min(T.words.length, h.j + 7);
      const ctx = T.words.slice(a, b).map((w, x) => (a + x >= h.i && a + x <= h.j) ? `<mark>${esc(w.t)}</mark>` : esc(w.t)).join(" ");
      return `<div class="hit" data-n="${n}" data-i="${h.i}"><time>${clock(T.words[h.i].s)}</time>${ctx}</div>`;
    }).join("");
    box.onclick = (e) => {
      const el = e.target.closest(".hit");
      if (el) select({ type: "word", i: +el.dataset.i }, { play: true });
    };
    return hits;
  }
  async function replaceAll() {
    const rx = findRegex();
    if (!rx) return;
    const rep = $("#fr").value;
    const { text, starts } = buildIndex();
    const hits = [...text.matchAll(rx)];
    if (!hits.length) return toast("No matches");
    const group = [];
    for (const mm of hits.reverse()) {
      const i = wordAt(starts, mm.index), j = wordAt(starts, mm.index + mm[0].length - 1);
      const prefix = text.slice(starts[i], mm.index), suffix = text.slice(mm.index + mm[0].length, starts[j] + T.words[j].t.length);
      replaceRange(i, j + 1, tokens(prefix + rep + suffix), group);
    }
    commit(group);
    toast(`Replaced ${hits.length}`);
    if ($("#fremember").checked && rep.trim() && /[A-Z0-9]/.test(rep)) {
      const s = await api("/api/settings");
      if (!s.vocab.some((v) => v.toLowerCase() === rep.trim().toLowerCase())) {
        await api("/api/settings", { method: "PUT", body: JSON.stringify({ vocab: [...s.vocab, rep.trim()] }) });
        toast(`Replaced ${hits.length}. “${rep.trim()}” added to your names list.`, 3500);
      }
    }
    findHits();
  }
  function openNames() {
    panel.classList.remove("hidden");
    const rare = new Set(job.transcript.rare || []);
    const counts = new Map();
    T.words.forEach((w) => {
      const disp = w.t.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "").replace(/(['’](s|ll|re|ve|d|m)|n['’]t)$/i, "");
      const k = key(disp);
      const isName = rare.has(k) || (w.u && /^[A-Z]/.test(w.t));
      if (!isName || k.length < 3) return;
      const e = counts.get(disp) || 0;
      counts.set(disp, e + 1);
    });
    const list = [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    panel.innerHTML = `<h3>Names &amp; unusual words <button class="btn sm ghost" id="pclose">✕</button></h3>
      <p class="muted" style="margin:0 0 10px;font-size:13.5px">Speech engines are weakest on names. Scan this list; if one is misspelled, click it to fix every occurrence at once.</p>
      <div class="names">${list.map(([w, n]) => `<button data-w="${esc(w)}">${esc(w)}<small>${n}</small></button>`).join("") || '<span class="faint">None found.</span>'}</div>`;
    $("#pclose").onclick = () => panel.classList.add("hidden");
    $(".names", panel).onclick = (e) => { const b = e.target.closest("button[data-w]"); if (b) { openFind(b.dataset.w); $("#fr").value = b.dataset.w; $("#fr").focus(); } };
  }
  $("#findBtn").onclick = () => openFind();
  $("#namesBtn").onclick = openNames;

  /* ---------- player */
  const playBtn = $("#play"), seek = $("#seek");
  playBtn.onclick = () => { stopAt = null; audio.paused ? audio.play().catch(() => {}) : audio.pause(); };
  audio.onplay = () => (playBtn.innerHTML = ICON.pause);
  audio.onpause = () => (playBtn.innerHTML = ICON.play);
  // backup for snippet stops: animation frames pause in background tabs, timeupdate doesn't
  audio.ontimeupdate = () => { if (stopAt != null && audio.currentTime >= stopAt) { audio.pause(); stopAt = null; } };
  audio.onloadedmetadata = () => ($("#dur").textContent = clock(audio.duration));
  $("#back5").onclick = () => (audio.currentTime = Math.max(0, audio.currentTime - 5));
  $("#speed").onchange = (e) => (audio.playbackRate = +e.target.value);
  let seeking = false;
  seek.oninput = () => { seeking = true; $("#cur").textContent = clock((seek.value / 1000) * (audio.duration || m.duration)); };
  seek.onchange = () => { stopAt = null; audio.currentTime = (seek.value / 1000) * (audio.duration || m.duration); seeking = false; };
  $("#followBtn").onclick = () => { follow = !follow; $("#followBtn").textContent = follow ? "Follow ✓" : "Follow"; };
  const onScroll = () => (lastScroll = Date.now());
  window.addEventListener("wheel", onScroll, { passive: true });
  window.addEventListener("touchmove", onScroll, { passive: true });
  cleanup.push(() => { window.removeEventListener("wheel", onScroll); window.removeEventListener("touchmove", onScroll); });
  function wordIndexAt(t) {
    let lo = 0, hi = T.words.length - 1, ans = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (T.words[mid].s <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
    return ans;
  }
  let raf;
  function tick() {
    const t = audio.currentTime;
    if (stopAt != null && t >= stopAt) { audio.pause(); stopAt = null; }
    if (!seeking) {
      $("#cur").textContent = clock(t);
      seek.value = Math.round((t / (audio.duration || m.duration || 1)) * 1000);
    }
    const i = wordIndexAt(t);
    if (i !== nowIdx) {
      spans[nowIdx]?.classList.remove("now");
      nowIdx = i;
      const sp = spans[i];
      if (sp && T.words[i].e + 0.6 >= t) {
        sp.classList.add("now");
        if (follow && !audio.paused && stopAt == null && Date.now() - lastScroll > 4000) ensureVisible(sp, false);
      }
    }
    raf = requestAnimationFrame(tick);
  }
  raf = requestAnimationFrame(tick);
  cleanup.push(() => { cancelAnimationFrame(raf); audio.pause(); audio.removeAttribute("src"); audio.load(); });

  /* ---------- keyboard */
  const onKey = (e) => {
    const typing = e.target.matches("input, textarea, select") || e.target.isContentEditable;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f") { e.preventDefault(); return openFind(); }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); return doUndo(); }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key;
    if (k === " ") { e.preventDefault(); stopAt = null; audio.paused ? audio.play().catch(() => {}) : audio.pause(); }
    else if (k === "n" || k === "N" || k === "]") { e.preventDefault(); nextReview(1); }
    else if (k === "p" || k === "P" || k === "[") { e.preventDefault(); nextReview(-1); }
    else if (k === "Enter" && sel) { e.preventDefault(); act(sel.type === "review" ? "keep" : "close"); }
    else if ((k === "a" || k === "A") && sel?.type === "review") { e.preventDefault(); act("alt"); }
    else if ((k === "y" || k === "Y") && sel?.type === "review") { e.preventDefault(); act("alt2"); }
    else if ((k === "r" || k === "R") && sel) { e.preventDefault(); replay(); }
    else if ((k === "e" || k === "E") && sel) { e.preventDefault(); $("#fix")?.focus(); $("#fix")?.select(); }
    else if (k === "Escape") { select(null); panel.classList.add("hidden"); }
    else if (k === "ArrowLeft") { e.preventDefault(); stopAt = null; audio.currentTime = Math.max(0, audio.currentTime - 5); }
    else if (k === "ArrowRight") { e.preventDefault(); stopAt = null; audio.currentTime += 5; }
  };
  document.addEventListener("keydown", onKey);
  cleanup.push(() => document.removeEventListener("keydown", onKey));

  render();
  const startAt = +new URLSearchParams(location.search).get("t");
  if (startAt) {
    const i = Math.max(0, wordIndexAt(startAt));
    setTimeout(() => select({ type: "word", i }, { play: false }), 50);
    audio.currentTime = startAt;
  }
}

function pendingView(job) {
  document.title = "Working… · Skrift";
  const draw = (j) => {
    view.innerHTML = `<div class="section-h"><h2>Still working on this one</h2><a href="/" class="btn sm ghost">← All transcripts</a></div><div class="jobs">${jobCard(j)}</div>`;
  };
  draw(job);
  let timer;
  const poll = async () => {
    try {
      const j = await api(`/api/jobs/${job.id}`);
      if (j.status === "done") return transcriptView(job.id);
      draw(j);
      if (["error", "cancelled"].includes(j.status)) return;
    } catch { /* keep trying */ }
    timer = setTimeout(poll, 1500);
  };
  timer = setTimeout(poll, 1500);
  view.onclick = async (e) => {
    const b = e.target.closest("button[data-retry],button[data-cancel]");
    if (!b) return;
    e.preventDefault();
    if (b.dataset.retry) await api(`/api/jobs/${b.dataset.retry}/retry`, { method: "POST" });
    if (b.dataset.cancel && confirm("Stop this transcription?")) await api(`/api/jobs/${b.dataset.cancel}/cancel`, { method: "POST" });
    clearTimeout(timer);
    poll();
  };
  cleanup.push(() => { clearTimeout(timer); view.onclick = null; });
}

/* ------------------------------------------------------------------ search */
function searchView() {
  document.title = "Search · Skrift";
  const q0 = new URLSearchParams(location.search).get("q") || "";
  view.innerHTML = `<h1 style="font-size:22px;margin:4px 0 14px">Search every transcript</h1>
    <form class="search-box" id="sf"><input type="search" id="sq" placeholder="A game, a name, a quote…" value="${esc(q0)}" autofocus><button class="btn primary">Search</button></form>
    <div id="sres"></div>`;
  const run = async (q) => {
    if (!q.trim()) { $("#sres").innerHTML = ""; return; }
    $("#sres").innerHTML = `<div class="empty">Searching…</div>`;
    const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
    const rx = new RegExp("(" + q.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig");
    $("#sres").innerHTML = r.results.length ? r.results.map((res) => `<div class="card sres"><h3>${esc(res.title)} <span class="pill">${res.count}×</span></h3>
      ${res.hits.map((h) => `<a class="hit" href="/t/${res.id}?t=${h.t}"><time>${clock(h.t)}</time>${h.lead ? "… " : ""}${esc(h.context).replace(rx, "<mark>$1</mark>")} …</a>`).join("")}</div>`).join("")
      : `<div class="empty">No transcript mentions “${esc(q)}”.</div>`;
  };
  $("#sf").onsubmit = (e) => { e.preventDefault(); const q = $("#sq").value; history.replaceState({}, "", "/search?q=" + encodeURIComponent(q)); run(q); };
  if (q0) run(q0);
}

/* ------------------------------------------------------------------ settings */
async function settingsView() {
  document.title = "Settings · Skrift";
  const s = await api("/api/settings");
  const mac = s.device === "mac";
  view.innerHTML = `<div class="settings">
    <h1 style="font-size:22px;margin:4px 0 2px">Settings</h1>
    <section class="card">
      <h2>Your names list</h2>
      <p>Names, games and slang that show up in your videos. Every transcript uses this list to spell them right. The video's title and description are added automatically each time.</p>
      <textarea id="vocab" rows="6" placeholder="One per line or comma-separated">${esc((s.vocab || []).join("\n"))}</textarea>
    </section>
    <section class="card">
      <h2>Accuracy</h2>
      <p>Default mode for new transcripts.</p>
      <div class="seg" id="dmode"><button data-v="accurate">Cross-checked (recommended)</button><button data-v="fast">Fast</button></div>
      <p style="margin-top:14px">Second engine for cross-checking.</p>
      <div class="seg" id="smodel"><button data-v="turbo">Whisper Turbo (faster)</button><button data-v="large-v3">Whisper Large-v3 (more thorough)</button></div>
      <p style="margin-top:14px">Tie-breaker. On YouTube videos, YouTube's own captions are always used as a free third vote. For Twitch VODs and files:</p>
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="third" ${s.third_vote !== false ? "checked" : ""}> Run a third engine as tie-breaker (it only listens where the first two disagree, so it usually adds just a few minutes per hour of video; fewer spots to check)</label>
      <p style="margin-top:14px">AI meaning check. A small AI model on this computer reads each spot where the engines disagree, in the context of the conversation, and picks the version that makes sense (“not even an <b>edition</b>”, not “addition”). It only chooses between what the engines heard and never invents words. Adds a couple of seconds per spot.</p>
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="meaning" ${s.meaning_check !== false ? "checked" : ""}> Use the AI meaning check</label>
      <div class="seg" id="mmodel" style="margin-top:10px${mac ? "" : ";display:none"}"><button data-v="standard">Standard (fast, 4 GB)</button><button data-v="thorough">Thorough (best, needs 16 GB+)</button></div>
      <p class="faint" style="margin-top:8px;font-size:13px${mac ? "" : ";display:none"}">Thorough uses a bigger model that gets more of the tricky spots right. On a 16 GB Mac it takes about 10 seconds per spot and the Mac may feel slower while it runs, so avoid running transcripts while you stream.</p>
    </section>
    <section class="card">
      <h2>Subscriber-only &amp; members-only videos</h2>
      <p>To transcribe videos that need a login (sub-only Twitch VODs, YouTube members videos), Skrift can borrow the login from a browser you're signed in with. Your computer may ask for permission the first time.${mac ? "" : " On Windows, use Firefox: Chrome and Edge keep their logins locked away from other apps."}</p>
      <select id="cookies" style="max-width:280px">
        ${[["", "Off (public videos only)"], ["chrome", "Use my Chrome login"], ["safari", "Use my Safari login"], ["firefox", "Use my Firefox login"], ["brave", "Use my Brave login"], ["edge", "Use my Edge login"]].filter(([v]) => mac || v !== "safari").map(([v, n]) => `<option value="${v}" ${s.cookies_browser === v ? "selected" : ""}>${n}</option>`).join("")}
      </select>
    </section>
    <section class="card">
      <h2>Privacy</h2>
      <p>Skrift runs entirely on this computer. By default only this computer can open it.</p>
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="lan" ${s.allow_lan ? "checked" : ""}> Let my other devices on this Wi-Fi (like my phone) open Skrift. Takes effect next time Skrift starts.</label>
    </section>
    <section class="card">
      <h2>Notifications</h2>
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="notify" ${s.notify ? "checked" : ""}> Show a notification when a transcript is ready</label>
    </section>
    <section class="card how">
      <h2>How the accuracy works</h2>
      <ol>
        <li><b>Engine 1</b> (NVIDIA Parakeet) writes the transcript. Measured against human-made captions, it got about <b>97% of words exactly right</b> on a 47-minute Nintendo Direct (narration over game audio) and about <b>95%</b> on a chaotic four-person podcast with crosstalk. Unlike Whisper, it doesn't invent sentences during music or silence.</li>
        <li><b>Engine 2</b> (OpenAI Whisper), a completely separate model, transcribes the same audio again.</li>
        <li>Skrift compares them word by word. Where both heard the same thing, you can trust it. Where they disagree, it's highlighted; in testing, about half of those highlights were real mistakes, so the list is worth a quick pass. Each one takes a couple of seconds: listen, then press Enter or A.</li>
        <li>On YouTube videos, YouTube's own captions are a third opinion: where two of three engines agree, it's settled automatically.</li>
        <li>Where they still disagree, a small AI model on this computer reads the conversation and picks the version that makes sense. It only chooses between what the engines heard, and it never changes a name on its own; for names it just makes a suggestion.</li>
        <li>Names are every speech engine's weak spot. Your names list and the video's description fix the ones Skrift knows about; the <b>Names</b> button lists every unusual word so you can catch the rest in one glance.</li>
      </ol>
      <p class="faint">Everything runs on this computer. No accounts, no limits, no subscription.</p>
    </section>
    <div><button class="btn primary" id="saveS">Save settings</button></div>
  </div>`;
  const segs = { dmode: s.default_mode, smodel: s.second_model, mmodel: s.meaning_model || "standard" };
  const paint = () => Object.entries(segs).forEach(([id, v]) => $$(`#${id} button`).forEach((b) => b.classList.toggle("on", b.dataset.v === v)));
  paint();
  ["dmode", "smodel", "mmodel"].forEach((id) => ($(`#${id}`).onclick = (e) => { if (e.target.dataset.v) { segs[id] = e.target.dataset.v; paint(); } }));
  $("#saveS").onclick = async () => {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ vocab: $("#vocab").value, default_mode: segs.dmode, second_model: segs.smodel, third_vote: $("#third").checked, meaning_check: $("#meaning").checked, meaning_model: segs.mmodel, cookies_browser: $("#cookies").value, notify: $("#notify").checked, allow_lan: $("#lan").checked }) });
    toast("Saved");
  };
}

route();
// keep Skrift running while a tab is open (it quits by itself a while after the last tab closes)
setInterval(() => { if (!document.hidden) fetch("/api/health").catch(() => {}); }, 60000);
