import { useEffect, useState } from "preact/hooks";
import { authApi } from "../api";
import { waitForBot } from "../components/CardSheet";
import { HistorySection } from "../components/HistorySection";
import { Img } from "../components/Img";
import { ddmm, ddmmhhmm, num, pct } from "../format";
import { rankLabel } from "../local";
import { me } from "../store";
import { ask, haptic, showToast } from "../ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
const Signed = ({ n }: { n: number }) => <span class={`ev ${n > 0 ? "good" : n < 0 ? "bad" : ""}`}>{n > 0 ? "+" : ""}{num(n)}</span>;
const ago = (sec?: number | null) => (sec == null ? "–" : sec < 120 ? `${sec} s` : sec < 7200 ? `${Math.round(sec / 60)} Min` : `${Math.round(sec / 3600)} Std`);
const ok = (good: boolean) => (good ? "🟢" : "🔴");

// Admin: System-Status, automatisch abgehakte Hits, Push an alle, Nutzer mit Statistik und Korrekturen
export function Users() {
  const user = me.value;
  const [users, setUsers] = useState<any[] | null>(null);
  const [st, setSt] = useState<any>(null);
  const [ticks, setTicks] = useState<any[]>([]);
  const [tick, setTick] = useState(0);
  const reload = () => setTick(tick + 1);

  useEffect(() => {
    if (!user?.admin) return;
    authApi("api/admin/users").then((r) => setUsers(r.users)).catch(() => setUsers([]));
    authApi("api/admin/status").then(setSt).catch(() => setSt(null));
    authApi("api/admin/auto_ticks").then((r) => setTicks(r.items || [])).catch(() => setTicks([]));
  }, [user?.user_id, tick]);

  if (user === undefined) return <div class="skeleton" style={{ height: "200px" }} />;
  if (!user?.admin) return <div class="empty">Nur für den Admin.</div>;

  return (
    <div>
      <div class="section-title" style={{ marginTop: "4px" }}><h2>👥 Nutzer</h2><span class="muted small">{users?.length ?? "…"}</span></div>
      <details class="section">
        <summary><span>🩺 System-Status</span><span>{st ? ok(st.heartbeat_age != null && st.heartbeat_age < 900) : ""}</span></summary>
        <div class="section-body">{st ? <div class="rows">
          <div class="line"><span>{ok(st.heartbeat_age != null && st.heartbeat_age < 900)} Bot (letzter Scrape)</span><span>vor {ago(st.heartbeat_age)}</span></div>
          <div class="line"><span>Letzte Pack-Bewegung</span><span>{st.last_pack_move ? st.last_pack_move.slice(11, 16) : "–"}</span></div>
          <div class="line"><span>{ok(st.data_age != null && st.data_age < 120)} App-Daten</span><span>vor {ago(st.data_age)}</span></div>
          <div class="line"><span>Aktive Banner</span><span>{num(st.active_banners)}</span></div>
          <div class="line"><span>{ok(!!st.backup_last)} Letztes Backup</span><span>{ddmmhhmm(st.backup_last)}</span></div>
          <div class="line"><span>Datenbank / App</span><span>{st.db_mb} / {st.app_db_mb} MB</span></div>
          <div class="line"><span>Discord</span><span>{st.slim ? `schlank · ${st.delay_minutes} Min` : "voll"} · {st.outbox ?? "–"} wartend</span></div>
          <div class="line"><span>Lernen: Fälle / Versand-Beobachtungen</span><span>{num(st.learn_cases || 0)} / {num(st.learn_observations || 0)}</span></div>
          <div class="line"><span>Hits unsichtbar gezogen (gelernt)</span><span>{st.hidden_rate != null ? pct(st.hidden_rate * 100) : "–"}</span></div>
          <div class="line"><span>Nutzer / gesperrt / Push-Geräte</span><span>{st.users} / {st.blocked} / {st.push_devices}</span></div>
        </div> : <p class="muted small">Status nicht verfügbar</p>}</div>
      </details>

      <details class="section">
        <summary><span>🤖 Automatisch abgehakt</span><span class="muted small">{ticks.length} · 14 Tage</span></summary>
        <div class="section-body">
          <p class="muted small" style={{ marginTop: 0 }}>Vom Bot aus Versand/Umwandlung erkannt. „war falsch“ nimmt den Haken sofort weg; der Hit wird bei diesem Banner nicht mehr automatisch abgehakt.</p>
          <div class="rows">{ticks.length ? ticks.map((t) => (
            <div class="line"><span><a href={`#/banner/${t.pack_id}`}><b>{t.pack_id}</b></a> {t.tier || ""} · {t.name || t.key}
              {t.value ? <span class="muted"> · {num(t.value)}</span> : null}<br />
              <span class="muted small">{t.at ? ddmmhhmm(t.at) : ""}{t.rebuild ? " · Neuberechnung" : ""}{t.rejected ? " · ❌ als falsch markiert" : ""}</span></span>
              <button class="btn small-btn" onClick={async () => {
                const undo = t.rejected;
                const c = await ask(undo ? "Wieder zulassen?" : "Erkennung war falsch?", <>{t.pack_id} · {t.tier} {t.name}<br /><br />{undo
                  ? "Der Bot darf diesen Hit wieder automatisch abhaken (bei der nächsten Auswertung)."
                  : "Der Haken verschwindet sofort, und der Bot hakt diesen Hit bei diesem Banner nicht mehr automatisch ab."}</>,
                ["Abbrechen", undo ? "Zulassen" : "War falsch"]);
                if (!c || c === "Abbrechen") return;
                const { id } = await authApi("api/admin/reject", { pack_id: t.pack_id, key: t.key, undo });
                haptic();
                await waitForBot(id, reload);
              }}>{t.rejected ? "↩️ zulassen" : "❌ war falsch"}</button></div>
          )) : <div class="line muted small">Nichts in den letzten 14 Tagen</div>}</div>
        </div>
      </details>

      <PushAll />

      {users == null ? <div class="skeleton" style={{ height: "200px", marginTop: "12px" }} />
        : users.length ? users.map((u) => <UserCard u={u} all={users} onChange={reload} />) : <div class="empty">Keine Nutzer.</div>}
    </div>
  );
}

function PushAll() {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  return (
    <details class="section">
      <summary><span>📢 Push an alle</span></summary>
      <div class="section-body">
        <input class="field left" maxLength={80} placeholder="Titel" value={title} onInput={(e) => setTitle((e.target as HTMLInputElement).value)} />
        <input class="field left" style={{ marginTop: "8px" }} maxLength={300} placeholder="Text" value={body} onInput={(e) => setBody((e.target as HTMLInputElement).value)} />
        <button class="btn primary" style={{ marginTop: "8px" }} onClick={async () => {
          if (!title.trim()) return showToast("Titel fehlt");
          if (await ask("An alle senden?", title, ["Abbrechen", "Senden"]) !== "Senden") return;
          try {
            const r = await authApi("api/admin/push", { title: title.trim(), body: body.trim() });
            showToast(`Gesendet an ${r.sent} Geräte ✓`);
            haptic();
          } catch (e) { showToast((e as Error).message); }
        }}>Senden</button>
      </div>
    </details>
  );
}

function UserCard({ u, all, onChange }: { u: any; all: any[]; onChange: () => void }) {
  const [h, setH] = useState<any>(null);
  const [err, setErr] = useState("");
  const p = u.profile || {};
  const t = u.total;

  async function load() {
    if (h) return;
    try { setH(await authApi(`api/admin/user/${u.user_id}`)); } catch (e) { setErr((e as Error).message); }
  }

  const others = all.filter((x) => !x.blocked && x.user_id !== u.user_id).slice(0, 8);
  return (
    <details class="section" onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && load()}>
      <summary>
        <span><b>{u.name || "Unbekannt"}</b>{u.blocked ? " ⛔" : ""}<br />
          <span class="muted small">{rankLabel(p.rank)} · {u.medals} Medaille{u.medals === 1 ? "" : "n"} · {u.saved_at ? `übertragen ${ddmm(u.saved_at)}` : "nie übertragen"}</span></span>
        <span>{t ? <Signed n={t.balance} /> : <span class="muted">–</span>}</span>
      </summary>
      <div class="section-body">
        {err ? <p class="muted small">Nicht ladbar: {err}</p> : !h ? <div class="skeleton" style={{ height: "80px" }} /> : (
          <>
            <div class="stats">
              <div class="stat"><div class="l">Mitgliedsrang</div><div class="v">{rankLabel(h.profile?.rank)}</div></div>
              <div class="stat"><div class="l">Aufgeladen (Monat)</div><div class="v">{h.profile?.charge != null ? `${num(h.profile.charge)} Coins` : "–"}</div>
                {h.profile?.charge_yen != null && <div class="s">{num(h.profile.charge_yen)} ¥</div>}</div>
              <div class="stat"><div class="l">Coin-Stand</div><div class="v">{h.member?.coins != null ? `${num(h.member.coins)} Coins` : "–"}</div></div>
              <div class="stat"><div class="l">Geräte</div><div class="v">{(h.devices || []).length}</div></div>
            </div>
            <div class="section-title"><h2>👤 GTCHA-Konten</h2>{h.sync?.exempt ? <span class="muted small">Admin – keine Pflicht</span> : null}</div>
            <div class="rows">
              <div class="line"><span>Anzahl Konten (alle müssen alle {h.sync?.days || 7} Tage übertragen)</span>
                <select class="select" value={String(h.sync?.expected || 1)} onChange={async (e) => {
                  await authApi("api/admin/accounts", { user_id: u.user_id, expected: Number((e.target as HTMLSelectElement).value) });
                  haptic();
                  showToast("Gespeichert ✓");
                }}>{[1, 2, 3].map((n) => <option value={n}>{n}</option>)}</select></div>
              {(h.sync?.accounts || []).map((a: any) => (
                <div class="line"><span>{a.ok ? "✅" : "⏳"} {a.label} · {ddmmhhmm(a.last_sync)}{a.account === "default" ? <span class="muted"> · altes Lesezeichen</span> : null}
                  {a.gtcha_id ? <b> · ID {a.gtcha_id}</b> : null}</span>
                  <button class="btn small-btn" onClick={async () => {
                    if (await ask("Konto zurücksetzen?", `${a.label} wird vergessen und muss neu übertragen werden.`, ["Abbrechen", "Zurücksetzen"]) !== "Zurücksetzen") return;
                    await authApi("api/admin/accounts", { user_id: u.user_id, remove: a.account });
                    haptic();
                    onChange();
                  }}>zurücksetzen</button></div>
              ))}
              {h.sync?.missing ? <div class="line muted small">❌ {h.sync.missing} Konto/Konten noch nie übertragen</div> : null}
            </div>
            {u.user_id !== String(me.value?.user_id) && (
              <button class="btn" style={{ marginTop: "10px" }} onClick={async () => {
                const block = !u.blocked;
                if (block && await ask("Sperren?", `${u.name || ""}: alle Geräte abmelden, Verknüpfen blockieren.`, ["Abbrechen", "Sperren"]) !== "Sperren") return;
                await authApi("api/admin/block", { user_id: u.user_id, blocked: block });
                haptic();
                onChange();
              }}>{u.blocked ? "Entsperren" : "⛔ Sperren"}</button>
            )}
            <div class="section-title"><h2>🏅 Medaillen</h2><span class="muted small">{(h.medals || []).length}</span></div>
            <div class="rows">{(h.medals || []).length ? h.medals.map((m: any) => (
              <div class="line claim-row"><span class="claim-thumb"><Img url={m.image} w={320} alt="" /></span>
                <span style={{ flex: 1 }}><b>{m.tier}</b> {m.name}<br /><span class="muted small">{m.title || `Banner ${m.banner_id}`}</span></span>
                <button class="icon-btn" aria-label="Medaille bearbeiten" onClick={async () => {
                  const labels = others.map((x) => `→ ${x.name || "Unbekannt"}`);
                  const choice = await ask(`${m.tier} · ${m.name}`, "Korrigieren:", ["Abbrechen", "Entfernen", ...labels]);
                  if (!choice || choice === "Abbrechen") return;
                  const payload: Record<string, unknown> = { pack_id: m.banner_id, tier: m.tier, action: "remove" };
                  if (choice.startsWith("→ ")) {
                    payload.action = "assign";
                    payload.user_id = others[labels.indexOf(choice)].user_id;
                  }
                  await authApi("api/admin/medal", payload);
                  haptic();
                  await ask("Gesendet", "Der Bot übernimmt es in wenigen Sekunden.", ["OK"]);
                  onChange();
                }}>✎</button></div>
            )) : <div class="line muted small">Keine</div>}</div>
            {h.empty ? <p class="muted small">Nichts übertragen.</p> : <HistorySection h={h} title="📊 Verlauf" />}
          </>
        )}
      </div>
    </details>
  );
}
