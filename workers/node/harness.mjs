// Fluxonata Node.js / TypeScript action harness, protocol v1 (same envelope as the Python harness).
// Reads input.json next to this file, imports action.mjs (or action.ts), calls the entrypoint and prints ONE JSON
// envelope on the real stdout. Everything the action prints (console.log/info) goes to stderr (logs).
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const realWrite = process.stdout.write.bind(process.stdout);
process.stdout.write = (chunk, ...rest) => process.stderr.write(chunk, ...rest); // user output -> logs
const emit = (envelope) => realWrite(JSON.stringify({ protocol: 1, ...envelope }) + "\n");

const here = dirname(fileURLToPath(import.meta.url));
try {
  const payload = JSON.parse(readFileSync(join(here, "input.json"), "utf8"));
  const file = existsSync(join(here, "action.ts")) ? "action.ts" : "action.mjs";
  const mod = await import(pathToFileURL(join(here, file)).href);
  const fn = mod[payload.entrypoint];
  if (typeof fn !== "function") throw new Error(`Point d'entrée introuvable : ${payload.entrypoint}`);
  const result = await fn(payload.inputs);
  emit({ ok: true, result: result === undefined ? null : result });
} catch (err) {
  console.error(err && err.stack ? err.stack : String(err));
  emit({ ok: false, error: { code: "ACTION_ERROR", message: `${err?.name ?? "Error"}: ${err?.message ?? err}`.slice(0, 300) } });
}
