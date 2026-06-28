import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { App } from "./App";
import "./index.css";
// Loaded after index.css so the ported instrument design system + app-shell
// chrome wins where it overlaps Tailwind's base layer. (A CSS `@import` cannot
// follow `@tailwind`, so the ordering is expressed here as a JS import.)
import "./styles/console.css";

// HashRouter keeps deep links working when the static build is served from any
// path or opened directly from disk (no server rewrite needed) — PRD §9 "static
// build, no SSR".
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <App />
    </HashRouter>
  </StrictMode>,
);
