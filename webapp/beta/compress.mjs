// Nach dem Build: gepackte Fassungen (.br, .gz) neben JS/CSS/HTML legen - der Server liefert sie automatisch aus
// (aiohttp FileResponse nimmt die passende Datei, wenn der Browser sie akzeptiert).
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(js|css|html|webmanifest|svg)$/.test(name)) {
      const data = readFileSync(path);
      writeFileSync(path + ".br", brotliCompressSync(data, { params: { [constants.BROTLI_PARAM_QUALITY]: 11 } }));
      writeFileSync(path + ".gz", gzipSync(data, { level: 9 }));
    }
  }
}
walk("dist");
