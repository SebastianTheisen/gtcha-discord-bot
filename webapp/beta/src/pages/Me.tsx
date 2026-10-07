import { useState } from "preact/hooks";
import { setToken } from "../api";
import { connectLive, loadBanners, loadMe, me, theme } from "../store";

const THEMES: [typeof theme.value, string][] = [["auto", "Automatisch"], ["dark", "Dunkel"], ["light", "Hell"]];

export function Me() {
  const user = me.value;
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");

  async function link() {
    setMsg("");
    const res = await fetch("api/link", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return setMsg(data.error || "Code ungültig");
    setToken(data.token);
    await loadMe();
    await loadBanners();
    connectLive();
  }

  return (
    <div style={{ display: "grid", gap: "12px", maxWidth: "640px", margin: "0 auto" }}>
      <div class="panel">
        {user ? (
          <>
            <div class="muted small">Verknüpft mit Discord</div>
            <div style={{ fontWeight: 800, fontSize: "20px" }}>{user.name || user.user_id}</div>
            {user.admin && <span class="pill ok">Admin</span>}
            {user.sync && !user.sync.exempt && (
              <div class="small" style={{ marginTop: "8px" }}>
                {user.sync.ok ? `✅ Übertragen – noch ${Math.floor(user.sync.days_left || 0)} Tage bis zur nächsten Pflicht`
                  : "⏳ Bitte GTCHA-Daten übertragen"}
              </div>
            )}
          </>
        ) : (
          <>
            <h3 style={{ marginTop: 0 }}>Gerät verknüpfen</h3>
            <p class="muted small">In Discord <b>/tracker-verknüpfen</b> eingeben und den Code hier eintragen. Ist das Gerät schon in der
              normalen App verknüpft, gilt das automatisch auch für die Beta.</p>
            <input class="field" maxLength={8} autoComplete="one-time-code" placeholder="Code" value={code}
              onInput={(e) => setCode((e.target as HTMLInputElement).value)} />
            <button class="btn primary block" style={{ marginTop: "10px" }} onClick={link}>Verknüpfen</button>
            {msg && <div class="notice" style={{ marginTop: "10px" }}>{msg}</div>}
          </>
        )}
      </div>

      <div class="panel">
        <h3 style={{ marginTop: 0 }}>Darstellung</h3>
        <div class="chips">
          {THEMES.map(([k, l]) => <button class={`chip ${theme.value === k ? "active" : ""}`} onClick={() => (theme.value = k)}>{l}</button>)}
        </div>
      </div>

      <div class="panel">
        <h3 style={{ marginTop: 0 }}>🧪 Beta</h3>
        <p class="muted small" style={{ marginTop: 0 }}>Neue Oberfläche zum Testen – gleiche Daten wie die normale App. Medaillen,
          Lesezeichen, Pushes, Suche, Bilanz und Admin gibt es vorerst nur in der normalen App.</p>
        <a class="btn block" href="../">Zur normalen App</a>
      </div>
    </div>
  );
}
