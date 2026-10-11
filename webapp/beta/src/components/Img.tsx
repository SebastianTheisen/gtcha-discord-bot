import { imgSrc } from "../format";

// Bild vom VPS-Zwischenspeicher; klappt das nicht, direkt von GTCHA laden; klappt auch das nicht, ausblenden.
// eager: sofort laden (sichtbare Bilder oben) - sonst lädt der Browser es erst kurz bevor es ins Bild kommt
export function Img({ url, w = 640, alt = "", eager = false }: { url?: string; w?: number; alt?: string; eager?: boolean }) {
  const src = imgSrc(url, w);
  if (!src) return null;
  return (
    <img src={src} alt={alt} loading={eager ? "eager" : "lazy"} decoding="async"
      fetchpriority={eager ? "high" : "auto"}
      onError={(e) => {
        const el = e.currentTarget;
        if (url && el.getAttribute("src") !== url) el.src = url;
        else el.style.visibility = "hidden";
      }} />
  );
}

// Bannerbild; neue Banner haben auf der Seite keins mehr, dort wie auf der Seite die Top-Karten als Fächer
export function Cover({ b, w = 640, alt = "", eager = false, max = 5 }:
  { b: { image?: string; cover?: string[] }; w?: number; alt?: string; eager?: boolean; max?: number }) {
  if (b.image || !b.cover?.length) return <Img url={b.image} w={w} alt={alt} eager={eager} />;
  const cards = b.cover.slice(0, max);
  return (
    <div class={`fan n${cards.length}`}>
      {cards.map((u, i) => <span class={`fan-card ${i === 0 ? "top" : ""}`} style={{ order: fanOrder(i, cards.length) }}>
        <Img url={u} w={320} alt={i === 0 ? alt : ""} eager={eager} /></span>)}
    </div>
  );
}

// wertvollste Karte in die Mitte, die nächsten abwechselnd links und rechts
export const fanOrder = (i: number, n: number) => (i === 0 ? n : i % 2 ? n - i : n + i);
