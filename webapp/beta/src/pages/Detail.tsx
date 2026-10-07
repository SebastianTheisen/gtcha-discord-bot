import { useEffect, useMemo, useState } from "preact/hooks";
import { api, LockedError } from "../api";
import { hitsText, STATUS, verdict } from "../components/BannerCard";
import { CardSheet, type CardInfo } from "../components/CardSheet";
import { AreaChart } from "../components/Chart";
import { Lock } from "../components/Lock";
import { countdown, day, dateTime, hhmm, imgSrc, num, pct, compact } from "../format";
import { pref } from "../store";
import type { BannerDetail, Hit, TimelineEvent } from "../types";

const tabPref = pref<string>("detailTab", "overview");
const TABS: [string, string][] = [["overview", "Übersicht"], ["hits", "Hits"], ["cards", "Karten"], ["history", "Verlauf"]];

export function Detail({ id }: { id: string }) {
  const [b, setB] = useState<BannerDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lockInfo, setLock] = useState<LockedError["info"] | null>(null);
  const [sheet, setSheet] = useState<CardInfo | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api<BannerDetail>(`api/banner/${id}`)
      .then((d) => alive && (setB(d), setError(null)))
      .catch((e) => alive && (e instanceof LockedError ? setLock(e.info) : setError("Banner nicht gefunden")));
    load();
    addEventListener("gtcha:update", load);   // Live-Update: Daten still nachladen
    return () => {
      alive = false;
      removeEventListener("gtcha:update", load);
    };
  }, [id]);

  if (lockInfo) return <Lock info={lockInfo} />;
  if (error) return <div class="empty">{error}<br /><a class="btn" href="#/">Zurück</a></div>;
  if (!b) return <div><div class="skeleton" style={{ height: "240px" }} /><div class="skeleton" style={{ height: "120px", marginTop: "12px" }} /></div>;

  const tab = tabPref.value;
  const [tone, label] = verdict(b);
  const [icon, statusLabel] = STATUS[b.status] || STATUS.running;
  return (
    <div style={{ paddingBottom: b.archived ? 0 : "90px" }}>
      <div class="hero">
        {b.image && <img src={imgSrc(b.image, 1280)} alt="" />}
        <a class="back glass" href="#/" onClick={(e) => { if (history.length > 1) { e.preventDefault(); history.back(); } }}>‹ Zurück</a>
      </div>
      <div class="detail-head">
        <div class="row" style={{ justifyContent: "flex-start" }}>
          <span class="pill glass">{icon} {statusLabel}</span>
          <span class="pill">#{b.id}</span>
          {b.category && <span class="pill">{b.category}</span>}
          {b.password && <span class="pill bad">🔑 Passwort</span>}
          {b.rank && <span class="pill">{b.rank}</span>}
        </div>
        <h1>{b.title}</h1>
        <div class={`verdict ${tone}`}>
          <div class="big">{b.ev_pct != null ? pct(b.ev_pct) : "–"}</div>
          <div>
            <div style={{ fontWeight: 800, fontSize: "18px" }}>{label}</div>
            <div class="muted small">
              {b.ev != null ? `Ø ${num(b.ev)} Coins pro Zug · ${b.ev_from_site ? "aus Zahlen der Seite" : "geschätzt"}` : "Noch keine Ø Rückgabe"}
              {b.ev_uncertain ? " · ⚠️ unsicher, nur noch wenige Packs" : ""}
            </div>
          </div>
        </div>
      </div>

      <div class="tabs">
        {TABS.map(([k, l]) => <button class={tab === k ? "active" : ""} onClick={() => (tabPref.value = k)}>{l}</button>)}
      </div>

      {tab === "overview" && <Overview b={b} />}
      {tab === "hits" && <Hits b={b} open={setSheet} />}
      {tab === "cards" && <Cards b={b} open={setSheet} />}
      {tab === "history" && <History events={b.pack_timeline || []} />}

      {!b.archived && b.buy_url && (
        <div class="buybar">
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 800 }} class="tnum">{num(b.price)} Coins</div>
            <div class="muted small">{num(b.remaining)} / {num(b.total)} Packs{b.end_ts ? ` · noch ${countdown(b.end_ts)}` : ""}</div>
          </div>
          <a class="btn primary" href={b.buy_url} target="_blank" rel="noopener noreferrer">Öffnen ↗</a>
        </div>
      )}
      {sheet && <CardSheet card={sheet} price={b.price} category={b.category} onClose={() => setSheet(null)} />}
    </div>
  );
}

function Stat({ l, v, s }: { l: string; v: string; s?: string }) {
  return <div class="stat"><div class="l">{l}</div><div class="v">{v}</div>{s && <div class="s">{s}</div>}</div>;
}

function Overview({ b }: { b: BannerDetail }) {
  const packs = (b.history || []).map((h) => ({ t: h.t, v: h.packs }));
  const ev = (b.ev_history || []).map((h) => ({ t: h.t, v: h.ev }));
  return (
    <div>
      <div class="stats">
        <Stat l="Hits drin" v={`${hitsText(b)}${b.unsure ? " ❓" : ""}`} s={b.tracked_hits ? "Versand-Hits" : "T1–T3"} />
        <Stat l="Ø Kosten bis Hit" v={b.cost_to_hit ? `${num(b.cost_to_hit)}` : "–"} s="Coins" />
        <Stat l="Packs übrig" v={`${num(b.remaining)}`} s={`von ${num(b.total)}`} />
        <Stat l="Wert aller Karten" v={b.pool_value ? compact(b.pool_value) : "–"}
          s={b.all_packs_cost && b.pool_value ? `${pct((b.pool_value / b.all_packs_cost) * 100, 0)} der Packkosten` : undefined} />
        {b.ship_cards != null && <Stat l="Verschickt" v={`${num(b.ship_cards)} Karten`} s={b.ship_value ? `${compact(b.ship_value)} Coins` : undefined} />}
        {b.min_value != null && <Stat l="Kleinste Karte" v={`${num(b.min_value)}`} s="Coins" />}
      </div>
      {(b.out || []).length > 0 && (
        <>
          <div class="section-title"><h2>Schon raus</h2></div>
          <div class="panel small">{(b.out || []).map((o) => <div>{o.via === "medaille" ? "🏅" : "📦"} {o.name} · {num(o.value)}</div>)}</div>
        </>
      )}
      <div class="section-title"><h2>💰 Ø Rückgabe im Verlauf</h2><span class="muted small">gestrichelt = 100 %</span></div>
      <div class="panel"><AreaChart points={ev} refLine={100} label={(v) => pct(v, 0)} /></div>
      <div class="section-title"><h2>📉 Packs im Verlauf</h2></div>
      <div class="panel"><AreaChart points={packs} label={(v) => num(v)} /></div>
    </div>
  );
}

const STATE_ICON: Record<Hit["state"], string> = { open: "🟢", pulled: "✅", unsure: "❓", maybe: "❔" };

function Hits({ b, open }: { b: BannerDetail; open: (c: CardInfo) => void }) {
  if (!b.hits?.length) return <div class="empty">Keine Hit-Liste für diesen Banner</div>;
  return (
    <div class="hits" style={{ marginTop: "8px" }}>
      {b.hits.map((h) => (
        <button class={`hit ${h.state}`} onClick={() => open({ ...h })}>
          {h.image ? <img src={imgSrc(h.image, 160)} alt="" loading="lazy" /> : <div class="ph" />}
          <div>
            <div class="tier">{h.tier} · {num(h.value)} Coins</div>
            <div class="name">{h.name}</div>
            {h.note && <div class="muted small">{h.note}{h.origin?.name ? ` · ${h.origin.name}` : ""}</div>}
          </div>
          <div class="state">{STATE_ICON[h.state]}</div>
        </button>
      ))}
    </div>
  );
}

const CARD_FILTERS: [string, string][] = [["all", "Alle"], ["above", "≥ Packpreis"], ["hits", "Versand-Hits"]];

function Cards({ b, open }: { b: BannerDetail; open: (c: CardInfo) => void }) {
  const [filter, setFilter] = useState("above");
  const list = useMemo(() => (b.cards || []).filter((c) =>
    filter === "hits" ? c.hit : filter === "above" ? c.value >= (b.price || 0) : true), [b.cards, filter, b.price]);
  const total = (b.cards || []).reduce((n, c) => n + c.copies, 0);
  return (
    <div>
      <div class="chips" style={{ marginTop: "8px" }}>
        {CARD_FILTERS.map(([k, l]) => <button class={`chip ${filter === k ? "active" : ""}`} onClick={() => setFilter(k)}>{l}</button>)}
        <span class="muted small" style={{ alignSelf: "center", whiteSpace: "nowrap" }}>{num(total)} Karten · {b.cards?.length || 0} verschiedene</span>
      </div>
      <div class="cards">
        {list.slice(0, 200).map((c) => (
          <button class={`card ${c.hit ? "hit-card" : ""}`} onClick={() => open(c)}>
            <div class="img">{c.image && <img src={imgSrc(c.image, 320)} alt="" loading="lazy" decoding="async" />}</div>
            <div class="n">{c.name}</div>
            <div class="v tnum">{num(c.value)}{c.copies > 1 ? <span class="muted"> ×{c.copies}</span> : null}</div>
          </button>
        ))}
      </div>
      {list.length > 200 && <p class="muted small">Die ersten 200 von {list.length} Karten</p>}
    </div>
  );
}

function History({ events }: { events: TimelineEvent[] }) {
  if (!events.length) return <div class="empty">Noch keine Pack-Bewegungen</div>;
  const days: { day: string; items: TimelineEvent[] }[] = [];
  for (const e of events) {
    const d = e.t ? day(e.t) : "Vor Beginn der Aufzeichnung";
    if (!days.length || days[days.length - 1].day !== d) days.push({ day: d, items: [] });
    days[days.length - 1].items.push(e);
  }
  return (
    <div class="timeline" style={{ marginTop: "8px" }}>
      {days.map((d, i) => {
        const sold = d.items.filter((e) => e.kind === "pack").reduce((n, e) => n + (e.old || 0) - (e.new || 0), 0);
        const shipped = d.items.filter((e) => e.kind === "out").reduce((n, e) => n + (e.ship_value || 0), 0);
        return (
          <details open={i === 0}>
            <summary><span>{d.day}</span><span class="muted small">−{num(sold)} Packs{shipped ? ` · ${compact(shipped)} verschickt` : ""}</span></summary>
            {d.items.map((e) => e.kind === "pack"
              ? <div class="tl-row"><span class="muted tnum">{e.t ? hhmm(e.t) : "–"}</span><span>📉 {num(e.old)} → {num(e.new)} <b>(−{num((e.old || 0) - (e.new || 0))})</b></span></div>
              : (
                <div class={`tl-row out ${e.explain.some((l) => l.icon === "✅") ? "hit" : ""}`}>
                  <span class="muted tnum">{e.t ? hhmm(e.t) : "–"}</span>
                  <span>
                    {e.ship_cards ? <>📦 <b>{num(e.ship_cards)}</b> {e.ship_cards === 1 ? "Karte" : "Karten"} · <b>{num(e.ship_value)}</b> Coins</> : null}
                    {e.converted ? <>{e.ship_cards ? " · " : ""}🪙 <b>{num(e.converted)}</b> umgewandelt</> : null}
                    {e.packs != null ? <span class="muted"> · bei {num(e.packs)} Packs</span> : null}
                    {e.explain.map((l) => <div>{l.icon} {l.text}</div>)}
                  </span>
                </div>
              ))}
          </details>
        );
      })}
      <p class="muted small">Stand {dateTime(Date.now() / 1000)}</p>
    </div>
  );
}
