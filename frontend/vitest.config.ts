import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
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
