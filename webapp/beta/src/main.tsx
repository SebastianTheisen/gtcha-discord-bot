import "@fontsource-variable/inter";
import "./styles.css";
import { render } from "preact";
import { useEffect } from "preact/hooks";
import { Lock } from "./components/Lock";
import { relative } from "./format";
import { Detail } from "./pages/Detail";
import { Inbox } from "./pages/Inbox";
import { List } from "./pages/List";
import { Me } from "./pages/Me";
import { Search } from "./pages/Search";
import { Top } from "./pages/Top";
import { Users } from "./pages/Users";
import { markRead, setBell, unread, updateBell } from "./push";
import { connectLive, live, loadBanners, loadError, loadMe, locked, me, refreshAll, route, theme, updated } from "./store";
import { closeDialogs, DialogHost, haptic } from "./ui";

function applyTheme() {
  const t = theme.value;
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}
theme.subscribe(applyTheme);

// Push angetippt: "#/banner/123?n=5" - Benachrichtigung 5 als gelesen markieren, Pfad ohne Zusatz
const path = () => route.value.split("?")[0];
route.subscribe((r) => {
  const n = r.match(/[?&]n=(\d+)/);
  if (n) markRead([Number(n[1])]);
  closeDialogs();
});

function Page() {
  const r = path();
  const m = r.match(/^\/banner\/(\d+)/);
  if (m) return <Detail id={m[1]} />;
  if (r === "/me" || r === "/settings") return <Me />;
  if (r === "/inbox") return <Inbox />;
  if (r === "/users") return <Users />;
  if (locked.value) return <Lock info={locked.value} />;
  if (r === "/top" || r === "/hot") return <Top />;
  if (r === "/search") return <Search />;
  return <List />;
}

const NAV: [string, string, string][] = [["/", "🎴", "Banner"], ["/top", "🔥", "Top 10"], ["/search", "🔍", "Suche"],
  ["/users", "👥", "Nutzer"], ["/me", "👤", "Ich"]];

function App() {
  useEffect(() => {
    loadMe();
    loadBanners().then(connectLive);
    updateBell();
    const t = setInterval(() => { if (!document.hidden) updateBell(); }, 60000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    window.scrollTo({ top: 0 });   // Seitenwechsel: nach oben
  }, [path()]);
  const state = live.value;
  const r = path();
  return (
    <div class="app">
      <header class="header">
        <a class="brand" href="#/">GTCHA Tracker <span class="beta">BETA</span></a>
        <span class="spacer" />
        <span class={`live ${state === "live" && !locked.value ? "on" : state === "offline" || locked.value ? "off" : ""}`}
          title={updated.value ? `Stand ${relative(updated.value)}` : ""}>
          <i />{locked.value ? "Gesperrt" : state === "live" ? "Live" : state === "offline" ? "Offline" : "Verbinde…"}
        </span>
        <a class={`icon-btn bell ${r === "/inbox" ? "active" : ""}`} href="#/inbox" aria-label="Benachrichtigungen">
          🔔{unread.value ? <span class="badge">{unread.value > 99 ? "99+" : unread.value}</span> : null}
        </a>
      </header>
      <main class="main">
        {loadError.value && <div class="notice" style={{ marginBottom: "12px" }}>{loadError.value} – neuer Versuch läuft</div>}
        <div class="page" key={r}><Page /></div>
      </main>
      <nav class="nav">
        {NAV.filter(([p]) => p !== "/users" || me.value?.admin).map(([p, icon, label]) => (
          <a href={`#${p}`} class={r === p || (p === "/" && r.startsWith("/banner")) ? "active" : ""}>
            <span class="ico">{icon}</span>{label}
          </a>
        ))}
      </nav>
      <DialogHost />
    </div>
  );
}

render(<App />, document.getElementById("app")!);

// Wischgesten: vom linken Rand nach rechts = zurück (Banner-Seite), oben nach unten ziehen = aktualisieren
const ptr = document.createElement("div");
ptr.className = "ptr";
ptr.textContent = "↻";
document.body.appendChild(ptr);
let touch: { x: number; y: number; edge: boolean; top: boolean; dx: number; dy: number } | null = null;
document.addEventListener("touchstart", (e) => {
  if (e.touches.length !== 1 || document.querySelector(".sheet")) return;
  const t = e.touches[0];
  touch = { x: t.clientX, y: t.clientY, edge: t.clientX < 24, top: window.scrollY <= 0, dx: 0, dy: 0 };
}, { passive: true });
document.addEventListener("touchmove", (e) => {
  if (!touch) return;
  const t = e.touches[0];
  touch.dx = t.clientX - touch.x;
  touch.dy = t.clientY - touch.y;
  const page = document.querySelector<HTMLElement>(".page");
  if (touch.edge && touch.dx > 0 && Math.abs(touch.dx) > Math.abs(touch.dy)) {
    if (page) page.style.transform = `translateX(${Math.min(touch.dx, 160)}px)`;
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
  const page = document.querySelector<HTMLElement>(".page");
  if (page) page.style.transform = "";
  if (edge && dx > 90 && location.hash.startsWith("#/banner/")) {
    haptic();
    if (history.length > 1) history.back();
    else location.hash = "#/";
  } else if (top && dy >= 80) {
    haptic();
    ptr.classList.add("spinning");
    await refreshAll();
    updateBell();
    ptr.classList.remove("spinning");
  }
  ptr.style.transform = "";
  ptr.classList.remove("ready");
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => { /* ohne Offline-Modus weiter */ });
  // Push kommt an, während die App offen ist -> Glocke sofort hochzählen
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data?.type === "push") setBell(e.data.unread || 0);
  });
}
