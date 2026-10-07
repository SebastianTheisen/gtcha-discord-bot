// Absprungpunkte zu Marktplätzen: öffentliche Suchseiten mit dem Kartennamen (keine Konten, keine Schnittstellen).
// Spiel aus der Kategorie des Banners; bei MIX/Bonus/Store wird es aus dem Namen geraten, sonst ohne Spiel gesucht.

type Game = "Pokemon" | "OnePiece" | "DragonBallSuper" | null;

const GAME_BY_CATEGORY: Record<string, Game> = {
  "Pokémon": "Pokemon",
  "One piece": "OnePiece",
  "Dragon Ball": "DragonBallSuper",
};

const HINTS: [RegExp, Game][] = [
  [/pok[eé]mon|pikachu|glurak|charizard|mewtwo|umbreon|nachtara|evoli|eevee|\bex\b|\bvmax\b|\bsar\b/i, "Pokemon"],
  [/one piece|luffy|ruffy|zoro|\bnami\b|sanji|\bace\b|shanks|\bop\d{2}\b|\bst\d{2}\b|\bpara(llel)?\b.*(op|eb)\d/i, "OnePiece"],
  [/dragon ?ball|goku|vegeta|gogeta|gohan|frieza|fusion world|\bfb\d{2}\b/i, "DragonBallSuper"],
];

export function guessGame(name: string, category?: string): Game {
  if (category && GAME_BY_CATEGORY[category] !== undefined) return GAME_BY_CATEGORY[category];
  for (const [re, game] of HINTS) if (re.test(name)) return game;
  return null;
}

// "[Parallel] Gogeta: GT (Included in OP16)" -> "Gogeta GT", "Mew LV.23" -> "Mew": Cardmarket findet nur, wenn
// alle Wörter im Produktnamen stehen - Level-Zahlen, Kartennummern, Seltenheiten und Klammerzusätze stehen dort nicht.
// Teil des Namens bleiben z. B. "LV.X", "ex", "GX", "V", "VMAX", "VSTAR".
export function searchName(name: string): string {
  return name
    .replace(/[\[【(（][^\]】)）]*[\]】)）]/g, " ")
    .replace(/\bLV\.?\s*\d+\b/gi, " ")
    .replace(/\b\d{1,3}\s*\/\s*\d{1,3}\b/g, " ")
    .replace(/\b(SAR|SR|AR|UR|HR|CHR|CSR|SSR|RRR|RR|PSA\s*\d+|BGS\s*[\d.]+|Promo)\b/gi, " ")
    .replace(/\b([XY])ex\b/g, "$1 ex")   // GTCHA schreibt "Mega Charizard Xex", Cardmarket "Mega Charizard X ex"
    .replace(/[★☆:：・/#\[\]【】()（）]/g, " ")   // auch einzelne Klammern ohne Gegenstück
    .replace(/\s+/g, " ")
    .trim();
}

// "M2110-080" -> Set "M2", Nummer "110/080" (japanische Kartennummer: Nummer/Setgröße); "Old Back" = alte Karte ohne Nummer
export function cardCode(model?: string | null): { set?: string; number?: string; text: string } | null {
  const m = (model || "").trim();
  if (!m) return null;
  const parts = m.match(/^(.*?)(\d{2,3})-(\d{2,3})$/);
  if (parts) {
    const set = parts[1].replace(/[-\s]+$/, "") || undefined;
    const number = `${parts[2]}/${parts[3]}`;
    return { set, number, text: [set, number].filter(Boolean).join(" · ") };
  }
  return { text: m };
}

export interface MarketLink {
  label: string;
  hint: string;
  url: string;
}

export function marketLinks(name: string, category?: string, model?: string | null): MarketLink[] {
  const q = searchName(name) || name;
  const enc = encodeURIComponent(q);
  // mit Kartennummer (neuere Karten) trifft die Suche die genaue Version
  const number = cardCode(model)?.number;
  const withNumber = number ? `${q} ${number}` : q;
  const encNum = encodeURIComponent(withNumber);
  const game = guessGame(name, category);
  const links: MarketLink[] = [];
  if (game) {
    links.push({
      label: "Cardmarket",
      hint: q === name.trim() ? "Angebote in Europa" : `Suche „${q}“`,
      url: `https://www.cardmarket.com/de/${game}/Products/Search?searchString=${enc}`,
    });
  }
  // Voller Name über Google, nur auf Cardmarket: trifft oft die genaue Version (Level, Set, Promo)
  links.push({
    label: "Cardmarket (Google)",
    hint: number ? `Suche „${withNumber}“` : "genaue Version suchen",
    url: `https://www.google.com/search?q=${encodeURIComponent(`site:cardmarket.com ${number ? withNumber : name.replace(/[\[\]【】]/g, " ").trim()}`)}`,
  });
  links.push(
    { label: "PriceCharting", hint: "Preisverlauf & Grading", url: `https://www.pricecharting.com/search-products?q=${number ? encNum : enc}&type=prices` },
    { label: "eBay verkauft", hint: "echte Verkaufspreise", url: `https://www.ebay.de/sch/i.html?_nkw=${number ? encNum : enc}&LH_Sold=1&LH_Complete=1` },
  );
  return links;
}
