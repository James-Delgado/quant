// Copies the M1 static export (src/quant/console/export/*.json) into
// public/data so the Vite dev server and the static build can fetch it.
// The export is the single source of truth; public/data is reproducible
// (gitignored) and regenerated on predev/prebuild/pretest. No logic here —
// a verbatim copy of whatever the Python service layer emitted.
import { cp, mkdir, rm, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(here, "../../src/quant/console/export");
const DEST = path.resolve(here, "../public/data");

async function exists(p) {
  try {
    await stat(p);
    return true;
  } catch {
    return false;
  }
}

if (!(await exists(SRC))) {
  console.warn(
    `[sync-data] export dir not found: ${SRC}\n` +
      `[sync-data] run \`python -m quant.console export\` first; ` +
      `serving with no data.`,
  );
  await mkdir(DEST, { recursive: true });
  process.exit(0);
}

await rm(DEST, { recursive: true, force: true });
await mkdir(DEST, { recursive: true });
await cp(SRC, DEST, {
  recursive: true,
  filter: (s) => !path.basename(s).startsWith("."),
});
console.log(`[sync-data] copied export -> ${path.relative(process.cwd(), DEST)}`);
