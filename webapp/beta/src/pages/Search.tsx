import { useEffect, useRef, useState } from "preact/hooks";
import { api, LockedError } from "../api";
import { CardSheet, type CardInfo } from "../components/CardSheet";
import { Img } from "../components/Img";
import { Lock } from "../components/Lock";
import { evTone, num, pct } from "../format";
import { isWish, load, save, wishList } from "../local";
import { toggleWish } from "../push";

interface FoundBanner { id: number; title: string; price: number; copies: number; out?: boolean; ev_pct?: number | null; remaining: number }
interface FoundCard { id: string; name: string; value: number | null; image?: string; banners: FoundBanner[] }

// Kartensuche über alle laufenden Banner und Wunschliste (⭐ = Push bei neuem Banner oder wenn gezogen)
export function Search() {
  const [q, setQ] = useState(load("searchQ", ""));
  const [cards, setCards] = useState<FoundCard[] | null>(null);
  const [lock, setLock] = useState<LockedError["info"] | null>(null);
  const [sheet, setSheet] = useState<CardInfo | null>(null);
  const timer = useRef<number | undefined>(undefined);

  async function run(text: string) {
    save("searchQ", text);
    try {
      if (text.length < 2) {
        const wish = wishList.value;
        if (!wish.length) return setCards([]);
        const res = await api<{ cards: FoundCard[] }>(`api/cards?ids=${wish.map((w) => w.id).join(",")}`);
        const byId = Object.fromEntries(res.cards.map((c) => [c.id, c]));
        setCards(wish.map((w) => byId[w.id] || { ...w, value: null, banners: [] }).reverse());
      } else {
        setCards((await api<{ cards: FoundCard[] }>(`api/cards?q=${encodeURIComponent(text)}`)).cards);
      }
      setLock(null);
    } catch (e) {
      if (e instanceof LockedError) setLock(e.info);
    }
  }

  useEffect(() => {
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => run(q.trim()), 300);
  }, [q, q.trim().length < 2 ? wishList.value : null]);

  if (lock) return <Lock info={lock} />;
  const wishMode = q.trim().length < 2;
  return (
    <div>
      <div class="section-title" style={{ marginTop: "4px" }}><h2>🔍 Karten suchen</h2></div>
      <form class="toolbar" role="search" onSubmit={(e) => { e.preventDefault(); (document.activeElement as HTMLElement | null)?.blur(); run(q.trim()); }}>
        <label class="search"><span>🔎</span>
          <input type="search" autoComplete="off" placeholder="Kartenname, z. B. Glurak" value={q}
            onInput={(e) => setQ((e.target as HTMLInputElement).value)} />
        </label>
      </form>
      {wishMode && <div class="section-title"><h2>⭐ Meine Wunschliste <small class="muted">{wishList.value.length}</small></h2></div>}
      {cards == null ? <div class="skeleton" style={{ height: "160px" }} />
        : !cards.length
          ? <div class="panel muted small">{wishMode ? "☆ Merken = Push bei neuem Banner oder wenn gezogen. Karten auf einer Banner-Seite oder hier über die Suche merken."
            : `Keine Karte „${q}“ in den laufenden Bannern.`}</div>
          : <div class="find">{cards.map((c) => (
            <div class="find-card">
              <button class="find-art" onClick={() => setSheet({ name: c.name, value: c.value || 0, image: c.image })}>
                <Img url={c.image} w={320} alt="" />
              </button>
              <div>
                <div class="row" style={{ alignItems: "flex-start" }}>
                  <b>{c.name}</b>
                  <button class={`star ${isWish(c.id) ? "on" : ""}`} onClick={() => toggleWish({ id: c.id, name: c.name, image: c.image })}>
                    {isWish(c.id) ? "⭐ Gemerkt" : "☆ Merken"}
                  </button>
                </div>
                {c.value != null && <div class="muted small tnum">{num(c.value)} Coins</div>}
                <div class="find-banners">
                  {c.banners.length ? c.banners.map((b) => (
                    <a class={`find-b ${b.out ? "out" : ""}`} href={`#/banner/${b.id}`}>
                      <span>{b.out ? "✅ raus · " : ""}<b>{b.title}</b> · {num(b.price)}{b.copies > 1 ? ` · ×${b.copies}` : ""}</span>
                      <span class="muted">{b.ev_pct != null ? <span class={`ev ${evTone(b.ev_pct)}`}>{pct(b.ev_pct)}</span> : null} · {num(b.remaining)} Packs</span>
                    </a>
                  )) : <div class="muted small">Gerade in keinem laufenden Banner.</div>}
                </div>
              </div>
            </div>
          ))}</div>}
      {sheet && <CardSheet card={sheet} onClose={() => setSheet(null)} />}
    </div>
  );
}
