import type { Config } from "tailwindcss";

/**
 * Tailwind theme bound to the frozen "research instrument" design tokens.
 * Colors/fonts resolve to the CSS variables defined in `src/styles/tokens.css`
 * (ported verbatim from `docs/project-e/mockups/console.css`), so the token
 * file stays the single source of truth and saturation lives only in data.
 */
const config: Config = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "var(--ink)",
        slate: "var(--slate)",
        "slate-2": "var(--slate-2)",
        raise: "var(--raise)",
        hair: "var(--hair)",
        "hair-2": "var(--hair-2)",
        bone: "var(--bone)",
        steel: "var(--steel)",
        dim: "var(--dim)",
        gain: "var(--gain)",
        loss: "var(--loss)",
        series: "var(--series)",
        warnc: "var(--warnc)",
      },
      fontFamily: {
        sans: "var(--sans)",
        mono: "var(--mono)",
        serif: "var(--serif)",
      },
      borderRadius: {
        DEFAULT: "var(--r)",
        sm: "var(--r-sm)",
      },
    },
  },
  plugins: [],
};

export default config;
