// Lesezeichen "An GTCHA Tracker" (Alles / Komplett übertragen) - lesbare Quelle.
// Läuft auf gtchaxonline.com, lädt die Verlaufsseiten in unsichtbaren Fenstern und schickt sie per Formular an
// <App>/api/import-form. Ab v19 liest es die Daten mit, die die Seiten dabei selbst laden (Versendet, Angefordert,
// Münzen usw. als JSON mit IDs) - stellt dafür keine eigenen Abfragen; nur die Kontoseite geht weiter als Text
// (nur Zeilen mit Beträgen, Rang, Datum). Nach jeder Änderung: node webapp/bookmarklet/build.mjs (schreibt die verkleinerte Fassung
// in webapp/static/app.js und webapp/beta/src/bookmarklet.ts). Android schneidet eingefügte Lesezeichen nach
// 5000 Zeichen ab - der Test test_bookmarklet_fits_android prüft die Länge.
// Platzhalter (setzt die App beim Kopieren ein): GT_FULL, GT_ORIGIN, GT_TOKEN, GT_V
(async () => {
  if (!/gtchaxonline\.com$/.test(location.hostname)) { alert('Bitte auf gtchaxonline.com öffnen'); return }
  // die ersten vier sind Kartenseiten; nur der Münzverlauf (Nr. 4) wird inkrementell übertragen (nur Neues)
  const P = 'undecided pending shipped downloaded'.split(' ').map(x => x + '-detail')
    .concat('buy-point-history purchase-history ticket-history change-member'.split(' '));
  const D = document, LS = 'gtchaTracker.marks';
  let M = {};
  try { M = JSON.parse(localStorage.getItem(LS) || '{}') } catch (e) {}
  // ab v19 merkt es sich die ID der neuesten Münzbuchung - ältere Merker (Text): einmal alles übertragen
  const FULL = GT_FULL || !M.at || Date.now() - M.at > 30 * 864e5 || !+M['buy-point-history'];
  const NM = { at: FULL ? Date.now() : M.at }, T0 = Date.now();
  const text = d => d.body ? d.body.innerText : '';
  const box = D.createElement('div');
  box.style.cssText = 'position:fixed;z-index:9999999;inset:9px 9px auto;padding:12px;background:#1f3a6e;color:#fff;font:15px sans-serif;border-radius:9px';
  D.body.appendChild(box);
  const say = t => { box.textContent = 'GTCHA Tracker: ' + t };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const all = (d, s) => [...d.querySelectorAll(s)];
  const keep = /[¥￥円]|coin|münz|ausgaben|rang|rank|20\d{2}[\/.-]\d{1,2}[\/.-]\d{1,2}|^[\d.,]{1,9}$/i;
  // Verlaufs-Daten, die die Seite lädt (nicht Konto- oder Punkte-Abfragen mit Name/Adresse)
  const DATA = /\/api\/user\/(history|point\/list|rewards\/history|purchase_list)\//;
  // mitlesen: sobald das Fenster der Seite existiert, ihre Abfragen beobachten (bevor die Seite lädt)
  const hook = async (fr, p, cap) => {
    for (let i = 0; i < 2000; i++) {
      try {
        const w = fr.contentWindow;
        if (w && w.location.href.includes(p) && !w.__gt) {
          w.__gt = 1;
          const X = w.XMLHttpRequest.prototype, open = X.open, send = X.send;
          X.open = function (m, u) { this.__u = String(u); return open.apply(this, arguments) };
          X.send = function () {
            this.addEventListener('load', () => {
              if (DATA.test(this.__u)) try { cap.push(JSON.parse(this.responseText)) } catch (e) {}
            });
            return send.apply(this, arguments);
          };
          const f = w.fetch;
          w.fetch = async (u, o) => {
            const r = await f(u, o);
            if (DATA.test(String(u && u.url || u))) r.clone().json().then(j => cap.push(j)).catch(() => {});
            return r;
          };
          return;
        }
      } catch (e) {}
      await sleep(4);
    }
  };
  const load = (fr, p, cap, go) => Promise.all([new Promise(r => { fr.onload = r; go() }), hook(fr, p, cap)]);
  // wartet, bis die Seite ihre Daten geladen hat und der Text sich nicht mehr ändert
  const settle = async (fr, cap, want, calm = 5) => {
    for (let i = 0, last = '', same = 0; i < 80; i++) {
      await sleep(400);
      const d = fr.contentDocument, t = d ? text(d) : '';
      if (t && t === last && (!want || cap.length || i >= 38)) { if (++same >= calm) return } else same = 0;
      last = t;
    }
  };
  const ids = j => (Array.isArray(j && j.list) ? j.list : []).map(x => x && x.id);
  const isNum = x => !x.children.length && /^\d+$/.test(x.textContent.trim());
  const out = [];
  let done = 0, next = 0;
  const area = async (p, i) => {
    const fr = D.createElement('iframe'), cap = [], coins = i == 4, member = i == 7;
    // sichtbar (fast durchsichtig) - Android bremst Fenster außerhalb des Bildschirms aus
    fr.style.cssText = 'position:fixed;inset:0;width:420px;height:900px;opacity:.01;pointer-events:none;border:0;z-index:9999998';
    D.body.appendChild(fr);
    try {
      await load(fr, p, cap, () => { fr.src = '/' + p });
      await settle(fr, cap, !member);
      if (!member && !cap.length) {   // Seite hat noch nichts geladen: einmal neu laden
        await load(fr, p, cap, () => fr.contentWindow.location.reload());
        await settle(fr, cap, 1);
      }
      const d = fr.contentDocument;
      if (!d || !d.body) { out[i] = { path: p, error: 'kein Zugriff' }; return }
      const mark = !FULL && coins && M[p];
      if (coins) NM[p] = ids(cap[0])[0] || M[p] || '';
      const known = () => !!mark && cap.some(j => ids(j).includes(mark));
      // weitere Seiten über die Seitenzahlen (die Seite lädt dabei die nächsten Einträge)
      for (let n = 2; n <= (FULL ? 5000 : 60) && !known(); n++) {
        const btn = all(d, 'a,button,li,span,div').find(e => isNum(e) && e.textContent.trim() == n
          && all(e.parentElement && e.parentElement.parentElement || e, '*').filter(isNum).length >= 3);
        if (!btn) break;
        const had = cap.length;
        btn.click();
        for (let w = 0; w < 60 && cap.length == had; w++) await sleep(250);
        if (cap.length == had) break;
        if (coins) say('Münzen: Seite ' + n + ' …');
      }
      const t = text(d);
      out[i] = { path: p, json: cap, partial: known(),
        pages: [{ text: (member ? t.split('\n').filter(l => keep.test(l)).join('\n') : cap.length ? '' : t).slice(0, 40000) }] };
    } finally { fr.remove(); say(++done + ' von ' + P.length + ' Bereichen geladen …') }
  };
  say('lade ' + P.length + ' Bereiche gleichzeitig …');
  await Promise.all([1, 2, 3, 4].map(async () => { while (next < P.length) { const i = next++; await area(P[i], i) } }));
  say('sende …');
  try { localStorage.setItem(LS, JSON.stringify(NM)) } catch (e) {}
  // GTCHA-Mitglieds-ID ordnet den Verlauf dem richtigen Konto zu (Name, Adresse usw. werden nicht übertragen)
  let MID = null;
  try {
    const j = await (await fetch('/api/user/detail', { credentials: 'include' })).json(), v = j.detail;
    MID = String(v.id || v.member_id || v.user_id || '') || null;
  } catch (e) {}
  const f = D.createElement('form'), inp = D.createElement('input');
  f.method = 'POST';
  f.acceptCharset = 'UTF-8';
  f.action = GT_ORIGIN + '/api/import-form';
  inp.type = 'hidden';
  inp.name = 'd';
  inp.value = JSON.stringify({ t: GT_TOKEN, v: GT_V, mid: MID, ms: Date.now() - T0, pages: out.filter(Boolean) });
  f.appendChild(inp);
  D.body.appendChild(f);
  f.submit();
})()
