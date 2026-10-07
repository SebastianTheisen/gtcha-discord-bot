// Kleines Flächendiagramm als SVG (ohne Bibliothek): Packs im Verlauf, Ø Rückgabe im Verlauf
interface Props {
  points: { t: number; v: number }[];
  refLine?: number;        // z. B. 100 % als gestrichelte Linie
  height?: number;
  label?: (v: number) => string;
}

export function AreaChart({ points, refLine, height = 140, label }: Props) {
  if (points.length < 2) return <div class="empty small">Noch zu wenige Daten für einen Verlauf</div>;
  const w = 600;
  const h = height;
  const t0 = points[0].t;
  const t1 = points[points.length - 1].t || t0 + 1;
  const vals = points.map((p) => p.v).concat(refLine != null ? [refLine] : []);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pad = (hi - lo) * 0.08 || 1;
  const y = (v: number) => h - ((v - (lo - pad)) / (hi - lo + 2 * pad)) * h;
  const x = (t: number) => ((t - t0) / (t1 - t0 || 1)) * w;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const last = points[points.length - 1];
  return (
    <div>
      <svg class="chart" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img">
        <path class="area" d={`${line}L${w},${h}L0,${h}Z`} />
        {refLine != null && <line class="ref" x1={0} x2={w} y1={y(refLine)} y2={y(refLine)} />}
        <path class="line" d={line} />
      </svg>
      {label && (
        <div class="row small muted">
          <span>min {label(Math.min(...points.map((p) => p.v)))}</span>
          <span>jetzt <b style={{ color: "var(--text)" }}>{label(last.v)}</b></span>
          <span>max {label(Math.max(...points.map((p) => p.v)))}</span>
        </div>
      )}
    </div>
  );
}
