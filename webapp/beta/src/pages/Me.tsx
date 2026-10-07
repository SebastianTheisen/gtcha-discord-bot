import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import { api, authApi, getToken, setToken } from "../api";
import { bookmarkletSync } from "../bookmarklet";
import { AccuracySection, HistorySection, type History } from "../components/HistorySection";
import { Img } from "../components/Img";
import { dateTime, ddmm, ddmmhhmm, num } from "../format";
import { GUIDE_DEVICES, GUIDES, guessDevice } from "../guides";
import { applyProfile, buyHref, isIOS, isStandalone, LINK_MODES, linkMode, load, myCharge, myRank, RANKS, save } from "../local";
import {
  disablePush, enablePush, EVENT_DEFAULTS, EVENT_LABELS, pushState, savePrefs, testPush, WATCH_DEFAULT, WATCH_LABELS, type Prefs,
} from "../push";
import { banners, connectLive, loadBanners, loadMe, me, theme } from "../store";
import { ask, haptic, showToast } from "../ui";

const THEMES: [typeof theme.value, string][] = [["auto", "Automatisch"], ["dark", "Dunkel"], ["light", "Hell"]];
/* eslint-disable @typescript-eslint/no-explicit-any */

// Abschnitt zum Auf-/Zuklappen; offen/zu wird je Abschnitt gemerkt
function Section({ id, title, extra, children, open: def = false }: { id: string; title: string; extra?: ComponentChildren; children: ComponentChildren; open?: boolean }) {
  const key = `beta.open.${id}`;
  const [open, setOpen] = useState(load(key, def ? "1" : "0") === "1");
  return (
    <details class="section" open={open} onToggle={(e) => { const o = (e.currentTarget as HTMLDetailsElement).open; setOpen(o); save(key, o ? "1" : "0"); }}>
      <summary><span>{title}</span>{extra ? <span class="muted small">{extra}</span> : null}</summary>
      <div class="section-body">{children}</div>
    </details>
  );
}

export function Me() {
  const user = me.value;
  const [medals, setMedals] = useState<any[]>([]);
  const [hist, setHist] = useState<History>(null);
  const [acc, setAcc] = useState<any>(null);
  const [devices, setDevices] = useState<any[]>([]);
  const [admin, setAdmin] = useState<any>(null);
  const [push, setPush] = useState<{ supported: boolean; sub: PushSubscription | null; prefs: Prefs } | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    api("api/accuracy").then(setAcc).catch(() => setAcc(null));
    pushState().then(setPush).catch(() => setPush({ supported: false, sub: null, prefs: { watch: {} } as Prefs }));
    if (!user) return;
    authApi("api/me/medals").then((r) => setMedals(r.medals)).catch(() => setMedals([]));
    authApi("api/me/history").then((h) => { setHist(h); applyProfile(h?.profile); }).catch(() => setHist(null));
    authApi("api/me/devices").then((r) => setDevices(r.devices)).catch(() => setDevices([]));
    if (user.admin) authApi("api/admin/settings").then(setAdmin).catch(() => setAdmin(null));
  }, [user?.user_id, tick]);

  return (
    <div class="me">
      <div class="section-title" style={{ marginTop: "4px" }}><h2>👤 {user ? user.name || "Ich" : "Ich"}</h2>{user?.admin ? <span class="pill ok">Admin</span> : null}</div>
      <LinkPanel devices={devices} onChange={() => setTick(tick + 1)} />
      {user && (
        <Section id="medals" title="🏅 Meine gemeldeten Hits" extra={`${medals.length} aktiv`} open>
          <div class="rows">{medals.length ? medals.map((m) => (
            <a class="line claim-row" href={`#/banner/${m.banner_id}`}>
              <span class="claim-thumb"><Img url={m.image} w={320} alt="" /></span>
              <span style={{ flex: 1 }}><b>{m.tier}</b> {m.name}<br />
                <span class="small" style={{ color: "var(--accent)", fontWeight: 700 }}>{m.value != null ? `${num(m.value)} Coins` : ""}</span>
                <span class="muted small"> · {m.title || `Banner ${m.banner_id}`}{m.t ? ` · ${dateTime(m.t)}` : ""}</span></span>
              <span class="muted">›</span>
            </a>
          )) : <div class="line muted small">Noch nichts gemeldet – auf einer Banner-Seite unter „Karten“ eine Karte antippen.</div>}</div>
        </Section>
      )}
      {user && <Section id="history" title="📊 Mein Verlauf" open><HistorySection h={hist} title="Bilanz" /></Section>}
      {user && <SyncPanel />}
      <Section id="accuracy" title="🎯 Treffsicherheit"><AccuracySection a={acc} /></Section>
      <Section id="rank" title="🎖️ Mein Mitgliedsrang">
        <div class="row">
          <select class="select wide" value={myRank.value} onChange={(e) => (myRank.value = (e.target as HTMLSelectElement).value)} aria-label="Mitgliedsrang">
            <option value="">Nicht angegeben</option>
            {RANKS.map(([k, l]) => <option value={k}>{l}</option>)}
          </select>
          <input class="field small-field" inputMode="numeric" placeholder="Aufladung" value={myCharge.value || ""}
            onChange={(e) => (myCharge.value = Number((e.target as HTMLInputElement).value.replace(/\D/g, "")) || 0)} />
        </div>
        <p class="muted small">Rang · diesen Monat gekaufte Coins{hist?.profile?.updated_at ? ` · 🔄 automatisch (${ddmm(hist.profile.updated_at)})` : ""}.
          Damit filtert „✅ Für mich“ Banner, die du kaufen kannst.</p>
      </Section>
      <PushPanel push={push} onChange={() => pushState().then(setPush)} />
      <Section id="guide" title="📖 Anleitung"><Guide /></Section>
      {admin && <AdminSettings a={admin} />}
      <Section id="look" title="🎨 Darstellung & Links">
        <div class="chips">{THEMES.map(([k, l]) => <button class={`chip ${theme.value === k ? "active" : ""}`} onClick={() => (theme.value = k)}>{l}</button>)}</div>
        <label class="small muted" style={{ display: "block", margin: "10px 0 4px" }}>🔗 GTCHA-Seite öffnen in (für „Öffnen ↗“)</label>
        <select class="select wide" value={linkMode.value} onChange={(e) => (linkMode.value = (e.target as HTMLSelectElement).value)}>
          {Object.entries(LINK_MODES).map(([k, l]) => <option value={k}>{l}</option>)}
        </select>
      </Section>
      <div class="panel small muted" style={{ marginTop: "12px" }}>
        🧪 Beta der neuen Oberfläche · inoffiziell, kein Angebot von GTCHA · Daten wie in der normalen App.
        <a class="btn block" style={{ marginTop: "10px" }} href="../">Zur normalen App</a>
      </div>
    </div>
  );
}

function LinkPanel({ devices, onChange }: { devices: any[]; onChange: () => void }) {
  const user = me.value;
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");

  async function link() {
    setMsg("");
    const res = await fetch("api/link", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return setMsg(data.error || "Code ungültig");
    setToken(data.token);
    haptic();
    await loadMe();
    await loadBanners();
    connectLive();
  }

  if (!user) {
    return (
      <div class="panel">
        <h3 style={{ marginTop: 0 }}>🔗 Discord verknüpfen</h3>
        <p class="muted small">In Discord <b>/tracker-verknüpfen</b> eingeben und den Code hier eintragen.</p>
        <input class="field" maxLength={8} autoComplete="one-time-code" autoCapitalize="characters" placeholder="Code, z. B. K7M2QX" value={code}
          onInput={(e) => setCode((e.target as HTMLInputElement).value)} />
        <button class="btn primary block" style={{ marginTop: "10px" }} onClick={link}>Verknüpfen</button>
        {msg && <div class="notice" style={{ marginTop: "10px" }}>{msg}</div>}
      </div>
    );
  }
  return (
    <Section id="link" title="🔗 Discord-Verknüpfung" extra={`${devices.length} Gerät${devices.length === 1 ? "" : "e"}`}>
      <p class="small" style={{ marginTop: 0 }}>Verknüpft als <b>{user.name}</b>{user.admin ? " · Admin" : ""}</p>
      <div class="row wrap" style={{ justifyContent: "flex-start" }}>
        <button class="btn" onClick={async () => {
          try { await navigator.clipboard.writeText(String(user.user_id)); showToast("Discord-ID kopiert ✓"); } catch { showToast("Kopieren nicht möglich"); }
        }}>Discord-ID kopieren</button>
        <button class="btn" onClick={async () => {
          if (await ask("Verknüpfung trennen?", "Dieses Gerät ist danach nicht mehr mit Discord verknüpft.") !== "Ja") return;
          await authApi("api/unlink", {}).catch(() => {});
          setToken("");
          me.value = null;
          await loadBanners();
        }}>Verknüpfung trennen</button>
      </div>
      {devices.length > 0 && <div class="rows" style={{ marginTop: "10px" }}>{devices.map((d) => (
        <div class="line"><span>{d.agent || "Gerät"}{d.current ? <b> · dieses Gerät</b> : null}<br /><span class="muted small">zuletzt {ddmmhhmm(d.last_seen)}</span></span>
          {!d.current && <button class="icon-btn" aria-label="Gerät abmelden" onClick={async () => {
            if (await ask("Gerät abmelden?", "Dieses Gerät ist danach nicht mehr mit Discord verknüpft.") !== "Ja") return;
            await authApi("api/me/devices/remove", { id: d.id }).catch(() => {});
            haptic();
            onChange();
          }}>✕</button>}</div>
      ))}</div>}
    </Section>
  );
}

// Übertragen per Lesezeichen (Pflicht alle 7 Tage) - gleiches Lesezeichen wie in der normalen App
function SyncPanel() {
  const user = me.value!;
  const s = user.sync;
  const [code, setCode] = useState<string | null>(null);
  async function copy(full: boolean) {
    const text = bookmarkletSync(getToken(), full);
    try {
      await navigator.clipboard.writeText(text);
      showToast("Lesezeichen kopiert ✓");
      haptic();
    } catch {
      setCode(text);
    }
  }
  const line = !s || s.exempt ? null : (s.accounts || []).length > 1 || (s.expected || 1) > 1
    ? (s.accounts || []).map((a) => `${a.ok ? "✅" : "⏳"} ${a.label}${a.gtcha_id ? ` (ID ${a.gtcha_id})` : ""}: ${ddmmhhmm(a.last_sync)}`).join(" · ")
    : !s.last_sync ? "Noch nie übertragen – bis dahin sind Banner & Co. gesperrt."
      : !s.ok ? `Zuletzt übertragen am ${ddmmhhmm(s.last_sync)} – länger als ${s.days} Tage her, App gesperrt.`
        : `Zuletzt übertragen: ${ddmmhhmm(s.last_sync)} · noch ${Math.floor(s.days_left || 0)} Tage bis zur nächsten Pflicht (alle ${s.days} Tage)`;
  return (
    <Section id="sync" title="📥 Eigene GTCHA-Daten" extra={s?.exempt ? "Admin – keine Pflicht" : s?.ok ? "✅" : "⏳"}>
      <p class="muted small" style={{ marginTop: 0 }}>Überträgt deine GTCHA-Verlaufsseiten – ohne Passwort. Name, Adresse, Telefon und E-Mail verlassen das Handy nicht.</p>
      {line && <div class={s?.ok ? "hint-box" : "notice warn"}>{line}</div>}
      <a class="btn primary block" style={{ marginTop: "10px" }} href={buyHref("https://gtchaxonline.com/pending-detail")} target="_blank" rel="noopener">📥 GTCHA öffnen</a>
      <p class="muted small">Dort das Lesezeichen „An GTCHA Tracker“ aufrufen (Einrichtung siehe 📖 Anleitung).</p>
      <button class="btn block" onClick={() => copy(false)}>Lesezeichen „Alles übertragen“ kopieren</button>
      <p class="muted small">Ab dem zweiten Mal nur Neues (alle 30 Tage einmal komplett).</p>
      <button class="btn block" onClick={() => copy(true)}>Lesezeichen „Komplett übertragen“ kopieren</button>
      <p class="muted small">Nur nötig, wenn im Verlauf eine Lücke gemeldet wird. Mehrere GTCHA-Konten? In jedem Browser einrichten und aufrufen –
        die Zuordnung läuft über die Mitglieds-ID. Der Code enthält deinen persönlichen Schlüssel – nicht weitergeben.</p>
      {code && <textarea class="field code" readOnly value={code} onFocus={(e) => (e.target as HTMLTextAreaElement).select()} />}
    </Section>
  );
}

function PushPanel({ push, onChange }: { push: { supported: boolean; sub: PushSubscription | null; prefs: Prefs } | null; onChange: () => void }) {
  const [msg, setMsg] = useState("");
  const [add, setAdd] = useState("");
  if (!push) return null;
  const { supported, sub, prefs } = push;
  const watched = Object.keys(prefs.watch || {}).sort((a, b) => Number(b) - Number(a));
  const byId = Object.fromEntries((banners.value || []).map((b) => [String(b.id), b]));
  const options = (banners.value || []).filter((b) => !prefs.watch[String(b.id)]).sort((a, b) => b.id - a.id);
  const setEvent = async (k: string, v: boolean) => {
    prefs[k] = v;
    if (sub) await savePrefs(sub, prefs);
    onChange();
  };
  return (
    <>
      <Section id="push" title="🔔 Push-Benachrichtigungen" extra={sub ? "an" : "aus"}>
        {!isStandalone() && isIOS() && <div class="notice" style={{ marginBottom: "10px" }}>Auf dem iPhone gehen Pushes nur in der installierten App (Teilen → Zum Home-Bildschirm).</div>}
        <p class="muted small" style={{ marginTop: 0 }}><b>Für alle Banner</b></p>
        {Object.entries(EVENT_LABELS).map(([k, [label, hint]]) => (
          <label class="toggle"><span>{label}{hint ? <span class="muted small"> · {hint}</span> : null}</span>
            <input type="checkbox" checked={Boolean(prefs[k] ?? EVENT_DEFAULTS[k])} onChange={(e) => setEvent(k, (e.target as HTMLInputElement).checked)} /></label>
        ))}
        <div class="row wrap" style={{ justifyContent: "flex-start", marginTop: "10px" }}>
          {!supported ? <span class="muted small">Hier nicht verfügbar.</span>
            : sub ? <>
              <button class="btn" onClick={async () => { await testPush(sub); setMsg("Test-Push gesendet."); }}>Test-Push senden</button>
              <button class="btn" onClick={async () => { await disablePush(sub); onChange(); }}>Pushes ausschalten</button>
            </>
              : <button class="btn primary" onClick={async () => {
                for (const k of Object.keys(EVENT_LABELS)) if (prefs[k] === undefined) prefs[k] = EVENT_DEFAULTS[k];
                try { setMsg((await enablePush(prefs)) || ""); onChange(); } catch (e) { setMsg(`Einschalten fehlgeschlagen: ${(e as Error).message}`); }
              }}>Pushes einschalten</button>}
        </div>
        {msg && <p class="small">{msg}</p>}
      </Section>
      {sub && (
        <Section id="watch" title="👀 Banner beobachten" extra={`${watched.length} beobachtet`}>
          <div class="row">
            <select class="select wide" value={add} onChange={(e) => setAdd((e.target as HTMLSelectElement).value)} aria-label="Banner wählen">
              <option value="">Banner wählen …</option>
              {options.map((b) => <option value={b.id}>{b.id} · {num(b.price)} Coins · {b.category}</option>)}
            </select>
            <button class="btn primary" onClick={async () => {
              if (!add) return;
              prefs.watch[add] = [...WATCH_DEFAULT];
              await savePrefs(sub, prefs);
              setAdd("");
              onChange();
            }}>Hinzufügen</button>
          </div>
          {watched.length ? watched.map((id) => (
            <div class="watch">
              <div class="row"><a href={`#/banner/${id}`}><b>ID {id}</b> <span class="muted small">{byId[id] ? `${num(byId[id].price)} Coins · ${byId[id].category}` : "nicht mehr online"}</span></a>
                <button class="icon-btn" aria-label="Nicht mehr beobachten" onClick={async () => { delete prefs.watch[id]; await savePrefs(sub, prefs); onChange(); }}>✕</button></div>
              <div class="chips wrap">{Object.entries(WATCH_LABELS).map(([k, label]) => (
                <button class={`chip small-chip ${prefs.watch[id].includes(k) ? "on" : ""}`} onClick={async () => {
                  const kinds = new Set(prefs.watch[id]);
                  if (kinds.has(k)) kinds.delete(k); else kinds.add(k);
                  prefs.watch[id] = Object.keys(WATCH_LABELS).filter((x) => kinds.has(x));
                  await savePrefs(sub, prefs);
                  onChange();
                }}>{label}</button>
              ))}</div>
            </div>
          )) : <p class="muted small">Noch keiner – oder auf einer Banner-Seite „🔔 Beobachten“ antippen.</p>}
        </Section>
      )}
    </>
  );
}

function Guide() {
  const [device, setDevice] = useState(load("guideDevice", guessDevice()));
  const g = GUIDES[device] || GUIDES.pc;
  return (
    <div>
      <select class="select wide" value={device} onChange={(e) => { const v = (e.target as HTMLSelectElement).value; setDevice(v); save("guideDevice", v); }}>
        {Object.entries(GUIDE_DEVICES).map(([k, l]) => <option value={k}>{l}</option>)}
      </select>
      <p class="small"><b>App installieren</b></p><ol class="guide">{g.install.map((i) => <li>{i}</li>)}</ol>
      <p class="small"><b>Push-Benachrichtigungen</b></p><p class="small muted">{g.push}</p>
      <p class="small"><b>Lesezeichen „An GTCHA Tracker“ (eigene Daten übertragen)</b></p><ol class="guide">{g.bookmark.map((i) => <li>{i}</li>)}</ol>
      <p class="muted small">Discord-Verknüpfung gilt je Gerät (auf dem iPhone auch je installierter App). Der Lesezeichen-Code enthält deinen persönlichen Schlüssel – nicht weitergeben.</p>
    </div>
  );
}

// Admin: was Discord zu sehen bekommt (App und VPS haben immer alles)
function AdminSettings({ a }: { a: any }) {
  const [mode, setMode] = useState(a.mode);
  const [delay, setDelay] = useState(String(a.delay_minutes));
  return (
    <Section id="admin" title="⚙️ Discord-Ansicht" extra="Admin">
      <div class="row">
        <select class="select wide" value={mode} onChange={(e) => setMode((e.target as HTMLSelectElement).value)}>
          <option value="slim">Schlank</option><option value="full">Voll (wie früher)</option>
        </select>
        <input class="field small-field" inputMode="numeric" value={delay} onInput={(e) => setDelay((e.target as HTMLInputElement).value)} aria-label="Verzögerung in Minuten" />
      </div>
      <p class="muted small">Modus · Verzögerung in Min (0 = sofort) · Admin: {(a.admins || []).map((x: any) => x.name || "–").join(", ") || "–"}</p>
      <button class="btn primary" onClick={async () => {
        try { await authApi("api/admin/settings", { mode, delay_minutes: Number(delay) || 0 }); showToast("Gespeichert ✓"); haptic(); }
        catch (e) { showToast(`Nicht gespeichert: ${(e as Error).message}`); }
      }}>Speichern</button>
    </Section>
  );
}
