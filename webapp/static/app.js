"use strict";

const view = document.getElementById("view");
const REFRESH_MS = 30000;
const STATUS = {
  running: ["🎯", "Läuft"], endspurt: ["⚡", "Endspurt"], hits_out: ["🔴", "Hits raus"], upcoming: ["🕒", "Bald"],
};
const CATEGORIES = [["Alle", "Alle"], ["Bonus", "BONUS"], ["MIX", "MIX"], ["Pokémon", "Pokémon"],
                    ["One piece", "One Piece"], ["Dragon Ball", "Dragon Ball"]];
const SORTS = {
  site: ["Wie auf der Seite", (a, b) => b.id - a.id],
  ev: ["Ø Rückgabe", (a, b) => (b.ev_pct ?? -1) - (a.ev_pct ?? -1)],
  remaining: ["Wenigste Packs", (a, b) => a.remaining - b.remaining],
  price_low: ["Preis aufsteigend", (a, b) => a.price - b.price],
  price_high: ["Preis absteigend", (a, b) => b.price - a.price],
};

const state = { category: load("category", "Alle"), sort: load("sort", "ev"), timer: null };

function load(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* privater Modus */ }
}

// --- Hilfsfunktionen ---
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));
const num = (n) => (n == null ? "–" : Math.round(n).toLocaleString("de-DE"));
const pct = (n) => (n == null ? "–" : n.toLocaleString("de-DE", { maximumFractionDigits: 1 }) + " %");
const safeUrl = (u) => (typeof u === "string" && /^https:\/\//.test(u) ? u : "");
const img = (u, alt = "") => (safeUrl(u) ? `<img src="${esc(u)}" alt="${esc(alt)}" loading="lazy" referrerpolicy="no-referrer">` : "");
const evClass = (p) => (p == null ? "" : p >= 100 ? "good" : p >= 70 ? "ok" : "");
const time = (t) => new Date(t * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const coins = (n) => `<span class="coin"></span>${n ? num(n) : "Gratis"}`;
const openLink = (b, label = "Öffnen ↗") => (safeUrl(b.buy_url)
  ? `<a class="open-btn" href="${esc(b.buy_url)}" target="_blank" rel="noopener noreferrer" data-stop>${label}</a>` : "");

async function api(path, options) {
  const res = await fetch(path, { cache: "no-store", ...options });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

function hitsText(b) {
  if (b.hits_open == null) return "";
  return b.tracked_hits ? `${b.hits_open}/${b.hits_total} Hits` : `Top 3: ${b.hits_open}`;
}

function flags(b) {
  const out = [];
  if (b.per_day) out.push(`Beschränkt auf ${b.per_day} Mal pro Tag`);
  if (b.min_charge) out.push(`${num(b.min_charge)} Coins benötigt diesen Monat`);
  if (b.password) out.push("🔒 Nur mit Passwort");
  return out.length ? `<div class="flags">${out.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}</div>` : "";
}

function untilText(end) {
  if (!end) return "";
  const t = String(end);
  return /erhältlich/i.test(t) ? t : `Erhältlich bis ${t}`;
}

// Banner-Zeile im Aufbau der Seite: Bild links, Infofeld rechts, darunter unsere Zusatzinfos
function row(b, rank) {
  const [icon, label] = STATUS[b.status] || STATUS.running;
  const left = b.total ? Math.max(0, Math.min(100, (b.remaining / b.total) * 100)) : 0;
  return `
    <div class="entry ${rank ? "ranked" : ""}">
      ${rank ? `<span class="rank-no ${rank <= 3 ? "r" + rank : ""}">${rank}</span>` : ""}
      ${flags(b)}
      <div class="row-card ${b.status === "hits_out" ? "done" : ""}" data-href="#/banner/${b.id}">
        <div class="media">
          ${img(b.image, b.title)}
          ${b.status !== "running" ? `<span class="status ${b.status}">${icon} ${label}</span>` : ""}
          <span class="price-pill">${coins(b.price)}</span>
        </div>
        <div class="panel-side">
          <div class="ranks">${(b.ranks || []).map((r) => `<span class="rank ${esc(r)}"></span>`).join("")}</div>
          ${openLink(b)}
          <div class="remaining">Verbleibend: <b>${num(b.remaining)} / ${num(b.total)}</b></div>
          <div class="bar"><span style="width:${left}%"></span></div>
          <div class="extra">
            ${b.ev_pct != null ? `<span class="pill ${evClass(b.ev_pct)}">Ø ${pct(b.ev_pct)}</span>` : ""}
            ${hitsText(b) ? `<span class="pill">${hitsText(b)}${b.unsure ? " ❓" : ""}</span>` : ""}
          </div>
          ${b.end ? `<div class="until">${esc(untilText(b.end))}</div>` : ""}
        </div>
      </div>
    </div>`;
}

function wireRows() {
  view.querySelectorAll("[data-stop]").forEach((a) => a.addEventListener("click", (e) => e.stopPropagation()));
  view.querySelectorAll("[data-href]").forEach((el) => el.addEventListener("click", () => { location.hash = el.dataset.href; }));
}

// --- Seiten ---
async function showList() {
  const { banners, updated } = await api("/api/banners");
  const sort = SORTS[state.sort] || SORTS.ev;
  const shown = banners.filter((b) => state.category === "Alle" || b.category === state.category).sort(sort[1]);
  view.innerHTML = `
    <div class="tabs">${CATEGORIES.map(([key, label]) =>
      `<button class="tab ${key === "Bonus" ? "bonus" : ""} ${key === state.category ? "active" : ""}" data-cat="${esc(key)}">${esc(label)}</button>`).join("")}</div>
    <div class="toolbar">
      <span class="count">${shown.length} Banner</span>
      <select id="sort" aria-label="Sortierung">${Object.entries(SORTS).map(([k, [label]]) =>
        `<option value="${k}" ${k === state.sort ? "selected" : ""}>${label}</option>`).join("")}</select>
    </div>
    ${shown.length ? `<div class="list">${shown.map((b) => row(b)).join("")}</div>` : `<div class="empty">Keine Banner</div>`}
    <div class="updated">Stand ${time(updated)}</div>`;
  view.querySelectorAll(".tab").forEach((el) => el.addEventListener("click", () => {
    state.category = el.dataset.cat; save("category", state.category); showList();
  }));
  view.querySelector("#sort").addEventListener("change", (e) => {
    state.sort = e.target.value; save("sort", state.sort); showList();
  });
  wireRows();
}

async function showHot() {
  const { hot } = await api("/api/hot");
  view.innerHTML = `
    <div class="section-title">🔥 Top 10 nach Ø Rückgabe</div>
    <div class="section-sub">Ziehbare Banner ohne Bonus, Gratis und Passwort · Ø = erwartete Rückgabe pro Zug</div>
    ${hot.length ? `<div class="list">${hot.map((b, i) => row(b, i + 1)).join("")}</div>`
      : `<div class="empty">Gerade kein ziehbarer Banner</div>`}`;
  wireRows();
}

function stat(label, value, sub = "") {
  return `<div class="stat"><div class="label">${label}</div><div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function chart(history) {
  if (!history || history.length < 2) return `<div class="chart"><div class="empty">Noch zu wenig Verlauf</div></div>`;
  const W = 600, H = 220, P = 28;
  const t0 = history[0].t, t1 = history[history.length - 1].t;
  const max = Math.max(...history.map((h) => h.packs)), min = Math.min(...history.map((h) => h.packs));
  const x = (t) => P + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * P);
  const y = (v) => P / 2 + (1 - (v - min) / Math.max(1, max - min)) * (H - P * 1.5);
  const pts = history.map((h) => `${x(h.t).toFixed(1)},${y(h.packs).toFixed(1)}`).join(" ");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Pack-Verlauf">
    <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#f08a24" stop-opacity=".35"/><stop offset="1" stop-color="#f08a24" stop-opacity="0"/></linearGradient></defs>
    <polygon points="${P},${H - P} ${pts} ${W - P},${H - P}" fill="url(#g)"/>
    <polyline points="${pts}" fill="none" stroke="#e5383b" stroke-width="2.5" vector-effect="non-scaling-stroke"/>
    <text class="axis" x="${P}" y="${H - 6}">${time(t0)}</text>
    <text class="axis" x="${W - P}" y="${H - 6}" text-anchor="end">${time(t1)}</text>
    <text class="axis" x="${W - P}" y="16" text-anchor="end">${num(max)}</text>
  </svg></div>`;
}

function podium(top) {
  return `<div class="podium">${top.map((h, i) => `
    <div class="slot p${i + 1} ${h.state === "pulled" ? "gone" : ""}">
      <div class="art">
        <span class="medal">${i + 1}</span>
        ${h.ship ? `<span class="ship-tag">Versand nur ✈</span>` : ""}
        ${img(h.image, h.name)}
      </div>
      <div class="base"></div>
      <div class="label">${esc(h.name)}<br><b>${num(h.value)} Coins</b>${h.note ? `<br><span class="muted">${h.state === "pulled" ? "✅" : "❓"} ${esc(h.note)}</span>` : ""}</div>
    </div>`).join("")}</div>`;
}

function hitCard(h) {
  return `<div class="hit ${h.state}">
    <div class="art">${img(h.image, h.name)}</div>
    <span class="tier">T${h.rank}</span>
    <div class="info">
      <div class="name">${esc(h.name)}</div>
      <div class="val">${num(h.value)} Coins</div>
      ${h.note ? `<div class="note">${h.state === "pulled" ? "✅" : "❓"} ${esc(h.note)}</div>` : ""}
    </div>
  </div>`;
}

async function showBanner(id) {
  const b = await api(`/api/banner/${encodeURIComponent(id)}`);
  const [icon, label] = STATUS[b.status] || STATUS.running;
  const hits = b.hits.map((h) => ({ ...h, ship: b.tracked_hits }));
  const open = hits.filter((h) => h.state === "open").length;
  const left = b.total ? Math.max(0, Math.min(100, (b.remaining / b.total) * 100)) : 0;
  view.innerHTML = `
    <a class="back" href="javascript:history.back()">‹ Zurück</a>
    ${flags(b)}
    <div class="hero">${img(b.image, b.title)}${b.status !== "running" ? `<span class="status ${b.status}">${icon} ${label}</span>` : ""}
      <span class="price-pill">${coins(b.price)}</span></div>
    ${hits.length ? `<div class="ribbon">Was du gewinnen kannst</div>${podium(hits.slice(0, 3))}` : ""}
    <h2>📊 Auswertung <small>ID ${b.id}</small></h2>
    <div class="stats">
      ${b.ev != null ? stat("Ø Rückgabe pro Zug", `<span class="ev ${evClass(b.ev_pct)}">${num(b.ev)} Coins</span>`, b.ev_pct != null ? pct(b.ev_pct) + " vom Preis" : "") : ""}
      ${b.hits_open != null ? stat("Hits noch drin", hitsText(b) + (b.unsure ? " ❓" : ""), b.cost_to_hit ? `Ø ${num(b.cost_to_hit)} Coins bis Hit` : "") : ""}
      ${b.min_value != null ? stat("Mindestens zurück", num(b.min_value) + " Coins", b.price ? pct(b.min_value / b.price * 100) + " vom Preis" : "") : ""}
      ${b.pool_value ? stat("Alle Karten", num(b.pool_value) + " Coins", b.all_packs_cost ? `Alle Packs: ${num(b.all_packs_cost)} (${pct(b.pool_value / b.all_packs_cost * 100)})` : "") : ""}
      ${b.shipped ? stat("Verschickt", esc(b.shipped).replace(/ · /, "<br>")) : ""}
      ${b.per_day ? stat("Pro Tag", `${b.per_day}×`) : ""}
    </div>
    ${b.conditions ? `<div class="notice">${esc(b.conditions).replace(/\*\*/g, "").replace(/\n/g, "<br>")}</div>` : ""}
    ${hits.length > 3 ? `<h2>🏆 Alle Hits <small>${open} von ${hits.length} noch drin</small></h2>
      <div class="hits">${hits.map(hitCard).join("")}</div>` : ""}
    <h2>📉 Pack-Verlauf</h2>
    ${chart(b.history)}
    <h2>📦 Versandschübe <small>Werte ohne 10 % Steuer</small></h2>
    ${b.shipments.length ? `<div class="rows">${b.shipments.map((s) => `
      <div class="line"><span class="muted">${time(s.t)}</span>
      <span>+${num(s.cards)} Karten · +${num(s.coins)} Coins${s.players ? ` · +${num(s.players)} Spieler` : ""}</span></div>`).join("")}</div>`
      : `<div class="rows"><div class="line muted">Noch keine Versandschübe aufgezeichnet</div></div>`}
    <div class="buybar">
      <div class="big">${coins(b.price)}</div>
      ${openLink(b)}
      <div class="remaining">Verbleibend: <b>${num(b.remaining)} / ${num(b.total)}</b>
        <div class="bar" style="margin-top:4px"><span style="width:${left}%"></span></div></div>
      ${b.end ? `<div class="until">${esc(untilText(b.end))}</div>` : ""}
    </div>`;
}

// --- Push ---
const isStandalone = () => window.navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;

async function currentSubscription() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

function b64ToBytes(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

const EVENT_LABELS = {
  value: ["💰 Lohnt sich", "Ein Banner steigt neu über 100 % Ø Rückgabe"],
  hit: ["📦 Hit verschickt", "Ein Hit wurde erkannt"],
  new: ["🆕 Neuer Banner", "Ein neuer Banner ist online"],
};

async function showSettings() {
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const sub = supported ? await currentSubscription() : null;
  const prefs = sub ? (await api("/api/push/prefs", post({ endpoint: sub.endpoint }))).prefs : {};
  view.innerHTML = `
    <div class="section-title">🔔 Push-Benachrichtigungen</div>
    ${!isStandalone() ? `<div class="notice" style="margin-bottom:12px">Auf dem iPhone gehen Pushes nur, wenn die App installiert ist:
      in Safari auf <b>Teilen</b> → <b>Zum Home-Bildschirm</b>, dann die App vom Home-Bildschirm öffnen.</div>` : ""}
    <div class="panel">
      ${Object.entries(EVENT_LABELS).map(([k, [label, hint]]) => `
        <label class="toggle"><span>${label}<br><span class="hint">${hint}</span></span>
        <input type="checkbox" data-event="${k}" ${prefs[k] !== false ? "checked" : ""}></label>`).join("")}
      ${!supported ? `<div class="hint">Dieses Gerät/dieser Browser unterstützt keine Push-Benachrichtigungen.</div>`
        : sub ? `<button class="btn" id="test">Test-Push senden</button>
                 <button class="btn" id="off">Pushes ausschalten</button>`
              : `<button class="btn primary" id="on">Pushes einschalten</button>`}
      <div class="hint" id="msg"></div>
    </div>
    <h2>ℹ️ Über diese App</h2>
    <div class="panel"><div class="hint">Private, inoffizielle App mit den Daten deines GTCHA-Discord-Bots.
      Kein Angebot von GTCHA. Gezogen wird immer auf der offiziellen Seite.</div></div>`;
  const msg = view.querySelector("#msg");
  const prefsNow = () => Object.fromEntries([...view.querySelectorAll("[data-event]")].map((i) => [i.dataset.event, i.checked]));
  view.querySelector("#on")?.addEventListener("click", async () => {
    try {
      if ((await Notification.requestPermission()) !== "granted") { msg.textContent = "Benachrichtigungen wurden nicht erlaubt."; return; }
      const { key } = await api("/api/push/key");
      const reg = await navigator.serviceWorker.ready;
      const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
      await api("/api/push/subscribe", post({ subscription: s.toJSON(), prefs: prefsNow() }));
      showSettings();
    } catch (e) { msg.textContent = "Einschalten fehlgeschlagen: " + e.message; }
  });
  view.querySelector("#off")?.addEventListener("click", async () => {
    await api("/api/push/unsubscribe", post({ endpoint: sub.endpoint }));
    await sub.unsubscribe();
    showSettings();
  });
  view.querySelector("#test")?.addEventListener("click", async () => {
    await api("/api/push/test", post({ endpoint: sub.endpoint }));
    msg.textContent = "Test-Push gesendet.";
  });
  view.querySelectorAll("[data-event]").forEach((el) => el.addEventListener("change", async () => {
    if (sub) await api("/api/push/subscribe", post({ subscription: sub.toJSON(), prefs: prefsNow() }));
  }));
}

function post(body) {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

// --- Navigation ---
async function route() {
  clearInterval(state.timer);
  const hash = location.hash || "#/";
  const tab = hash.startsWith("#/hot") ? "hot" : hash.startsWith("#/settings") ? "settings" : "list";
  const banner = hash.match(/^#\/banner\/(\d+)/);
  document.querySelectorAll(".tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab && !banner));
  const render = banner ? () => showBanner(banner[1]) : tab === "hot" ? showHot : tab === "settings" ? showSettings : showList;
  if (!view.innerHTML || banner) view.innerHTML = `<div class="loading">Lädt …</div>`;
  try {
    await render();
    window.scrollTo({ top: 0 });
  } catch (e) {
    view.innerHTML = `<div class="empty">Daten nicht erreichbar (${esc(e.message)})</div>`;
  }
  if (tab !== "settings") state.timer = setInterval(() => render().catch(() => {}), REFRESH_MS);
}

// Bilder, die nicht laden, ausblenden statt Alt-Text zu zeigen
document.addEventListener("error", (e) => {
  if (e.target.tagName === "IMG") e.target.style.visibility = "hidden";
}, true);
window.addEventListener("hashchange", route);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
route();
