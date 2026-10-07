import { countdown, evTone, imgSrc, num, pct, type Tone } from "../format";
import { changedIds } from "../store";
import type { Banner, Status } from "../types";

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
  b.hits_open == null ? "–" : b.tracked_hits ? `${b.hits_open}/${b.hits_total}` : `${b.hits_open} offen`;

export function BannerCard({ b }: { b: Banner }) {
  const [icon, label] = STATUS[b.status] || STATUS.running;
  const left = b.total ? Math.max(0, Math.min(100, (b.remaining / b.total) * 100)) : 0;
  const tone = evTone(b.ev_pct);
  return (
    <a class={`bcard ${changedIds.value.has(b.id) ? "flash" : ""}`} href={`#/banner/${b.id}`}>
      <div class="media">
        {b.image ? <img src={imgSrc(b.image, 640)} alt="" loading="lazy" decoding="async" /> : null}
        <div class="badges">
          <span class="pill glass">{icon} {label}</span>
          {b.ev_pct != null && !b.archived && (
            <span class={`pill ${tone}`} style={{ backdropFilter: "blur(10px)" }}>
              Ø {pct(b.ev_pct)}{b.ev_uncertain ? " ⚠️" : ""}
            </span>
          )}
        </div>
        <div class="over">
          <div class="title">{b.title}</div>
          <div class="sub">#{b.id} · {b.category}{b.end_ts ? ` · endet in ${countdown(b.end_ts)}` : ""}</div>
        </div>
      </div>
      <div class="body">
        <div class="kpis">
          <div class="kpi"><div class="v">{num(b.price)}</div><div class="l">Coins / Pack</div></div>
          <div class={`kpi ${tone}`}><div class="v">{b.ev != null ? num(b.ev) : "–"}</div><div class="l">Ø Rückgabe</div></div>
          <div class="kpi"><div class="v">{hitsText(b)}{b.unsure ? " ❓" : ""}</div><div class="l">Hits drin</div></div>
        </div>
        <div>
          <div class="row small"><span class="muted">📦 {num(b.remaining)} von {num(b.total)} übrig</span>
            {b.cost_to_hit ? <span class="muted">Ø bis Hit {num(b.cost_to_hit)}</span> : null}</div>
          <div class="bar" style={{ marginTop: "6px" }}><span style={{ width: `${left}%` }} /></div>
        </div>
      </div>
    </a>
  );
}

export function BannerSkeleton() {
  return <div class="skeleton" style={{ height: "300px" }} />;
}
