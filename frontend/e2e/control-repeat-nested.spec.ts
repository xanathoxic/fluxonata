import { expect, test, type Page } from "@playwright/test";
import { login } from "./helpers";

/** Contrôle à « lignes répétées » : un contrôle de la bibliothèque s'y ajoute comme sous-contrôle, et chaque ligne se duplique avec les mêmes valeurs. */
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
        body: body ? JSON.stringify(body) : undefined,
      });
      return { status: r.status, data: await r.json().catch(() => ({})) };
    },
    { method, path, body },
  );
}
const section = (page: Page, name: string) => page.getByRole("navigation", { name: "Sections du contrôle" }).getByRole("button", { name }).click();
const text = (key: string, label: string) => ({ key, label, type: "text", layout: { row: 1, col: 1, span: 12 } });

test("lignes répétées : sous-contrôle de la bibliothèque dans le concepteur, puis duplication d'une ligne dans le formulaire", async ({ page }) => {
  await login(page);
  const id = Date.now().toString().slice(-6);
  const address = `adresse-${id}`;
  const lines = `invites-${id}`;
  // le contrôle « Adresse » (groupe) est publié ; « Invités » (lignes répétées) n'a qu'un champ pour l'instant
  expect(
    (
      await call(page, "POST", "/control-library/", {
        slug: address,
        name: `Adresse ${id}`,
        spec: {
          version: 1,
          mode: "group",
          parts: [
            { ...text("rue", "Rue"), layout: { row: 1, col: 1, span: 8 } },
            { ...text("ville", "Ville"), layout: { row: 1, col: 9, span: 4 } },
          ],
        },
      })
    ).status,
  ).toBe(201);
  expect((await call(page, "POST", `/control-library/${address}/publish/`, {})).status).toBe(201);
  expect(
    (
      await call(page, "POST", "/control-library/", {
        slug: lines,
        name: `Invités ${id}`,
        spec: { version: 1, mode: "repeat", row_label: "Invité", parts: [text("nom", "Nom")] },
      })
    ).status,
  ).toBe(201);

  // concepteur des lignes répétées : la palette propose le contrôle publié ; un clic l'ajoute à la ligne
  await page.goto(`/library/controls/${lines}/design`);
  await section(page, "Champs");
  const palette = page.getByRole("complementary", { name: "Palette de champs" });
  await expect(palette.getByRole("heading", { name: "Contrôles personnalisés" })).toBeVisible();
  await palette.getByRole("button", { name: `Adresse ${id}` }).click();
  await expect(page.getByText("Brouillon enregistré")).toBeVisible({ timeout: 20_000 });
  const draft = (await call(page, "GET", `/control-library/${lines}/`)).data;
  const nested =
    draft.draft_spec?.parts?.find((p: { type: string }) => p.type === "custom") ?? draft.spec?.parts?.find((p: { type: string }) => p.type === "custom");
  expect(nested?.control?.ref).toBe(address); // le sous-contrôle est dans le brouillon enregistré, pas seulement à l'écran

  // publication, puis un formulaire qui utilise les lignes répétées
  expect((await call(page, "POST", `/control-library/${lines}/publish/`, {})).status).toBe(201);
  const card = (await call(page, "GET", "/control-library/?published=1")).data.results.find((c: { slug: string }) => c.slug === lines);
  const def = {
    version: 1,
    sections: [
      {
        id: "s1",
        title: "T",
        fields: [
          {
            key: "invites",
            label: "Invités",
            type: "custom",
            layout: { row: 1, col: 1, span: 12 },
            control: { ref: card.slug, version: card.published.version, name: card.published.name, icon: card.published.icon, spec: card.published.spec },
          },
        ],
      },
    ],
  };
  const form = `soiree-${id}`;
  expect((await call(page, "POST", "/form-library/", { slug: form, name: `Soirée ${id}`, definition: def })).status).toBe(201);
  await page.goto(`/library/forms/${form}`);
  await page.getByRole("tab", { name: "Aperçu" }).click();
  await page.getByRole("button", { name: "Ajouter une ligne" }).click(); // aucun minimum : le formulaire démarre sans ligne
  const first = page.getByRole("group", { name: "Invité 1" });
  await first.getByLabel("Nom").fill("Ada");
  await first.getByLabel("Rue").fill("1 rue des Lilas");
  await first.getByLabel("Ville").fill("Paris");

  // dupliquer : une nouvelle ligne avec les mêmes valeurs saisies, sous-contrôle compris
  await first.getByRole("button", { name: "Dupliquer invité 1" }).click();
  const second = page.getByRole("group", { name: "Invité 2" });
  await expect(second.getByLabel("Nom")).toHaveValue("Ada");
  await expect(second.getByLabel("Rue")).toHaveValue("1 rue des Lilas");
  await expect(second.getByLabel("Ville")).toHaveValue("Paris");
  await second.getByLabel("Nom").fill("Bob"); // indépendante de l'original
  await expect(first.getByLabel("Nom")).toHaveValue("Ada");
});
