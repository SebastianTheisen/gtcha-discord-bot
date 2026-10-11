// Lesezeichen "An GTCHA Tracker" (Alles / Komplett übertragen) - lesbare Quelle.
// Läuft auf gtchaxonline.com, lädt die Verlaufsseiten in unsichtbaren Fenstern und schickt sie per Formular an
// <App>/api/import-form. Nach jeder Änderung: node webapp/bookmarklet/build.mjs (schreibt die verkleinerte Fassung
// in webapp/static/app.js und webapp/beta/src/bookmarklet.ts). Android schneidet eingefügte Lesezeichen nach
// 5000 Zeichen ab - der Test test_bookmarklet_fits_android prüft die Länge.
// Platzhalter (setzt die App beim Kopieren ein): GT_FULL, GT_ORIGIN, GT_TOKEN, GT_V
(async () => {
  if (!/gtchaxonline\.com$/.test(location.hostname)) { alert('Bitte auf gtchaxonline.com öffnen'); return }
  // die ersten vier sind Kartenseiten; Nr. 2-6 überträgt das Lesezeichen inkrementell (nur Neues)
  const P = 'undecided pending shipped downloaded'.split(' ').map(x => x + '-detail')
    .concat('buy-point-history purchase-history ticket-history change-member'.split(' '));
  const D = document, LS = 'gtchaTracker.marks', CARD = 'img[src*="/card/"]';
  let M = {};
  try { M = JSON.parse(localStorage.getItem(LS) || '{}') } catch (e) {}
  const FULL = GT_FULL || !M.at || Date.now() - M.at > 30 * 864e5;
  const NM = { at: FULL ? Date.now() : M.at }, T0 = Date.now();
  const text = d => d.body ? d.body.innerText : '';
  const lines = t => t.split('\n').map(l => l.trim()).filter(Boolean);
  const joined = t => lines(t).join('\n');
  // Merkmal des neuesten Eintrags: ab hier ist beim nächsten Mal alles bekannt
  const sig = t => {
    const L = lines(t), k = L.findIndex(l => /^\d{1,2}\.\d{1,2}\.\d{4}$/.test(l));
    if (k >= 0 && /^\d{1,2}:\d{2}/.test(L[k + 2] || '')) return L.slice(k + 1, k + 4).join('\n');
    const i = L.findIndex(l => /\d{2,4}\/\d{2}\/\d{2}/.test(l));
    return i < 0 ? '' : L.slice(i, i + 4).join('\n');
  };
  const box = D.createElement('div');
  box.style.cssText = 'position:fixed;z-index:9999999;inset:9px 9px auto;padding:12px;background:#1f3a6e;color:#fff;font:15px sans-serif;border-radius:9px';
  D.body.appendChild(box);
  const say = t => { box.textContent = 'GTCHA Tracker: ' + t };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const all = (d, s) => [...d.querySelectorAll(s)];
  const leaf = (d, s, f) => all(d, s).find(e => !e.children.length && f(e.textContent.trim()));
  const keep = /[¥￥円]|coin|münz|ausgaben|rang|rank|20\d{2}[\/.-]\d{1,2}[\/.-]\d{1,2}|^[\d.,]{1,9}$/i;
  // je Kartenbild Name, Bild und Kopfzeile der Gruppe (Datum, Sendungsnummer); die Bilderliste bildet der Server
  const per = d => all(d, CARD).slice(0, 800).map(im => {
    const b = im.closest('button,a,[aria-label]'), s = im.closest('section'), h = s && s.querySelector('header');
    return {
      src: im.getAttribute('src'), alt: im.alt, label: (b && b.getAttribute('aria-label')) || '',
      group: h ? h.textContent.replace(/\s+/g, ' ').trim() : '', showcase: !!im.closest('.c3d,.cshow'),
    };
  });
  const grab = (d, cp) => {
    const t = text(d), H = t.indexOf('HISTORY');
    return {
      cards: cp ? per(d) : undefined,
      text: (/member/.test(d.location.pathname) ? t.split('\n').filter(l => keep.test(l)).join('\n')
        : H >= 0 && /point/.test(d.location.pathname) ? t.slice(H) : t).slice(0, 40000),
    };
  };
  // wartet, bis sich der Seitentext nicht mehr ändert (Kartenseiten: bis Kartenbilder da sind)
  const settle = async (fr, want, calm = 6) => {
    for (let i = 0, last = '', same = 0; i < 80; i++) {
      await sleep(400);
      const d = fr.contentDocument, t = d ? text(d) : '';
      const ready = !want || i >= 38 || !!(d && (d.querySelector(CARD) || (i >= 10 && d.querySelector('.hpage'))));
      if (t && t === last && ready) { if (++same >= calm) return } else same = 0;
      last = t;
    }
  };
  const size = d => all(d, CARD).length * 1e6 + text(d).length;
  // nachladen ("Mehr anzeigen" / scrollen), bis zweimal nichts mehr dazukommt
  const more = async (d, stop) => {
    for (let k = 0, still = 0; k < 120 && still < 2; k++) {
      if (stop && stop()) return;
      const n = size(d), b = leaf(d, 'button,a,div,span', t => /^((mehr|weitere) anzeigen|show more|load more|もっと見る)$/i.test(t));
      if (b) b.click();
      try {
        d.defaultView.scrollTo(0, d.body.scrollHeight);
        all(d, 'main,.v-main,.contents').forEach(x => { x.scrollTop = x.scrollHeight });
      } catch (e) {}
      let grew = false;
      for (let w = 0; w < 5 && !grew; w++) { await sleep(300); grew = size(d) > n }
      still = grew ? 0 : still + 1;
    }
  };
  const changed = async (d, before, n, ms) => {
    for (let w = 0; w < n; w++) { await sleep(ms); if (text(d) !== before) return true }
  };
  const tab = async (fr, d, label) => {
    const b = leaf(d, 'button,a,li,div,span', t => t === label);
    if (!b) return;
    const before = text(d);
    b.click();
    await changed(d, before, 20, 400);
    await settle(fr, 1);
    return 1;
  };
  const isNum = x => !x.children.length && /^\d+$/.test(x.textContent.trim());
  const out = [];
  let done = 0, next = 0;
  const area = async (p, i) => {
    const fr = D.createElement('iframe'), cp = i < 4, inc = i > 1 && i < 7, ship = i == 2;
    // sichtbar (fast durchsichtig) - Android bremst Fenster außerhalb des Bildschirms aus
    fr.style.cssText = 'position:fixed;inset:0;width:420px;height:900px;opacity:.01;pointer-events:none;border:0;z-index:9999998';
    D.body.appendChild(fr);
    try {
      await new Promise(r => { fr.onload = r; fr.src = '/' + p });
      await settle(fr, cp);
      let d = fr.contentDocument;
      if (cp && d && !d.querySelector(CARD) && !d.querySelector('.hpage')) {
        await new Promise(r => { fr.onload = r; fr.contentWindow.location.reload() });
        await settle(fr, 1);
        d = fr.contentDocument;
      }
      if (!d || !d.body) { out[i] = { path: p, error: 'kein Zugriff' }; return }
      const mark = !FULL && inc && M[p];
      if ((cp || inc) && !(ship && !FULL)) await more(d, () => !!mark && joined(text(d)).includes(mark));
      const pages = [grab(d, cp)];
      if (ship && await tab(fr, d, 'Sendungen')) { await more(d); pages.push(grab(d, 1)) }
      const shipKey = c => c.group + '|' + c.label;
      const known = g => mark && (ship ? (g.cards || []).some(c => c.group && shipKey(c) === mark) : joined(g.text).includes(mark));
      const last = pages[pages.length - 1];
      if (inc) {
        const c = ship && (last.cards || []).find(c => c.group && !c.showcase);
        NM[p] = (ship ? c && shipKey(c) : sig(pages[0].text)) || M[p] || '';
      }
      let partial = !!known(last);
      // weitere Seiten über die Seitenzahlen
      for (let n = 2; n <= (FULL ? 5000 : 60) && !partial; n++) {
        const btn = all(d, 'a,button,li,span,div').find(e => isNum(e) && e.textContent.trim() == n
          && all(e.parentElement && e.parentElement.parentElement || e, '*').filter(isNum).length >= 3);
        if (!btn) break;
        const before = text(d);
        btn.click();
        if (!await changed(d, before, 30, 500)) break;
        await settle(fr, cp, 3);
        const g = grab(d, cp);
        pages.push(g);
        partial = !!known(g);
      }
      out[i] = { path: p, pages, partial };
    } finally { fr.remove(); say(++done + ' von ' + P.length + ' Bereichen geladen …') }
  };
  say('lade ' + P.length + ' Bereiche gleichzeitig …');
  await Promise.all([1, 2, 3, 4].map( async () => { while (next < P.length) { const i = next++; await area(P[i], i) } }));
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
  f.action = GT_ORIGIN + '/api/import-form';
  inp.type = 'hidden';
  inp.name = 'd';
  inp.value = JSON.stringify({ t: GT_TOKEN, v: GT_V, mid: MID, ms: Date.now() - T0, pages: out.filter(Boolean) });
  f.appendChild(inp);
  D.body.appendChild(f);
  f.submit();
})()
