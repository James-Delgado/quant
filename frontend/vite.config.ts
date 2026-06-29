import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

function gitShortSha(): string {
  try {
    return execSync("git rev-parse --short HEAD", { encoding: "utf8" }).trim();
  } catch {
    return "dev";
  }
}

// App version from package.json (not hardcoded) — embedded in the build and
// auto-captured by the Report-an-issue modal (E1-M6). Read via fs so tsc needs
// no resolveJsonModule and the value tracks the manifest.
function appVersion(): string {
  try {
    const pkg = JSON.parse(
      readFileSync(path.resolve(__dirname, "package.json"), "utf8"),
    ) as { version?: string };
    return typeof pkg.version === "string" ? pkg.version : "0.0.0";
  } catch {
    return "0.0.0";
  }
}

// Relative base so the static build works when served from any sub-path or
// opened directly from `dist/`. The data client fetches `${BASE_URL}data/*.json`.
export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  define: {
    __BUILD_SHA__: JSON.stringify(gitShortSha()),
    __APP_VERSION__: JSON.stringify(appVersion()),
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
  },
});
