import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

// ADR 0025 (amendement du 2026-11-26) : un assistant DEMANDE un lancement, une personne le confirme ou le refuse dans Approbations.
// La demande est posée directement en base (c'est ce que fait l'outil MCP `request_run`) ; ici on refuse : aucun run n'est créé dans la démo.
// La confirmation (qui crée un run) est couverte par les tests du serveur et ceux de l'interface.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const BACKEND = path.resolve(HERE, "../../backend");
const PYTHON = path.resolve(HERE, "../../.venv/bin/python");

function shell(code: string): string {
  return execFileSync(PYTHON, ["manage.py", "shell", "-c", code], { cwd: BACKEND, stdio: "pipe" }).toString();
}

function seedRequest(): string {
  const out = shell(`
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from runs.models import RunRequest
from tenancy.models import Environment, Workspace
from workflows.models import Workflow
ws = Workspace.objects.get(slug="demo")
RunRequest.objects.filter(workspace=ws, reason__startswith="E2E").delete()
wf = next(w for w in Workflow.objects.filter(workspace=ws) if w.latest_version() and not (w.latest_version().input_schema or {}).get("required"))
env, _ = Environment.objects.get_or_create(workspace=ws, kind="test", defaults={"name": "Recette"})
RunRequest.objects.create(workspace=ws, workflow_version=wf.latest_version(), environment=env, inputs={}, reason="E2E : vérifier le workflow", client="e2e",
    requested_by=get_user_model().objects.get(username="alice"), expires_at=timezone.now() + timedelta(minutes=10))
print("NAME=" + wf.name)
`);
  return (out.split("NAME=")[1] ?? "").trim();
}

function cleanup() {
  shell(`
from runs.models import RunRequest
RunRequest.objects.filter(reason__startswith="E2E").delete()
`);
}

test.afterAll(cleanup);

test("lancement demandé par un assistant : examiné puis refusé, sans rien lancer", async ({ page }) => {
  const name = seedRequest();
  await login(page);
  await goNav(page, "Approbations");
  const section = page.getByRole("region", { name: /Lancements demandés par un assistant/ });
  await expect(section).toBeVisible();
  await section.getByRole("button", { name: `Examiner le lancement de ${name}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("E2E : vérifier le workflow")).toBeVisible();
  await expect(dialog.getByText("rien ne s'exécute sans votre confirmation")).toBeVisible();
  await expect(dialog.getByRole("button", { name: /Confirmer et lancer en recette/ })).toBeEnabled();
  await expectNoA11yViolations(page);
  await dialog.getByRole("button", { name: "Refuser" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(section.getByRole("button", { name: `Examiner le lancement de ${name}` })).toHaveCount(0);
});

test("un lecteur ne peut pas décider d'un lancement demandé", async ({ page }) => {
  const name = seedRequest();
  await login(page, "bob");
  await goNav(page, "Approbations");
  const section = page.getByRole("region", { name: /Lancements demandés par un assistant/ });
  await section.getByRole("button", { name: `Examiner le lancement de ${name}` }).click();
  await expect(page.getByRole("dialog").getByRole("button", { name: /Confirmer et lancer/ })).toBeDisabled();
});
