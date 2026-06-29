/// <reference types="vite/client" />

// Build-time git short SHA, injected via Vite `define` (see vite.config.ts).
declare const __BUILD_SHA__: string;

// Build-time app version (package.json), injected via Vite `define`.
declare const __APP_VERSION__: string;
