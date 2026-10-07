import { imgSrc, num, pct } from "../format";
import { marketLinks } from "../links";
import { Sheet } from "./Sheet";

export interface CardInfo {
  name: string;
  value: number;
  image?: string;
  tier?: string;
  note?: string | null;
  copies?: number;
  hit?: boolean;
}

// Karte groß mit Wert und Absprung zu Marktplätzen (Cardmarket, PriceCharting, eBay verkaufte Angebote)
export function CardSheet({ card, price, category, onClose }: { card: CardInfo; price?: number; category?: string; onClose: () => void }) {
  return (
    <Sheet onClose={onClose}>
      <div class="card-big">
        {card.image ? <img src={imgSrc(card.image, 320)} alt="" /> : <div class="ph" />}
        <div>
          {card.tier && <div class="pill" style={{ color: "var(--accent)" }}>{card.tier}</div>}
          <h3 style={{ margin: "6px 0" }}>{card.name}</h3>
          <div style={{ fontSize: "22px", fontWeight: 800 }} class="tnum">{num(card.value)} Coins</div>
          {price ? <div class="muted small">{pct((card.value / price) * 100, 0)} vom Packpreis</div> : null}
          {card.copies && card.copies > 1 ? <div class="muted small">{card.copies}× im Banner</div> : null}
          {card.hit && <div class="muted small">Versand-Hit (nicht umwandelbar)</div>}
          {card.note && <div class="small" style={{ marginTop: "6px" }}>{card.note}</div>}
        </div>
      </div>
      <div class="links">
        {marketLinks(card.name, category).map((l) => (
          <a href={l.url} target="_blank" rel="noopener noreferrer">
            <span>{l.label} <span class="muted small" style={{ fontWeight: 400 }}>· {l.hint}</span></span>
            <span>↗</span>
          </a>
        ))}
      </div>
      <p class="muted small">Suche über den Kartennamen – bei japanischen oder Sonder-Karten bitte das Ergebnis prüfen.</p>
    </Sheet>
  );
}
