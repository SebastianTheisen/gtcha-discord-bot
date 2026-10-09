import { useState } from "preact/hooks";
import { authApi } from "../api";
import { cardName, num, pct } from "../format";
import { buyHref, isWish } from "../local";
import { cardCode, marketLinks } from "../links";
import { toggleWish } from "../push";
import { me } from "../store";
import type { Hit } from "../types";
import { ask, haptic, showToast } from "../ui";
import { Img } from "./Img";
import { Sheet } from "./Sheet";

export interface CardInfo {
  id?: string;
  name: string;
  value: number;
  image?: string;
  tier?: string;
  note?: string | null;
  copies?: number;
  hit?: boolean;
  model?: string | null;
  rarity?: string | null;
}

// Der Bot arbeitet Meldungen alle paar Sekunden ab - so lange auf das Ergebnis warten
export async function waitForBot(id: number, done: () => void) {
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 1500));
    const r = await authApi<{ status: string; reason?: string }>(`api/medal/${id}`);
    if (r.status === "ok") {
      haptic();
      showToast("✅ Übernommen");
      done();
      return;
    }
    if (r.status === "rejected") {
      await ask("Nicht übernommen", r.reason || "", ["OK"]);
      done();
      return;
    }
  }
  await ask("Gesendet", "Der Bot hat noch nicht geantwortet – die Meldung wird gleich verarbeitet.", ["OK"]);
  done();
}

interface Props {
  card: CardInfo;
  price?: number;
  category?: string;
  packId?: number;
  units?: Hit[];          // meldbare Exemplare dieser Karte (aus der Hit-Liste), nach Platz
  archived?: boolean;
  onClose: () => void;
  onChanged?: () => void;
}

// Karte groß: Wert, Melden/Zurücknehmen (Medaille), Admin-Abhaken, Merken, Absprung zu Marktplätzen
export function CardSheet({ card, price, category, packId, units = [], archived, onClose, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const user = me.value;
  // frei = ohne Medaille: zuerst noch offene Exemplare, sonst automatisch erkannte (nachträglich beanspruchen)
  const open = units.find((u) => !u.medal_user && u.state !== "pulled");
  const free = open || units.find((u) => !u.medal_user && u.origin?.via !== "admin");
  const own = user ? units.find((u) => u.medal_user === String(user.user_id)) : undefined;
  const marked = units.find((u) => u.origin?.via === "admin");
  const canReport = !!packId && !archived && units.length > 0;

  async function send(path: string, payload: Record<string, unknown>, question: string) {
    if (await ask(question, <>{card.name} · {num(card.value)} Coins</>, ["Abbrechen", "Ja"]) !== "Ja") return;
    setBusy(true);
    haptic();
    try {
      const { id } = await authApi<{ id: number }>(path, payload);
      onClose();
      await waitForBot(id, () => onChanged?.());
    } catch (e) {
      await ask("Fehler", (e as Error).message, ["OK"]);
    } finally {
      setBusy(false);
    }
  }

  const wishId = card.id;
  return (
    <Sheet onClose={onClose}>
      <div class="card-big">
        {card.image ? <Img url={card.image} w={320} alt="" eager /> : <div class="ph" />}
        <div>
          {(units.length ? units.map((u) => u.tier).join(" · ") : card.tier) && (
            <div class="pill" style={{ color: "var(--accent)" }}>{units.length ? units.map((u) => u.tier).join(" · ") : card.tier}</div>
          )}
          <h3 style={{ margin: "6px 0" }}>{cardName(card.name)}</h3>
          {(card.model || card.rarity) && <div class="muted small">{[cardCode(card.model)?.text, card.rarity].filter(Boolean).join(" · ")}</div>}
          <div style={{ fontSize: "22px", fontWeight: 800 }} class="tnum">{num(card.value)} Coins</div>
          {price ? <div class="muted small">{pct((card.value / price) * 100, 0)} vom Packpreis</div> : null}
          {card.copies && card.copies > 1 ? <div class="muted small">{card.copies}× im Banner</div> : null}
          {card.hit && <div class="muted small">Versand nur ✈ (nicht umwandelbar)</div>}
          {card.note && <div class="small" style={{ marginTop: "6px" }}>{card.note}</div>}
        </div>
      </div>

      <div class="actions">
        {canReport && !user && <a class="btn primary block" href="#/me" onClick={onClose}>Zum Melden mit Discord verknüpfen</a>}
        {canReport && user && free && (
          <button class="btn primary block" disabled={busy}
            onClick={() => send("api/medal", { pack_id: packId, tier: free.tier, action: "claim" },
              free.state === "pulled" ? `${free.tier} wurde automatisch erkannt – nachträglich als von dir gezogen melden?`
                : `${free.tier} als von dir gezogen melden?`)}>
            🏅 {free.tier} {free.state === "pulled" ? "für mich beanspruchen" : "melden"}
          </button>
        )}
        {canReport && user && own && (
          <button class="btn block" disabled={busy}
            onClick={() => send("api/medal", { pack_id: packId, tier: own.tier, action: "unclaim" }, `${own.tier} zurücknehmen?`)}>
            ↩️ {own.tier} zurücknehmen
          </button>
        )}
        {canReport && user?.admin && open && (
          <button class="btn block" disabled={busy}
            onClick={() => send("api/admin/medal", { pack_id: packId, tier: open.tier, action: "mark" }, `${open.tier} ohne Person abhaken?`)}>
            🛠️ {open.tier} abhaken (ohne Person)
          </button>
        )}
        {canReport && user?.admin && marked && (
          <button class="btn block" disabled={busy}
            onClick={() => send("api/admin/medal", { pack_id: packId, tier: marked.tier, action: "remove" }, `Abhaken von ${marked.tier} aufheben?`)}>
            🛠️ {marked.tier} Abhaken aufheben
          </button>
        )}
        {canReport && user && !free && !own && !(user.admin && marked) && (
          <div class="muted small">Alle Exemplare dieser Karte sind schon gemeldet oder als gezogen erkannt.</div>
        )}
        {!units.length && packId && !archived && price ? (
          <div class="muted small">{card.value < price ? "Unter dem Packpreis – kann nicht gemeldet werden." : "Keine meldbare Karte."}</div>
        ) : null}
        {wishId && (
          <button class={`btn block ${isWish(wishId) ? "on" : ""}`}
            onClick={() => toggleWish({ id: wishId, name: card.name, image: card.image }).then(() => showToast(isWish(wishId) ? "⭐ Gemerkt" : "Entfernt"))}>
            {isWish(wishId) ? "⭐ Gemerkt – nicht mehr merken" : "☆ Merken (Push bei neuem Banner)"}
          </button>
        )}
      </div>

      <div class="links">
        {marketLinks(card.name, category, card.model).map((l) => (
          // in der installierten App im echten Browser öffnen (wie „Öffnen ↗“) - im eingebetteten Fenster lädt z. B.
          // Cardmarket seine Bilder nicht
          <a href={buyHref(l.url)} target="_blank" rel="noopener noreferrer">
            <span>{l.label} <span class="muted small" style={{ fontWeight: 400 }}>· {l.hint}</span></span>
            <span>↗</span>
          </a>
        ))}
      </div>
      <p class="muted small">Suche über den Kartennamen – bei japanischen oder Sonder-Karten bitte das Ergebnis prüfen.</p>
    </Sheet>
  );
}
