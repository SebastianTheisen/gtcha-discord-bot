// Verkleinert webapp/bookmarklet/sync.js (SYNC_CODE) und diag.js (DIAG_CODE) und schreibt sie in Live-App und Beta.
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

function build(name) {
  const src = fs.readFileSync(path.join(here, name), "utf8");
  const { code, errors } = minifySync(name, src, { compress: true, mangle: { toplevel: true } });
  if (errors && errors.length) throw new Error(errors.map((e) => e.message).join("\n"));
  // Zeilenumbrüche stehen nur noch in Zeichenketten (`...`): als \n schreiben, ein Lesezeichen ist eine Zeile.
  // "%" würde der Browser in javascript:-Adressen als Kodierung lesen.
  const minified = code.trim().replace(/;$/, "").replace(/\n/g, "\\n");
  if (minified.includes("%")) throw new Error(`% im Lesezeichen ${name}`);
  return minified;
}

const parts = { SYNC_CODE: build("sync.js"), DIAG_CODE: build("diag.js") };
let stale = false;
for (const file of [path.join(webapp, "static", "app.js"), path.join(webapp, "beta", "src", "bookmarklet.ts")]) {
  const old = fs.readFileSync(file, "utf8");
  let updated = old;
  for (const [name, code] of Object.entries(parts)) {
    const line = `const ${name} = ${JSON.stringify(code)};`;
    if (!new RegExp(`^(export )?const ${name} = .*;$`, "m").test(updated)) throw new Error(`${name} fehlt in ${file}`);
    updated = updated.replace(new RegExp(`^(export )?const ${name} = .*;$`, "m"), (m, ex) => (ex || "") + line);
  }
  if (updated === old) continue;
  stale = true;
  if (!process.argv.includes("--check")) fs.writeFileSync(file, updated);
}
if (process.argv.includes("--check") && stale) {
  console.error("SYNC_CODE/DIAG_CODE veraltet: node webapp/bookmarklet/build.mjs ausführen");
  process.exit(1);
}
console.log(`Lesezeichen: ${parts.SYNC_CODE.length} Zeichen, Diagnose: ${parts.DIAG_CODE.length} (ohne Schlüssel und Adresse)`);
