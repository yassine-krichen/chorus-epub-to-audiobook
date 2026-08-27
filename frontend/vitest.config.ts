import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

const dirname = path.dirname(fileURLToPath(import.meta.url));

// Pure-logic unit tests only (no React components/e2e yet), so the default
// "node" environment is enough — jsdom isn't needed.
export default defineConfig({
  test: {
    environment: "node",
    include: ["**/*.test.ts"],
    exclude: ["node_modules", ".next"],
  },
  resolve: {
    alias: {
      // Mirrors tsconfig.json's "@/*": ["./*"] path alias.
      "@": dirname,
      // The real "server-only" package throws when imported outside a
      // bundler's "react-server" condition, which plain Node/Vitest never
      // sets. Swap in a no-op so server-only source files stay importable.
      "server-only": path.resolve(dirname, "test/stubs/server-only.ts"),
    },
  },
});
