// Lesezeichen "GTCHA Diagnose" (nur Admin, einmalig): Aufbau der Verlaufs-Daten, die die Seiten "Versendet",
// "Angefordert" usw. für den eingeloggten Nutzer laden - damit "Alles übertragen" sie künftig direkt lesen kann
// statt den sichtbaren Text. Schickt je Bereich Feldnamen, Typen und EINEN Beispiel-Eintrag; persönliche Felder
// (Name, Adresse, Telefon, Mail ...) werden vorher entfernt. Ändert nichts auf der Seite.
// Platzhalter: GT_ORIGIN, GT_TOKEN. Verkleinert durch build.mjs (DIAG_CODE).
(async () => {
  if (!/gtchaxonline\.com$/.test(location.hostname)) { alert('Bitte auf gtchaxonline.com öffnen'); return }
  const SECRET = /mail|tel|phone|addr|zip|post|pref|city|shiku|build|first|last|middle|kana|birth|bank|account|card_no|token|csrf|pass/i;
  const shape = (v, d = 0) => d > 4 ? '…' : Array.isArray(v) ? (v.length ? [shape(v[0], d + 1)] : [])
    : v && typeof v == 'object' ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, shape(x, d + 1)]))
      : v === null ? 'null' : typeof v;
  const clean = (v, d = 0) => d > 4 ? '…' : Array.isArray(v) ? v.slice(0, 2).map(x => clean(x, d + 1))
    : v && typeof v == 'object' ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, SECRET.test(k) ? '(entfernt)' : clean(x, d + 1)]))
      : typeof v == 'string' ? v.slice(0, 80) : v;
  const areas = ['history/shipped/1', 'history/pending/1', 'history/undecided/1', 'history/downloaded/1',
    'purchase_list/history/list/1', 'rewards/history/1'];
  const out = {};
  for (const a of areas) {
    try {
      const r = await fetch('/api/user/' + a, { credentials: 'include', headers: { Accept: 'application/json' } });
      const j = await r.json().catch(() => null);
      out[a] = { status: r.status, shape: shape(j), page: j && j.page, sample: clean(j && Array.isArray(j.list) ? j.list[0] : null) };
    } catch (e) { out[a] = { error: String(e).slice(0, 120) } }
  }
  const f = document.createElement('form'), i = document.createElement('input');
  f.method = 'POST';
  f.action = GT_ORIGIN + '/api/import-diag';
  i.type = 'hidden';
  i.name = 'd';
  i.value = JSON.stringify({ t: GT_TOKEN, areas: out });
  f.appendChild(i);
  document.body.appendChild(f);
  f.submit();
})()
