import type { LockInfo } from "../api";

const shortDate = (s?: string) => (s ? `${s.slice(8, 10)}.${s.slice(5, 7)}.` : null);

// Sperrbildschirm wie in der Live-App: erst verknüpfen bzw. alle 7 Tage die GTCHA-Daten übertragen
export function Lock({ info }: { info: LockInfo }) {
  const last = shortDate(info.last_sync);
  const why = info.reason === "link"
    ? "Bitte dieses Gerät zuerst mit Discord verknüpfen."
    : info.reason === "accounts"
      ? `Du spielst mit ${info.expected} GTCHA-Konten – ${info.missing} davon wurde(n) noch nie übertragen.`
      : `Alle ${info.days || 7} Tage müssen deine GTCHA-Daten einmal übertragen werden${last ? ` – zuletzt am ${last}` : ""}.`;
  return (
    <div class="lock">
      <div class="icon">🔒</div>
      <h2 style={{ margin: 0 }}>{info.reason === "link" ? "Gerät nicht verknüpft" : "Bitte Daten übertragen"}</h2>
      <p class="muted" style={{ margin: 0, maxWidth: "420px" }}>{why}</p>
      {info.reason === "link"
        ? <a class="btn primary" href="#/me">Jetzt verknüpfen</a>
        : <a class="btn primary" href="https://gtchaxonline.com/pending-detail" target="_blank" rel="noopener">📥 GTCHA öffnen</a>}
      {info.reason !== "link" && <p class="muted small">Dort das Lesezeichen „An GTCHA Tracker“ aufrufen.</p>}
    </div>
  );
}
