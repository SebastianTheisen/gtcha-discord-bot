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

// "[Parallel] Gogeta: GT (Included in OP16)" -> "Gogeta GT" - Zusätze in Klammern stören die Suche
export function searchName(name: string): string {
  return name
    .replace(/[\[【(（][^\]】)）]*[\]】)）]/g, " ")
    .replace(/[★☆:：・/]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export interface MarketLink {
  label: string;
  hint: string;
  url: string;
}

export function marketLinks(name: string, category?: string): MarketLink[] {
  const q = searchName(name) || name;
  const enc = encodeURIComponent(q);
  const game = guessGame(name, category);
  const links: MarketLink[] = [];
  if (game) {
    links.push({
      label: "Cardmarket",
      hint: "Angebote in Europa",
      url: `https://www.cardmarket.com/de/${game}/Products/Search?searchString=${enc}`,
    });
  }
  links.push(
    { label: "PriceCharting", hint: "Preisverlauf & Grading", url: `https://www.pricecharting.com/search-products?q=${enc}&type=prices` },
    { label: "eBay verkauft", hint: "echte Verkaufspreise", url: `https://www.ebay.de/sch/i.html?_nkw=${enc}&LH_Sold=1&LH_Complete=1` },
  );
  return links;
}
