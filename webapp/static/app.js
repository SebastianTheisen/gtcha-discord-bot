"use strict";

const APP_VERSION = 85;   // zusammen mit ?v= in index.html und sw.js erhöhen

const view = document.getElementById("view");
const REFRESH_MS = 30000;
const STATUS = {
  running: ["🎯", "Läuft"], endspurt: ["⚡", "Endspurt"], hits_out: ["🔴", "Hits raus"], upcoming: ["🕒", "Bald"],
  ended: ["🗄️", "Beendet"],
};
const CATEGORIES = [["Alle", "Alle"], ["Bonus", "BONUS"], ["MIX", "MIX"], ["Pokémon", "Pokémon"],
                    ["One piece", "One Piece"], ["Dragon Ball", "Dragon Ball"], ["Store", "🏪 Store"],
                    ["Wunsch", "⭐ Wunschkarten"], ["Archiv", "🗄️ Archiv"]];
const SORTS = {
  site: ["Wie auf der Seite", (a, b) => b.id - a.id],
  ev: ["Ø Rückgabe", (a, b) => (b.ev_pct ?? -1) - (a.ev_pct ?? -1)],
  remaining: ["Wenigste Packs", (a, b) => a.remaining - b.remaining],
  price_low: ["Preis aufsteigend", (a, b) => a.price - b.price],
  price_high: ["Preis absteigend", (a, b) => b.price - a.price],
};

const CARDS_PER_PAGE = 20;
const QUICK_FILTERS = {
  ev: ["💰 ≥ 100 %", (b) => b.ev_pct != null && b.ev_pct >= 100],
  hits: ["🎯 Hits drin", (b) => b.status !== "hits_out" && (b.hits_open || 0) > 0],
  watched: ["👀 Beobachtet", (b) => state.watchIds?.has(String(b.id))],
  mine: ["✅ Für mich", (b) => canBuy(b) !== false],
};
const state = { category: load("category", "Alle"), sort: load("sort", "ev"), timer: null, render: null, cardPage: {},
                detailTab: {}, cardFilter: load("cardFilter", "all"), cardSize: load("cardSize", "4"),
                listView: load("listView", "big"), filters: new Set(JSON.parse(load("filters", "[]"))) };

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
// Bilder von GTCHA über den Zwischenspeicher des VPS laden (schneller, bleiben 30 Tage im iPhone-Cache)
// v= ändert die Adresse, wenn sich die Auslieferung ändert: Safari hält Bilder 30 Tage und würde sonst
// alte (kaputte) Antworten weiterverwenden
const IMG_VERSION = 3;
// w = Breite in Pixeln: der VPS liefert eine verkleinerte Kopie (Banner 640, Karten 320)
const imgSrc = (u, w = 640) => (/^https:\/\/([\w-]+\.)*gtchaxonline\.com\//.test(u)
  ? `/img?v=${IMG_VERSION}&w=${w}&u=${encodeURIComponent(u)}` : u);
const img = (u, alt = "", eager = false, w = 640) => (safeUrl(u)
  ? `<img src="${esc(imgSrc(u, w))}" data-orig="${esc(u)}" alt="${esc(alt)}" loading="eager">` : "");
const evClass = (p) => (p == null ? "" : p >= 100 ? "good" : p >= 70 ? "ok" : "");
const time = (t) => new Date(t * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const coins = (n) => `<span class="coin"></span>${n ? num(n) : "Gratis"}`;
// Installierte App: GTCHA im echten Browser öffnen (dort ist man eingeloggt), nicht im App-Fenster.
// iOS 17+: x-safari-https:// öffnet Safari, googlechromes:// öffnet Chrome.
const LINK_MODES = { safari: "Safari", chrome: "Chrome", app: "in der App" };
const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
function buyHref(url) {
  const mode = load("linkMode", "safari");
  const installed = window.navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;
  if (!installed || !isIOS() || mode === "app") return url;
  return mode === "chrome" ? url.replace(/^https:/, "googlechromes:") : `x-safari-${url}`;
}
const openLink = (b, label = "Öffnen ↗") => (safeUrl(b.buy_url) && !b.archived
  ? `<a class="open-btn" href="${esc(buyHref(b.buy_url))}" target="_blank" rel="noopener noreferrer" data-stop>${label}</a>` : "");

// HTML nur ersetzen, wenn es sich geändert hat - sonst laden alle Bilder neu (alle 30 s, beim Start doppelt)
function setHtml(el, html) {
  if (!el || el.__html === html) return false;
  el.innerHTML = html;
  el.__html = html;
  return true;
}

async function api(path, options) {
  const token = load("deviceToken", "");
  const res = await fetch(path, { cache: "no-store", ...options,
    headers: { ...(options?.headers || {}), ...(token ? { "X-Device-Token": token } : {}) } });
  if (res.status === 423) {   // gesperrt: nicht verknüpft oder länger als 7 Tage nicht übertragen
    const err = new Error("locked");
    err.locked = (await res.json().catch(() => ({}))).locked || {};
    throw err;
  }
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

// "Letzter Abgleich" mit Resttagen bis zur Sperre (Admins ausgenommen)
function syncLine(user) {
  const s = user?.sync;
  if (!s || s.exempt) return "";
  if (!s.last_sync) return `<div class="notice">Noch nie übertragen – bis dahin sind Banner & Co. gesperrt.</div>`;
  const last = `${s.last_sync.slice(8, 10)}.${s.last_sync.slice(5, 7)}. ${s.last_sync.slice(11, 16)}`;
  if (!s.ok) return `<div class="notice">Zuletzt übertragen am ${last} – länger als ${s.days} Tage her, App gesperrt.</div>`;
  const left = Math.floor(s.days_left);
  return `<div class="${left <= 2 ? "notice" : "hint"}">Zuletzt übertragen: ${last} · ${left <= 0 ? "heute"
    : `noch ${left} Tag${left === 1 ? "" : "e"}`} bis zur nächsten Pflicht (alle ${s.days} Tage)</div>`;
}

// Sperrbildschirm: Banner & Co. erst nach Verknüpfen bzw. Übertragen (alle 7 Tage)
function lockHtml(info) {
  const last = info.last_sync ? `${info.last_sync.slice(8, 10)}.${info.last_sync.slice(5, 7)}.` : null;
  const why = info.reason === "link"
    ? "Bitte zuerst dieses Gerät mit Discord verknüpfen (Reiter <b>Ich</b> → „Discord verknüpfen“)."
    : `Alle ${info.days || 7} Tage müssen deine GTCHA-Daten einmal übertragen werden${last
      ? ` – zuletzt am <b>${last}</b>` : " – bisher noch nie"}. Danach ist sofort alles wieder offen.`;
  return `<div class="lock">
    <div class="lock-icon">🔒</div>
    <div class="lock-title">${info.reason === "link" ? "Gerät nicht verknüpft" : "Bitte Daten übertragen"}</div>
    <div class="lock-text">${why}</div>
    ${info.reason === "link" ? `<a class="btn primary" href="#/settings">Zu „Ich“</a>`
      : `<a class="btn primary" href="${esc(buyHref("https://gtchaxonline.com/pending-detail"))}" target="_blank" rel="noopener">📥 GTCHA öffnen</a>
         <div class="lock-text">Dort das Lesezeichen „An GTCHA Tracker“ aufrufen. Noch nicht eingerichtet?
           <a href="#/settings">Ich → 📖 Anleitung</a></div>`}
  </div>`;
}

function hitsText(b) {
  if (b.hits_open == null) return "";
  return b.tracked_hits ? `${b.hits_open}/${b.hits_total} Hits` : `Top 3: ${b.hits_open}`;
}

function flags(b) {
  if (b.archived) return "";   // Kaufbedingungen gelten nicht mehr
  const out = [];
  if (b.per_day) out.push(`Beschränkt auf ${b.per_day} Mal pro Tag`);
  if (b.min_charge) out.push(`${num(b.min_charge)} Coins benötigt diesen Monat`);
  if (b.password) out.push("🔒 Nur mit Passwort");
  const wished = state.wishBanners?.get(b.id);
  if (wished) out.push(`⭐ ${wished.join(", ")}`);
  return out.length ? `<div class="flags">${out.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}</div>` : "";
}

// Mein Mitgliedsrang und meine Aufladung diesen Monat (nur auf diesem Gerät gespeichert)
const RANKS = [["white", "Weiß"], ["bronze", "Bronze"], ["silver", "Silber"], ["gold", "Gold"],
               ["rainbow", "Rainbow"], ["black", "Black"]];
const myRank = () => load("myRank", "");
const myCharge = () => Number(load("myCharge", "0")) || 0;
// Rang und Aufladung aus dem letzten "Alles übertragen" übernehmen - nur wenn es neue Daten gibt,
// eine Änderung von Hand gilt also bis zum nächsten Übertragen. Ohne Übertragen bleibt alles manuell.
function applyProfile(p) {
  if (!p || !p.updated_at || (!p.rank && p.charge == null)) return false;
  const stamp = `${p.updated_at}|${p.rank}|${p.charge}`;
  if (load("profileApplied", "") === stamp) return false;
  if (p.rank) save("myRank", p.rank);
  if (p.charge != null) save("myCharge", String(p.charge));
  save("profileApplied", stamp);
  return true;
}
// true = kann ich kaufen, false = nicht, null = unbekannt (Rang nicht eingestellt)
function canBuy(b) {
  if (!myRank()) return null;
  if (b.password) return false;
  if (b.ranks?.length && !b.ranks.includes(myRank())) return false;
  if (b.min_charge && myCharge() < b.min_charge) return false;
  return true;
}
function whyNot(b) {
  if (b.password) return "nur mit Passwort";
  if (b.ranks?.length && !b.ranks.includes(myRank())) return "nicht für deinen Mitgliedsrang";
  if (b.min_charge && myCharge() < b.min_charge) return `erst ab ${num(b.min_charge)} Coins Aufladung im Monat`;
  return "";
}
const notMineNote = (b) => (canBuy(b) === false ? `<div class="notice warn">🚫 Für dich nicht kaufbar: ${whyNot(b)}</div>` : "");

// Verkaufsende einheitlich in deutscher Zeit (die Seite liefert verschiedene Formate, teils japanisch)
function untilText(b) {
  if (b.archived) return b.ended_at ? `Beendet am ${time(b.ended_at)} Uhr` : "Beendet";
  if (b.end_ts) {
    const d = new Date(b.end_ts * 1000);
    const date = d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "Europe/Berlin" });
    const clock = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Berlin" });
    return `Erhältlich bis ${date}, ${clock} Uhr`;
  }
  const t = String(b.end || "");
  return /erhältlich/i.test(t) ? t : `Erhältlich bis ${t}`;
}

// Banner-Zeile im Aufbau der Seite: Bild links, Infofeld rechts, darunter unsere Zusatzinfos
function row(b, rank) {
  const [icon, label] = STATUS[b.status] || STATUS.running;
  const left = b.total ? Math.max(0, Math.min(100, (b.remaining / b.total) * 100)) : 0;
  return `
    <div class="entry ${rank ? "ranked" : ""} ${canBuy(b) === false ? "not-mine" : ""}">
      ${rank ? `<span class="rank-no ${rank <= 3 ? "r" + rank : ""}">${rank}</span>` : ""}
      ${flags(b)}
      <div class="row-card ${b.status === "hits_out" ? "done" : ""}" data-href="#/banner/${b.id}">
        ${b.headline ? `<div class="row-title">${esc(b.headline)}</div>` : ""}
        <div class="media">
          ${img(b.image, b.title)}
          ${b.status !== "running" ? `<span class="status ${b.status}">${icon} ${label}</span>` : ""}
          <span class="price-pill">${coins(b.price)}</span>
        </div>
        <div class="panel-side">
          <div class="pid">ID ${b.id}</div>
          <div class="ranks">${(b.ranks || []).map((r) => `<span class="rank ${esc(r)}"></span>`).join("")}</div>
          ${openLink(b)}
          <div class="remaining">Verbleibend: <b>${num(b.remaining)} / ${num(b.total)}</b></div>
          <div class="bar"><span style="width:${left}%"></span></div>
          <div class="extra">
            ${b.ev_pct != null && !b.archived ? `<span class="pill ${evClass(b.ev_pct)}">Ø ${pct(b.ev_pct)}</span>` : ""}
            ${hitsText(b) ? `<span class="pill">${hitsText(b)}${b.unsure ? " ❓" : ""}</span>` : ""}
          </div>
          ${shipLine(b)}
          ${outLine(b)}
          ${b.end || b.archived ? `<div class="until">${esc(untilText(b))}</div>` : ""}
        </div>
      </div>
    </div>`;
}

// Kompaktansicht: eine Zeile pro Banner
function compactRow(b) {
  const [icon] = STATUS[b.status] || STATUS.running;
  const left = b.total ? Math.max(0, Math.min(100, (b.remaining / b.total) * 100)) : 0;
  return `<div class="crow ${b.status === "hits_out" ? "done" : ""} ${canBuy(b) === false ? "not-mine" : ""}" data-href="#/banner/${b.id}">
    <div class="crow-img">${img(b.image, b.title)}</div>
    <div class="crow-main">
      <div class="crow-top"><b>${b.id}</b> <span class="muted">${esc(b.category || "")}</span>
        ${b.status !== "running" ? `<span class="crow-status">${icon}</span>` : ""}</div>
      ${b.headline ? `<div class="crow-title">${esc(b.headline)}</div>` : ""}
      <div class="crow-sub">${num(b.remaining)} / ${num(b.total)} Packs${hitsText(b) ? ` · ${hitsText(b)}` : ""}${b.unsure ? " ❓" : ""}</div>
      <div class="bar thin"><span style="width:${left}%"></span></div>
    </div>
    <div class="crow-right"><div class="crow-price">${coins(b.price)}</div>
      ${b.ev_pct != null && !b.archived ? `<span class="pill ${evClass(b.ev_pct)}">Ø ${pct(b.ev_pct)}</span>` : ""}</div>
  </div>`;
}

// "✅ Raus: Lugia Kristall, Pikachu Promo" - Hits, die sicher raus sind (wie in der Hit-Liste)
function outLine(b) {
  const out = b.out || [];
  if (!out.length && !b.out_unsure) return "";
  const names = out.slice(0, 2).map((h) => esc(h.name)).join(", ") + (out.length > 2 ? ` +${out.length - 2}` : "");
  return `<div class="out">${out.length ? `✅ Raus: <b>${names}</b>` : ""}${b.out_unsure
    ? `${out.length ? " · " : ""}❓ ${b.out_unsure} unklar` : ""}</div>`;
}

// "📦 18 verschickt · 103.070 Coins" (Kartenwert der verschickten Karten)
function shipLine(b) {
  if (b.ship_cards == null) return "";
  if (!b.ship_cards) return `<div class="ship muted">📦 Noch nichts verschickt</div>`;
  return `<div class="ship">📦 <b>${num(b.ship_cards)}</b> verschickt · <b>${num(b.ship_value)}</b> Coins</div>`;
}

function wireRows() {
  view.querySelectorAll("[data-stop]").forEach((a) => a.addEventListener("click", (e) => e.stopPropagation()));
  view.querySelectorAll("[data-href]").forEach((el) => el.addEventListener("click", () => { location.hash = el.dataset.href; }));
}

// --- Seiten ---
// Suche: Banner-ID (Teil reicht) oder Titel; sucht über alle Kategorien
function matches(b, q) {
  const digits = q.replace(/\D/g, "");
  return (digits && String(b.id).includes(digits)) || (b.title || "").toLowerCase().includes(q.toLowerCase());
}

async function showList() {
  const { banners, updated } = await api("/api/banners");
  state.listData = { banners, updated };
  // Grundgerüst nur einmal bauen, damit das Suchfeld beim Aktualisieren den Fokus behält
  if (!view.querySelector("#search")) {
    view.innerHTML = `
      <div class="tabs">${CATEGORIES.map(([key, label]) =>
        `<button class="tab ${key === "Bonus" ? "bonus" : ""}" data-cat="${esc(key)}">${esc(label)}</button>`).join("")}</div>
      <form class="search" id="search-form" role="search">
        <input id="search" type="search" inputmode="numeric" autocomplete="off" placeholder="🔍 Banner-ID suchen, z. B. 24149">
      </form>
      <div class="qfilters">${Object.entries(QUICK_FILTERS).map(([k, [label]]) =>
        `<button class="qf" data-qf="${k}">${label}</button>`).join("")}</div>
      <div class="toolbar">
        <span class="count" id="count"></span>
        <div class="toolbar-right">
          <button class="view-toggle" id="view-toggle" aria-label="Ansicht wechseln"></button>
          <select id="sort" aria-label="Sortierung">${Object.entries(SORTS).map(([k, [label]]) =>
            `<option value="${k}">${label}</option>`).join("")}</select>
        </div>
      </div>
      <div id="results"></div>`;
    view.querySelectorAll(".tab").forEach((el) => el.addEventListener("click", () => {
      state.category = el.dataset.cat; save("category", state.category); drawList();
    }));
    view.querySelector("#sort").addEventListener("change", (e) => {
      state.sort = e.target.value; save("sort", state.sort); drawList();
    });
    view.querySelectorAll(".qf").forEach((el) => el.addEventListener("click", () => {
      const k = el.dataset.qf;
      if (k === "mine" && !myRank()) {
        ask("Mitgliedsrang fehlt", "Stell im Reiter <b>Ich</b> deinen Mitgliedsrang ein, dann zeigt der Filter nur Banner, die du kaufen kannst.",
          ["Später", "Einstellen"]).then((r) => { if (r === "Einstellen") location.hash = "#/settings"; });
        return;
      }
      state.filters.has(k) ? state.filters.delete(k) : state.filters.add(k);
      save("filters", JSON.stringify([...state.filters]));
      drawList();
    }));
    view.querySelector("#view-toggle").addEventListener("click", () => {
      state.listView = state.listView === "big" ? "compact" : "big"; save("listView", state.listView); drawList();
    });
    const input = view.querySelector("#search");
    input.value = state.query || "";
    input.addEventListener("input", () => { state.query = input.value.trim(); drawList(); });
    // Enter: genau ein Treffer -> öffnen; volle ID, die nicht (mehr) aktiv ist -> trotzdem versuchen
    view.querySelector("#search-form").addEventListener("submit", (e) => {
      e.preventDefault();
      input.blur();
      const hits = state.listData.banners.filter((b) => matches(b, state.query || ""));
      const digits = (state.query || "").replace(/\D/g, "");
      if (hits.length === 1) location.hash = `#/banner/${hits[0].id}`;
      else if (digits.length >= 4) location.hash = `#/banner/${digits}`;
    });
  }
  view.querySelector("#sort").value = state.sort;
  drawList();
  // Banner mit Wunschkarten (noch nicht gezogen) für die Kategorie "⭐ Wunschkarten"
  const wish = wishList();
  if (wish.length) {
    api(`/api/cards?ids=${wish.map((w) => w.id).join(",")}`).then((res) => {
      const map = new Map();
      for (const c of res.cards) for (const wb of c.banners) {
        if (!wb.out) map.set(wb.id, [...(map.get(wb.id) || []), c.name]);
      }
      state.wishBanners = map;
      drawList();
    }).catch(() => {});
  } else state.wishBanners = new Map();
  // beobachtete Banner für den Filter (aus den Push-Einstellungen dieses Geräts)
  if (state.watchIds === undefined) {
    state.watchIds = new Set();
    pushState().then(({ prefs }) => { state.watchIds = new Set(Object.keys(prefs.watch || {})); drawList(); }).catch(() => {});
  }
}

function drawList() {
  const q = state.query || "";
  const archive = state.category === "Archiv" && !q;
  if (archive && !state.archive) {   // erst beim Öffnen der Kategorie laden (selten gebraucht)
    state.archive = { banners: [], updated: state.listData.updated };
    api("/api/archive").then((d) => { state.archive = d; drawList(); }).catch(() => { state.archive = null; });
  }
  const { banners, updated } = archive ? state.archive : state.listData;
  const sort = SORTS[state.sort] || SORTS.ev;
  const active = [...state.filters].filter((k) => QUICK_FILTERS[k] && (k !== "mine" || myRank()));
  const shown = banners
    .filter((b) => (q ? matches(b, q) : archive || state.category === "Alle" || b.category === state.category
      || (state.category === "Wunsch" && state.wishBanners?.has(b.id) && canBuy(b) !== false)))
    .filter((b) => active.every((k) => QUICK_FILTERS[k][1](b)))
    .sort(sort[1]);
  view.querySelectorAll(".tab").forEach((el) => el.classList.toggle("active", !q && el.dataset.cat === state.category));
  view.querySelectorAll(".qf").forEach((el) => el.classList.toggle("on", active.includes(el.dataset.qf)));
  view.querySelector("#view-toggle").textContent = state.listView === "big" ? "☰ Kompakt" : "▦ Groß";
  view.querySelector("#count").textContent = q ? `${shown.length} Treffer für „${q}“` : `${shown.length} Banner`;
  const changed = setHtml(view.querySelector("#results"), `
    ${shown.length ? (state.listView === "compact"
      ? `<div class="clist">${shown.map(compactRow).join("")}</div>`
      : `<div class="list">${shown.map((b) => row(b)).join("")}</div>`)
      : `<div class="empty">${q ? "Nicht gefunden – Enter öffnet die ID"
        : state.category === "Wunsch" ? (wishList().length ? "Gerade keine Wunschkarte kaufbar."
          : "Noch keine Wunschkarten (⭐ merken).")
        : archive ? "Noch keine beendeten Banner (sie bleiben hier 30 Tage)"
        : active.length ? "Keine Banner für diese Filter" : "Keine Banner"}</div>`}
    <div class="updated" id="updated"></div>`);
  view.querySelector("#updated").textContent = `Stand ${time(updated)}`;
  if (changed) wireRows();
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

// Was alle Restpacks zusammen kosten und wie viel rechnerisch dabei rauskommt (aktualisiert sich mit den Packs)
function restCost(b) {
  if (!b.price || !b.remaining) return "";
  const cost = b.price * b.remaining;
  const diff = (b.left_value || 0) - cost;
  const cls = diff >= 0 ? "good" : "bad";
  return `<br>Alle ${num(b.remaining)} Restpacks kosten <b>${num(cost)} Coins</b>`
    + `<br><span class="ev ${cls}">${diff >= 0 ? "+" : "−"}${num(Math.abs(diff))} Coins ${diff >= 0 ? "mehr zurück" : "weniger zurück"}</span>`;
}

function stat(label, value, sub = "") {
  return `<div class="stat"><div class="label">${label}</div><div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

// Ø Rückgabe (%) über die Zeit, mit 100-%-Linie
function evChart(points) {
  if (!points || points.length < 2) return `<div class="chart"><div class="empty">Wird ab jetzt aufgezeichnet</div></div>`;
  const W = 600, H = 200, P = 28;
  const t0 = points[0].t, t1 = points[points.length - 1].t;
  const vals = points.map((p) => p.ev);
  const max = Math.max(110, ...vals), min = Math.min(60, ...vals);
  const x = (t) => P + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * P);
  const y = (v) => P / 2 + (1 - (v - min) / Math.max(1, max - min)) * (H - P * 1.5);
  const pts = points.map((p) => `${x(p.t).toFixed(1)},${y(p.ev).toFixed(1)}`).join(" ");
  const last = vals[vals.length - 1];
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Verlauf der Ø Rückgabe">
    <line x1="${P}" x2="${W - P}" y1="${y(100)}" y2="${y(100)}" stroke="#1f9d55" stroke-dasharray="6 5" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
    <polyline points="${pts}" fill="none" stroke="#3d7fd9" stroke-width="2.5" vector-effect="non-scaling-stroke"/>
    <text class="axis" x="${P}" y="${H - 6}">${time(t0)}</text>
    <text class="axis" x="${W - P}" y="${H - 6}" text-anchor="end">${time(t1)}</text>
    <text class="axis" x="${W - P}" y="16" text-anchor="end">jetzt ${pct(last)}</text>
  </svg></div>`;
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

// Meldbare Exemplare einer Karte (aus der Hit-Liste des Banners), nach Platz
const unitsOf = (b, card) => (b.hits || []).filter((h) => h.key === card.id || h.key.startsWith(card.id + "#"))
  .sort((x, y) => x.rank - y.rank);

// Unter der Karte: wer hat sie, wann und wie (Medaille aus Discord/App/Lesezeichen) oder wann verschickt
// kurz, weil die Kachel schmal ist: Platz nur bei mehreren Exemplaren, Weg als Symbol (💬 Discord, 📱 App, 🔖 Lesezeichen)
const VIA = { discord: "💬", app: "📱", lesezeichen: "🔖" };
const shortTime = (t) => new Date(t * 1000).toLocaleString("de-DE",
  { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(",", "");
function originLines(units, meId) {
  const many = units.length > 1;
  return units.map((u) => {
    const o = u.origin, tier = many ? `${u.tier} ` : "";
    if (!o) return u.state === "unsure" && u.odds != null ? `❓ ${tier}~${u.odds} % raus` : "";
    if (o.via === "admin") return `🛠️ ${tier}durch Admin abgehakt${o.at ? ` · ${shortTime(o.at)}` : ""}`;
    if (o.via === "versand") {
      return u.state === "unsure"
        ? `❓ ${tier}${u.odds != null ? `~${u.odds} % · ` : ""}📦 ${shortTime(o.shipped_at)}`
        : `📦 ${tier}verschickt ${shortTime(o.shipped_at)}`;
    }
    const who = meId && o.user === String(meId) ? "du" : o.name || "unbekannt";
    const when = o.via === "lesezeichen" && o.pulled_on ? `gez. ${o.pulled_on.slice(8, 10)}.${o.pulled_on.slice(5, 7)}.`
      : o.at ? shortTime(o.at) : "";
    return `🏅 ${tier}${who} ${VIA[o.via] || "💬"}${when ? ` · ${when}` : ""}`;
  }).filter(Boolean).slice(0, 3);
}

function cardTile(c, price, units = [], meId = null) {
  const gone = c.pulled >= c.copies;
  const share = c.share.toLocaleString("de-DE", { maximumFractionDigits: c.share < 0.1 ? 3 : c.share < 1 ? 2 : 1 }) + " %";
  const tiers = units.length ? (units.length === 1 ? units[0].tier : `${units[0].tier}–${units[units.length - 1].tier}`) : "";
  const mine = meId && units.some((u) => u.medal_user === String(meId));
  return `<div class="tile ${gone ? "gone" : ""} ${c.unsure && !gone ? "unsure" : ""} ${units.length ? "claimable" : ""}"
      data-card="${esc(c.id)}">
    <div class="tile-art">
      ${img(c.image, c.name, false, 320)}
      ${tiers ? `<span class="tier-badge">${tiers}</span>` : ""}
      ${c.copies > 1 ? `<span class="copies">×${c.copies}</span>` : ""}
      ${c.hit ? `<span class="ship-tag small">Versand nur ✈</span>` : ""}
    </div>
    <div class="tile-base"></div>
    <div class="tile-value ${price && c.value >= price ? "above" : ""}"><span class="coin"></span>${num(c.value)}</div>
    <div class="tile-name">${esc(c.name)}</div>
    <div class="tile-meta">${share}${c.pulled && !gone ? ` · ${c.pulled}/${c.copies} gezogen` : ""}${mine ? " · von dir" : ""}</div>
    ${c.unsure && !gone && !units.some((u) => u.odds != null) ? `<div class="tile-meta unsure-note">❓ ${esc(c.unsure)}</div>` : ""}
    ${originLines(units, meId).map((l) => `<div class="tile-meta origin">${esc(l)}</div>`).join("")}
  </div>`;
}

function pager(page, pages) {
  if (pages <= 1) return "";
  const start = Math.max(1, Math.min(page - 2, pages - 4)), end = Math.min(pages, start + 4);
  const btn = (p, label, off = false, active = false) =>
    `<button class="pg ${active ? "active" : ""}" data-page="${p}" ${off ? "disabled" : ""}>${label}</button>`;
  let out = btn(1, "«", page === 1) + btn(page - 1, "‹", page === 1);
  for (let p = start; p <= end; p++) out += btn(p, p, false, p === page);
  return `<div class="pager">${out}${btn(page + 1, "›", page === pages)}${btn(pages, "»", page === pages)}</div>`;
}

const CARD_FILTERS = { all: "Alle", above: "Ab Packpreis", open: "Noch offen" };

function renderCards(b) {
  const box = view.querySelector("#cards");
  if (!box) return;
  const filter = state.cardFilter;
  const list = b.cards.filter((c) => filter === "above" ? b.price && c.value >= b.price
    : filter === "open" ? c.pulled < c.copies : true);
  const perPage = state.cardSize === "5" ? 25 : CARDS_PER_PAGE;
  const pages = Math.max(1, Math.ceil(list.length / perPage));
  const page = Math.min(state.cardPage[b.id] || 1, pages);
  const shown = list.slice((page - 1) * perPage, page * perPage);
  const meId = state.me?.user_id || null;
  if (!setHtml(box, `<div class="card-tools">
      <div class="seg">${Object.entries(CARD_FILTERS).map(([k, label]) =>
        `<button class="seg-btn ${k === filter ? "on" : ""}" data-filter="${k}">${label}</button>`).join("")}</div>
      <button class="seg-btn size" data-size="${state.cardSize === "5" ? "4" : "5"}" aria-label="Kachelgröße">
        ${state.cardSize === "5" ? "▦ groß" : "▦ klein"}</button>
    </div>
    ${list.length ? `<div class="tiles ${state.cardSize === "5" ? "small" : ""}">${shown.map((c) =>
      cardTile(c, b.price, unitsOf(b, c), meId)).join("")}</div>${pager(page, pages)}`
      : `<div class="empty">Keine Karten für diesen Filter</div>`}`)) return;
  box.querySelectorAll("[data-filter]").forEach((el) => el.addEventListener("click", () => {
    state.cardFilter = el.dataset.filter; save("cardFilter", state.cardFilter); state.cardPage[b.id] = 1; renderCards(b);
  }));
  box.querySelector("[data-size]")?.addEventListener("click", (e) => {
    state.cardSize = e.currentTarget.dataset.size; save("cardSize", state.cardSize); state.cardPage[b.id] = 1; renderCards(b);
  });
  box.querySelectorAll(".tile[data-card]").forEach((el) => el.addEventListener("click", () => {
    const card = b.cards.find((c) => c.id === el.dataset.card);
    if (card && !b.archived) claimFlow(b, card).catch((e) => ask("Fehler", esc(e.message), ["OK"]));
  }));
  box.querySelectorAll("[data-page]").forEach((el) => el.addEventListener("click", () => {
    state.cardPage[b.id] = Number(el.dataset.page);
    renderCards(b);
    view.querySelector(".dtabs")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
}

// Ampel: lohnt sich (ab 100 %), knapp (90-100 %), lohnt sich nicht
function verdict(b) {
  if (b.archived) return ["grey", "Beendet", ""];
  if (b.ev_pct == null) return ["grey", "Keine Daten", ""];
  if (b.status === "upcoming") return ["grey", "Noch nicht gestartet", ""];
  if (b.ev_pct >= 100) return ["green", "Lohnt sich", ""];
  if (b.ev_pct >= 90) return ["yellow", "Knapp", ""];
  return ["red", "Lohnt sich nicht", ""];
}

function glance(b) {
  const [color, title, sub] = verdict(b);
  const diff = b.price && b.remaining && b.left_value != null && !b.archived ? b.left_value - b.price * b.remaining : null;
  return `<div class="glance ${color}">
    <div class="glance-head"><span class="light"></span><div><div class="glance-title">${title}</div>
      ${sub ? `<div class="glance-sub">${sub}</div>` : ""}</div>
      ${b.ev_pct != null && !b.archived ? `<div class="glance-pct">${pct(b.ev_pct)}<small>Ø ${num(b.ev)} Coins/Zug</small></div>` : ""}</div>
    <div class="glance-facts">
      ${b.hits_open != null ? `<span>🎯 <b>${hitsText(b)}</b>${b.unsure ? " ❓" : ""}</span>` : ""}
      <span>📦 <b>${num(b.remaining)}</b> von ${num(b.total)} ${b.archived ? "übrig beim Ende" : "übrig"}</span>
      ${diff != null ? `<span>💰 Rest kaufen: <b class="${diff >= 0 ? "pos" : "neg"}">${diff >= 0 ? "+" : "−"}${num(Math.abs(diff))}</b></span>` : ""}
      ${b.cost_to_hit && !b.archived ? `<span>⏱ Ø <b>${num(b.cost_to_hit)}</b> bis Hit</span>` : ""}
    </div>
    ${b.end || b.archived ? `<div class="glance-until">${b.archived ? "🗄️" : "⏳"} ${esc(untilText(b))}</div>` : ""}
    ${b.archived ? archiveSummary(b) : ""}
    <div class="glance-note">${b.archived ? "Stand beim Ende · bleibt 30 Tage im Archiv"
      : b.ev_from_site ? "aus Seitenzahlen" : "geschätzt"}</div>
  </div>`;
}

// Archiv: kurze Bilanz des beendeten Banners (wie die Hits rausgingen, was noch bei Spielern liegt)
function archiveSummary(b) {
  const out = b.out || [];
  const medals = out.filter((h) => h.via === "medaille").length, detected = out.length - medals;
  const parts = [];
  if (out.length) parts.push(`🏅 ${medals} per Medaille · 📦 ${detected} Versand erkannt`);
  if (b.out_unsure) parts.push(`❓ ${b.out_unsure} unklar`);
  if (b.undecided) parts.push(`🎒 ${num(b.undecided)} Coins noch bei Spielern`);
  return parts.length ? `<div class="glance-until">${parts.join(" · ")}</div>` : "";
}

const DETAIL_TABS = { overview: "Übersicht", cards: "Karten", history: "Verlauf" };

async function showBanner(id) {
  const b = await api(`/api/banner/${encodeURIComponent(id)}`);
  const [icon, label] = STATUS[b.status] || STATUS.running;
  const hits = b.hits.map((h) => ({ ...h, ship: b.tracked_hits }));
  const open = hits.filter((h) => h.state === "open").length;
  const left = b.total ? Math.max(0, Math.min(100, (b.remaining / b.total) * 100)) : 0;
  const tab = state.detailTab[b.id] || "overview";
  const sig = JSON.stringify(b);
  if (state.detailSig === sig && view.querySelector(".dtabs")) return;   // unverändert: Bilder nicht neu laden
  state.detailSig = sig;
  view.innerHTML = `
    <a class="back" href="#/" data-back>‹ Zurück</a>
    ${flags(b)}
    <div class="hero">${img(b.image, b.title, true)}${b.status !== "running" ? `<span class="status ${b.status}">${icon} ${label}</span>` : ""}
      <span class="price-pill">${coins(b.price)}</span></div>
    ${b.headline ? `<h1 class="d-title">${esc(b.headline)}</h1>` : ""}
    ${notMineNote(b)}
    ${glance(b)}
    <div class="watch-row" ${b.archived ? "hidden" : ""}><button class="watch-btn" id="watch-btn">🔔 Beobachten</button>
      <a class="hint" href="#/settings">Einstellungen ›</a></div>
    <div class="dtabs" role="tablist">${Object.entries(DETAIL_TABS).map(([k, l]) =>
      `<button class="dtab ${k === tab ? "on" : ""}" data-tab="${k}" role="tab">${l}</button>`).join("")}</div>

    <section class="pane" data-pane="overview" ${tab === "overview" ? "" : "hidden"}>
      <div class="stats">
        ${b.ev != null && !b.archived ? stat("Ø Rückgabe pro Zug", `<span class="ev ${evClass(b.ev_pct)}">${num(b.ev)} Coins</span>`, (b.ev_pct != null ? pct(b.ev_pct) + " vom Preis" : "") + (b.ev_from_site ? " · aus Zahlen der Seite" : " · geschätzt")) : ""}
        ${b.hits_open != null ? stat(b.archived ? "Hits beim Ende" : "Hits noch drin", hitsText(b) + (b.unsure ? " ❓" : ""), b.cost_to_hit && !b.archived ? `Ø ${num(b.cost_to_hit)} Coins bis Hit` : "") : ""}
        ${b.min_value != null ? stat("Mindestens zurück", num(b.min_value) + " Coins", b.price ? pct(b.min_value / b.price * 100) + " vom Preis" : "") : ""}
        ${b.pool_value ? stat("Alle Karten", num(b.pool_value) + " Coins", b.all_packs_cost ? `Alle Packs: ${num(b.all_packs_cost)} (${pct(b.pool_value / b.all_packs_cost * 100)})` : "") : ""}
        ${b.out_total != null ? stat("Aus dem Banner raus", `${num(b.out_total + (b.undecided || 0))} Coins`,
          `📦 verschickt: ${num(b.ship_cards)} ${b.ship_cards === 1 ? "Karte" : "Karten"} · ${num(b.ship_value)} Coins`
           + `${b.ship_players ? ` · ${num(b.ship_players)} Spieler` : ""}<br>
           🪙 umgewandelt: ${num(b.converted)} Coins${b.converted_max_cards != null ? ` · höchstens ${num(b.converted_max_cards)} Karten` : ""}<br>
           ${b.undecided != null ? `🎒 noch bei Spielern: ${num(b.undecided)} Coins (gezogen, noch nicht verschickt oder umgewandelt)<br>` : ""}
           <i>Kartenwerte; die Seite zählt den Versand ohne 10 % Steuer (${num(b.ship_counted)})</i>`) : ""}
        ${b.left_value != null && !b.archived ? stat("Noch im Banner (rechnerisch)", `${num(b.left_value)} Coins`,
          (b.left_per_pack != null ? `Ø ${num(b.left_per_pack)} pro Restpack${b.price ? ` (${pct(b.left_per_pack / b.price * 100)} vom Preis)` : ""}` : "")
          + restCost(b)) : ""}
        ${b.ship_cards != null && b.out_total == null ? stat("Verschickt", b.ship_cards ? `${num(b.ship_cards)} Karten` : "Noch nichts",
          b.ship_cards ? `${num(b.ship_value)} Coins Kartenwert · ${num(b.ship_players)} Spieler` : "") : ""}
        ${b.per_day && !b.archived ? stat("Pro Tag", `${b.per_day}×`) : ""}
      </div>
      ${b.conditions ? `<div class="notice">${esc(b.conditions).replace(/\*\*/g, "").replace(/\n/g, "<br>")}</div>` : ""}
    </section>

    <section class="pane" data-pane="cards" ${tab === "cards" ? "" : "hidden"}>
      ${b.cards.length ? `<h2 id="cards-title">🃏 Alle Karten <small>${num(b.cards.reduce((n, c) => n + c.copies, 0))} Karten · ${b.cards.length} verschiedene</small></h2>
        <div class="hint pane-hint">${b.share_above_price != null ? `${pct(b.share_above_price)} ≥ Packpreis (<b>gold</b>) · ` : ""}${b.archived ? "beendet" : "Antippen = melden"}</div>
        <div id="cards"></div>`
        : hits.length ? `<h2>🏆 Hits <small>${open} von ${hits.length} noch drin</small></h2>
        <div class="hits">${hits.map(hitCard).join("")}</div>` : `<div class="empty">Kartenliste noch nicht geladen</div>`}
    </section>

    <section class="pane" data-pane="history" ${tab === "history" ? "" : "hidden"}>
      <h2>📉 Pack-Verlauf</h2>
      ${chart(b.history)}
      <h2>💰 Ø Rückgabe im Verlauf <small>gestrichelt = 100 %</small></h2>
      ${evChart(b.ev_history)}
      <h2>📦 Versandschübe <small>Kartenwert = gezählter Wert × 1,1 (Steuer)</small></h2>
      ${b.shipments.length ? `<div class="rows">${b.shipments.map((s) => `
        <div class="batch ${s.kind === "hits" ? "has-hit" : ""}">
          <div class="line"><span class="muted">${time(s.t)}</span>
            <span><b>+${num(s.cards)}</b> ${s.cards === 1 ? "Karte" : "Karten"} · <b>${num(s.value)}</b> Coins${s.players ? ` · +${num(s.players)} Spieler` : ""}</span></div>
          ${s.explain.length ? `<div class="explain">${s.explain.map((l) =>
            `<div><span class="ico">${esc(l.icon)}</span>${esc(l.text)}</div>`).join("")}</div>` : ""}
        </div>`).join("")}</div>`
        : `<div class="rows"><div class="line muted">Noch keine Versandschübe aufgezeichnet</div></div>`}
    </section>

    <div class="buybar" ${b.archived ? "hidden" : ""}>
      <div class="big">${coins(b.price)}</div>
      <div class="remaining"><b>${num(b.remaining)}</b> / ${num(b.total)}
        <div class="bar"><span style="width:${left}%"></span></div></div>
      ${openLink(b)}
    </div>`;
  view.querySelectorAll(".dtab").forEach((el) => el.addEventListener("click", () => {
    state.detailTab[b.id] = el.dataset.tab;
    view.querySelectorAll(".dtab").forEach((t) => t.classList.toggle("on", t === el));
    view.querySelectorAll(".pane").forEach((p) => { p.hidden = p.dataset.pane !== el.dataset.tab; });
  }));
  renderCards(b);
  wireWatchButton(String(b.id)).catch(() => {});
  me().then(() => renderCards(b)).catch(() => {});
}

// --- Push ---
const isStandalone = () => window.navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;

// --- Anleitung je Gerät: App installieren, Pushes, Lesezeichen zum Übertragen ---
const GUIDE_DEVICES = { ios_safari: "iPhone/iPad · Safari", ios_chrome: "iPhone/iPad · Chrome",
                        android: "Android · Chrome", pc: "PC/Mac · Chrome oder Edge" };
function guessDevice() {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua)) return /CriOS/.test(ua) ? "ios_chrome" : "ios_safari";
  if (/Android/.test(ua)) return "android";
  return "pc";
}
const GUIDES = {
  ios_safari: {
    install: ["Tailscale-App installieren, mit eurem Konto anmelden, VPN einschalten.",
              "Diese Seite in Safari öffnen → Teilen-Symbol (□↑) → „Zum Home-Bildschirm“.",
              "Ab jetzt die App über das Symbol auf dem Home-Bildschirm öffnen."],
    push: "Nur in der installierten App (ab iOS 16.4): hier unten „Pushes einschalten“ und erlauben.",
    bookmark: ["Discord verknüpfen (oben), dann unter „📥 Eigene GTCHA-Daten“ „Lesezeichen … kopieren“ antippen.",
               "In Safari irgendeine Seite als Lesezeichen sichern: Teilen → „Lesezeichen hinzufügen“, Name „An GTCHA Tracker“.",
               "Lesezeichen öffnen (Buch-Symbol) → „Bearbeiten“ → das neue Lesezeichen → Adresse löschen, kopierten Code einfügen.",
               "Übertragen: In Safari eingeloggt gtchaxonline.com öffnen, Adressleiste antippen, „An GTCHA Tracker“ eintippen und den Lesezeichen-Vorschlag wählen."],
  },
  ios_chrome: {
    install: ["Tailscale-App installieren, mit eurem Konto anmelden, VPN einschalten.",
              "Diese Seite in Chrome öffnen → Teilen-Symbol (□↑, rechts in der Adressleiste) → „Zum Home-Bildschirm“ (ab iOS 16.4).",
              "Ab jetzt die App über das Symbol auf dem Home-Bildschirm öffnen."],
    push: "Nur in der installierten App (ab iOS 16.4): hier unten „Pushes einschalten“ und erlauben.",
    bookmark: ["Discord verknüpfen (oben), dann unter „📥 Eigene GTCHA-Daten“ „Lesezeichen … kopieren“ antippen.",
               "In Chrome irgendeine Seite öffnen → „⋯“ → „Zu Lesezeichen hinzufügen“.",
               "„⋯“ → „Lesezeichen“ → das neue Lesezeichen lange drücken → „Lesezeichen bearbeiten“: Name „An GTCHA Tracker“, bei URL den kopierten Code einfügen.",
               "Übertragen: In Chrome eingeloggt gtchaxonline.com öffnen, Adressleiste antippen, „An GTCHA Tracker“ eintippen und den Vorschlag mit dem Stern wählen.",
               "Unter „🔗 GTCHA-Seite öffnen in“ unten „Chrome“ wählen, damit „Öffnen ↗“ in Chrome landet, wo du eingeloggt bist."],
  },
  android: {
    install: ["Tailscale-App aus dem Play Store installieren, mit eurem Konto anmelden, verbinden.",
              "Diese Seite in Chrome öffnen → „⋮“ → „App installieren“ (oder „Zum Startbildschirm hinzufügen“).",
              "Ab jetzt die App über das Symbol auf dem Startbildschirm öffnen."],
    push: "In Chrome und in der installierten App: hier unten „Pushes einschalten“ und erlauben.",
    bookmark: ["Discord verknüpfen (oben), dann unter „📥 Eigene GTCHA-Daten“ „Lesezeichen … kopieren“ antippen.",
               "In Chrome irgendeine Seite öffnen → „⋮“ → Stern (☆) antippen → „Bearbeiten“.",
               "Name „An GTCHA Tracker“, bei URL alles löschen und den kopierten Code einfügen, speichern.",
               "Übertragen: In Chrome eingeloggt gtchaxonline.com öffnen, Adressleiste antippen, „An GTCHA Tracker“ eintippen und den Vorschlag mit dem Stern wählen (nicht die Google-Suche)."],
  },
  pc: {
    install: ["Tailscale für Windows/Mac installieren (tailscale.com/download), mit eurem Konto anmelden.",
              "Diese Seite in Chrome oder Edge öffnen. Installieren (optional): Symbol „App installieren“ rechts in der Adressleiste, oder Chrome „⋮“ → „Streamen, speichern und teilen“ → „Seite als App installieren“ / Edge „…“ → „Apps“ → „Diese Website als App installieren“.",
              "Geht auch ohne Installation einfach im Browser-Tab (auch in Firefox, dort ohne Installation)."],
    push: "In Chrome/Edge: hier unten „Pushes einschalten“ und erlauben. Pushes kommen, solange der Browser läuft.",
    bookmark: ["Discord verknüpfen (oben), dann unter „📥 Eigene GTCHA-Daten“ „Lesezeichen … kopieren“ anklicken.",
               "Lesezeichenleiste einblenden (Strg+Umschalt+B, Mac: ⌘+Umschalt+B) → Rechtsklick auf die Leiste → „Seite hinzufügen“ / „Favorit hinzufügen“.",
               "Name „An GTCHA Tracker“, bei URL den kopierten Code einfügen, speichern.",
               "Übertragen: Eingeloggt gtchaxonline.com öffnen und auf „An GTCHA Tracker“ in der Leiste klicken."],
  },
};
function guideHtml(device) {
  const g = GUIDES[device] || GUIDES.pc;
  const list = (items) => `<ol class="guide">${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ol>`;
  return `<div class="hint"><b>App installieren</b></div>${list(g.install)}
    <div class="hint"><b>Push-Benachrichtigungen</b></div><div class="hint">${esc(g.push)}</div>
    <div class="hint" style="margin-top:8px"><b>Lesezeichen „An GTCHA Tracker“ (eigene Daten übertragen)</b></div>${list(g.bookmark)}
    <div class="hint">Discord-Verknüpfung gilt je Gerät: auf jedem Gerät einmal „Discord verknüpfen“. Der Lesezeichen-Code
      enthält deinen persönlichen Schlüssel – nicht weitergeben.</div>`;
}

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
  new: ["🆕 Neuer Banner", ""],
  value: ["💰 Lohnt sich", "über 100 %"],
  hit: ["🎯 Hit raus", ""],
  packs: ["📉 Pack-Bewegung", "gesammelt, max. 1× pro Minute"],
  ship: ["📦 Versand", ""],
  low: ["⚡ Endspurt", ""],
  end: ["🏁 Beendet", ""],
};
// Vorgaben wie auf dem Server (DEFAULTS in webapp/push.py)
const EVENT_DEFAULTS = { new: true, value: true, hit: true, packs: false, ship: false, low: true, end: false };
const WATCH_LABELS = {
  hit: "🎯 Hit raus", packs: "📉 Packs weniger", ship: "📦 Versand",
  ev: "💰 Über 100 %", low: "⚡ Endspurt", end: "🏁 Beendet",
};
// Vorauswahl beim Beobachten: alles außer "Packs weniger" (das kann sehr oft kommen)
const WATCH_DEFAULT = ["hit", "ship", "ev", "low", "end"];

async function pushState() {
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const sub = supported ? await currentSubscription() : null;
  const prefs = sub ? (await api("/api/push/prefs", post({ endpoint: sub.endpoint }))).prefs : {};
  prefs.watch = prefs.watch || {};
  return { supported, sub, prefs };
}

// Mit Geräteschlüssel: der Server weiß dann, zu welcher Discord-Verknüpfung das Push-Abo gehört
const authPost = (body) => ({ ...post(body), headers: { "Content-Type": "application/json", "X-Device-Token": deviceToken() } });
async function savePrefs(sub, prefs) {
  await api("/api/push/subscribe", authPost({ subscription: sub.toJSON(), prefs }));
}

function watchCard(id, kinds, banner) {
  const title = banner ? `${num(banner.price)} Coins · ${esc(banner.category || "")}` : "nicht mehr online";
  return `<div class="watch" data-watch="${id}">
    <div class="watch-head"><a href="#/banner/${id}"><b>ID ${id}</b> <span class="muted">${title}</span></a>
      <button class="icon-btn" data-remove="${id}" aria-label="Nicht mehr beobachten">✕</button></div>
    <div class="chipset">${Object.entries(WATCH_LABELS).map(([k, label]) =>
      `<button class="pick ${kinds.includes(k) ? "on" : ""}" data-kind="${k}">${label}</button>`).join("")}</div>
  </div>`;
}

async function showSettings() {
  const { supported, sub, prefs } = await pushState();
  const { banners } = sub ? await api("/api/banners").catch(() => ({ banners: [] })) : { banners: [] };
  const byId = Object.fromEntries(banners.map((b) => [b.id, b]));
  const watched = Object.keys(prefs.watch);
  const options = banners.filter((b) => !prefs.watch[b.id]).sort((a, b) => b.id - a.id);
  const user = await me();
  const [medals, hist, acc, devices] = await Promise.all([
    user ? authApi("/api/me/medals").then((r) => r.medals).catch(() => []) : [],
    user ? authApi("/api/me/history").catch(() => null) : null,
    api("/api/accuracy").catch(() => null),
    user ? authApi("/api/me/devices").then((r) => r.devices).catch(() => []) : []]);
  const admin = user?.admin ? await authApi("/api/admin/settings").catch(() => null) : null;
  applyProfile(hist?.profile);
  const prof = hist?.profile;
  view.innerHTML = `
    <div class="section-title">👤 ${user ? esc(user.name) : "Ich"}</div>
    ${linkPanel(user, devices)}
    ${user ? `<h2>🏅 Meine gemeldeten Hits <small>${medals.length} aktiv</small></h2>
      <div class="rows">${medals.length ? medals.map((m) => `
        <a class="line claim-row" href="#/banner/${m.banner_id}">
          <span class="claim-thumb">${img(m.image, m.name, false, 320)}</span>
          <span class="claim-text"><b>${m.tier}</b> ${esc(m.name)}<br>
            <span class="claim-value">${m.value != null ? num(m.value) + " Coins" : ""}</span>
            <span class="muted"> · ${esc(m.title || "Banner " + m.banner_id)}${m.t ? " · " + time(m.t) : ""}</span></span>
          <span class="muted">›</span></a>`).join("")
        : `<div class="line muted">Noch nichts gemeldet – auf einer Banner-Seite unter „Karten“ eine Karte antippen.</div>`}</div>` : ""}
    ${user ? historySection(hist) : ""}
    ${accuracySection(acc)}
    <h2>🎖️ Mein Mitgliedsrang</h2>
    <div class="panel">
      <div class="add-watch">
        <select id="my-rank" aria-label="Mitgliedsrang"><option value="">Nicht angegeben</option>
          ${RANKS.map(([k, l]) => `<option value="${k}" ${k === myRank() ? "selected" : ""}>${l}</option>`).join("")}</select>
        <input id="my-charge" class="code-input plain" inputmode="numeric" placeholder="Aufladung" value="${myCharge() || ""}">
      </div>
      <div class="hint">Rang · diesen Monat gekaufte Coins${prof && prof.updated_at && (prof.rank || prof.charge != null)
        ? ` · 🔄 automatisch (${esc(prof.updated_at.slice(8, 10) + "." + prof.updated_at.slice(5, 7) + ".")})` : ""}</div>
    </div>
    <h2>📖 Anleitung</h2>
    <div class="panel">
      <select id="guide-device" aria-label="Gerät">${Object.entries(GUIDE_DEVICES).map(([k, label]) =>
        `<option value="${k}" ${k === load("guideDevice", guessDevice()) ? "selected" : ""}>${label}</option>`).join("")}</select>
      <div id="guide">${guideHtml(load("guideDevice", guessDevice()))}</div>
    </div>
    <h2>🔔 Push-Benachrichtigungen</h2>
    ${!isStandalone() && isIOS() ? `<div class="notice" style="margin-bottom:12px">Auf dem iPhone gehen Pushes nur in der installierten App (siehe Anleitung oben).</div>` : ""}
    <div class="panel">
      <div class="hint"><b>Für alle Banner</b></div>
      ${Object.entries(EVENT_LABELS).map(([k, [label, hint]]) => `
        <label class="toggle"><span>${label}${hint ? `<br><span class="hint">${hint}</span>` : ""}</span>
        <input type="checkbox" data-event="${k}" ${(prefs[k] ?? EVENT_DEFAULTS[k]) ? "checked" : ""}></label>`).join("")}
      ${!supported ? `<div class="hint">Hier nicht verfügbar.</div>`
        : sub ? `<button class="btn" id="test">Test-Push senden</button>
                 <button class="btn" id="off">Pushes ausschalten</button>`
              : `<button class="btn primary" id="on">Pushes einschalten</button>`}
      <div class="hint" id="msg"></div>
    </div>
    ${sub ? `<h2>👀 Banner beobachten <small>${watched.length} beobachtet</small></h2>
      <div class="panel">
        <div class="add-watch">
          <select id="watch-add" aria-label="Banner wählen">
            <option value="">Banner wählen …</option>
            ${options.map((b) => `<option value="${b.id}">${b.id} · ${num(b.price)} Coins · ${esc(b.category || "")}</option>`).join("")}
          </select>
          <button class="btn primary" id="watch-add-btn">Hinzufügen</button>
        </div>
        ${watched.length ? watched.sort((a, b) => b - a).map((id) => watchCard(id, prefs.watch[id], byId[id])).join("")
          : `<div class="hint">Noch keiner.</div>`}
      </div>` : ""}
    ${user ? `<h2>📥 Eigene GTCHA-Daten</h2>
      <div class="panel">
        <div class="hint">Überträgt deine GTCHA-Verlaufsseiten – ohne Passwort.</div>
        ${syncLine(user)}
        <div class="gt-links">
          <a class="btn primary" href="${esc(buyHref("https://gtchaxonline.com/pending-detail"))}" target="_blank" rel="noopener">📥 GTCHA öffnen</a>
        </div>
        <div class="hint">Öffnet GTCHA – dort das Lesezeichen „An GTCHA Tracker“ aufrufen (Einrichtung siehe 📖 Anleitung oben).</div>
        <button class="btn primary" id="bm-sync">Lesezeichen „Alles übertragen“ kopieren</button>
        <div class="hint">Auf irgendeiner gtchaxonline-Seite antippen: lädt deine Verlaufsseiten (Gacha, Versand,
          Münzen, Käufe, Tickets, Ausgaben in ¥) samt allen Seitenzahlen und überträgt sie. Von der Kontoseite nur
          Zeilen mit Beträgen, Rang und Datum – Name und Adresse nicht. Ab dem zweiten Mal nur Neues: es hört auf
          zu blättern, sobald es bekannte Einträge sieht (alle 30 Tage einmal komplett).</div>
        <button class="btn" id="bm-full">Lesezeichen „Komplett übertragen“ kopieren</button>
        <div class="hint">Nur nötig, wenn im Verlauf eine Lücke gemeldet wird – überträgt wieder alle Seiten.</div>
        <div class="hint">Einrichten (einmalig): siehe 📖 Anleitung oben – für Safari, Chrome (iPhone/Android) und PC.
          Der Code enthält deinen persönlichen Schlüssel – nicht weitergeben.</div>
        <div class="hint" id="bm-msg"></div>
      </div>` : ""}
    ${admin ? adminSection(admin) : ""}
    <h2>🔗 GTCHA-Seite öffnen in</h2>
    <div class="panel">
      <select id="link-mode" aria-label="GTCHA-Seite öffnen in">${Object.entries(LINK_MODES).map(([k, label]) =>
        `<option value="${k}" ${k === load("linkMode", "safari") ? "selected" : ""}>${label}</option>`).join("")}</select>
      <div class="hint">Für „Öffnen ↗“.</div>
    </div>
    <h2>ℹ️ Über diese App</h2>
    <div class="panel"><div class="hint">Inoffiziell, kein Angebot von GTCHA · Version ${APP_VERSION}</div></div>
    ${admin ? `<h2>⏱ Geschwindigkeit <small>Admin</small></h2>
    <div class="panel"><button class="btn" id="speed">Geschwindigkeit testen</button>
      <div class="hint" id="speed-out"></div></div>` : ""}`;
  view.querySelector("#guide-device")?.addEventListener("change", (e) => {
    save("guideDevice", e.target.value);
    view.querySelector("#guide").innerHTML = guideHtml(e.target.value);
  });
  view.querySelectorAll("#bm-sync, #bm-full").forEach((btn) => btn.addEventListener("click", async () => {
    const code = bookmarkletSync(deviceToken(), btn.id === "bm-full");
    const msg = view.querySelector("#bm-msg");
    try { await navigator.clipboard.writeText(code); msg.textContent = "Kopiert ✓"; haptic(); }
    catch (e) { msg.innerHTML = `<textarea class="bm-code" readonly>${esc(code)}</textarea>`; msg.querySelector("textarea").select(); }
  }));
  makeCollapsible(view);
  view.querySelector("#link-mode")?.addEventListener("change", (e) => save("linkMode", e.target.value));
  view.querySelector("#admin-save")?.addEventListener("click", async () => {
    const msg = view.querySelector("#admin-msg");
    try {
      const res = await authApi("/api/admin/settings", { mode: view.querySelector("#admin-mode").value,
        delay_minutes: Number(view.querySelector("#admin-delay").value) || 0 });
      msg.textContent = "Gespeichert ✓";
      haptic();
    } catch (e) { msg.textContent = "Nicht gespeichert: " + e.message; }
  });
  view.querySelector("#speed")?.addEventListener("click", () => speedTest(view.querySelector("#speed-out")));
  view.querySelector("#my-rank")?.addEventListener("change", (e) => save("myRank", e.target.value));
  view.querySelector("#my-charge")?.addEventListener("change", (e) => save("myCharge", String(Number(e.target.value.replace(/\D/g, "")) || 0)));
  wireLinkPanel();
  const msg = view.querySelector("#msg");
  const readToggles = () => view.querySelectorAll("[data-event]").forEach((i) => { prefs[i.dataset.event] = i.checked; });
  view.querySelector("#on")?.addEventListener("click", async () => {
    try {
      if ((await Notification.requestPermission()) !== "granted") { msg.textContent = "Benachrichtigungen wurden nicht erlaubt."; return; }
      const { key } = await api("/api/push/key");
      const reg = await navigator.serviceWorker.ready;
      const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
      readToggles();
      await savePrefs(s, prefs);
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
    readToggles();
    if (sub) await savePrefs(sub, prefs);
  }));
  view.querySelector("#watch-add-btn")?.addEventListener("click", async () => {
    const id = view.querySelector("#watch-add").value;
    if (!id) return;
    prefs.watch[id] = [...WATCH_DEFAULT];
    await savePrefs(sub, prefs);
    showSettings();
  });
  view.querySelectorAll("[data-remove]").forEach((el) => el.addEventListener("click", async () => {
    delete prefs.watch[el.dataset.remove];
    await savePrefs(sub, prefs);
    showSettings();
  }));
  view.querySelectorAll(".watch .pick").forEach((el) => el.addEventListener("click", async () => {
    const id = el.closest("[data-watch]").dataset.watch;
    const kinds = new Set(prefs.watch[id]);
    kinds.has(el.dataset.kind) ? kinds.delete(el.dataset.kind) : kinds.add(el.dataset.kind);
    prefs.watch[id] = Object.keys(WATCH_LABELS).filter((k) => kinds.has(k));
    el.classList.toggle("on");
    await savePrefs(sub, prefs);
  }));
}

// Knopf auf der Banner-Seite: beobachten an/aus (Ereignisse im Push-Tab anpassbar)
async function wireWatchButton(id) {
  const btn = view.querySelector("#watch-btn");
  if (!btn) return;
  const { sub, prefs } = await pushState();
  const paint = () => {
    const on = !!prefs.watch[id];
    btn.textContent = on ? "🔔 Beobachtet ✓" : "🔔 Beobachten";
    btn.classList.toggle("on", on);
  };
  paint();
  btn.addEventListener("click", async () => {
    if (!sub) { location.hash = "#/settings"; return; }
    if (prefs.watch[id]) delete prefs.watch[id]; else prefs.watch[id] = [...WATCH_DEFAULT];
    haptic();
    await savePrefs(sub, prefs);
    paint();
  });
}

// --- Discord-Verknüpfung und Medaillen ---
const deviceToken = () => load("deviceToken", "");
async function me() {
  if (!deviceToken()) return null;
  if (state.me !== undefined) return state.me;
  const res = await fetch("/api/me", { headers: { "X-Device-Token": deviceToken() }, cache: "no-store" });
  state.me = res.ok ? await res.json() : null;
  if (res.status === 401) save("deviceToken", "");
  return state.me;
}
async function authApi(path, body) {
  const res = await fetch(path, { method: body ? "POST" : "GET", cache: "no-store",
    headers: { "Content-Type": "application/json", "X-Device-Token": deviceToken() },
    body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(async () => ({ error: await res.text().catch(() => "") }));
  if (!res.ok) throw new Error(data.error || data.text || `Fehler ${res.status}`);
  return data;
}

// Kurze Rückfrage (Ja/Nein) als eigenes Fenster
function ask(title, html, buttons = ["Nein", "Ja"]) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "modal-wrap";
    wrap.innerHTML = `<div class="modal" role="dialog" aria-modal="true"><div class="modal-title">${esc(title)}</div>
      <div class="modal-text">${html}</div><div class="modal-buttons">${buttons.map((label, i) =>
        `<button class="btn ${i === buttons.length - 1 ? "primary" : ""}" data-i="${i}">${esc(label)}</button>`).join("")}</div></div>`;
    document.body.appendChild(wrap);
    wrap.addEventListener("click", (e) => {
      const i = e.target.dataset?.i;
      if (i === undefined && e.target !== wrap) return;
      wrap.remove();
      resolve(i !== undefined ? buttons[Number(i)] : null);
    });
  });
}

// Tippen auf eine Karte: melden bzw. zurücknehmen (mit Rückfrage), Bot postet es im Discord-Thread
async function claimFlow(b, card) {
  const units = unitsOf(b, card);
  // in jedem Dialog: Karte auf die Wunschliste setzen / davon entfernen
  const star = isWish(card.id) ? "☆ Nicht mehr merken" : "⭐ Merken";
  const wishPicked = async (choice) => {
    if (choice !== star) return false;
    await toggleWish({ id: card.id, name: card.name, image: card.image });
    await ask(card.name, isWish(card.id) ? "⭐ Gemerkt" : "Entfernt", ["OK"]);
    return true;
  };
  if (!units.length) {
    await wishPicked(await ask(card.name, `${num(card.value)} Coins – unter dem Packpreis, kann nicht gemeldet werden.`, [star, "OK"]));
    return;
  }
  const user = await me();
  if (!user) {
    const choice = await ask("Discord verknüpfen", "Zum Melden einmal mit Discord verknüpfen (Reiter „Ich“).", [star, "Später", "Verknüpfen"]);
    if (await wishPicked(choice)) return;
    if (choice === "Verknüpfen") location.hash = "#/settings";
    return;
  }
  const free = units.find((u) => !u.medal_user && u.state !== "pulled");
  const own = units.find((u) => u.medal_user === String(user.user_id));
  // Admin: ohne Person abhaken ("Hit ist raus") oder das wieder aufheben - löst keine Versand-Frist aus
  const marked = units.find((u) => u.origin?.via === "admin");
  if (user.admin && (free || marked)) {
    const opts = [star, "Abbrechen", ...(free ? ["Selbst melden", `🛠️ ${free.tier} abhaken (ohne Person)`] : []),
                  ...(marked ? [`🛠️ ${marked.tier} Abhaken aufheben`] : [])];
    const choice = await ask("Admin", `${esc(card.name)} · ${num(card.value)} Coins<br><br>
        Abhaken ohne Person, wenn ihr denkt, der Hit ist raus – darunter steht dann „durch Admin abgehakt“.`, opts);
    if (await wishPicked(choice) || !choice || choice === "Abbrechen") return;
    if (choice.startsWith("🛠️")) {
      const payload = choice.includes("aufheben") ? { pack_id: b.id, tier: marked.tier, action: "remove" }
        : { pack_id: b.id, tier: free.tier, action: "mark" };
      haptic();
      const { id } = await authApi("/api/admin/medal", payload);
      return waitForBot(id);
    }
  }
  let unit, action;
  if (free && own) {
    // eigenes Exemplar gemeldet und noch eins frei: zurücknehmen oder ein weiteres melden
    const choice = await ask("Was möchtest du tun?", `${esc(card.name)} · ${num(card.value)} Coins<br><br>
        <b>${own.tier}</b> hast du gemeldet. <b>${free.tier}</b> ist noch frei.`,
      [star, "Abbrechen", `${own.tier} zurücknehmen`, `${free.tier} melden`]);
    if (await wishPicked(choice)) return;
    if (choice === `${own.tier} zurücknehmen`) { unit = own; action = "unclaim"; }
    else if (choice === `${free.tier} melden`) { unit = free; action = "claim"; }
    else return;
  } else if (free) {
    const choice = await ask("Hit beanspruchen?", `<b>${free.tier}</b> · ${esc(card.name)}<br>${num(card.value)} Coins<br><br>
        Als von dir gezogen melden? Der Bot postet das im Discord-Thread.`, [star, "Nein", "Ja"]);
    if (await wishPicked(choice) || choice !== "Ja") return;
    unit = free; action = "claim";
  } else if (own) {
    const choice = await ask("Zurücknehmen?", `<b>${own.tier}</b> · ${esc(card.name)} ist als von dir gemeldet.<br><br>
        Meldung zurücknehmen?`, [star, "Nein", "Ja"]);
    if (await wishPicked(choice) || choice !== "Ja") return;
    unit = own; action = "unclaim";
  } else {
    await wishPicked(await ask(card.name, "Alle Exemplare dieser Karte sind schon gemeldet oder als gezogen erkannt.", [star, "OK"]));
    return;
  }
  haptic();
  const { id } = await authApi("/api/medal", { pack_id: b.id, tier: unit.tier, action });
  return waitForBot(id);
}

async function waitForBot(id) {
  for (let i = 0; i < 20; i++) {        // der Bot arbeitet Meldungen alle 5 s ab
    await new Promise((r) => setTimeout(r, 1500));
    const r = await authApi(`/api/medal/${id}`);
    if (r.status === "ok") { haptic(); await state.current(); return; }
    if (r.status === "rejected") { await ask("Nicht übernommen", esc(r.reason || ""), ["OK"]); await state.current(); return; }
  }
  await ask("Gesendet", "Der Bot hat noch nicht geantwortet – die Meldung wird gleich verarbeitet.", ["OK"]);
}

function linkPanel(user, devices = []) {
  const when = (t) => (t ? `${t.slice(8, 10)}.${t.slice(5, 7)}. ${t.slice(11, 16)}` : "–");
  return `<h2>🔗 Discord verknüpfen</h2><div class="panel">${user
    ? `<div>Verknüpft als <b>${esc(user.name)}</b>${user.admin ? " · Admin" : ""}</div>
       <button class="btn" id="copy-id">Discord-ID kopieren</button>
       <button class="btn" id="unlink">Verknüpfung trennen</button>
       ${devices.length ? `<div class="hint" style="margin-top:12px"><b>Geräte (${devices.length})</b></div>
       <div class="rows" style="margin:6px 0 0">${devices.map((d) => `
         <div class="line"><span>${esc(d.agent || "Gerät")}${d.current ? " · <b>dieses Gerät</b>" : ""}<br>
           <span class="muted">zuletzt ${when(d.last_seen)}</span></span>
           ${d.current ? "" : `<button class="icon-btn" data-device="${esc(d.id)}" aria-label="Gerät abmelden">✕</button>`}</div>`).join("")}</div>
` : ""}`
    : `<div class="hint">In Discord <b>/app-verknüpfen</b>, Code hier eingeben.</div>
       <div class="add-watch"><input id="link-code" class="code-input" maxlength="8" autocomplete="one-time-code"
         autocapitalize="characters" placeholder="Code, z. B. K7M2QX">
       <button class="btn primary" id="link-btn">Verknüpfen</button></div>
       <div class="hint" id="link-msg"></div>`}</div>`;
}

function wireLinkPanel() {
  view.querySelector("#copy-id")?.addEventListener("click", async (e) => {
    try { await navigator.clipboard.writeText(String(state.me?.user_id || "")); e.target.textContent = "Kopiert ✓"; haptic(); }
    catch (err) { e.target.textContent = "Kopieren nicht möglich"; }
  });
  view.querySelectorAll("[data-device]").forEach((btn) => btn.addEventListener("click", async () => {
    if (await ask("Gerät abmelden?", "Dieses Gerät ist danach nicht mehr mit Discord verknüpft.") !== "Ja") return;
    await authApi("/api/me/devices/remove", { id: btn.dataset.device }).catch(() => {});
    haptic();
    showSettings();
  }));
  view.querySelector("#unlink")?.addEventListener("click", async () => {
    await authApi("/api/unlink", {}).catch(() => {});
    save("deviceToken", ""); state.me = undefined;
    showSettings();
  });
  view.querySelector("#link-btn")?.addEventListener("click", async () => {
    const msg = view.querySelector("#link-msg");
    try {
      const res = await fetch("/api/link", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: view.querySelector("#link-code").value }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Code ungültig");
      save("deviceToken", data.token); state.me = { user_id: data.user_id, name: data.name };
      showSettings();
    } catch (e) { msg.textContent = e.message; }
  });
}

// Messung: wo geht die Zeit verloren - iPhone-Speicher, VPS über Tailscale oder Server?
async function speedTest(out) {
  out.textContent = "Messe …";
  const ms = (t) => `${Math.round(performance.now() - t)} ms`;
  const lines = [];
  // Echte Bilder seit dem App-Start (vor dem Test): wann angefordert, wie lange unterwegs
  const real = performance.getEntriesByType("resource").filter((e) => e.name.includes("/img?") && !e.name.includes("nosw"));
  const data = performance.getEntriesByType("resource").find((e) => e.name.includes("/api/banners"));
  if (real.length) {
    const r = (n) => Math.round(n);
    const starts = real.map((e) => e.startTime), ends = real.map((e) => e.responseEnd), durs = real.map((e) => e.duration);
    lines.push(`— Seit App-Start: ${real.length} Bilder —`);
    if (data) lines.push(`Daten fertig nach: ${r(data.responseEnd)} ms`);
    lines.push(`Erstes Bild angefordert nach: ${r(Math.min(...starts))} ms`);
    lines.push(`Erstes Bild fertig nach: ${r(Math.min(...ends))} ms`);
    lines.push(`Letztes Bild fertig nach: ${r(Math.max(...ends))} ms`);
    lines.push(`Dauer pro Bild: Ø ${r(durs.reduce((a, b) => a + b, 0) / durs.length)} ms, max ${r(Math.max(...durs))} ms`);
    lines.push("— Test jetzt —");
  }
  let t = performance.now();
  const { banners } = await api("/api/banners");
  lines.push(`Daten (Bannerliste): ${ms(t)}`);
  const urls = banners.map((b) => b.image).filter((u) => safeUrl(u)).slice(0, 10).map(imgSrc).filter((u) => u.startsWith("/img?"));
  const sw = navigator.serviceWorker?.controller;
  lines.push(`Hintergrund-Helfer aktiv: ${sw ? "ja" : "nein"}`);
  if (sw) {
    const stats = await new Promise((resolve) => {
      const ch = new MessageChannel();
      ch.port1.onmessage = (e) => resolve(e.data);
      sw.postMessage({ type: "stats" }, [ch.port2]);
      setTimeout(() => resolve(null), 3000);
    });
    lines.push(`Bilder im iPhone-Speicher: ${stats ? stats.images : "?"}${stats?.queue ? ` (noch ${stats.queue} in der Warteschlange)` : ""}`);
  }
  t = performance.now();
  await Promise.all(urls.map((u) => fetch(u).then((r) => r.blob())));
  lines.push(`10 Bilder über die App (Speicher): ${ms(t)}`);
  t = performance.now();
  await Promise.all(urls.map((u) => fetch(u + "&nosw=1&r=" + Math.random(), { cache: "no-store" }).then((r) => r.blob())));
  lines.push(`10 Bilder frisch vom VPS: ${ms(t)}`);
  t = performance.now();
  await fetch(urls[0] + "&nosw=1&r=" + Math.random(), { cache: "no-store" }).then((r) => r.blob());
  lines.push(`1 Bild frisch vom VPS: ${ms(t)}`);
  out.innerHTML = lines.map(esc).join("<br>");
}

// Abschnitte im Reiter "Ich" auf-/zuklappbar; offen/zu wird pro Abschnitt gemerkt
function makeCollapsible(root) {
  const open = JSON.parse(load("openSections", '["Discord verknüpfen","Meine gemeldeten Hits","Mein Verlauf"]'));
  [...root.querySelectorAll(":scope > h2")].forEach((h2) => {
    const title = h2.textContent.replace(/^\W+/u, "").replace(/\s+\d.*$/, "").trim();
    const det = document.createElement("details");
    det.className = "section";
    det.open = open.some((t) => title.startsWith(t));
    const sum = document.createElement("summary");
    sum.innerHTML = h2.innerHTML;
    det.appendChild(sum);
    let el = h2.nextElementSibling;
    while (el && el.tagName !== "H2") {
      const next = el.nextElementSibling;
      det.appendChild(el);
      el = next;
    }
    h2.replaceWith(det);
    det.addEventListener("toggle", () => {
      const now = new Set(JSON.parse(load("openSections", "[]")));
      det.open ? now.add(title) : now.delete(title);
      save("openSections", JSON.stringify([...now]));
    });
  });
}

// Haptisches Feedback: iOS 18 vibriert beim Umschalten eines Schalters (input switch), sonst navigator.vibrate
const hapticBox = (() => {
  const label = document.createElement("label");
  label.style.cssText = "position:fixed;left:-99px;width:1px;height:1px;overflow:hidden";
  label.innerHTML = '<input type="checkbox" switch>';
  document.body.appendChild(label);
  return label;
})();
function haptic() {
  try { hapticBox.click(); } catch (e) { /* egal */ }
  navigator.vibrate?.(12);
}

// "Alles übertragen": lädt die eigenen Verlaufsseiten nacheinander unsichtbar (iframe, gleiche Seite),
// blättert jeweils durch alle Seitenzahlen und schickt alles per Formular an den VPS. change-member
// liefert die Ausgaben in Yen; von dort gehen nur Zeilen mit Beträgen/Rang/Datum mit (keine Name/Adresse).
const SYNC_PAGES = ["undecided-detail", "pending-detail", "shipped-detail", "downloaded-detail",
                    "buy-point-history", "purchase-history", "ticket-history", "change-member"];
// Nur Neues: Das Lesezeichen merkt sich (im Speicher von gtchaxonline.com auf diesem Gerät) den neuesten
// Eintrag je Verlaufsbereich und hört auf zu blättern, sobald eine Seite ihn enthält. Der VPS hängt dann
// nur das Neue an. Alle 30 Tage (oder mit "komplett") wird wieder alles übertragen.
const SYNC_VERSION = 3;    // mit BOOKMARKLET_VERSION in webapp/server.py erhöhen, wenn sich das Lesezeichen ändert
const SYNC_PARALLEL = 4;   // Bereiche gleichzeitig (je ein unsichtbares Fenster)
const SYNC_INCREMENTAL = ["buy-point-history", "shipped-detail", "ticket-history", "purchase-history", "downloaded-detail"];
function bookmarkletSync(token, full = false) {
  const src = `(async()=>{
if(!/gtchaxonline\\.com$/.test(location.hostname)){alert('Bitte auf gtchaxonline.com öffnen');return}
const P=${JSON.stringify(SYNC_PAGES)};const INC=${JSON.stringify(SYNC_INCREMENTAL)};const LS='gtchaTracker.marks';
let M={};try{M=JSON.parse(localStorage.getItem(LS)||'{}')}catch(e){}
const FULL=${full ? "true" : "false"}||!M.at||Date.now()-M.at>30*864e5;
const lines=t=>t.split('\\n').map(l=>l.trim()).filter(Boolean);
const sig=t=>{const L=lines(t);const i=L.findIndex(l=>/\\d{2,4}\\/\\d{2}\\/\\d{2}/.test(l));return i<0?'':L.slice(i,i+4).join('\\n')};
const NM={at:FULL?Date.now():M.at};const PAR=${SYNC_PARALLEL};const T0=Date.now();
const box=document.createElement('div');box.style.cssText='position:fixed;z-index:2147483647;left:10px;right:10px;top:10px;padding:12px;background:#1f3a6e;color:#fff;font:15px sans-serif;border-radius:10px';document.body.appendChild(box);
const say=t=>{box.textContent='GTCHA Tracker: '+t};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const keep=/[¥￥円]|coin|münz|ausgaben|rang|rank|20\\d{2}[\\/.-]\\d{1,2}[\\/.-]\\d{1,2}|^[\\d.,]{1,9}$/i;
const grab=d=>({text:(d.location.pathname.includes('change-member')?d.body.innerText.split('\\n').filter(l=>keep.test(l)).join('\\n'):d.body.innerText).slice(0,40000),images:[...d.querySelectorAll('img')].map(i=>i.getAttribute('src')).filter(s=>s&&s.includes('/card/')).slice(0,400)});
const settle=async fr=>{let last='',same=0;for(let i=0;i<40;i++){await sleep(400);const d=fr.contentDocument;const t=d&&d.body?d.body.innerText:'';if(t&&t===last){if(++same>=6)return}else same=0;last=t}};
const isNum=x=>x.children.length===0&&/^\\d+$/.test(x.textContent.trim());
const out=[];let done=0;
const area=async(p,i)=>{
const fr=document.createElement('iframe');fr.style.cssText='position:fixed;left:-3000px;top:0;width:420px;height:900px';document.body.appendChild(fr);
try{await new Promise(r=>{fr.onload=r;fr.src='/'+p});await settle(fr);
const d=fr.contentDocument;if(!d||!d.body){out[i]={path:p,error:'kein Zugriff'};return}
const pages=[grab(d)];const mark=!FULL&&INC.includes(p)&&M[p];let partial=false;
const known=g=>mark&&lines(g.text).join('\\n').includes(mark);
if(INC.includes(p))NM[p]=sig(pages[0].text)||M[p]||'';
if(known(pages[0]))partial=true;
for(let n=2;n<=40&&!partial;n++){
const btn=[...d.querySelectorAll('a,button,li,span,div')].find(e=>isNum(e)&&e.textContent.trim()===String(n)&&[...((e.parentElement&&e.parentElement.parentElement)||e).querySelectorAll('*')].filter(isNum).length>=3);
if(!btn)break;const before=d.body.innerText;btn.click();
let g=null;for(let w=0;w<30;w++){await sleep(500);if(d.body.innerText!==before){await settle(fr);g=grab(d);break}}
if(!g)break;pages.push(g);if(known(g))partial=true}
out[i]={path:p,pages,partial}}finally{fr.remove();say((++done)+' von '+P.length+' Bereichen geladen …')}};
say('lade '+P.length+' Bereiche gleichzeitig …');
let next=0;await Promise.all(Array.from({length:PAR},async()=>{while(next<P.length){const i=next++;await area(P[i],i)}}));
say('sende …');try{localStorage.setItem(LS,JSON.stringify(NM))}catch(e){}
const f=document.createElement('form');f.method='POST';f.action=${JSON.stringify(location.origin)}+'/api/import-form';
const i=document.createElement('input');i.type='hidden';i.name='d';i.value=JSON.stringify({t:${JSON.stringify(token)},v:${SYNC_VERSION},at:new Date().toISOString(),ms:Date.now()-T0,pages:out.filter(Boolean)});
f.appendChild(i);document.body.appendChild(f);f.submit()})()`;
  return "javascript:" + src.replace(/\n/g, "");
}

// --- Mein Verlauf (aus "Alles übertragen") ---
const signed = (n) => `<span class="ev ${n > 0 ? "good" : n < 0 ? "bad" : ""}">${n > 0 ? "+" : ""}${num(n)}</span>`;
const CLAIM_STATUS = { ok: "✅ gemeldet", rejected: "❌ abgelehnt", pending: "⏳ wird gemeldet" };

function cardLine(c) {
  return `<div class="line claim-row"><span class="claim-thumb">${img(c.image, c.name, false, 320)}</span>
    <span class="claim-text"><b>${esc(c.name)}</b>${c.rarity ? ` <span class="muted">${esc(c.rarity)}</span>` : ""}<br>
    <span class="muted">${esc(c.number || "")}${c.date ? " · " + esc(c.date.split("-").reverse().join(".")) : ""}</span>
    ${c.value ? `<br><span class="claim-value">${num(c.value)} Coins</span>` : ""}</span></div>`;
}

// Bilanz je Tag (Balken grün/rot) und aufsummiert (Linie), die letzten 30 Tage mit Daten
function balanceChart(daysNewestFirst) {
  const days = daysNewestFirst.slice(0, 30).reverse();
  let sum = 0;
  const cum = days.map((d) => (sum += d.balance));
  const W = 600, H = 220, P = 30;
  const max = Math.max(0, ...days.map((d) => d.balance), ...cum), min = Math.min(0, ...days.map((d) => d.balance), ...cum);
  const y = (v) => P / 2 + (1 - (v - min) / Math.max(1, max - min)) * (H - P * 1.5);
  const step = (W - 2 * P) / days.length, bw = Math.max(2, step * 0.6);
  const x = (i) => P + step * i + step / 2;
  const bars = days.map((d, i) => `<rect x="${(x(i) - bw / 2).toFixed(1)}" width="${bw.toFixed(1)}"
    y="${Math.min(y(d.balance), y(0)).toFixed(1)}" height="${Math.max(1, Math.abs(y(d.balance) - y(0))).toFixed(1)}"
    fill="${d.balance >= 0 ? "#1f9d55" : "#e5383b"}" opacity=".75"/>`).join("");
  const line = cum.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const label = (d) => d.day.slice(8, 10) + "." + d.day.slice(5, 7) + ".";
  return `<div class="chart" style="margin:0 10px"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Bilanz pro Tag">
    <line x1="${P}" x2="${W - P}" y1="${y(0)}" y2="${y(0)}" stroke="#98a1bd" stroke-width="1" vector-effect="non-scaling-stroke"/>
    ${bars}
    <polyline points="${line}" fill="none" stroke="#3d7fd9" stroke-width="2.5" vector-effect="non-scaling-stroke"/>
    <text class="axis" x="${P}" y="${H - 6}">${label(days[0])}</text>
    <text class="axis" x="${W - P}" y="${H - 6}" text-anchor="end">${label(days[days.length - 1])}</text>
    <text class="axis" x="${W - P}" y="16" text-anchor="end">gesamt ${sum > 0 ? "+" : ""}${num(sum)}</text>
  </svg></div>`;
}

function historySection(h) {
  if (!h || h.empty) {
    return `<h2>📊 Mein Verlauf</h2><div class="panel"><div class="hint">Noch keine Daten.</div></div>`;
  }
  const s = h.summary, t = s.total, m = h.member || {};
  const banners = s.banners.map((b) => `
    <a class="line claim-row" href="#/banner/${b.banner}">
      <span class="claim-thumb">${img(b.image, "", false, 320)}</span>
      <span class="claim-text"><b>${esc(b.title || "Banner " + b.banner)}</b><br>
        <span class="muted">${b.pulls} Züge · ${num(b.spent)} rein · ${num(b.returned)} zurück${b.hits_value
          ? ` · ${b.hits} Hit${b.hits > 1 ? "s" : ""} ${num(b.hits_value)}` : ""}</span>
        ${b.luck_pct != null ? `<br><span class="pill ${b.luck_pct >= 100 ? "good" : ""}">${b.luck_pct >= 100 ? "🍀 Glück" : "🌧 Pech"} ${b.luck_pct} %</span>` : ""}</span>
      <span>${signed(b.balance + (b.hits_value || 0))}</span></a>`).join("");
  const monthName = (m) => new Date(m + "-01T00:00:00").toLocaleDateString("de-DE", { month: "long", year: "numeric" });
  const monthRows = (s.months || []).map((m) => `
    <div class="line"><span>${esc(monthName(m.month))} <span class="muted">· ${m.opens} Öffnungen${m.bought_yen
      ? ` · ${num(m.bought_yen)} ¥ aufgeladen` : ""}</span></span><span>${signed(m.balance)}</span></div>`).join("");
  const days = s.days.slice(0, 14).map((d) => `
    <div class="line"><span>${esc(d.day.split("-").reverse().join("."))} <span class="muted">· ${d.opens} Öffnungen</span></span>
      <span>${signed(d.balance)}</span></div>`).join("");
  const claims = (h.auto_claims || []).map((c) => `
    <a class="line" href="#/banner/${c.pack_id}"><span><b>${esc(c.tier)}</b> ${esc(c.title || "Banner " + c.pack_id)}
      ${c.reason ? `<br><span class="muted">${esc(c.reason)}</span>` : ""}</span>
      <span class="muted">${CLAIM_STATUS[c.status] || esc(c.status || "")}</span></a>`).join("");
  const [d, tm] = (h.saved_at || "").split(" ");
  return `<h2>📊 Mein Verlauf <small>Stand ${esc(d ? d.split("-").reverse().join(".") + " " + (tm || "") : "–")}</small></h2>
    <div class="stats hist-stats">
      ${m.spent_month_yen != null ? stat("Ausgaben diesen Monat", num(m.spent_month_yen) + " ¥", "laut Kontoseite") : ""}
      ${m.coins != null ? stat("Coin-Stand", num(m.coins) + " Coins") : ""}
      ${stat("Bilanz inkl. Hits", signed(t.balance_with_hits ?? t.balance) + " Coins",
        `${num(t.spent)} rein · ${num(t.returned)} umgewandelt${t.hits ? ` · ${num(t.hits_value)} in ${t.hits} Hits` : ""}`)}
      ${t.hits ? stat("Nur Coins", signed(t.balance) + " Coins", "ohne angeforderte/verschickte Karten") : ""}
      ${t.bought ? stat("Coins gekauft", num(t.bought), num(t.bought_yen) + " ¥") : ""}
    </div>
    ${h.gap ? `<div class="notice warn">⚠️ Lücke – einmal „Komplett übertragen“.</div>` : ""}
    <div class="hint pad">Seit ${esc((s.since || "").split(" ")[0].split("-").reverse().join("."))} · ${t.opens} Öffnungen</div>
    ${banners ? `<h3 class="sub-title">Pro Banner</h3><div class="rows">${banners}</div>` : ""}
    ${s.unassigned_opens ? `<div class="hint pad">${s.unassigned_opens} nicht zugeordnet</div>` : ""}
    ${monthRows ? `<h3 class="sub-title">Pro Monat</h3><div class="rows">${monthRows}</div>` : ""}
    ${s.days.length > 1 ? `<h3 class="sub-title">Bilanz pro Tag</h3>
      ${balanceChart(s.days)}` : ""}
    ${days ? `<h3 class="sub-title">Pro Tag</h3><div class="rows">${days}</div>` : ""}
    ${h.pending.length ? `<h3 class="sub-title">Angefordert, noch nicht verschickt (${h.pending.length})</h3>
      <div class="rows">${h.pending.map(cardLine).join("")}</div>` : ""}
    ${claims ? `<h3 class="sub-title">Automatisch gemeldete Medaillen</h3><div class="rows">${claims}</div>` : ""}
    ${h.shipped.length ? `<details class="sub-details"><summary>Verschickt (${h.shipped.length})</summary>
      <div class="rows">${h.shipped.map(cardLine).join("")}</div></details>` : ""}`;
}

// --- Admin: was Discord zu sehen bekommt (App und VPS haben immer alles) ---
function adminSection(a) {
  return `<h2>⚙️ Discord-Ansicht <small>Admin</small></h2>
    <div class="panel">
      <div class="add-watch">
        <select id="admin-mode" aria-label="Modus">
          <option value="slim" ${a.mode === "slim" ? "selected" : ""}>Schlank</option>
          <option value="full" ${a.mode === "full" ? "selected" : ""}>Voll (wie früher)</option>
        </select>
        <input id="admin-delay" class="code-input plain" inputmode="numeric" value="${a.delay_minutes}" aria-label="Verzögerung in Minuten">
      </div>
      <div class="hint">Modus · Verzögerung in Min (0 = sofort)</div>
      <button class="btn primary" id="admin-save">Speichern</button>
      <div class="hint">Admin: ${(a.admins || []).map((x) => esc(x.name || "–")).join(", ") || "–"}</div>
      <div class="hint" id="admin-msg"></div>
    </div>`;
}

// --- Admin: Statistiken aller Nutzer (je Person aufklappbar) ---
const rankLabel = (k) => (RANKS.find(([key]) => key === k) || [null, k || "–"])[1];
async function showUsers() {
  const user = await me();
  if (!user?.admin) {
    view.innerHTML = `<div class="empty">Nur für den Admin.</div>`;
    return;
  }
  const [{ users }, st, ticks] = await Promise.all([authApi("/api/admin/users"),
    authApi("/api/admin/status").catch(() => null), authApi("/api/admin/auto_ticks").catch(() => ({ items: [] }))]);
  const tickRows = (ticks.items || []).map((t, i) => `<div class="line"><span>
      <a href="#/banner/${t.pack_id}"><b>${t.pack_id}</b></a> ${esc(t.tier || "")} · ${esc(t.name || t.key)}
      ${t.value ? `<span class="muted">· ${num(t.value)}</span>` : ""}<br>
      <span class="muted">${esc(t.at ? t.at.slice(8, 10) + "." + t.at.slice(5, 7) + ". " + t.at.slice(11, 16) : "")}
      ${t.rebuild ? "· Neuberechnung" : ""}${t.rejected ? " · ❌ als falsch markiert" : ""}</span></span>
      <button class="seg-btn" data-tick="${i}">${t.rejected ? "↩️ zulassen" : "❌ war falsch"}</button></div>`).join("");
  const ago = (sec) => (sec == null ? "–" : sec < 120 ? `${sec} s` : sec < 7200 ? `${Math.round(sec / 60)} Min` : `${Math.round(sec / 3600)} Std`);
  const ok = (good) => (good ? "🟢" : "🔴");
  const statusHtml = st ? `<div class="rows">
      <div class="line"><span>${ok(st.heartbeat_age != null && st.heartbeat_age < 900)} Bot (letzter Scrape)</span><span>vor ${ago(st.heartbeat_age)}</span></div>
      <div class="line"><span>Letzte Pack-Bewegung</span><span>${esc(st.last_pack_move ? st.last_pack_move.slice(11, 16) : "–")}</span></div>
      <div class="line"><span>${ok(st.data_age != null && st.data_age < 120)} App-Daten</span><span>vor ${ago(st.data_age)}</span></div>
      <div class="line"><span>Aktive Banner</span><span>${num(st.active_banners)}</span></div>
      <div class="line"><span>${ok(!!st.backup_last)} Letztes Backup</span><span>${esc(st.backup_last ? st.backup_last.slice(8, 10) + "." + st.backup_last.slice(5, 7) + ". " + st.backup_last.slice(11, 16) : "–")}</span></div>
      <div class="line"><span>Datenbank / App</span><span>${st.db_mb} / ${st.app_db_mb} MB</span></div>
      <div class="line"><span>Discord</span><span>${st.slim ? `schlank · ${st.delay_minutes} Min` : "voll"} · ${st.outbox ?? "–"} wartend${st.slim && !st.cleanup_done ? " · räumt auf" : ""}</span></div>
      <div class="line"><span>Lernen: Fälle / Versand-Beobachtungen</span><span>${num(st.learn_cases || 0)} / ${num(st.learn_observations || 0)}</span></div>
      <div class="line"><span>Nutzer / gesperrt / Push-Geräte</span><span>${st.users} / ${st.blocked} / ${st.push_devices}</span></div>
    </div>` : `<div class="hint pad">Status nicht verfügbar</div>`;
  view.innerHTML = `<div class="section-title">👥 Nutzer <small class="muted">${users.length}</small></div>
    <details class="user-card"><summary><b>🩺 System-Status</b><span>${st ? ok(st.heartbeat_age != null && st.heartbeat_age < 900) : ""}</span></summary>
      <div class="user-body" style="padding-top:8px">${statusHtml}</div></details>
    <details class="user-card"><summary><b>🤖 Automatisch abgehakt</b><span class="muted">${(ticks.items || []).length} · 14 Tage</span></summary>
      <div class="user-body" style="padding-top:8px"><div class="hint pad">Vom Bot aus Versand/Umwandlung erkannt.
        „war falsch“ nimmt den Haken sofort weg; der Hit wird bei diesem Banner nicht mehr automatisch abgehakt.</div>
        <div class="rows">${tickRows || `<div class="line muted">Nichts in den letzten 14 Tagen</div>`}</div></div></details>
    <details class="user-card"><summary><b>📢 Push an alle</b><span></span></summary>
      <div class="user-body" style="padding:10px 12px">
        <input id="ap-title" class="code-input plain" maxlength="80" placeholder="Titel" style="width:100%;margin-bottom:8px">
        <input id="ap-body" class="code-input plain" maxlength="300" placeholder="Text" style="width:100%;margin-bottom:8px">
        <button class="btn primary" id="ap-send">Senden</button><div class="hint" id="ap-msg"></div>
      </div></details>
    ${users.map((u) => {
      const p = u.profile || {}, t = u.total;
      return `<details class="user-card" data-user="${esc(u.user_id)}">
        <summary><span><b>${esc(u.name || "Unbekannt")}</b>${u.blocked ? " ⛔" : ""}<br>
          <span class="muted">${esc(rankLabel(p.rank))} · ${u.medals} Medaille${u.medals === 1 ? "" : "n"}
            · ${u.saved_at ? "übertragen " + esc(u.saved_at.slice(8, 10) + "." + u.saved_at.slice(5, 7) + ".") : "nie übertragen"}</span></span>
          <span>${t ? signed(t.balance) : `<span class="muted">–</span>`}</span></summary>
        <div class="user-body"><div class="loading">Lädt …</div></div>
      </details>`;
    }).join("") || `<div class="empty">Keine Nutzer.</div>`}`;
  view.querySelectorAll("[data-tick]").forEach((btn) => btn.addEventListener("click", async () => {
    const t = ticks.items[Number(btn.dataset.tick)];
    const undo = t.rejected;
    const choice = await ask(undo ? "Wieder zulassen?" : "Erkennung war falsch?",
      `${t.pack_id} · ${esc(t.tier || "")} ${esc(t.name || "")}<br><br>${undo
        ? "Der Bot darf diesen Hit wieder automatisch abhaken (bei der nächsten Auswertung)."
        : "Der Haken verschwindet sofort, und der Bot hakt diesen Hit bei diesem Banner nicht mehr automatisch ab."}`,
      ["Abbrechen", undo ? "Zulassen" : "War falsch"]);
    if (choice === "Abbrechen" || !choice) return;
    const { id } = await authApi("/api/admin/reject", { pack_id: t.pack_id, key: t.key, undo });
    haptic();
    await waitForBot(id);
    showUsers();
  }));
  view.querySelector("#ap-send").addEventListener("click", async () => {
    const msg = view.querySelector("#ap-msg");
    const title = view.querySelector("#ap-title").value.trim();
    if (!title) { msg.textContent = "Titel fehlt"; return; }
    if (await ask("An alle senden?", esc(title), ["Abbrechen", "Senden"]) !== "Senden") return;
    try {
      const r = await authApi("/api/admin/push", { title, body: view.querySelector("#ap-body").value.trim() });
      msg.textContent = `Gesendet an ${r.sent} Geräte ✓`; haptic();
    } catch (e) { msg.textContent = e.message; }
  });
  const others = users.filter((u) => !u.blocked);
  view.querySelectorAll(".user-card[data-user]").forEach((card) => card.addEventListener("toggle", async () => {
    if (!card.open || card.dataset.loaded) return;
    card.dataset.loaded = "1";
    const body = card.querySelector(".user-body");
    const u = users.find((x) => x.user_id === card.dataset.user);
    try {
      const h = await authApi(`/api/admin/user/${card.dataset.user}`);
      const p = h.profile || {};
      body.innerHTML = `<div class="stats hist-stats" style="margin-top:10px">
          ${stat("Mitgliedsrang", esc(rankLabel(p.rank)))}
          ${stat("Aufgeladen (Monat)", p.charge != null ? num(p.charge) + " Coins" : "–", p.charge_yen != null ? num(p.charge_yen) + " ¥" : "")}
          ${stat("Coin-Stand", h.member?.coins != null ? num(h.member.coins) + " Coins" : "–")}
          ${stat("Geräte", String((h.devices || []).length))}
        </div>
        ${u.user_id !== String(user.user_id) ? `<div style="margin:0 10px 6px"><button class="btn" data-block="${u.blocked ? 0 : 1}">
          ${u.blocked ? "Entsperren" : "⛔ Sperren"}</button></div>` : ""}
        <h3 class="sub-title">🏅 Medaillen (${(h.medals || []).length})</h3>
        <div class="rows">${(h.medals || []).map((m, i) => `
          <div class="line claim-row"><span class="claim-thumb">${img(m.image, m.name, false, 320)}</span>
            <span class="claim-text"><b>${esc(m.tier)}</b> ${esc(m.name)}<br><span class="muted">${esc(m.title || "Banner " + m.banner_id)}</span></span>
            <button class="icon-btn" data-medal="${i}" aria-label="Medaille bearbeiten">✎</button></div>`).join("")
          || `<div class="line muted">Keine</div>`}</div>
        ${h.empty ? `<div class="hint pad">Nichts übertragen.</div>`
          : historySection(h).replace(/<h2>📊 Mein Verlauf/, "<h2>📊 Verlauf")}`;
      body.querySelector("[data-block]")?.addEventListener("click", async (e) => {
        const block = e.target.dataset.block === "1";
        if (block && await ask("Sperren?", `${esc(u.name || "")}: alle Geräte abmelden, Verknüpfen blockieren.`,
          ["Abbrechen", "Sperren"]) !== "Sperren") return;
        await authApi("/api/admin/block", { user_id: u.user_id, blocked: block });
        haptic(); showUsers();
      });
      body.querySelectorAll("[data-medal]").forEach((btn) => btn.addEventListener("click", async () => {
        const m = h.medals[Number(btn.dataset.medal)];
        const targets = others.filter((x) => x.user_id !== u.user_id).slice(0, 8);
        const choice = await ask(`${m.tier} · ${m.name}`, "Korrigieren:",
          ["Abbrechen", "Entfernen", ...targets.map((x) => `→ ${x.name || "Unbekannt"}`)]);
        if (!choice || choice === "Abbrechen") return;
        const payload = { pack_id: m.banner_id, tier: m.tier, action: "remove" };
        if (choice.startsWith("→ ")) {
          payload.action = "assign";
          payload.user_id = targets[["Abbrechen", "Entfernen", ...targets.map((x) => `→ ${x.name || "Unbekannt"}`)].indexOf(choice) - 2].user_id;
        }
        await authApi("/api/admin/medal", payload);
        haptic();
        await ask("Gesendet", "Der Bot übernimmt es in wenigen Sekunden.", ["OK"]);
        showUsers();
      }));
    } catch (e) { body.innerHTML = `<div class="hint pad">Nicht ladbar: ${esc(e.message)}</div>`; }
  }));
}

// --- Treffsicherheit: vorhergesagte Ø Rückgabe gegenüber dem, was tatsächlich raus kam ---
function accuracySection(a) {
  if (!a) return "";
  const head = `<h2>🎯 Treffsicherheit <small>${a.count ? `${a.count} Banner` : "sammelt Daten"}</small></h2>`;
  if (!a.count) {
    return head + `<div class="panel"><div class="hint">Ergebnis ab 30 verkauften Packs je Banner, gemessen 24 Stunden
      später (gezogene Karten zählt die Seite erst, wenn sie verschickt oder umgewandelt sind).</div></div>`;
  }
  const dir = a.bias > 1 ? "eher zu optimistisch" : a.bias < -1 ? "eher zu vorsichtig" : "ohne klare Richtung";
  return head + `<div class="stats hist-stats">
      ${stat("Ø Abweichung", `${a.mean_abs.toLocaleString("de-DE")} %-Punkte`, dir + " · Ergebnis 24 Std. später gemessen")}
    </div>
    <div class="rows">${a.items.map((i) => `
      <a class="line" href="#/banner/${i.id}"><span><b>${esc(i.title || "Banner " + i.id)}</b><br>
        <span class="muted">${num(i.sold)} Packs · vorhergesagt ${pct(i.predicted)} · tatsächlich ${pct(i.realized)}</span></span>
        <span class="ev ${Math.abs(i.diff) <= 5 ? "good" : Math.abs(i.diff) <= 15 ? "ok" : "bad"}">${i.diff > 0 ? "+" : ""}${i.diff.toLocaleString("de-DE")}</span></a>`).join("")}</div>`;
}

// --- Kartensuche und Wunschliste ---
// Wunschliste liegt auf dem Gerät; mit eingeschalteten Pushes zusätzlich beim Server (für die Wunschkarten-Pushes)
const wishList = () => JSON.parse(load("wish", "[]"));
const isWish = (id) => wishList().some((w) => w.id === id);
async function toggleWish(card) {
  const list = wishList();
  const next = isWish(card.id) ? list.filter((w) => w.id !== card.id) : [...list, { id: card.id, name: card.name, image: card.image }];
  save("wish", JSON.stringify(next.slice(-100)));
  haptic();
  try {
    const { sub, prefs } = await pushState();
    if (sub) await savePrefs(sub, { ...prefs, wish: next.map((w) => w.id) });
  } catch (e) { /* ohne Pushes bleibt die Liste nur auf dem Gerät */ }
}

function findCard(c) {
  const banners = c.banners.map((b) => `
    <a class="find-b ${b.out ? "out" : ""}" href="#/banner/${b.id}">
      <span>${b.out ? "✅ raus · " : ""}<b>${esc(b.title)}</b> · ${coins(b.price)}${b.copies > 1 ? ` · ×${b.copies}` : ""}</span>
      <span class="muted">${b.ev_pct != null ? `<span class="ev ${evClass(b.ev_pct)}">${pct(b.ev_pct)}</span> · ` : ""}${num(b.remaining)} Packs</span></a>`).join("");
  return `<div class="find-card">
    <div class="find-art">${img(c.image, c.name, false, 320)}</div>
    <div><div class="find-head"><b>${esc(c.name)}</b>
      <button class="star ${isWish(c.id) ? "on" : ""}" data-wish="${esc(c.id)}">${isWish(c.id) ? "⭐ Gemerkt" : "☆ Merken"}</button></div>
      <div class="muted">${c.value != null ? coins(c.value) : ""}</div></div>
    <div class="find-banners">${banners || `<div class="hint">Gerade in keinem laufenden Banner.</div>`}</div>
  </div>`;
}

async function showSearch() {
  const q = load("searchQ", "");
  view.innerHTML = `<div class="section-title">🔍 Karten suchen</div>
    <form class="search" id="card-search" role="search">
      <input id="card-q" type="search" autocomplete="off" placeholder="Kartenname, z. B. Glurak" value="${esc(q)}">
    </form>
    <div id="find-out" style="margin-top:12px"></div>`;
  const out = view.querySelector("#find-out");
  const input = view.querySelector("#card-q");
  const wire = (cards) => out.querySelectorAll("[data-wish]").forEach((btn) => btn.addEventListener("click", async () => {
    const card = cards.find((c) => c.id === btn.dataset.wish);
    await toggleWish(card);
    btn.classList.toggle("on", isWish(card.id));
    btn.textContent = isWish(card.id) ? "⭐ Gemerkt" : "☆ Merken";
  }));
  const run = async () => {
    const text = input.value.trim();
    save("searchQ", text);
    if (text.length < 2) {
      const wish = wishList();
      if (!wish.length) {
        out.innerHTML = `<div class="panel"><div class="hint">☆ Merken = Push bei neuem Banner oder wenn gezogen.</div></div>`;
        return;
      }
      const res = await api(`/api/cards?ids=${wish.map((w) => w.id).join(",")}`);
      const byId = Object.fromEntries(res.cards.map((c) => [c.id, c]));
      const cards = wish.map((w) => byId[w.id] || { ...w, value: null, banners: [] }).reverse();
      out.innerHTML = `<div class="sub-title">⭐ Meine Wunschliste (${wish.length})</div><div class="find">${cards.map(findCard).join("")}</div>`;
      wire(cards);
      return;
    }
    const res = await api(`/api/cards?q=${encodeURIComponent(text)}`);
    out.innerHTML = res.cards.length ? `<div class="find">${res.cards.map(findCard).join("")}</div>`
      : `<div class="empty">Keine Karte „${esc(text)}“ in den laufenden Bannern.</div>`;
    wire(res.cards);
  };
  let timer;
  const guarded = () => run().catch((e) => { if (e.locked) view.querySelector("#find-out").innerHTML = lockHtml(e.locked); });
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(guarded, 300); });
  view.querySelector("#card-search").addEventListener("submit", (e) => { e.preventDefault(); input.blur(); guarded(); });
  await guarded();
}

// --- Benachrichtigungen: Verlauf der Pushes dieses Geräts (Glocke oben) ---
async function inboxApi(path, body = {}) {
  const sub = await currentSubscription().catch(() => null);
  if (!sub) return null;
  return api(path, authPost({ endpoint: sub.endpoint, ...body }));
}

function setBell(unread) {
  const el = document.getElementById("bell-count");
  if (!el) return;
  el.hidden = !unread;
  el.textContent = unread > 99 ? "99+" : String(unread || "");
  try { unread ? navigator.setAppBadge?.(unread) : navigator.clearAppBadge?.(); } catch (e) { /* nicht unterstützt */ }
}

async function updateBell() {
  const res = await inboxApi("/api/push/inbox", { limit: 1 }).catch(() => null);
  setBell(res ? res.unread : 0);
}

async function markRead(ids) {
  await inboxApi("/api/push/read", ids ? { ids } : { all: true }).catch(() => null);
  await updateBell();
}

function inboxTime(t) {
  const d = new Date(t.replace(" ", "T") + "Z");   // Server: UTC
  return { day: d.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" }),
           time: d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) };
}

function inboxItem(n) {
  const href = n.banner_id ? `#/banner/${n.banner_id}?n=${n.id}` : "#/inbox";
  return `<a class="inbox-item ${n.read ? "" : "unread"}" href="${href}" data-id="${n.id}">
    <span class="t">${inboxTime(n.t).time}</span><b>${esc(n.title)}</b>
    ${n.body ? `<div class="b">${esc(n.body)}</div>` : ""}</a>`;
}

function inboxList(items) {
  let html = "", day = "";
  for (const n of items) {
    const d = inboxTime(n.t).day;
    if (d !== day) { html += `${day ? "</div>" : ""}<div class="inbox-day">${esc(d)}</div><div class="rows">`; day = d; }
    html += inboxItem(n);
  }
  return html + (day ? "</div>" : "");
}

async function showInbox() {
  const sub = await currentSubscription().catch(() => null);
  if (!sub) {
    view.innerHTML = `<div class="section-title">🔔 Benachrichtigungen</div>
      <div class="panel"><div class="hint">Pushes sind aus.</div><a class="btn primary" href="#/settings">Einschalten</a></div>`;
    return;
  }
  let items = [], more = false;
  const draw = (unread) => {
    view.innerHTML = `<div class="section-title">🔔 Benachrichtigungen</div>
      <div class="inbox-actions"><span class="hint">${unread ? `${unread} ungelesen` : "Alles gelesen"} · letzte 30 Tage</span>
        ${unread ? `<button class="btn" id="read-all">✓ Alle gelesen</button>` : ""}</div>
      ${items.length ? inboxList(items) : `<div class="empty">Keine Benachrichtigungen.</div>`}
      ${more ? `<div style="margin:12px 10px"><button class="btn" id="more">Ältere laden</button></div>` : ""}`;
    view.querySelector("#read-all")?.addEventListener("click", async () => {
      haptic();
      await markRead(null);
      items = items.map((n) => ({ ...n, read: true }));
      draw(0);
    });
    view.querySelector("#more")?.addEventListener("click", async () => {
      const res = await inboxApi("/api/push/inbox", { limit: 30, before: items[items.length - 1].id });
      items = items.concat(res.items);
      more = res.more;
      draw(res.unread);
    });
    view.querySelectorAll(".inbox-item.unread").forEach((a) => a.addEventListener("click", () => {
      markRead([Number(a.dataset.id)]);
    }));
  };
  const res = await inboxApi("/api/push/inbox", { limit: 30 });
  items = res.items;
  more = res.more;
  setBell(res.unread);
  draw(res.unread);
}

function post(body) {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

// --- Navigation ---
async function route() {
  clearInterval(state.timer);
  // offene Dialoge gehören zur alten Seite
  document.querySelectorAll(".modal-wrap").forEach((m) => m.remove());
  const hash = location.hash || "#/";
  const tab = hash.startsWith("#/hot") ? "hot" : hash.startsWith("#/settings") ? "settings"
    : hash.startsWith("#/search") ? "search" : hash.startsWith("#/users") ? "users" : "list";
  const readId = hash.match(/[?&]n=(\d+)/);
  if (readId) markRead([Number(readId[1])]);
  document.getElementById("bell")?.classList.toggle("active", hash.startsWith("#/inbox"));
  if (hash.startsWith("#/inbox")) {
    document.querySelectorAll(".tabbar a").forEach((a) => a.classList.remove("active"));
    state.render = null;
    await showInbox().catch((e) => { view.innerHTML = `<div class="empty">Nicht erreichbar (${esc(e.message)})</div>`; });
    window.scrollTo({ top: 0 });
    return;
  }
  const banner = hash.match(/^#\/banner\/(\d+)/);
  document.querySelectorAll(".tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab && !banner));
  const render = banner ? () => showBanner(banner[1]) : tab === "hot" ? showHot : tab === "settings" ? showSettings
    : tab === "search" ? showSearch : tab === "users" ? showUsers : showList;
  state.render = tab === "settings" || tab === "search" || tab === "users" ? null : render;
  state.current = render;
  if (!view.innerHTML || banner) view.innerHTML = `<div class="loading">Lädt …</div>`;
  try {
    await render();
    window.scrollTo({ top: 0 });
  } catch (e) {
    view.innerHTML = e.locked ? lockHtml(e.locked) : e.message === "404"
      ? `<div class="empty">Banner nicht gefunden.<br><a class="back" href="#/">‹ Zur Übersicht</a></div>`
      : `<div class="empty">Daten nicht erreichbar (${esc(e.message)})</div>`;
  }
  if (!["settings", "search", "users"].includes(tab)) state.timer = setInterval(() => render().catch(() => {}), REFRESH_MS);
}

// Bilder, die nicht laden, ausblenden statt Alt-Text zu zeigen
// Bild vom VPS nicht da -> direkt von GTCHA laden; klappt auch das nicht, ausblenden statt Alt-Text
document.addEventListener("error", (e) => {
  const el = e.target;
  if (el.tagName !== "IMG") return;
  if (el.dataset.orig && el.getAttribute("src") !== el.dataset.orig) {
    el.removeAttribute("referrerpolicy");
    el.src = el.dataset.orig;
  } else {
    el.style.visibility = "hidden";
  }
}, true);
// Tiefe jedes Verlaufseintrags innerhalb der App (bleibt auch bei Zurück-/Vor-Gesten des Browsers richtig)
let navDepth = history.state?.depth ?? 0;
if (history.state?.depth == null) history.replaceState({ ...(history.state || {}), depth: 0 }, "");
window.addEventListener("hashchange", () => {
  if (history.state?.depth == null) {   // neuer Eintrag (Link angetippt)
    navDepth += 1;
    history.replaceState({ ...(history.state || {}), depth: navDepth }, "");
  } else {
    navDepth = history.state.depth;      // Zurück/Vor
  }
  route();
});

// Zurück: eine Ebene zurück in der App; ohne Vorgeschichte (direkt geöffnet, Push, Neustart) zur Übersicht
function goBack() {
  if (navDepth > 0) history.back();
  else location.hash = "#/";
}
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-back]");
  if (!el) return;
  e.preventDefault();
  goBack();
});
// ↻ in der Kopfzeile: aktuelle Seite neu laden, Scroll-Position bleibt
const refreshBtn = document.getElementById("refresh");
refreshBtn?.addEventListener("click", async () => {
  if (!state.current || refreshBtn.classList.contains("spinning")) return;
  const y = window.scrollY;
  refreshBtn.classList.add("spinning");
  try {
    await state.current();
    window.scrollTo({ top: y });
  } catch (e) {
    view.insertAdjacentHTML("afterbegin", `<div class="notice">Aktualisieren fehlgeschlagen (${esc(e.message)})</div>`);
  } finally {
    setTimeout(() => refreshBtn.classList.remove("spinning"), 300);
  }
});

// Wischgesten: vom linken Rand nach rechts = zurück (Banner-Seite), oben nach unten ziehen = aktualisieren
const ptr = document.createElement("div");
ptr.className = "ptr";
ptr.textContent = "↻";
document.body.appendChild(ptr);
let touch = null;
document.addEventListener("touchstart", (e) => {
  if (e.touches.length !== 1 || document.querySelector(".modal-wrap")) return;
  const t = e.touches[0];
  touch = { x: t.clientX, y: t.clientY, edge: t.clientX < 24, top: window.scrollY <= 0, dx: 0, dy: 0 };
}, { passive: true });
document.addEventListener("touchmove", (e) => {
  if (!touch) return;
  const t = e.touches[0];
  touch.dx = t.clientX - touch.x;
  touch.dy = t.clientY - touch.y;
  if (touch.edge && touch.dx > 0 && Math.abs(touch.dx) > Math.abs(touch.dy)) {
    view.style.transform = `translateX(${Math.min(touch.dx, 160)}px)`;
  } else if (touch.top && touch.dy > 0 && touch.dy > Math.abs(touch.dx)) {
    const d = Math.min(touch.dy, 120);
    ptr.style.transform = `translate(-50%, ${d - 50}px) rotate(${d * 3}deg)`;
    ptr.classList.toggle("ready", d >= 80);
  }
}, { passive: true });
document.addEventListener("touchend", async () => {
  if (!touch) return;
  const { edge, top, dx, dy } = touch;
  touch = null;
  view.style.transform = "";
  if (edge && dx > 90 && location.hash.startsWith("#/banner/")) {
    haptic();
    goBack();
  } else if (top && dy >= 80 && state.current) {
    haptic();
    ptr.classList.add("spinning");
    try { await state.current(); } catch (e) { /* Hinweis kommt über die Seite */ }
    ptr.classList.remove("spinning");
  }
  ptr.style.transform = "";
  ptr.classList.remove("ready");
});

// Zurück aus dem Hintergrund: sofort frische Daten statt bis zu 30 s alte
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (state.render) state.render().catch(() => {});
  updateBell();
});
// Push kommt an, während die App offen ist -> Glocke sofort hochzählen
navigator.serviceWorker?.addEventListener("message", (e) => {
  if (e.data?.type !== "push") return;
  setBell(e.data.unread || 0);
  if (location.hash.startsWith("#/inbox")) showInbox().catch(() => {});
});
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
route();
updateBell();
// Reiter "Nutzer" nur für Admins
me().then((u) => { const t = document.getElementById("users-tab"); if (t) t.hidden = !u?.admin; }).catch(() => {});
setInterval(() => { if (document.visibilityState === "visible") updateBell(); }, 60000);
// Rang/Aufladung beim Start aus dem letzten Übertragen holen (Banner-Liste danach neu zeichnen)
if (deviceToken()) authApi("/api/me/profile").then((p) => { if (applyProfile(p) && state.render) state.render().catch(() => {}); }).catch(() => {});
