import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login, publishViaApi } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);
const graph = {
  inputs: { type: "object", required: ["text"], properties: { text: { type: "string", title: "Texte" } } }, outputs: {}, edges: [],
  nodes: [{ id: "a", type: "action", name: "Analyser", action: "text-stats", inputs: { text: "{{ inputs.text }}" }, ui: { x: 0, y: 0 } }],
};

test("assistant IA de l'éditeur : propose, applique, annule ; absent quand l'IA est désactivée", async ({ browser }) => {
  const slug = `chat-${uniq()}`;
  const designer = await (await browser.newContext()).newPage();
  await login(designer, "alice");
  expect(await publishViaApi(designer, slug, graph)).toBe(201);
  await designer.goto(`/workflows/${slug}/schema`);
  await expect(designer.getByRole("toolbar", { name: "Outils du schéma" })).toBeVisible();
  await expect(designer.getByRole("button", { name: "Assistant IA" })).toHaveCount(0); // IA désactivée par défaut

  const admin = await (await browser.newContext()).newPage();
  await login(admin, "admin");
  await goNav(admin, "Administration");
  await admin.getByRole("tab", { name: "Assistant IA" }).click();
  try {
    await admin.getByLabel("Mode").selectOption({ label: "Local strict (aucun envoi cloud, aucun repli externe)" });
    await admin.getByLabel(/Budget quotidien/).fill("50000000"); // le faux fournisseur compte des jetons : les essais répétés épuisent le budget du jour
    await admin.getByLabel(/Autoriser l'essai à blanc/).uncheck(); // ce test porte sur la conversation, pas sur l'essai (des conteneurs, lent)
    await admin.getByRole("button", { name: "Enregistrer", exact: true }).click();
    await expect(admin.getByText("Enregistré.")).toBeVisible();
    if ((await admin.getByText(/^fake — /).count()) === 0) {
      await admin.getByRole("button", { name: "Ajouter un fournisseur" }).click();
      const dlg = admin.getByRole("dialog", { name: "Ajouter un fournisseur" });
      await dlg.getByLabel("Nom", { exact: true }).fill("fake");
      await dlg.getByLabel("Type").selectOption("fake");
      await dlg.getByRole("button", { name: "Ajouter", exact: true }).click();
      await expect(admin.getByText(/^fake — /)).toBeVisible();
    }

    await designer.reload();
    await designer.getByRole("button", { name: "Assistant IA" }).click();
    const chat = designer.getByRole("region", { name: "Assistant IA" });
    await expect(chat).toBeVisible();
    await expectNoA11yViolations(designer);

    await chat.getByLabel("Votre demande à l'assistant").fill("ajoute un script à la fin");
    await chat.getByRole("button", { name: "Envoyer" }).click();
    await expect(chat.getByText("Modifications proposées")).toBeVisible({ timeout: 30_000 });
    await expect(chat).toContainText("Étape ajoutée : « Script ajouté » (script)");
    await expect(chat).toContainText("1 script(s) à relire");
    await expect(designer.locator(".flow-node")).toHaveCount(1); // rien n'est appliqué sans accord
    await expectNoA11yViolations(designer);

    await chat.getByRole("button", { name: "Appliquer au brouillon" }).click();
    await expect(designer.locator(".flow-node")).toHaveCount(2);
    await expect(designer.locator(".flow-node", { hasText: "Script ajouté" })).toBeVisible();
    await expect(designer.getByRole("button", { name: /problème\(s\) bloquant/ })).toBeVisible(); // script à relire : la publication reste bloquée

    await chat.getByRole("button", { name: "Annuler", exact: true }).click(); // annulation depuis la conversation
    await expect(designer.locator(".flow-node")).toHaveCount(1);

    // une note (annotation visuelle) : proposée, appliquée avec une position, annulable
    await chat.getByLabel("Votre demande à l'assistant").fill("ajoute une note");
    await chat.getByRole("button", { name: "Envoyer" }).click();
    await expect(chat.getByText("Note ajoutée : « Note ajoutée par l'assistant »")).toBeVisible({ timeout: 30_000 });
    await chat.getByRole("button", { name: "Appliquer au brouillon" }).last().click();
    await expect(designer.locator(".flow-note", { hasText: "Note ajoutée par l'assistant" })).toBeVisible();
    await designer.getByRole("button", { name: "Annuler", exact: true }).first().click();
    await expect(designer.locator(".flow-note")).toHaveCount(0);

    // les autres types d'étapes (ici une approbation) : proposés, appliqués, annulables
    await chat.getByLabel("Votre demande à l'assistant").fill("ajoute une approbation");
    await chat.getByRole("button", { name: "Envoyer" }).click();
    await expect(chat.getByText("Étape ajoutée : « Approbation ajoutée » (approbation)")).toBeVisible({ timeout: 30_000 });
    await chat.getByRole("button", { name: "Appliquer au brouillon" }).last().click();
    await expect(designer.locator(".flow-node", { hasText: "Approbation ajoutée" })).toBeVisible();
    await designer.getByRole("button", { name: "Annuler", exact: true }).first().click();
    await expect(designer.locator(".flow-node")).toHaveCount(1);

    // une demande qui n'est pas une modification : réponse sans proposition
    await chat.getByLabel("Votre demande à l'assistant").fill("que fait ce workflow ?");
    await chat.getByLabel("Votre demande à l'assistant").press("Enter");
    await expect(chat.getByText("Je n'ai rien modifié")).toBeVisible({ timeout: 30_000 });
    await expect(chat.getByRole("button", { name: "Appliquer au brouillon" })).toHaveCount(0);

    // la conversation survit à un changement d'onglet du workflow
    await designer.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name: "Informations", exact: true }).click();
    await designer.getByRole("navigation", { name: "Sections du workflow" }).getByRole("link", { name: "Conception", exact: true }).click();
    await designer.getByRole("button", { name: "Assistant IA" }).click();
    await expect(designer.getByRole("region", { name: "Assistant IA" })).toContainText("ajoute un script à la fin");
  } finally {
    await admin.getByLabel("Mode").selectOption({ label: "IA désactivée" });
    await admin.getByRole("button", { name: "Enregistrer", exact: true }).click();
    await expect(admin.getByText("Enregistré.")).toBeVisible();
  }
});

test("un brouillon écrit par l'IA, sans aucune position (étapes et notes), s'ouvre et s'affiche", async ({ page }) => {
  const slug = `sanspos-${uniq()}`;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page, "alice");
  const draft = {
    inputs: { type: "object", required: ["text"], properties: { text: { type: "string", title: "Texte" } } }, outputs: {},
    nodes: [
      { id: "a", type: "action", name: "Analyser", action: "text-stats", inputs: { text: "{{ inputs.text }}" } },
      { id: "w", type: "wait", name: "Pause", seconds: 5 },
    ],
    edges: [{ from: "a", to: "w" }],
    notes: [{ id: "note_1", text: "Première note sans position" }, { id: "note_2", text: "Seconde note" }],
  };
  expect(await publishViaApi(page, slug, draft)).toBe(201);
  await page.goto(`/workflows/${slug}/schema`);
  await expect(page.locator(".flow-node")).toHaveCount(2);
  await expect(page.locator(".flow-note")).toHaveCount(2);
  expect(errors).toEqual([]);
});
