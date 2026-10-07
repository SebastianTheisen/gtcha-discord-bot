import "@fontsource-variable/inter";
import "./styles.css";
import { render } from "preact";
import { useEffect } from "preact/hooks";
import { Lock } from "./components/Lock";
import { Detail } from "./pages/Detail";
import { List } from "./pages/List";
import { Me } from "./pages/Me";
import { Top } from "./pages/Top";
import { connectLive, live, loadBanners, loadError, loadMe, locked, route, theme, updated } from "./store";
import { relative } from "./format";

function applyTheme() {
  const t = theme.value;
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}
theme.subscribe(applyTheme);

const NAV: [string, string, string][] = [["/", "🎴", "Banner"], ["/top", "🔥", "Top 10"], ["/me", "👤", "Ich"]];

function Page() {
  const r = route.value;
  const m = r.match(/^\/banner\/(\d+)/);
  if (m) return <Detail id={m[1]} />;
  if (r === "/me") return <Me />;
  if (locked.value) return <Lock info={locked.value} />;
  if (r === "/top") return <Top />;
  return <List />;
}

function App() {
  useEffect(() => {
    loadMe();
    loadBanners().then(connectLive);
  }, []);
  useEffect(() => {
    window.scrollTo({ top: 0 });   // Seitenwechsel: nach oben
  }, [route.value]);
  const state = live.value;
  return (
    <div class="app">
      <header class="header">
        <a class="brand" href="#/">GTCHA Tracker <span class="beta">BETA</span></a>
        <span class="spacer" />
        <span class={`live ${state === "live" ? "on" : state === "offline" ? "off" : ""}`}
          title={updated.value ? `Stand ${relative(updated.value)}` : ""}>
          <i />{locked.value ? "Gesperrt" : state === "live" ? "Live" : state === "offline" ? "Offline" : "Verbinde…"}
        </span>
      </header>
      <main class="main">
        {loadError.value && <div class="notice" style={{ marginBottom: "12px" }}>{loadError.value} – neuer Versuch läuft</div>}
        <div class="page" key={route.value}><Page /></div>
      </main>
      <nav class="nav">
        {NAV.map(([path, icon, label]) => (
          <a href={`#${path}`} class={route.value === path || (path === "/" && route.value.startsWith("/banner")) ? "active" : ""}>
            <span class="ico">{icon}</span>{label}
          </a>
        ))}
      </nav>
    </div>
  );
}

render(<App />, document.getElementById("app")!);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => { /* ohne Offline-Modus weiter */ });
}
