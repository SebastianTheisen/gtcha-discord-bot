import type { ComponentChildren } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { LockedError } from "../api";
import { Flags, hitsText, STATUS, verdict } from "../components/BannerCard";
import { CardSheet, type CardInfo } from "../components/CardSheet";
import { AreaChart } from "../components/Chart";
import { Img } from "../components/Img";
import { Lock } from "../components/Lock";
import { compact, countdown, dateTime, day, evTone, hhmm, num, pct, shortTime, untilText } from "../format";
import { cardCode } from "../links";
import { buyHref, canBuy, whyNot } from "../local";
import { pushState, toggleWatch, watchIds } from "../push";
import { changedIds, detailCache, fetchDetail, me, pref } from "../store";
import type { BannerDetail, Card, Hit, Shipment, TimelineEvent } from "../types";
import { ask } from "../ui";

const tabPref = pref<string>("detailTab", "overview");
const cardFilter = pref<string>("cardFilter", "all");
const TABS: [string, string][] = [["overview", "Übersicht"], ["cards", "Karten"], ["hits", "Hits"], ["history", "Verlauf"]];

// Meldbare Exemplare einer Karte (aus der Hit-Liste des Banners), nach Platz
const unitsOf = (b: BannerDetail, card: Card) =>
  (b.hits || []).filter((h) => h.key === card.id || h.key.startsWith(card.id + "#")).sort((x, y) => x.rank - y.rank);

export function Detail({ id }: { id: string }) {
  const [b, setB] = useState<BannerDetail | null>(detailCache.get(id) || null);
  const [error, setError] = useState<string | null>(null);
  const [lockInfo, setLock] = useState<LockedError["info"] | null>(null);
  const [sheet, setSheet] = useState<{ card: CardInfo; units: Hit[] } | null>(null);
  const [reload, setReload] = useState(0);

  // nur neu laden, wenn sich dieser Banner geändert hat (Live-Update) oder nach dem Melden
  const changed = changedIds.value.has(Number(id));
  useEffect(() => {
    let alive = true;
    if (reload) detailCache.delete(id);
    fetchDetail(id)
      .then((d) => alive && (setB(d), setError(null)))
      .catch((e) => alive && (e instanceof LockedError ? setLock(e.info)
        : setError(String(e.message) === "404" ? "Banner nicht gefunden" : `Daten nicht erreichbar (${e.message})`)));
    return () => {
      alive = false;
    };
  }, [id, changed, reload]);

  useEffect(() => {
    pushState().catch(() => {});   // beobachtete Banner für den Knopf
  }, []);

  if (lockInfo) return <Lock info={lockInfo} />;
  if (error) return <div class="empty">{error}<br /><br /><a class="btn" href="#/">‹ Zur Übersicht</a></div>;
  if (!b) return <div><div class="skeleton" style={{ height: "240px" }} /><div class="skeleton" style={{ height: "120px", marginTop: "12px" }} /></div>;

  const tab = tabPref.value;
  const [tone, label] = verdict(b);
  const [icon, statusLabel] = STATUS[b.status] || STATUS.running;
  const diff = b.price && b.remaining && b.left_value != null && !b.archived ? b.left_value - b.price * b.remaining : null;
  const until = untilText(b);
  const watched = watchIds.value.has(String(b.id));
  const openCard = (c: Card) => setSheet({ card: { ...c }, units: unitsOf(b, c) });
  const openHit = (h: Hit) => {
    const card = (b.cards || []).find((c) => h.key === c.id || h.key.startsWith(c.id + "#"));
    setSheet({ card: card ? { ...card, note: h.note } : { name: h.name, value: h.value, image: h.image, tier: h.tier, note: h.note },
      units: card ? unitsOf(b, card) : [] });
  };

  async function watch() {
    const r = await toggleWatch(String(b!.id));
    if (r === null && await ask("Pushes sind aus", "Zum Beobachten zuerst unter „Ich“ die Pushes einschalten.", ["Später", "Zu „Ich“"]) === "Zu „Ich“") {
      location.hash = "#/me";
    }
  }

  return (
    <div style={{ paddingBottom: b.archived ? 0 : "90px" }}>
      <div class="hero">
        <Img url={b.image} w={960} alt="" eager />
        <a class="back glass" href="#/" onClick={(e) => { if (history.length > 1) { e.preventDefault(); history.back(); } }}>‹ Zurück</a>
      </div>
      <div class="detail-head">
        <div class="row wrap" style={{ justifyContent: "flex-start" }}>
          <span class="pill glass">{icon} {statusLabel}</span>
          <span class="pill">#{b.id}</span>
          {b.category && <span class="pill">{b.category}</span>}
          {b.price ? <span class="pill">{num(b.price)} Coins</span> : <span class="pill good">Gratis</span>}
        </div>
        <h1>{b.headline || b.title}</h1>
        <Flags b={b} />
        {canBuy(b) === false && <div class="notice warn">🚫 Für dich nicht kaufbar: {whyNot(b)}</div>}
        <div class={`verdict ${tone}`}>
          <div class="big">{b.ev_pct != null && !b.archived ? pct(b.ev_pct) : "–"}</div>
          <div>
            <div style={{ fontWeight: 800, fontSize: "18px" }}>{label}</div>
            <div class="muted small">
              {b.ev != null && !b.archived ? `Ø ${num(b.ev)} Coins pro Zug` : ""}
              {b.ev_uncertain ? " · ⚠️ unsicher, nur noch wenige Packs" : ""}
              {b.archived ? "Stand beim Ende · bleibt 30 Tage im Archiv" : b.ev_from_site ? " · aus Zahlen der Seite" : " · geschätzt"}
            </div>
          </div>
        </div>
        <div class="facts">
          {b.hits_open != null && <span>🎯 <b>{hitsText(b)}</b>{b.unsure ? " ❓" : ""}</span>}
          <span>📦 <b>{num(b.remaining)}</b> von {num(b.total)} {b.archived ? "übrig beim Ende" : "übrig"}</span>
          {diff != null && <span>💰 Rest kaufen: <b class={diff >= 0 ? "pos" : "neg"}>{diff >= 0 ? "+" : "−"}{num(Math.abs(diff))}</b></span>}
          {b.cost_to_hit && !b.archived ? <span>⏱ Ø <b>{num(b.cost_to_hit)}</b> bis Hit</span> : null}
        </div>
        {until && <div class="small muted" style={{ marginTop: "6px" }}>{b.archived ? "🗄️" : "⏳"} {until}</div>}
        {b.archived && <ArchiveSummary b={b} />}
        {!b.archived && (
          <div class="row" style={{ marginTop: "10px" }}>
            <button class={`btn ${watched ? "on" : ""}`} onClick={watch}>{watched ? "🔔 Beobachtet ✓" : "🔔 Beobachten"}</button>
            <a class="muted small" href="#/me">Einstellungen ›</a>
          </div>
        )}
      </div>

      <div class="tabs" role="tablist">
        {TABS.map(([k, l]) => <button role="tab" class={tab === k ? "active" : ""} onClick={() => (tabPref.value = k)}>{l}</button>)}
      </div>

      {tab === "overview" && <Overview b={b} />}
      {tab === "cards" && <Cards b={b} open={openCard} />}
      {tab === "hits" && <Hits b={b} open={openHit} />}
      {tab === "history" && <History b={b} />}

      {!b.archived && b.buy_url && (
        <div class="buybar">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 800 }} class="tnum">{b.price ? `${num(b.price)} Coins` : "Gratis"}</div>
            <div class="muted small">{num(b.remaining)} / {num(b.total)} Packs{b.end_ts ? ` · noch ${countdown(b.end_ts)}` : ""}</div>
          </div>
          <a class="btn primary" href={buyHref(b.buy_url)} target="_blank" rel="noopener noreferrer">Öffnen ↗</a>
        </div>
      )}
      {sheet && (
        <CardSheet card={sheet.card} units={sheet.units} price={b.price} category={b.category} packId={b.id} archived={b.archived}
          onClose={() => setSheet(null)} onChanged={() => setReload((n) => n + 1)} />
      )}
    </div>
  );
}

// Archiv: kurze Bilanz des beendeten Banners (wie die Hits rausgingen, was noch bei Spielern liegt)
function ArchiveSummary({ b }: { b: BannerDetail }) {
  const out = b.out || [];
  const medals = out.filter((h) => h.via === "medaille").length;
  const parts: string[] = [];
  if (out.length) parts.push(`🏅 ${medals} per Medaille · 📦 ${out.length - medals} Versand erkannt`);
  if (b.out_unsure) parts.push(`❓ ${b.out_unsure} unklar`);
  if (b.undecided) parts.push(`🎒 ${num(b.undecided)} Coins noch bei Spielern`);
  return parts.length ? <div class="small" style={{ marginTop: "6px" }}>{parts.join(" · ")}</div> : null;
}

function Stat({ l, v, s, tone }: { l: string; v: ComponentChildren; s?: ComponentChildren; tone?: string }) {
  return <div class="stat"><div class="l">{l}</div><div class={`v ${tone || ""}`}>{v}</div>{s ? <div class="s">{s}</div> : null}</div>;
}

function Overview({ b }: { b: BannerDetail }) {
  const restCost = b.price && b.remaining ? b.price * b.remaining : null;
  const restDiff = restCost != null ? (b.left_value || 0) - restCost : null;
  return (
    <div>
      <div class="stats">
        {b.ev != null && !b.archived && (
          <Stat l="Ø Rückgabe pro Zug" v={`${num(b.ev)} Coins`} tone={evTone(b.ev_pct)}
            s={`${b.ev_pct != null ? pct(b.ev_pct) + " vom Preis" : ""}${b.ev_uncertain ? " · ⚠️ unsicher" : ""}${b.ev_from_site ? " · aus Zahlen der Seite" : " · geschätzt"}`} />
        )}
        {b.hits_open != null && (
          <Stat l={b.archived ? "Hits beim Ende" : "Hits noch drin"} v={`${hitsText(b)}${b.unsure ? " ❓" : ""}`}
            s={b.cost_to_hit && !b.archived ? `Ø ${num(b.cost_to_hit)} Coins bis Hit` : undefined} />
        )}
        {b.min_value != null && <Stat l="Mindestens zurück" v={`${num(b.min_value)} Coins`} s={b.price ? `${pct((b.min_value / b.price) * 100)} vom Preis` : undefined} />}
        {b.pool_value ? <Stat l="Alle Karten" v={`${num(b.pool_value)} Coins`}
          s={b.all_packs_cost ? `Alle Packs: ${num(b.all_packs_cost)} (${pct((b.pool_value / b.all_packs_cost) * 100)})` : undefined} /> : null}
        {b.left_value != null && !b.archived && (
          <Stat l="Noch im Banner (rechnerisch)" v={`${num(b.left_value)} Coins`}
            s={<>{b.left_per_pack != null ? `Ø ${num(b.left_per_pack)} pro Restpack${b.price ? ` (${pct((b.left_per_pack / b.price) * 100)})` : ""}` : ""}
              {restCost != null && restDiff != null && <><br />Alle {num(b.remaining)} Restpacks kosten <b>{num(restCost)}</b><br />
                <span class={restDiff >= 0 ? "pos" : "neg"}>{restDiff >= 0 ? "+" : "−"}{num(Math.abs(restDiff))} Coins {restDiff >= 0 ? "mehr zurück" : "weniger zurück"}</span></>}</>} />
        )}
        {b.per_day && !b.archived ? <Stat l="Pro Tag" v={`${b.per_day}×`} /> : null}
      </div>
      {b.out_total != null && (
        <div class="panel small" style={{ marginTop: "10px" }}>
          <div style={{ fontWeight: 800, fontSize: "15px", marginBottom: "4px" }}>Aus dem Banner raus: {num(b.out_total + (b.undecided || 0))} Coins</div>
          <div>📦 verschickt: {num(b.ship_cards)} {b.ship_cards === 1 ? "Karte" : "Karten"} · {num(b.ship_value)} Coins{b.ship_players ? ` · ${num(b.ship_players)} Spieler` : ""}</div>
          <div>🪙 umgewandelt: {num(b.converted)} Coins{b.converted_max_cards != null ? ` · höchstens ${num(b.converted_max_cards)} Karten` : ""}</div>
          {b.undecided != null && <div>🎒 noch bei Spielern: {num(b.undecided)} Coins (gezogen, noch nicht verschickt oder umgewandelt)</div>}
          <div class="muted"><i>Kartenwerte; die Seite zählt den Versand ohne 10 % Steuer ({num(b.ship_counted)})</i></div>
        </div>
      )}
      {b.ship_cards != null && b.out_total == null && (
        <div class="panel small" style={{ marginTop: "10px" }}>
          📦 {b.ship_cards ? `${num(b.ship_cards)} Karten verschickt · ${num(b.ship_value)} Coins Kartenwert · ${num(b.ship_players)} Spieler` : "Noch nichts verschickt"}
        </div>
      )}
      {(b.out || []).length > 0 && (
        <>
          <div class="section-title"><h2>Schon raus</h2>{b.out_unsure ? <span class="muted small">❓ {b.out_unsure} unklar</span> : null}</div>
          <div class="panel small">{(b.out || []).map((o) => <div>{o.via === "medaille" ? "🏅" : "📦"} {o.name} · {num(o.value)}</div>)}</div>
        </>
      )}
      {b.conditions && <div class="notice" style={{ marginTop: "12px", whiteSpace: "pre-line" }}>{b.conditions.replace(/\*\*/g, "")}</div>}
      <div class="section-title"><h2>💰 Ø Rückgabe im Verlauf</h2><span class="muted small">gestrichelt = 100 %</span></div>
      <div class="panel"><AreaChart points={(b.ev_history || []).map((h) => ({ t: h.t, v: h.ev }))} refLine={100} label={(v) => pct(v, 0)} /></div>
      <div class="section-title"><h2>📉 Packs im Verlauf</h2></div>
      <div class="panel"><AreaChart points={(b.history || []).map((h) => ({ t: h.t, v: h.packs }))} label={(v) => num(v)} /></div>
    </div>
  );
}

const STATE_ICON: Record<Hit["state"], string> = { open: "🟢", pulled: "✅", unsure: "❓", maybe: "❔" };
const VIA: Record<string, string> = { discord: "💬", app: "📱", lesezeichen: "🔖" };

// Unter der Karte: wer hat sie, wann und wie (Medaille aus Discord/App/Lesezeichen) oder wann verschickt
function originLines(units: Hit[], meId?: string): string[] {
  const many = units.length > 1;
  return units.map((u) => {
    const o = u.origin;
    const tier = many ? `${u.tier} ` : "";
    if (!o) return u.state === "unsure" && u.odds != null ? `❓ ${tier}~${u.odds} % raus` : "";
    if (o.via === "admin") return `🛠️ ${tier}durch Admin abgehakt${o.at ? ` · ${shortTime(o.at)}` : ""}`;
    if (o.via === "versand") {
      return u.state === "unsure"
        ? `❓ ${tier}${u.odds != null ? `~${u.odds} % · ` : ""}📦 ${o.shipped_at ? shortTime(o.shipped_at) : ""}`
        : `📦 ${tier}verschickt ${o.shipped_at ? shortTime(o.shipped_at) : ""}`;
    }
    const who = meId && o.user === String(meId) ? "du" : o.name || "unbekannt";
    const when = o.via === "lesezeichen" && o.pulled_on ? `gez. ${o.pulled_on.slice(8, 10)}.${o.pulled_on.slice(5, 7)}.`
      : o.at ? shortTime(o.at) : "";
    return `🏅 ${tier}${who} ${VIA[o.via] || "💬"}${when ? ` · ${when}` : ""}`;
  }).filter(Boolean).slice(0, 3);
}

function Hits({ b, open }: { b: BannerDetail; open: (h: Hit) => void }) {
  if (!b.hits?.length) return <div class="empty">Keine Hit-Liste für diesen Banner</div>;
  const left = b.hits.filter((h) => h.state === "open").length;
  const meId = me.value?.user_id;
  return (
    <div style={{ marginTop: "8px" }}>
      <p class="muted small">{left} von {b.hits.length} noch drin · {b.archived ? "beendet" : "antippen = melden"}</p>
      <div class="hits">
        {b.hits.map((h) => (
          <button class={`hit ${h.state}`} onClick={() => open(h)}>
            {h.image ? <Img url={h.image} w={320} alt="" /> : <div class="ph" />}
            <div>
              <div class="tier">{h.tier} · {num(h.value)} Coins</div>
              <div class="name">{h.name}</div>
              {h.note && <div class="muted small">{h.note}</div>}
              {originLines([h], meId).map((l) => <div class="muted small">{l}</div>)}
            </div>
            <div class="state">{STATE_ICON[h.state]}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

const CARD_FILTERS: [string, string][] = [["all", "Alle"], ["above", "Ab Packpreis"], ["open", "Noch offen"], ["hits", "Versand-Hits"]];
const PAGE = 48;

function Cards({ b, open }: { b: BannerDetail; open: (c: Card) => void }) {
  const [limit, setLimit] = useState(PAGE);
  const filter = cardFilter.value;
  const list = useMemo(() => (b.cards || []).filter((c) =>
    filter === "above" ? b.price && c.value >= b.price : filter === "open" ? c.pulled < c.copies
      : filter === "hits" ? c.hit : true), [b.cards, filter, b.price]);
  const total = (b.cards || []).reduce((n, c) => n + c.copies, 0);
  const meId = me.value?.user_id;
  if (!b.cards?.length) return <div class="empty">Kartenliste noch nicht geladen</div>;
  return (
    <div>
      <p class="muted small" style={{ margin: "10px 2px 6px" }}>
        {num(total)} Karten · {b.cards.length} verschiedene{b.share_above_price != null ? ` · ${pct(b.share_above_price)} ≥ Packpreis (gold)` : ""}
        {" · "}{b.archived ? "beendet" : "antippen = melden / merken"}
      </p>
      <div class="chips">
        {CARD_FILTERS.map(([k, l]) => <button class={`chip ${filter === k ? "active" : ""}`} onClick={() => { cardFilter.value = k; setLimit(PAGE); }}>{l}</button>)}
      </div>
      {list.length ? (
        <div class="cards">
          {list.slice(0, limit).map((c) => {
            const units = unitsOf(b, c);
            const gone = c.pulled >= c.copies;
            const tiers = units.length ? (units.length === 1 ? units[0].tier : `${units[0].tier}–${units[units.length - 1].tier}`) : "";
            const mine = meId && units.some((u) => u.medal_user === String(meId));
            const share = c.share.toLocaleString("de-DE", { maximumFractionDigits: c.share < 0.1 ? 3 : c.share < 1 ? 2 : 1 }) + " %";
            return (
              <button class={`card ${c.hit ? "hit-card" : ""} ${gone ? "gone" : ""} ${b.price && c.value >= b.price ? "above" : ""}`} onClick={() => open(c)}>
                <div class="img">
                  <Img url={c.image} w={320} alt="" />
                  {tiers && <span class="tier-badge">{tiers}</span>}
                  {c.copies > 1 && <span class="copies">×{c.copies}</span>}
                  {c.hit && <span class="ship-tag">✈</span>}
                </div>
                <div class="v tnum">{num(c.value)}</div>
                <div class="n">{c.name}</div>
                {(c.model || c.rarity) && <div class="m">{[cardCode(c.model)?.text, c.rarity].filter(Boolean).join(" · ")}</div>}
                <div class="m">{share}{c.pulled && !gone ? ` · ${c.pulled}/${c.copies} gezogen` : ""}{mine ? " · von dir" : ""}</div>
                {c.unsure && !gone && !units.some((u) => u.odds != null) && <div class="m">❓ {c.unsure}</div>}
                {originLines(units, meId).map((l) => <div class="m">{l}</div>)}
              </button>
            );
          })}
        </div>
      ) : <div class="empty">Keine Karten für diesen Filter</div>}
      {list.length > limit && <button class="btn block" style={{ marginTop: "12px" }} onClick={() => setLimit(limit + PAGE * 2)}>Weitere {Math.min(list.length - limit, PAGE * 2)} Karten zeigen</button>}
    </div>
  );
}

function History({ b }: { b: BannerDetail }) {
  return (
    <div>
      <div class="section-title"><h2>📉 Pack-Verlauf</h2><span class="muted small">mit Versand/Umwandlung</span></div>
      <Timeline events={b.pack_timeline || []} />
      <div class="section-title"><h2>📦 Versandschübe</h2><span class="muted small">Kartenwert = gezählt × 1,1</span></div>
      <Shipments list={b.shipments || []} />
    </div>
  );
}

function Shipments({ list }: { list: Shipment[] }) {
  if (!list.length) return <div class="panel muted small">Noch keine Versandschübe aufgezeichnet</div>;
  return (
    <div class="timeline">
      {list.map((s) => (
        <div class={`panel batch ${s.kind === "hits" ? "has-hit" : ""}`}>
          <div class="row small"><span class="muted">{s.t ? dateTime(s.t) : "vor Beginn der Aufzeichnung"}</span>
            <span><b>+{num(s.cards)}</b> {s.cards === 1 ? "Karte" : "Karten"} · <b>{num(s.value)}</b> Coins{s.players ? ` · +${num(s.players)} Spieler` : ""}</span></div>
          {s.explain.length > 0 && <div class="small" style={{ marginTop: "6px" }}>{s.explain.map((l) => <div>{l.icon} {l.text}</div>)}</div>}
        </div>
      ))}
    </div>
  );
}

function Timeline({ events }: { events: TimelineEvent[] }) {
  if (!events.length) return <div class="panel muted small">Noch keine Pack-Bewegungen</div>;
  const days: { day: string; items: TimelineEvent[] }[] = [];
  for (const e of events) {
    const d = e.t ? day(e.t) : "Vor Beginn der Aufzeichnung";
    if (!days.length || days[days.length - 1].day !== d) days.push({ day: d, items: [] });
    days[days.length - 1].items.push(e);
  }
  return (
    <div class="timeline">
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
                    {e.players ? <span class="muted"> · +{num(e.players)} Spieler</span> : null}
                    {e.explain.map((l) => <div>{l.icon} {l.text}</div>)}
                  </span>
                </div>
              ))}
          </details>
        );
      })}
    </div>
  );
}
