// Flat-config ESLint for the Research & Trust Console SPA (Project E1).
// The JS-side analog of the repo's `ruff` gate (METHODOLOGY §19): typescript-eslint
// for type-aware correctness, react-hooks for hook rules, jsx-a11y for accessibility
// (Project E honesty + the web a11y rules), react-refresh for Vite HMR safety.
// `eslint-config-prettier` is applied LAST so formatting is owned by Prettier, not ESLint.
//
// Scope: application + test source under src/, plus the JS tooling files (this config,
// postcss.config.js, scripts/*.mjs). The root TypeScript config files (vite.config.ts,
// tailwind.config.ts) are intentionally out of scope here — they are already
// type-checked by `tsc -b` via tsconfig.node.json.
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import jsxA11y from "eslint-plugin-jsx-a11y";
import prettier from "eslint-config-prettier";

export default tseslint.config(
  // Never lint build output, deps, copied data, coverage, or the TS config files.
  {
    ignores: [
      "dist",
      "public",
      "node_modules",
      "coverage",
      "*.config.ts",
      "*.tsbuildinfo",
    ],
  },

  // Application + test source: browser TS/TSX.
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.es2022 },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.flatConfigs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      // Allow intentionally-unused args/vars when prefixed with `_`.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },

  // Vitest test files also get the jsdom/node test globals.
  {
    files: ["src/test/**/*.{ts,tsx}", "src/**/*.test.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.node, vi: "readonly" },
    },
  },

  // JS tooling: this config, postcss.config.js, scripts/*.mjs (ESM, node globals).
  {
    files: ["scripts/**/*.{js,mjs}", "*.{js,mjs}"],
    extends: [js.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.node },
    },
  },

  // Keep ESLint out of formatting's lane — Prettier owns it.
  prettier,
);
