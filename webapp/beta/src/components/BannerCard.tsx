import { countdown, evTone, num, pct, untilText, type Tone } from "../format";
import { canBuy } from "../local";
import { changedIds, prefetchDetail, wishBanners } from "../store";
import type { Banner, Status } from "../types";
import { Img } from "./Img";

export const STATUS: Record<Status, [string, string]> = {
  running: ["🎯", "Läuft"],
  endspurt: ["⚡", "Endspurt"],
  hits_out: ["🔴", "Hits raus"],
  upcoming: ["🕒", "Bald"],
  ended: ["🗄️", "Beendet"],
};

export function verdict(b: Banner): [Tone, string] {
  if (b.archived) return ["muted", "Beendet"];
  if (b.status === "upcoming") return ["muted", "Noch nicht gestartet"];
  if (b.ev_pct == null) return ["muted", "Keine Daten"];
  if (b.ev_pct >= 100) return ["good", "Lohnt sich"];
  if (b.ev_pct >= 90) return ["ok", "Knapp"];
  return ["bad", "Lohnt sich nicht"];
}

export const hitsText = (b: Banner) =>
  b.hits_open == null ? "–" : b.tracked_hits ? `${b.hits_open}/${b.hits_total}` : `Top 3: ${b.hits_open}`;

// Kaufbedingungen und gemerkte Wunschkarten als kleine Hinweise über der Karte
export function Flags({ b }: { b: Banner }) {
  if (b.archived) return null;
  const out: string[] = [];
  if (b.per_day) out.push(`Beschränkt auf ${b.per_day} Mal pro Tag`);
  if (b.min_charge) out.push(`${num(b.min_charge)} Coins Aufladung im Monat nötig`);
  if (b.password) out.push("🔒 Nur mit Passwort");
  const wished = wishBanners.value.get(b.id);
  if (wished) out.push(`⭐ ${wished.join(", ")}`);
  return out.length ? <div class="flags">{out.map((f) => <span class="flag">{f}</span>)}</div> : null;
}

// "✅ Raus: Lugia, Pikachu +2 · ❓ 1 unklar" - Hits, die sicher raus sind
export function OutLine({ b }: { b: Banner }) {
  const out = b.out || [];
  if (!out.length && !b.out_unsure) return null;
  const names = out.slice(0, 2).map((h) => h.name).join(", ") + (out.length > 2 ? ` +${out.length - 2}` : "");
  return (
    <div class="small out-line">
      {out.length ? <>✅ Raus: <b>{names}</b></> : null}
      {b.out_unsure ? `${out.length ? " · " : ""}❓ ${b.out_unsure} unklar` : null}
    </div>
  );
}

export function ShipLine({ b }: { b: Banner }) {
  if (b.ship_cards == null) return null;
  if (!b.ship_cards) return <div class="small muted">📦 Noch nichts verschickt</div>;
  return <div class="small">📦 <b>{num(b.ship_cards)}</b> verschickt · <b>{num(b.ship_value)}</b> Coins</div>;
}

export function BannerCard({ b, rank, eager = false }: { b: Banner; rank?: number; eager?: boolean }) {
  const [icon, label] = STATUS[b.status] || STATUS.running;
  const left = b.total ? Math.max(0, Math.min(100, (b.remaining / b.total) * 100)) : 0;
  const tone = evTone(b.ev_pct);
  const until = untilText(b);
  return (
    <div class={`entry ${canBuy(b) === false ? "not-mine" : ""}`}>
      <Flags b={b} />
      <a class={`bcard ${changedIds.value.has(b.id) ? "flash" : ""} ${b.status === "hits_out" ? "done" : ""}`} href={`#/banner/${b.id}`}
        onPointerDown={() => prefetchDetail(b.id)}>
        <div class="media">
          <Img url={b.image} w={640} alt={b.title} eager={eager} />
          <div class="badges">
            <span class="pill glass">{rank ? <b class={`rank-no r${rank}`}>#{rank}</b> : null} {icon} {label}</span>
            {b.ev_pct != null && !b.archived && (
              <span class={`pill ${tone} solid`}>Ø {pct(b.ev_pct)}{b.ev_uncertain ? " ⚠️" : ""}</span>
            )}
          </div>
          <div class="over">
            <div class="title">{b.headline || b.title}</div>
            <div class="sub">#{b.id} · {b.category}{b.end_ts && !b.archived ? ` · endet in ${countdown(b.end_ts)}` : ""}</div>
          </div>
        </div>
        <div class="body">
          <div class="kpis">
            <div class="kpi"><div class="v">{b.price ? num(b.price) : "Gratis"}</div><div class="l">Coins / Pack</div></div>
            <div class={`kpi ${tone}`}><div class="v">{b.ev != null ? num(b.ev) : "–"}</div><div class="l">Ø Rückgabe</div></div>
            <div class="kpi"><div class="v">{hitsText(b)}{b.unsure ? " ❓" : ""}</div><div class="l">Hits drin</div></div>
          </div>
          <div>
            <div class="row small"><span class="muted">📦 {num(b.remaining)} von {num(b.total)} übrig</span>
              {b.cost_to_hit && !b.archived ? <span class="muted">Ø bis Hit {num(b.cost_to_hit)}</span> : null}</div>
            <div class="bar" style={{ marginTop: "6px" }}><span style={{ width: `${left}%` }} /></div>
          </div>
          <ShipLine b={b} />
          <OutLine b={b} />
          {until && <div class="small muted">{b.archived ? "🗄️" : "⏳"} {until}</div>}
        </div>
      </a>
    </div>
  );
}

// Kompaktansicht: eine Zeile pro Banner
export function CompactRow({ b, rank, eager = false }: { b: Banner; rank?: number; eager?: boolean }) {
  const [icon] = STATUS[b.status] || STATUS.running;
  const left = b.total ? Math.max(0, Math.min(100, (b.remaining / b.total) * 100)) : 0;
  return (
    <a class={`crow ${b.status === "hits_out" ? "done" : ""} ${canBuy(b) === false ? "not-mine" : ""} ${changedIds.value.has(b.id) ? "flash" : ""}`}
      href={`#/banner/${b.id}`} onPointerDown={() => prefetchDetail(b.id)}>
      <div class="crow-img"><Img url={b.image} w={320} alt="" eager={eager} /></div>
      <div class="crow-main">
        <div class="small"><b>{rank ? `#${rank} · ` : ""}{b.id}</b> <span class="muted">{b.category}</span> {b.status !== "running" ? icon : ""}</div>
        <div class="crow-title">{b.headline || b.title}</div>
        <div class="small muted">{num(b.remaining)} / {num(b.total)} Packs · {hitsText(b)}{b.unsure ? " ❓" : ""}</div>
        <div class="bar thin"><span style={{ width: `${left}%` }} /></div>
      </div>
      <div class="crow-right">
        <div class="tnum" style={{ fontWeight: 800 }}>{b.price ? num(b.price) : "Gratis"}</div>
        {b.ev_pct != null && !b.archived && <span class={`pill ${evTone(b.ev_pct)}`}>Ø {pct(b.ev_pct)}{b.ev_uncertain ? " ⚠️" : ""}</span>}
      </div>
    </a>
  );
}

export function BannerSkeleton() {
  return <div class="skeleton" style={{ height: "300px" }} />;
}
