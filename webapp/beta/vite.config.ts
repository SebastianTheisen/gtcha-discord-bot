import { defineConfig, type Plugin } from "vite";
import preact from "@preact/preset-vite";

// Kennung dieses Builds: steckt im Code und in version.json - weichen beide ab, gibt es eine neue Version
const BUILD_ID = new Date().toISOString();

const versionFile = (): Plugin => ({
  name: "version-file",
  generateBundle() {
    this.emitFile({ type: "asset", fileName: "version.json", source: JSON.stringify({ build: BUILD_ID }) });
  },
});

// Beta läuft unter /beta (tailscale serve --set-path /beta) - alle Pfade relativ
export default defineConfig({
  base: "./",
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  plugins: [preact(), versionFile()],
  build: { outDir: "dist", assetsDir: "assets", sourcemap: false, target: "es2022" },
  server: { proxy: { "/api": "http://127.0.0.1:8080", "/img": "http://127.0.0.1:8080" } },
});
