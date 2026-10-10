// Lesezeichen "An GTCHA Tracker": unverändert aus der Live-App übernommen (gleiche SYNC_VERSION), damit beide Apps
// dasselbe Lesezeichen erzeugen. Es läuft auf gtchaxonline.com und schickt an <Ursprung>/api/import-form.
const SYNC_PAGES = ["undecided-detail", "pending-detail", "shipped-detail", "downloaded-detail",
                    "buy-point-history", "purchase-history", "ticket-history", "change-member"];
export const SYNC_VERSION = 7;    // mit BOOKMARKLET_VERSION in webapp/server.py und app.js der Live-App erhöhen
const SYNC_PARALLEL = 4;
const SYNC_INCREMENTAL = ["buy-point-history", "shipped-detail", "ticket-history", "purchase-history", "downloaded-detail"];
export function bookmarkletSync(token: string, full = false): string {
  const src = `(async()=>{
if(!/gtchaxonline\\.com$/.test(location.hostname)){alert('Bitte auf gtchaxonline.com öffnen');return}
const P=${JSON.stringify(SYNC_PAGES)};const INC=${JSON.stringify(SYNC_INCREMENTAL)};const LS='gtchaTracker.marks';
let M={};try{M=JSON.parse(localStorage.getItem(LS)||'{}')}catch(e){}
const FULL=${full ? "true" : "false"}||!M.at||Date.now()-M.at>30*864e5;
const lines=t=>t.split('\\n').map(l=>l.trim()).filter(Boolean);
const sig=t=>{const L=lines(t);const i=L.findIndex(l=>/\\d{2,4}\\/\\d{2}\\/\\d{2}/.test(l));return i<0?'':L.slice(i,i+4).join('\\n')};
const NM={at:FULL?Date.now():M.at};const PAR=${SYNC_PARALLEL};const T0=Date.now();
const box=document.createElement('div');box.style.cssText='position:fixed;z-index:2147483647;left:10px;right:10px;top:10px;padding:12px;background:#1f3a6e;color:#fff;font:15px sans-serif;border-radius:10px';document.body.appendChild(box);
const say=t=>{box.textContent='GTCHA Tracker: '+t};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const keep=/[¥￥円]|coin|münz|ausgaben|rang|rank|20\\d{2}[\\/.-]\\d{1,2}[\\/.-]\\d{1,2}|^[\\d.,]{1,9}$/i;
const grab=d=>({text:(d.location.pathname.includes('change-member')?d.body.innerText.split('\\n').filter(l=>keep.test(l)).join('\\n'):d.body.innerText).slice(0,40000),images:[...d.querySelectorAll('img')].map(i=>i.getAttribute('src')).filter(s=>s&&s.includes('/card/')).slice(0,400)});
const CP=['undecided-detail','pending-detail','shipped-detail','downloaded-detail'];const settle=async(fr,want)=>{let last='',same=0;for(let i=0;i<80;i++){await sleep(400);const d=fr.contentDocument;const t=d&&d.body?d.body.innerText:'';const ready=!want||i>=38||!!(d&&d.querySelector('img[src*="/card/"]'));if(t&&t===last&&ready){if(++same>=6)return}else same=0;last=t}};
const isNum=x=>x.children.length===0&&/^\\d+$/.test(x.textContent.trim());
const out=[];let done=0;
const area=async(p,i)=>{
const fr=document.createElement('iframe');fr.style.cssText='position:fixed;left:-3000px;top:0;width:420px;height:900px';document.body.appendChild(fr);
try{await new Promise(r=>{fr.onload=r;fr.src='/'+p});await settle(fr,CP.includes(p));
const d=fr.contentDocument;if(!d||!d.body){out[i]={path:p,error:'kein Zugriff'};return}
const pages=[grab(d)];const mark=!FULL&&INC.includes(p)&&M[p];let partial=false;
const known=g=>mark&&lines(g.text).join('\\n').includes(mark);
if(INC.includes(p))NM[p]=sig(pages[0].text)||M[p]||'';
if(known(pages[0]))partial=true;
for(let n=2;n<=40&&!partial;n++){
const btn=[...d.querySelectorAll('a,button,li,span,div')].find(e=>isNum(e)&&e.textContent.trim()===String(n)&&[...((e.parentElement&&e.parentElement.parentElement)||e).querySelectorAll('*')].filter(isNum).length>=3);
if(!btn)break;const before=d.body.innerText;btn.click();
let g=null;for(let w=0;w<30;w++){await sleep(500);if(d.body.innerText!==before){await settle(fr,CP.includes(p));g=grab(d);break}}
if(!g)break;pages.push(g);if(known(g))partial=true}
out[i]={path:p,pages,partial}}finally{fr.remove();say((++done)+' von '+P.length+' Bereichen geladen …')}};
say('lade '+P.length+' Bereiche gleichzeitig …');
let next=0;await Promise.all(Array.from({length:PAR},async()=>{while(next<P.length){const i=next++;await area(P[i],i)}}));
say('sende …');try{localStorage.setItem(LS,JSON.stringify(NM))}catch(e){}
let ACC=null,AS=null,MID=null;try{const r=await fetch('/api/user/detail',{credentials:'include'});const j=await r.json();const v=j&&j.detail&&(j.detail.id||j.detail.member_id||j.detail.user_id);if(v){MID=String(v);AS='api'}}catch(e){}
if(!MID){const m=(document.body.textContent||'').match(/(?:Mitglieds-ID|Member ID|会員ID|会員番号)\\s*[:：]?\\s*([0-9]{4,})/i);if(m){MID=m[1];AS='text'}}
try{if(MID){const h=await crypto.subtle.digest('SHA-256',new TextEncoder().encode('gtcha-tracker:'+MID));ACC=[...new Uint8Array(h)].slice(0,8).map(b=>b.toString(16).padStart(2,'0')).join('')}}catch(e){AS='hash-fehler'}
const f=document.createElement('form');f.method='POST';f.action=${JSON.stringify(location.origin)}+'/api/import-form';
const i=document.createElement('input');i.type='hidden';i.name='d';i.value=JSON.stringify({t:${JSON.stringify(token)},v:${SYNC_VERSION},mid:MID,acc:ACC,accsrc:AS,at:new Date().toISOString(),ms:Date.now()-T0,pages:out.filter(Boolean)});
f.appendChild(i);document.body.appendChild(f);f.submit()})()`;
  return "javascript:" + src.replace(/\n/g, "");
}
