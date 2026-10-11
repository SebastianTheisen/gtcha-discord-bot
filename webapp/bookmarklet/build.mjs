// Verkleinert webapp/bookmarklet/sync.js und schreibt das Ergebnis als SYNC_CODE in die Live-App und die Beta.
//   node webapp/bookmarklet/build.mjs           schreibt beide Dateien
//   node webapp/bookmarklet/build.mjs --check   bricht ab, wenn die eingebaute Fassung veraltet ist
// Der Verkleinerer kommt aus den Abhängigkeiten der Beta (cd webapp/beta && npm ci).
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const webapp = path.dirname(here);
const require = createRequire(path.join(webapp, "beta", "package.json"));
const { minifySync } = await import(require.resolve("rolldown/experimental"));

const src = fs.readFileSync(path.join(here, "sync.js"), "utf8");
const { code, errors } = minifySync("sync.js", src, { compress: true, mangle: { toplevel: true } });
if (errors && errors.length) throw new Error(errors.map((e) => e.message).join("\n"));
// Zeilenumbrüche stehen nur noch in Zeichenketten (`...`): als \n schreiben, ein Lesezeichen ist eine Zeile.
// "%" würde der Browser in javascript:-Adressen als Kodierung lesen.
const minified = code.trim().replace(/;$/, "").replace(/\n/g, "\\n");
if (minified.includes("%")) throw new Error("% im Lesezeichen");
const line = `const SYNC_CODE = ${JSON.stringify(minified)};`;

let stale = false;
for (const file of [path.join(webapp, "static", "app.js"), path.join(webapp, "beta", "src", "bookmarklet.ts")]) {
  const old = fs.readFileSync(file, "utf8");
  const updated = old.replace(/^const SYNC_CODE = .*;$/m, () => line);
  if (updated === old) continue;
  stale = true;
  if (!process.argv.includes("--check")) fs.writeFileSync(file, updated);
}
if (process.argv.includes("--check") && stale) {
  console.error("SYNC_CODE ist veraltet: node webapp/bookmarklet/build.mjs ausführen");
  process.exit(1);
}
console.log(`Lesezeichen: ${minified.length} Zeichen (ohne Schlüssel und Adresse)`);
