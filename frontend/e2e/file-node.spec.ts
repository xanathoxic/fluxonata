import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, login, publishViaApi } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);

async function call(page: Page, method: string, path: string, body?: unknown) {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf =
        document.cookie
          .split("; ")
          .find((c) => c.startsWith("csrftoken="))
          ?.split("=")[1] ?? "";
      const r = await fetch(`/api/v1/workspaces/demo${path}`, {
        method,
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: r.status, json: r.status === 204 ? null : await r.json().catch(() => null) };
    },
    { method, path, body },
  );
}

test("Créer un fichier : un workflow produit un PDF, un tableur, une présentation et une archive, téléchargeables depuis la page de l'exécution", async ({
  page,
}) => {
  const slug = `e2e-fichiers-${uniq()}`;
  await login(page);
  expect(
    await publishViaApi(page, slug, {
      inputs: { type: "object", properties: {}, required: [] },
      outputs: { archive: "{{ steps.lot.output.file }}" },
      nodes: [
        {
          id: "t",
          type: "transform",
          name: "Données",
          op: "set",
          fields: {
            rows: [
              { nom: "Élodie", note: 5 },
              { nom: "Marc", note: 4 },
            ],
          },
        },
        { id: "pdf", type: "file", name: "Rapport PDF", format: "pdf", filename: "rapport", title: "Rapport", content: "Deux personnes notées." },
        { id: "xls", type: "file", name: "Tableur", format: "xlsx", filename: "notes", rows: "{{ steps.t.output.rows }}" },
        { id: "ppt", type: "file", name: "Diaporama", format: "pptx", filename: "deck", title: "Bilan", content: "Résultats\n- 5 et 4" },
        {
          id: "lot",
          type: "file",
          name: "Lot",
          format: "zip",
          filename: "tout",
          sources: ["{{ steps.pdf.output.file }}", "{{ steps.xls.output.file }}", "{{ steps.ppt.output.file }}"],
        },
      ],
      edges: [
        { from: "t", to: "pdf" },
        { from: "pdf", to: "xls" },
        { from: "xls", to: "ppt" },
        { from: "ppt", to: "lot" },
      ],
    }),
  ).toBe(201);
  const started = await call(page, "POST", `/workflows/${slug}/runs/`, { environment: "test", inputs: {} });
  const id = started.json.id as string;
  await expect.poll(async () => (await call(page, "GET", `/workflow-runs/${id}/`)).json.status, { timeout: 40_000 }).toBe("succeeded");

  await page.goto(`/runs/${id}`);
  const panel = page.getByRole("region", { name: /fichiers à télécharger/i });
  await expect(panel).toBeVisible();
  for (const name of ["rapport.pdf", "notes.xlsx", "deck.pptx", "tout.zip"]) await expect(panel.getByRole("link", { name })).toBeVisible();

  const [dl] = await Promise.all([page.waitForEvent("download"), panel.getByRole("link", { name: "rapport.pdf" }).click()]);
  expect(dl.suggestedFilename()).toBe("rapport.pdf");
  const bytes = await (await import("node:fs/promises")).readFile(await dl.path());
  expect(bytes.subarray(0, 8).toString()).toBe("%PDF-1.4");

  const [all] = await Promise.all([page.waitForEvent("download"), panel.getByRole("link", { name: "Tout télécharger (.zip)" }).click()]);
  expect(all.suggestedFilename()).toMatch(/^fichiers-.*\.zip$/);
  const zip = await (await import("node:fs/promises")).readFile(await all.path());
  expect(zip.subarray(0, 2).toString()).toBe("PK");

  await expectNoA11yViolations(page);
});

test("Modèle de document : l'éditeur s'ouvre au centre, aperçu HTML en direct, image ajoutée, puis le modèle devient un fichier Word et un e-mail", async ({
  page,
}) => {
  const slug = `e2e-modele-${uniq()}`;
  await login(page);
  expect(
    await publishViaApi(page, slug, {
      inputs: { type: "object", properties: { nom: { type: "string", default: "Alice" } }, required: [] },
      outputs: { fichier: "{{ steps.word.output.file }}" },
      nodes: [
        { id: "modele", type: "model", name: "Courrier", title: "", markdown: "# Bonjour {{ inputs.nom }}\n\nTexte **simple**." },
        { id: "word", type: "file", name: "Word", format: "docx", filename: "courrier", model: "{{ steps.modele.output }}" },
      ],
      edges: [{ from: "modele", to: "word" }],
    }),
  ).toBe(201);
  await page.goto(`/workflows/${slug}/schema`);
  await page.getByText("Courrier").first().click();
  await page.getByRole("tab", { name: "Modèle" }).click();
  await page.getByRole("button", { name: "Ouvrir l'éditeur" }).click();
  const dialog = page.getByRole("dialog", { name: /Modèle de document : Courrier/ });
  await expect(dialog).toBeVisible();

  const source = dialog.getByLabel("Texte (Markdown)");
  const preview = dialog.getByRole("region", { name: "Aperçu" });
  await expect(preview.getByRole("heading", { level: 1 })).toContainText("Bonjour"); // aperçu rendu par le serveur
  await expect(preview.locator(".md-var").first()).toHaveText("inputs.nom"); // la variable n'est pas évaluée à la conception
  await source.fill("# Titre\n\nDu **gras**, une [page](https://example.org) et <script>alert(1)</script>\n\n- un\n- deux\n\n| a | b |\n|---|---|\n| 1 | 2 |");
  await expect(preview.locator("strong")).toHaveText("gras");
  await expect(preview.locator("li")).toHaveCount(2);
  await expect(preview.locator("table")).toBeVisible();
  await expect(preview.locator("script")).toHaveCount(0); // le HTML saisi n'est jamais actif
  await expect(preview).toContainText("<script>alert(1)</script>");

  await source.press("Control+a");
  await dialog.getByRole("button", { name: "Gras (Ctrl+B)" }).click();
  await expect(source).toHaveValue(/^\*\*# Titre/);
  await source.fill("Logo :");

  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
  await dialog.locator('input[type="file"]').setInputFiles({ name: `pixel-${uniq()}.png`, mimeType: "image/png", buffer: png });
  await expect(source).toHaveValue(/!\[pixel-\d+\]\(image:[0-9a-f-]{36}\)/);
  await expect(preview.locator("img")).toBeVisible();
  await expectNoA11yViolations(page);

  await dialog.getByRole("button", { name: "Appliquer" }).click();
  await expect(dialog).toBeHidden();
  await page.getByText("Word").first().click();
  await page.getByRole("tab", { name: "Fichier" }).click();
  await expect(page.getByRole("combobox", { name: "Modèle de document" })).toHaveValue("modele");
  await expect(page.getByLabel("Contenu")).toHaveCount(0);

  // le serveur refuse une image qui n'en est pas une
  const refused = await page.evaluate(async () => {
    const csrf =
      document.cookie
        .split("; ")
        .find((c) => c.startsWith("csrftoken="))
        ?.split("=")[1] ?? "";
    const form = new FormData();
    form.append("file", new Blob(["<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"], { type: "image/svg+xml" }), "x.svg");
    const r = await fetch("/api/v1/workspaces/demo/model-images/", { method: "POST", headers: { "X-CSRFToken": csrf }, body: form });
    return r.status;
  });
  expect(refused).toBe(400);
  await call(page, "DELETE", `/workflows/${slug}/delete/`);
});
