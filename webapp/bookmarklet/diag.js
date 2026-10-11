// Lesezeichen "GTCHA Diagnose" (nur Admin, einmalig): liest mit, welche Abfragen die Seiten "Versendet",
// "Angefordert", "Münzen" usw. für den eingeloggten Nutzer stellen und wie die Antworten aufgebaut sind - damit
// "Alles übertragen" diese Daten künftig direkt lesen kann statt den sichtbaren Text. Es stellt selbst keine
// Abfragen, sondern öffnet die Seiten unsichtbar und schaut nur zu. Je Abfrage: Adresse, Parameter (ohne csrf),
// Feldnamen, Typen und EIN Beispiel-Eintrag; persönliche Felder (Name, Adresse, Telefon, Mail ...) werden entfernt.
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
  const params = b => {
    try {
      if (!b) return null;
      if (typeof b != 'string') return '(' + (b.constructor && b.constructor.name) + ')';
      const q = new URLSearchParams(b), o = {};
      for (const [k, v] of q) o[k] = /csrf/i.test(k) ? '(csrf)' : k == 'json' ? JSON.parse(v) : v.slice(0, 80);
      return Object.keys(o).length ? o : b.slice(0, 200);
    } catch (e) { return String(b).slice(0, 200) }
  };
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;z-index:9999999;inset:9px 9px auto;padding:12px;background:#6e1f5a;color:#fff;font:15px sans-serif;border-radius:9px';
  document.body.appendChild(box);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const pages = ['shipped-detail', 'pending-detail', 'undecided-detail', 'downloaded-detail', 'buy-point-history',
    'purchase-history', 'ticket-history'];
  const out = {};
  for (const p of pages) {
    box.textContent = 'GTCHA Diagnose: ' + p + ' …';
    const seen = out[p] = [];
    const fr = document.createElement('iframe');
    fr.style.cssText = 'position:fixed;inset:0;width:420px;height:900px;opacity:.01;pointer-events:none;border:0';
    document.body.appendChild(fr);
    // mitlesen: sobald das Fenster der Seite existiert, XMLHttpRequest und fetch beobachten (bevor die Seite lädt)
    const hook = w => {
      if (!w || w.__gtd) return;
      w.__gtd = 1;
      const X = w.XMLHttpRequest.prototype, open = X.open, send = X.send;
      X.open = function (m, u) { this.__m = m; this.__u = String(u); return open.apply(this, arguments) };
      X.send = function (b) {
        this.addEventListener('load', () => {
          if (!/\/api\//.test(this.__u) || seen.length > 12) return;
          let j = null;
          try { j = JSON.parse(this.responseText) } catch (e) {}
          seen.push({ method: this.__m, url: this.__u.replace(location.origin, ''), body: params(b), status: this.status,
            shape: shape(j), page: j && j.page, sample: clean(j && Array.isArray(j.list) ? j.list[0] : null) });
        });
        return send.apply(this, arguments);
      };
      const f = w.fetch;
      w.fetch = async (u, o) => {
        const r = await f(u, o), url = String(u && u.url || u);
        if (/\/api\//.test(url) && seen.length <= 12) {
          r.clone().json().then(j => seen.push({ method: (o && o.method) || 'GET', url: url.replace(location.origin, ''),
            body: params(o && o.body), status: r.status, shape: shape(j), page: j && j.page,
            sample: clean(j && Array.isArray(j.list) ? j.list[0] : null) })).catch(() => {});
        }
        return r;
      };
    };
    fr.src = '/' + p;
    for (let i = 0; i < 2000; i++) {   // bis zu 8 s auf das neue Fenster warten
      try { const w = fr.contentWindow; if (w && w.location.href.includes(p)) { hook(w); break } } catch (e) {}
      await sleep(4);
    }
    await sleep(9000);
    fr.remove();
  }
  box.textContent = 'GTCHA Diagnose: sende …';
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
