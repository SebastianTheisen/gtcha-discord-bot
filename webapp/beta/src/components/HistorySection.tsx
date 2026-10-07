// "Mein Verlauf" aus "Alles übertragen": Bilanz, pro Banner, pro Monat, pro Tag, angeforderte und verschickte Karten
import { evTone, num } from "../format";
import { Img } from "./Img";

/* eslint-disable @typescript-eslint/no-explicit-any */
export type History = any;

const Signed = ({ n }: { n: number }) => <span class={`ev ${n > 0 ? "good" : n < 0 ? "bad" : ""}`}>{n > 0 ? "+" : ""}{num(n)}</span>;
const CLAIM_STATUS: Record<string, string> = { ok: "✅ gemeldet", rejected: "❌ abgelehnt", pending: "⏳ wird gemeldet" };
const dmy = (s?: string) => (s || "").split(" ")[0].split("-").reverse().join(".");

function Stat({ l, v, s }: { l: string; v: preact.ComponentChildren; s?: preact.ComponentChildren }) {
  return <div class="stat"><div class="l">{l}</div><div class="v">{v}</div>{s ? <div class="s">{s}</div> : null}</div>;
}

function CardLine({ c }: { c: any }) {
  return (
    <div class="line claim-row">
      <span class="claim-thumb"><Img url={c.image} w={320} alt="" /></span>
      <span><b>{c.name}</b>{c.rarity ? <span class="muted"> {c.rarity}</span> : null}<br />
        <span class="muted small">{c.number || ""}{c.date ? ` · ${dmy(c.date)}` : ""}</span>
        {c.value ? <><br /><span class="small" style={{ color: "var(--accent)", fontWeight: 700 }}>{num(c.value)} Coins</span></> : null}</span>
    </div>
  );
}

// Bilanz je Tag (Balken grün/rot) und aufsummiert (Linie), die letzten 30 Tage mit Daten
function BalanceChart({ daysNewestFirst }: { daysNewestFirst: { day: string; balance: number }[] }) {
  const days = daysNewestFirst.slice(0, 30).reverse();
  let sum = 0;
  const cum = days.map((d) => (sum += d.balance));
  const W = 600, H = 200, P = 26;
  const max = Math.max(0, ...days.map((d) => d.balance), ...cum);
  const min = Math.min(0, ...days.map((d) => d.balance), ...cum);
  const y = (v: number) => P / 2 + (1 - (v - min) / Math.max(1, max - min)) * (H - P * 1.5);
  const step = (W - 2 * P) / days.length;
  const bw = Math.max(2, step * 0.6);
  const x = (i: number) => P + step * i + step / 2;
  const label = (d: { day: string }) => `${d.day.slice(8, 10)}.${d.day.slice(5, 7)}.`;
  return (
    <svg class="chart tall" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Bilanz pro Tag">
      <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} class="ref" />
      {days.map((d, i) => (
        <rect x={x(i) - bw / 2} width={bw} y={Math.min(y(d.balance), y(0))} height={Math.max(1, Math.abs(y(d.balance) - y(0)))}
          fill={d.balance >= 0 ? "var(--good)" : "var(--bad)"} opacity={0.75} />
      ))}
      <polyline class="line" points={cum.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} />
      <text class="axis" x={P} y={H - 4}>{label(days[0])}</text>
      <text class="axis" x={W - P} y={H - 4} text-anchor="end">{label(days[days.length - 1])}</text>
      <text class="axis" x={W - P} y={14} text-anchor="end">gesamt {sum > 0 ? "+" : ""}{num(sum)}</text>
    </svg>
  );
}

export function HistorySection({ h, title = "📊 Mein Verlauf" }: { h: History; title?: string }) {
  if (!h || h.empty) return <><div class="section-title"><h2>{title}</h2></div><div class="panel muted small">Noch keine Daten – einmal das Lesezeichen „An GTCHA Tracker“ aufrufen.</div></>;
  const s = h.summary, t = s.total, m = h.member || {};
  const monthName = (mo: string) => new Date(mo + "-01T00:00:00").toLocaleDateString("de-DE", { month: "long", year: "numeric" });
  const [d, tm] = (h.saved_at || "").split(" ");
  return (
    <>
      <div class="section-title"><h2>{title}</h2><span class="muted small">Stand {d ? `${dmy(d)} ${tm || ""}` : "–"}</span></div>
      <div class="stats">
        {m.spent_month_yen != null && <Stat l="Ausgaben diesen Monat" v={`${num(m.spent_month_yen)} ¥`} s="laut Kontoseite" />}
        {m.coins != null && <Stat l="Coin-Stand" v={`${num(m.coins)} Coins`} />}
        <Stat l="Bilanz inkl. Hits" v={<><Signed n={t.balance_with_hits ?? t.balance} /> Coins</>}
          s={`${num(t.spent)} rein · ${num(t.returned)} umgewandelt${t.hits ? ` · ${num(t.hits_value)} in ${t.hits} Hits` : ""}`} />
        {t.hits ? <Stat l="Nur Coins" v={<><Signed n={t.balance} /> Coins</>} s="ohne angeforderte/verschickte Karten" /> : null}
        {t.bought ? <Stat l="Coins gekauft" v={num(t.bought)} s={`${num(t.bought_yen)} ¥`} /> : null}
      </div>
      {h.gap && <div class="notice warn" style={{ marginTop: "10px" }}>⚠️ Lücke – einmal „Komplett übertragen“.</div>}
      <p class="muted small">Seit {dmy(s.since)} · {t.opens} Öffnungen{s.unassigned_opens ? ` · ${s.unassigned_opens} nicht zugeordnet` : ""}</p>
      {s.days.length > 1 && <div class="panel"><BalanceChart daysNewestFirst={s.days} /></div>}
      {s.banners.length > 0 && (
        <details class="fold" open>
          <summary>Pro Banner <span class="muted">{s.banners.length}</span></summary>
          <div class="rows">{s.banners.map((b: any) => (
            <a class="line claim-row" href={`#/banner/${b.banner}`}>
              <span class="claim-thumb"><Img url={b.image} w={320} alt="" /></span>
              <span style={{ flex: 1 }}><b>{b.title || `Banner ${b.banner}`}</b><br />
                <span class="muted small">{b.pulls} Züge · {num(b.spent)} rein · {num(b.returned)} zurück{b.hits_value ? ` · ${b.hits} Hit${b.hits > 1 ? "s" : ""} ${num(b.hits_value)}` : ""}</span>
                {b.luck_pct != null && <><br /><span class={`pill ${b.luck_pct >= 100 ? "good" : ""}`}>{b.luck_pct >= 100 ? "🍀 Glück" : "🌧 Pech"} {b.luck_pct} %</span></>}</span>
              <span><Signed n={b.balance + (b.hits_value || 0)} /></span>
            </a>
          ))}</div>
        </details>
      )}
      {(s.months || []).length > 0 && (
        <details class="fold">
          <summary>Pro Monat</summary>
          <div class="rows">{s.months.map((mo: any) => (
            <div class="line"><span>{monthName(mo.month)} <span class="muted small">· {mo.opens} Öffnungen{mo.bought_yen ? ` · ${num(mo.bought_yen)} ¥ aufgeladen` : ""}</span></span><Signed n={mo.balance} /></div>
          ))}</div>
        </details>
      )}
      {s.days.length > 0 && (
        <details class="fold">
          <summary>Pro Tag</summary>
          <div class="rows">{s.days.slice(0, 14).map((dd: any) => (
            <div class="line"><span>{dmy(dd.day)} <span class="muted small">· {dd.opens} Öffnungen</span></span><Signed n={dd.balance} /></div>
          ))}</div>
        </details>
      )}
      {h.pending?.length > 0 && (
        <details class="fold"><summary>Angefordert, noch nicht verschickt <span class="muted">{h.pending.length}</span></summary>
          <div class="rows">{h.pending.map((c: any) => <CardLine c={c} />)}</div></details>
      )}
      {(h.auto_claims || []).length > 0 && (
        <details class="fold"><summary>Automatisch gemeldete Medaillen</summary>
          <div class="rows">{h.auto_claims.map((c: any) => (
            <a class="line" href={`#/banner/${c.pack_id}`}><span><b>{c.tier}</b> {c.title || `Banner ${c.pack_id}`}
              {c.reason ? <><br /><span class="muted small">{c.reason}</span></> : null}</span>
              <span class="muted small">{CLAIM_STATUS[c.status] || c.status || ""}</span></a>
          ))}</div></details>
      )}
      {h.shipped?.length > 0 && (
        <details class="fold"><summary>Verschickt <span class="muted">{h.shipped.length}</span></summary>
          <div class="rows">{h.shipped.map((c: any) => <CardLine c={c} />)}</div></details>
      )}
    </>
  );
}

// Treffsicherheit: vorhergesagte Ø Rückgabe gegenüber dem, was tatsächlich raus kam
export function AccuracySection({ a }: { a: any }) {
  if (!a) return null;
  if (!a.count) {
    return <><div class="section-title"><h2>🎯 Treffsicherheit</h2><span class="muted small">sammelt Daten</span></div>
      <div class="panel muted small">Ergebnis ab 30 verkauften Packs je Banner, gemessen 24 Stunden später.</div></>;
  }
  const dir = a.bias > 1 ? "eher zu optimistisch" : a.bias < -1 ? "eher zu vorsichtig" : "ohne klare Richtung";
  return (
    <>
      <div class="section-title"><h2>🎯 Treffsicherheit</h2><span class="muted small">{a.count} Banner</span></div>
      <div class="stats">
        <Stat l="Ø Abweichung" v={`${a.mean_abs.toLocaleString("de-DE")} %-Punkte`} s={`${dir} · Ergebnis 24 Std. später`} />
        {a.weighted_predicted != null && <Stat l="Gesamt (nach Einsatz)" v={`${a.weighted_predicted.toLocaleString("de-DE")} → ${a.weighted_realized.toLocaleString("de-DE")} %`}
          s={`vorhergesagt → tatsächlich · ${num(a.spent)} Coins Einsatz`} />}
      </div>
      <details class="fold"><summary>Je Banner</summary>
        <div class="rows">{a.items.map((i: any) => (
          <a class="line" href={`#/banner/${i.id}`}><span><b>{i.title || `Banner ${i.id}`}</b><br />
            <span class="muted small">{num(i.sold)} Packs · vorhergesagt {i.predicted.toLocaleString("de-DE")} % · tatsächlich {i.realized.toLocaleString("de-DE")} %</span></span>
            <span class={`ev ${Math.abs(i.diff) <= 5 ? "good" : Math.abs(i.diff) <= 15 ? "ok" : "bad"}`}>{i.diff > 0 ? "+" : ""}{i.diff.toLocaleString("de-DE")}</span></a>
        ))}</div></details>
    </>
  );
}

export { evTone };
