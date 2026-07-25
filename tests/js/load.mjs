import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { installDom } from "./dom.mjs";

const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "voisr", "web");

/* Evaluates the app's classic scripts in global scope against the stubs.
   (0, eval) is indirect eval: it runs at global scope, so the scripts' bare
   `window`/`document` references resolve to the stubs installDom just set. */
export function loadVoisr(files) {
  const env = installDom();
  for (const rel of files) {
    (0, eval)(readFileSync(path.join(WEB, rel), "utf8"));
  }
  return { V: globalThis.window.Voisr, ...env };
}
