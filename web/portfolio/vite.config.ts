import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  build: {
    modulePreload: false,
    outDir: "dist",
    sourcemap: false,
  },
});
