import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

// Beta läuft unter /beta (tailscale serve --set-path /beta) - alle Pfade relativ
export default defineConfig({
  base: "./",
  plugins: [preact()],
  build: { outDir: "dist", assetsDir: "assets", sourcemap: false, target: "es2022" },
  server: { proxy: { "/api": "http://127.0.0.1:8080", "/img": "http://127.0.0.1:8080" } },
});
