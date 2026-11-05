import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

// ADR 0025 : une action « proposée » par un assistant externe se relit, puis s'active ; avant cela elle ne se lance pas.
// L'état « proposée » ne peut être posé que par le serveur (le futur outil MCP) : on le prépare directement dans la base de démonstration.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const BACKEND = path.resolve(HERE, "../../backend");
const PYTHON = path.resolve(HERE, "../../.venv/bin/python");

function seedProposal(slug: string, name: string) {
  const code = `
from catalog.models import Action, ActionVersion
from tenancy.models import Workspace
ws = Workspace.objects.get(slug="demo")
a, _ = Action.objects.update_or_create(workspace=ws, slug=${JSON.stringify(slug)}, defaults=dict(name=${JSON.stringify(name)}, description="Compte les voyelles d'un texte.", status="proposed"))
ActionVersion.objects.get_or_create(action=a, version="1.0.0", defaults=dict(source="def run(inputs):\\n    return {'n': 0}", input_schema={"type": "object", "properties": {}}, output_schema={"type": "object", "properties": {"n": {"type": "integer"}}}, limits={"timeout_seconds": 20, "memory_mb": 64}))
`;
  execFileSync(PYTHON, ["manage.py", "shell", "-c", code], { cwd: BACKEND, stdio: "pipe" });
}

test("proposition d'un assistant : à relire, non lançable, puis activée par une personne", async ({ page }) => {
  const slug = "e2e-proposition"; // identifiant fixe : la même action est remise à « proposée » à chaque exécution (les actions publiées ne se suppriment pas)
  const name = "Proposition E2E";
  seedProposal(slug, name);
  await login(page);
  await goNav(page, "Bibliothèques");

  const panel = page.getByRole("region", { name: /Propositions d'un assistant/ });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("link", { name, exact: true })).toBeVisible();
  await expect(panel.getByText("À relire").first()).toBeVisible();
  // la proposition n'est pas dans le catalogue ordinaire des actions
  await page.getByLabel("Rechercher une action").fill(name);
  await expect(page.getByRole("table", { name: "Actions de la bibliothèque" }).getByRole("row", { name: new RegExp(name) })).toHaveCount(0);
  await expectNoA11yViolations(page);

  await panel.getByRole("link", { name: `Relire ${name}`, exact: true }).click();
  await expect(page.getByRole("note").filter({ hasText: "Proposition d'un assistant" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Lancer un essai" })).toHaveCount(0); // ni lancée…
  await expectNoA11yViolations(page);

  await page.getByRole("button", { name: "Activer cette action" }).click();
  await expect(page.getByRole("link", { name: "Lancer un essai" })).toBeVisible(); // …avant d'avoir été activée par une personne
  await expect(page.getByRole("note").filter({ hasText: "Proposition d'un assistant" })).toHaveCount(0);
});

test("un lecteur ne voit pas les propositions", async ({ page }) => {
  const slug = "e2e-proposition-lecteur";
  const name = "Lecteur : proposition E2E";
  seedProposal(slug, name);
  await login(page, "bob");
  await page.goto(`/library/actions/${slug}`);
  await expect(page.getByText(name)).toHaveCount(0);
  await expect(page.getByRole("alert").or(page.getByRole("heading", { name: /introuvable|404/i })).first()).toBeVisible();
});
