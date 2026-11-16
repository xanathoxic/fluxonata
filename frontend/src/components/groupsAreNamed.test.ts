import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** Garde statique : un groupe ARIA (`role="group"` ou `radiogroup`) sans nom est annoncé sans contexte. Le nom est exigé sur la même
 *  ligne, par `aria-label` ou `aria-labelledby` (les noms par `aria-labelledby` sont vérifiés au rendu dans DefinitionFormGroupNames.test.tsx). */
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return sources(p);
    return /\.tsx$/.test(p) && !/\.test\.tsx$/.test(p) ? [p] : [];
  });
}

describe("groupes nommés", () => {
  it("chaque role=group ou radiogroup du code source porte un nom sur sa ligne", () => {
    const unnamed: string[] = [];
    let seen = 0;
    for (const file of sources("src")) {
      readFileSync(file, "utf-8").split("\n").forEach((line, i) => {
        if (!/role="(?:group|radiogroup)"/.test(line)) return;
        seen += 1;
        if (!/aria-label(?:ledby)?=/.test(line)) unnamed.push(`${file}:${i + 1}`);
      });
    }
    expect(seen).toBeGreaterThan(30);
    expect(unnamed, `groupes sans nom : ${unnamed.join(", ")}`).toEqual([]);
  });
});
