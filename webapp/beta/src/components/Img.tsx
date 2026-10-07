import { imgSrc } from "../format";

// Bild vom VPS-Zwischenspeicher; klappt das nicht, direkt von GTCHA laden; klappt auch das nicht, ausblenden
export function Img({ url, w = 640, alt = "", eager = false }: { url?: string; w?: number; alt?: string; eager?: boolean }) {
  const src = imgSrc(url, w);
  if (!src) return null;
  return (
    <img src={src} alt={alt} loading={eager ? "eager" : "lazy"} decoding="async"
      onError={(e) => {
        const el = e.currentTarget;
        if (url && el.getAttribute("src") !== url) el.src = url;
        else el.style.visibility = "hidden";
      }} />
  );
}
