import type { LockInfo } from "../api";
import { ddmm, ddmmhhmm } from "../format";
import { buyHref } from "../local";

// Je GTCHA-Konto: wann zuletzt übertragen (für Personen mit mehreren Konten)
function accountLines(s: LockInfo): string[] {
  const lines = (s.accounts || []).map((a) => `${a.ok ? "✅" : "⏳"} ${a.label}${a.gtcha_id ? ` (ID ${a.gtcha_id})` : ""}: ${ddmmhhmm(a.last_sync)}${a.ok
    ? ` · noch ${Math.floor(a.days_left)} Tag${Math.floor(a.days_left) === 1 ? "" : "e"}` : " · abgelaufen"}`);
  for (let i = 0; i < (s.missing || 0); i++) lines.push(`❌ Konto ${(s.accounts || []).length + i + 1}: noch nie übertragen`);
  return lines;
}

// Sperrbildschirm wie in der Live-App: erst verknüpfen bzw. alle 7 Tage die GTCHA-Daten übertragen
export function Lock({ info }: { info: LockInfo }) {
  const last = info.last_sync ? ddmm(info.last_sync) : null;
  const many = (info.accounts || []).length > 1 || (info.expected || 1) > 1;
  const why = info.reason === "link"
    ? "Bitte dieses Gerät zuerst mit Discord verknüpfen (unten „Ich“ → Code aus /tracker-verknüpfen)."
    : info.reason === "accounts"
      ? `Du spielst mit ${info.expected} GTCHA-Konten – ${info.missing === 1 ? "eins wurde" : `${info.missing} wurden`} noch nie übertragen. Im anderen Browser (eingeloggt mit dem anderen Konto) das Lesezeichen aufrufen.`
      : `Alle ${info.days || 7} Tage müssen deine GTCHA-Daten einmal übertragen werden${last ? ` – zuletzt am ${last}` : " – bisher noch nie"}. Danach ist sofort alles wieder offen.`;
  return (
    <div class="lock">
      <div class="icon">🔒</div>
      <h2 style={{ margin: 0 }}>{info.reason === "link" ? "Gerät nicht verknüpft" : "Bitte Daten übertragen"}</h2>
      <p class="muted" style={{ margin: 0, maxWidth: "440px" }}>{why}</p>
      {many && <div class="panel small" style={{ textAlign: "left" }}>{accountLines(info).map((l) => <div>{l}</div>)}</div>}
      {info.reason === "link"
        ? <a class="btn primary" href="#/me">Jetzt verknüpfen</a>
        : <a class="btn primary" href={buyHref("https://gtchaxonline.com/pending-detail")} target="_blank" rel="noopener">📥 GTCHA öffnen</a>}
      {info.reason !== "link" && <p class="muted small">Dort das Lesezeichen „An GTCHA Tracker“ aufrufen. Noch nicht eingerichtet? <a href="#/me">Ich → 📖 Anleitung</a></p>}
    </div>
  );
}
