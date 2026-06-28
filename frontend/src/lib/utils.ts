import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui class-name helper: merge conditional + Tailwind classes. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Short git SHA injected at build time by Vite (`define`). */
export const BUILD_SHA: string =
  typeof __BUILD_SHA__ === "string" ? __BUILD_SHA__ : "dev";

/** App version (package.json) injected at build time by Vite (`define`). */
export const APP_VERSION: string =
  typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "0.0.0";
