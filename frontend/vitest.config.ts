import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  // Components use the automatic JSX runtime (Next.js convention) and do not
  // import React explicitly, so transform JSX with the automatic runtime here.
  esbuild: {
    jsx: "automatic",
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
  },
  resolve: {
    alias: {
      // Mirror the tsconfig "@/*": ["./*"] mapping (frontend root).
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
});
