import { useEffect, useState } from "preact/hooks";
import { currentSubscription, inboxApi, markRead, setBell, type InboxItem } from "../push";
import { haptic } from "../ui";

function when(t: string) {
  const d = new Date(t.replace(" ", "T") + "Z");   // Server: UTC
  return { day: d.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" }),
    time: d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) };
}

// Glocke: Verlauf der Pushes dieses Geräts (30 Tage)
export function Inbox() {
  const [hasSub, setHasSub] = useState<boolean | null>(null);
  const [items, setItems] = useState<InboxItem[]>([]);
  const [more, setMore] = useState(false);
  const [count, setCount] = useState(0);

  useEffect(() => {
    currentSubscription().catch(() => null).then(async (sub) => {
      setHasSub(!!sub);
      if (!sub) return;
      const res = await inboxApi<{ items: InboxItem[]; more: boolean; unread: number }>("api/push/inbox", { limit: 30 });
      if (!res) return;
      setItems(res.items);
      setMore(res.more);
      setCount(res.unread);
      setBell(res.unread);
    });
  }, []);

  if (hasSub === null) return <div class="skeleton" style={{ height: "200px" }} />;
  if (!hasSub) {
    return <div><div class="section-title"><h2>🔔 Benachrichtigungen</h2></div>
      <div class="panel"><p class="muted small" style={{ marginTop: 0 }}>Pushes sind auf diesem Gerät aus.</p><a class="btn primary" href="#/me">Einschalten</a></div></div>;
  }

  const groups: { day: string; items: InboxItem[] }[] = [];
  for (const n of items) {
    const d = when(n.t).day;
    if (!groups.length || groups[groups.length - 1].day !== d) groups.push({ day: d, items: [] });
    groups[groups.length - 1].items.push(n);
  }

  return (
    <div>
      <div class="section-title"><h2>🔔 Benachrichtigungen</h2>
        {count ? <button class="btn" onClick={async () => { haptic(); await markRead(null); setItems(items.map((n) => ({ ...n, read: true }))); setCount(0); }}>✓ Alle gelesen</button> : null}
      </div>
      <p class="muted small">{count ? `${count} ungelesen` : "Alles gelesen"} · letzte 30 Tage</p>
      {groups.length ? groups.map((g) => (
        <>
          <div class="muted small" style={{ margin: "14px 4px 6px", fontWeight: 700 }}>{g.day}</div>
          <div class="rows">{g.items.map((n) => (
            <a class={`line inbox ${n.read ? "" : "unread"}`} href={n.banner_id ? `#/banner/${n.banner_id}` : "#/inbox"}
              onClick={() => { if (!n.read) markRead([n.id]); }}>
              <span><b>{n.title}</b>{n.body ? <><br /><span class="muted small">{n.body}</span></> : null}</span>
              <span class="muted small">{when(n.t).time}</span>
            </a>
          ))}</div>
        </>
      )) : <div class="empty">Keine Benachrichtigungen.</div>}
      {more && <button class="btn block" style={{ marginTop: "12px" }} onClick={async () => {
        const res = await inboxApi<{ items: InboxItem[]; more: boolean; unread: number }>("api/push/inbox", { limit: 30, before: items[items.length - 1].id });
        if (res) { setItems(items.concat(res.items)); setMore(res.more); setCount(res.unread); }
      }}>Ältere laden</button>}
    </div>
  );
}
