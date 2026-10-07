"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

let data = { version: -1, shipments: [], sync: {} };
let hiddenShipments = [];
let filter = localStorage.getItem("filter") || "active";
const rendered = new Map(); // id -> html string, so unchanged cards are never touched
let haystacks = new Map(); // id -> lowercased searchable text, rebuilt once per data load
let terms = [];

const FILTERS = {
  active: (s) => s.status === "active" || s.status === "problem" || deliveredToday(s), // a delivery stays in view until midnight
  problem: (s) => ["problem", "lost", "returned", "cancelled"].includes(s.status),
  delivered: (s) => s.status === "delivered",
  all: () => true,
};
const STATUS = {
  active: "On the way", problem: "Needs attention", delivered: "Delivered",
  lost: "Lost", returned: "Returned", cancelled: "Cancelled", stale: "No recent updates",
};

// ---------- time formatting ----------
const fmtDay = (d) => d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const fmtShort = (ms) => new Date(ms).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const fmtFull = (ms) => new Date(ms).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function ago(ms) {
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  if (m < 1440) return `${Math.round(m / 60)}h ago`;
  const d = Math.round(m / 1440);
  return d < 30 ? `${d}d ago` : fmtShort(ms);
}
function deliveredToday(s) {
  const d = s.status === "delivered" && s.notches.find((n) => n.key === "delivered" && n.ts);
  return !!d && new Date(d.ts).toDateString() === new Date().toDateString();
}
function clock(hm) {
  const [h, m] = hm.split(":").map(Number);
  return [`${h % 12 || 12}${m ? `:${String(m).padStart(2, "0")}` : ""}`, h >= 12 ? "pm" : "am"];
}
// the carrier's slot: "2:30–6:30pm", "11:25am–3:25pm", "by 5pm"
function slot(w) {
  const [to, toAp] = clock(w.to);
  if (!w.from) return `by ${to}${toAp}`;
  const [from, fromAp] = clock(w.from);
  return `${from}${fromAp === toAp ? "" : fromAp}–${to}${toAp}`;
}

let relativeDates = (() => { try { return localStorage.getItem("dates") !== "date"; } catch { return true; } })();
// "today", "tomorrow" or "yesterday" when Settings allows it, otherwise the date
function day(d) {
  const days = Math.round((new Date(d.toDateString()) - new Date(new Date().toDateString())) / 864e5); // whole days, give or take a clock change
  return (relativeDates && { "-1": "Yesterday", 0: "Today", 1: "Tomorrow" }[days]) || fmtDay(d);
}
function eta(iso, guess) {
  const d = new Date(iso + "T00:00");
  return `${iso < new Date().toLocaleDateString("en-CA") ? "Expected" : guess ? "Estimated" : "Arriving"} ${day(d)}`;
}

// ---------- rendering ----------
function timeline(s) {
  const last = s.notches.length - 1;
  const pos = (i) => (last > 0 ? (i / last) * 100 : 100);
  const cur = Math.max(s.notches.findLastIndex((n) => n.reached), 0);
  const blocked = s.exceptions.some((x) => x.active && x.terminal);
  const ticks = s.notches.map((n, i) => `<i class="tick${n.reached ? " on" : ""}" style="left:${pos(i)}%"></i>`).join("");
  const flags = s.exceptions.map((x) => {
    const i = Math.max(x.after, 0);
    const at = i < last ? (pos(i) + pos(i + 1)) / 2 : pos(last);
    return `<b class="flag${x.active ? " on" : ""}" style="left:${at}%" title="${esc(x.label)} · ${esc(fmtFull(x.ts))}">!</b>`;
  }).join("");
  const labels = s.notches.map((n, i) => {
    const cls = [n.reached && "reached", n.current && "current", i === 0 && "first", i === last && "last"].filter(Boolean).join(" ");
    return `<li class="${cls}" style="--at:${pos(i)}%"><span class="lbl">${esc(n.label)}</span><span class="when">${n.ts ? esc(fmtShort(n.ts)) : ""}</span></li>`;
  }).join("");
  return `<div class="tl${blocked ? " blocked" : ""}" style="--n:${last};--fill:${pos(cur)}%">
    <div class="bar"><span class="fill"></span>${ticks}${flags}</div><ol>${labels}</ol></div>`;
}

function when(s) {
  if (s.eta && FILTERS.active(s)) {
    const t = eta(s.eta, s.eta_guess);
    const upcoming = s.eta >= new Date().toLocaleDateString("en-CA"); // a slot for a day gone by is no help
    return `<span class="w-k">${esc(t.split(" ")[0])}</span><span class="w-d">${esc(t.split(" ").slice(1).join(" "))}</span>${
      s.window && upcoming ? `<span class="w-t">${esc(slot(s.window))}</span>` : ""}`;
  }
  const d = s.notches.find((n) => n.key === "delivered" && n.ts);
  if (d) return `<span class="w-k">Delivered</span><span class="w-d quiet">${esc(day(new Date(d.ts)))}</span>`;
  return `<span class="w-k">Ordered</span><span class="w-d quiet">${esc(fmtDay(new Date(s.started)))}</span>`;
}

function card(s) {
  const active = s.exceptions.filter((x) => x.active);
  const track = s.tracking.map((t) => `<a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.number)}</a>`).join(", ");
  const meta = [
    s.merchant !== s.title && `<span>${esc(s.merchant)}</span>`,
    s.carrier && !s.merchant.toLowerCase().startsWith(s.carrier.toLowerCase()) && `<span>via ${esc(s.carrier)}</span>`,
    track && `<span class="mono">${track}</span>`,
    s.orders.length && `<span class="mono order">#${esc(s.orders[0])}</span>`,
  ].filter(Boolean).join("");
  const events = s.events.map((e) => `<li>
      <time>${esc(fmtFull(e.ts))}</time>
      <span class="ev-stage">${esc(e.label)}</span>
      <span class="ev-text">${e.source === "owner" ? `${esc(e.subject)} <small>By you, in Parcels</small>` : e.source === "email"
        ? `<a href="https://mail.google.com/mail/u/0/#all/${esc(e.id)}" target="_blank" rel="noopener">${esc(e.subject)}</a><small>${esc(e.snippet)}</small>`
        : `${esc(e.subject)}${e.location ? ` <small>${esc(e.location)}</small>` : ""} <small>· carrier scan</small>`}</span></li>`).join("");
  const chip = active.length ? active[active.length - 1].label : STATUS[s.status] || s.status;
  return `<details class="c-log"><summary>
    <header class="c-head">
      <div class="c-id"><h2>${esc(s.title)}</h2>
        <div class="c-meta"><span class="chip st-${s.status}">${esc(chip)}</span>${meta}<span class="ago">${esc(ago(s.updated))}</span></div></div>
      <div class="c-when${s.eta && FILTERS.active(s) ? " has-eta" : ""}">${when(s)}</div>
    </header>
    ${timeline(s)}</summary>
    <div class="log">
      <div class="actions"><button type="button" class="link" data-act="rename">Rename</button>${s.original_title ? `<button type="button" class="link" data-act="reset">Reset name</button>` : ""}${s.marked_delivered ? `<button type="button" class="link" data-act="undeliver">Not delivered</button>` : s.status !== "delivered" ? `<button type="button" class="link" data-act="deliver">Mark delivered</button>` : ""}<button type="button" class="link" data-act="hide">Hide parcel</button><span class="msg" role="status"></span></div>
      <form class="rename-form" hidden><input name="title" value="${esc(s.title)}" maxlength="120" required aria-label="Parcel name">
        <button class="btn">Save</button><button type="button" class="btn ghost" data-act="cancel">Cancel</button><span class="msg" role="status"></span></form>
      ${s.items.length > 1 ? `<ul class="items">${s.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}<ol>${events}</ol></div></details>`;
}

// ---------- search: an index per data load, then plain substring checks per keystroke ----------
const fold = (s) => String(s ?? "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
function indexShipments() {
  haystacks = new Map(data.shipments.map((s) => [s.id, fold([
    s.title, s.items.join(" "), s.merchant, s.carrier, STATUS[s.status], s.current,
    s.eta && `${s.eta} ${eta(s.eta, s.eta_guess)}`, s.window && slot(s.window), fmtFull(s.started), s.orders.join(" "),
    s.tracking.map((t) => t.number).join(" "), s.notches.map((n) => n.label).join(" "),
    s.exceptions.map((x) => x.label).join(" "),
    s.events.map((e) => `${e.label} ${e.subject} ${e.snippet ?? ""} ${e.sender ?? ""} ${e.location ?? ""}`).join(" "),
  ].join("\n"))]));
}
const matches = (s) => terms.every((t) => haystacks.get(s.id).includes(t));

function applySearch() {
  for (const b of document.querySelectorAll("#filters button")) {
    $("b", b).textContent = data.shipments.filter((s) => FILTERS[b.dataset.f](s) && matches(s)).length || "";
  }
  let shown = 0;
  for (const el of $("#list").children) {
    if (!el.dataset.id) continue;
    el.hidden = !matches({ id: el.dataset.id });
    shown += !el.hidden;
  }
  const list = $("#list");
  const empty = list.querySelector(".empty") || list.appendChild(Object.assign(document.createElement("p"), { className: "empty" }));
  empty.hidden = shown > 0;
  if (!shown) empty.innerHTML = emptyText();
}

function render() {
  const list = $("#list");
  const shown = data.shipments.filter(FILTERS[filter]);
  const visible = filter === "active" ? shown.toSorted((a, b) => (a.status === "delivered") - (b.status === "delivered")) : shown;
  for (const b of document.querySelectorAll("#filters button")) b.setAttribute("aria-pressed", b.dataset.f === filter);
  const keep = new Set(visible.map((s) => s.id));
  for (const el of [...list.children]) if (el.dataset.id && !keep.has(el.dataset.id)) { el.remove(); rendered.delete(el.dataset.id); }
  let prev = null;
  for (const s of visible) {
    let el = list.querySelector(`[data-id="${CSS.escape(s.id)}"]`);
    const html = card(s);
    if (!el) {
      el = document.createElement("article");
      el.className = "card";
      el.dataset.id = s.id;
    }
    const editing = $(".rename-form:not([hidden])", el); // a redraw would throw away a half-typed name
    if (rendered.get(s.id) !== html && !editing) {
      const open = $(".c-log", el)?.open;
      el.innerHTML = html;
      if (open) $(".c-log", el).open = true;
      if (rendered.has(s.id)) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }
      rendered.set(s.id, html);
    }
    el.dataset.status = s.status;
    const want = prev ? prev.nextSibling : list.firstChild;
    if (el !== want) list.insertBefore(el, want);
    prev = el;
  }
  applySearch();
}

function emptyText() {
  if (!status.connected) return `Gmail isn't connected yet. Open <button class="link" onclick="openSettings()">Settings</button> to set it up.`;
  if (data.sync.state === "syncing") return "Reading your mail… parcels will appear here as they're found.";
  if (terms.length) {
    const all = data.shipments.filter(matches).length;
    return all ? `No matches here. <button class="link" onclick="setFilter('all')">${all} in All</button>` : "No parcels match that search.";
  }
  return filter === "active" ? "Nothing on the way right now." : "Nothing here.";
}

function renderSync(sync) {
  data.sync = sync;
  const el = $("#sync");
  el.dataset.state = sync.state || "idle";
  $(".txt", el).textContent = sync.state === "idle" && sync.last ? `Synced ${ago(sync.last)}` : sync.message || "Idle";
  el.title = sync.message || "";
}

// ---------- data + live updates ----------
async function load() {
  const r = await fetch("/api/shipments");
  const j = await r.json();
  data = { ...j, shipments: j.shipments.filter((s) => !s.hidden) };
  hiddenShipments = j.shipments.filter((s) => s.hidden);
  renderHidden();
  indexShipments();
  renderSync(data.sync);
  render();
}

function connect() {
  const es = new EventSource("/api/stream");
  es.addEventListener("hello", (e) => { if (+e.data !== data.version) load(); });
  es.addEventListener("shipments", (e) => { if (+e.data !== data.version) load(); });
  es.addEventListener("sync", (e) => renderSync(JSON.parse(e.data)));
  es.onerror = () => renderSync({ state: "offline", message: "Reconnecting…" }); // EventSource retries by itself
}

// ---------- settings ----------
let status = { connected: true };
const msg = (t) => ($("#settings-msg").textContent = t);
async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
async function loadStatus() {
  status = await (await fetch("/api/status")).json();
  $("#client-status").textContent = status.has_client ? "✓ Client JSON uploaded." : "Not uploaded yet.";
  $("#redirect-uri").textContent = status.redirect_uri;
  $("#gmail-status").textContent = status.connected ? "✓ Connected (read-only)." : "Not connected.";
  $("#connect").textContent = status.connected ? "Reconnect Gmail" : "Connect Gmail";
  $("#connect").classList.toggle("disabled", !status.has_client);
  $("#track-status").textContent = status.track17 ? "✓ Key saved." : "";
  $("#years-form").years.value = status.backfill_years;
  $("#email-count").textContent = `${status.emails[0]} emails read, ${status.emails[1]} about deliveries.`;
}
window.openSettings = () => { loadStatus(); $("#hidden-box").open = false; $("#settings").showModal(); }; // hidden parcels stay out of sight until asked for

$("#open-settings").onclick = openSettings;
$("#sync").onclick = openSettings;
$("#client-file").onchange = async (e) => {
  try { await post("/api/client-secret", JSON.parse(await e.target.files[0].text())); msg("Client JSON saved."); loadStatus(); }
  catch (err) { msg(`Upload failed: ${err.message}`); }
};
$("#paste-form").onsubmit = async (e) => {
  e.preventDefault();
  try { await post("/api/auth/paste", { url: e.target.url.value }); msg("Gmail connected. Reading your mail…"); loadStatus(); }
  catch (err) { msg(err.message); }
};
$("#track-form").onsubmit = async (e) => {
  e.preventDefault();
  try { await post("/api/settings", { track17_key: e.target.key.value }); e.target.reset(); msg("Saved."); loadStatus(); }
  catch (err) { msg(err.message); }
};
$("#years-form").onsubmit = async (e) => {
  e.preventDefault();
  try { await post("/api/settings", { backfill_years: e.target.years.value }); await post("/api/backfill"); msg("Searching history…"); }
  catch (err) { msg(err.message); }
};
$("#rebuild").onclick = async () => {
  try { await post("/api/rebuild"); msg("Re-read all stored emails."); } catch (err) { msg(err.message); }
};
$("#connect").onclick = (e) => { if (!status.has_client) { e.preventDefault(); msg("Upload the client JSON first."); } };
window.setFilter = (f) => {
  filter = f;
  try { localStorage.setItem("filter", filter); } catch {}
  render();
};
$("#filters").onclick = (e) => {
  const b = e.target.closest("button");
  if (b) setFilter(b.dataset.f);
};
let searchFrame = 0;
// ---------- rename: the owner's own name for a parcel, e.g. a Vinted buy that only the carrier emailed about ----------
const renameEditor = (el, open) => {
  $(".actions", el).hidden = open;
  const f = $(".rename-form", el);
  f.hidden = !open;
  $(".msg", f).textContent = "";
  const input = $("input", f);
  if (open) { input.focus(); input.select(); } else input.value = input.defaultValue;
};
async function saveName(el, title) {
  try {
    await post(`/api/shipments/${encodeURIComponent(el.dataset.id)}/name`, { title });
    renameEditor(el, false); // the server's new list then redraws this card with the new name
    await load();
  } catch (err) {
    $(".rename-form .msg", el).textContent = err.message || "Couldn't save the name. Try again.";
    $(".rename-form", el).hidden = false;
    $(".actions", el).hidden = true;
  }
}
async function setHidden(id, hidden) {
  await post(`/api/shipments/${encodeURIComponent(id)}/hidden`, { hidden });
  await load();
}
function renderHidden() {
  $("#hidden-list").innerHTML = hiddenShipments.map((s) => `<li data-id="${esc(s.id)}">
    <span><b>${esc(s.title)}</b><small class="muted">${esc([s.title !== s.merchant && s.merchant, fmtFull(s.started)].filter(Boolean).join(" · "))}</small></span>
    <button type="button" class="btn ghost" data-act="restore">Restore</button></li>`).join("");
  $("#hidden-none").hidden = hiddenShipments.length > 0;
  $("#hidden-box").hidden = !hiddenShipments.length;
  $("#hidden-count").textContent = hiddenShipments.length;
}
$("#hidden-list").addEventListener("click", (e) => {
  const li = e.target.closest('[data-act="restore"]')?.closest("li");
  if (li) setHidden(li.dataset.id, false).catch((err) => msg(err.message));
});

$("#list").addEventListener("click", (e) => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  const el = e.target.closest(".card");
  if (!act || !el) return;
  if (act === "rename") renameEditor(el, true);
  if (act === "cancel") renameEditor(el, false);
  if (act === "reset") saveName(el, "");
  if (act === "deliver" || act === "undeliver") post(`/api/shipments/${encodeURIComponent(el.dataset.id)}/delivered`, { delivered: act === "deliver" })
    .then(load).catch((err) => ($(".actions .msg", el).textContent = err.message || "Couldn't save that. Try again."));
  if (act === "hide") setHidden(el.dataset.id, true).catch((err) => ($(".actions .msg", el).textContent = err.message || "Couldn't hide it. Try again."));
});
$("#list").addEventListener("submit", (e) => {
  e.preventDefault();
  saveName(e.target.closest(".card"), $("input", e.target).value.trim());
});
$("#list").addEventListener("keydown", (e) => {
  if (e.key === "Escape" && e.target.closest(".rename-form")) { e.stopPropagation(); renameEditor(e.target.closest(".card"), false); }
});

$("#q").addEventListener("input", (e) => {
  cancelAnimationFrame(searchFrame); // coalesce fast typing into one pass per frame
  searchFrame = requestAnimationFrame(() => { terms = fold(e.target.value).split(/\s+/).filter(Boolean); applySearch(); });
});
document.addEventListener("keydown", (e) => {
  const q = $("#q");
  if (e.key === "/" && document.activeElement !== q && !e.target.closest("input, textarea, dialog")) { e.preventDefault(); q.focus(); }
  if (e.key === "Escape" && document.activeElement === q && q.value) { q.value = ""; q.dispatchEvent(new Event("input")); }
});

let lastTick = Date.now();
setInterval(() => { // refresh "x ago" labels; an e-ink panel flashes on every redraw, so it gets them every 10 minutes
  if (document.documentElement.dataset.theme === "eink" && Date.now() - lastTick < 600000) return;
  lastTick = Date.now();
  rendered.clear(); render(); renderSync(data.sync);
}, 60000);
loadStatus().then(() => { if (!status.connected) openSettings(); });
load().catch(() => {});
connect();

// ---------- dates: "today"/"tomorrow" unless this device chose plain dates ----------
for (const r of document.querySelectorAll("#dates input")) {
  r.checked = r.value === (relativeDates ? "relative" : "date");
  r.onchange = () => {
    relativeDates = r.value === "relative";
    try { localStorage.setItem("dates", r.value); } catch {}
    rendered.clear(); render();
  };
}

// ---------- theme: light unless this device chose otherwise ----------
for (const r of document.querySelectorAll("#theme input")) {
  r.checked = r.value === document.documentElement.dataset.theme;
  r.onchange = () => {
    document.documentElement.dataset.theme = r.value;
    document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    try { localStorage.setItem("theme", r.value); } catch {}
  };
}
