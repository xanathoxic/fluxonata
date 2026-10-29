import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

// La génération d'un brouillon est une tâche suivie en direct : barre de progression + journal des phases, puis rapport
// (dont l'essai à blanc). Les réponses de l'API sont simulées ici pour vérifier l'affichage de façon déterministe.
const t0 = 1_700_000_000;
const running = {
  id: "job-1", status: "running", progress: 61, result: null, error: null,
  events: [
    { t: t0, phase: "queued", label: "En attente d'un travailleur", state: "done", detail: "", pct: 0 },
    { t: t0 + 1, phase: "context", label: "Préparation du contexte (catalogue, workflows, skills)", state: "done", detail: "6 action(s), 4 workflow(s) appelable(s), 0 skill(s)", pct: 2 },
    { t: t0 + 9, phase: "plan", label: "Le modèle rédige le plan du workflow", state: "done", detail: "", pct: 6 },
    { t: t0 + 10, phase: "build", label: "Construction du schéma à partir du plan", state: "done", detail: "12 étape(s), 14 liaison(s) construites", pct: 38 },
    { t: t0 + 12, phase: "dryrun", label: "Essai à blanc : exécution des scripts et des conditions", state: "running", detail: "Scénario 2/3 : limite (vide, zéro, faux)", pct: 65 },
  ],
};
const finished = {
  ...running, status: "succeeded", progress: 100,
  events: [...running.events.slice(0, 4), { ...running.events[4], state: "done" }, { t: t0 + 30, phase: "done", label: "Terminé", state: "done", detail: "Le brouillon est prêt : relisez-le avant de le publier.", pct: 100 }],
  result: {
    workflow: "essai-progression", issues: [{ node: "calcul", level: "warning", code: "dryrun_unexpected", message: "Résultat différent de l'attendu pour « Calcul »." }],
    graph: { nodes: [{ id: "calcul", name: "Calcul" }, { id: "fin", name: "Fin" }] },
    check: { dryrun: { ran: true, problems: 1, coverage: { executed: 2, total: 3, never: ["Branche rare"] },
      scenarios: [{ label: "nominal", outcome: { status: "succeeded", message: "" }, executed: 2, steps: [] }, { label: "limite", outcome: { status: "failed", message: "exception « Stop »" }, executed: 1, steps: [] }] } },
    usage: { tokens: 12345, today: 20000, budget: 1000000 },
  },
};

test("génération d'un brouillon : progression en direct, journal des phases, rapport avec l'essai à blanc", async ({ page }) => {
  let polls = 0;
  await page.route("**/ai/policy/", (r) => r.fulfill({ json: { mode: "local_strict", daily_token_budget: 1000000, used_today: 0, modes: {} } }));
  await page.route("**/ai/draft-jobs/", (r) => (r.request().method() === "GET" ? r.fulfill({ json: { results: [] } }) : r.fulfill({ status: 202, json: { id: "job-1", status: "queued" } })));
  await page.route("**/ai/draft-jobs/job-1/", (r) => { polls += 1; return r.fulfill({ json: polls <= 2 ? running : finished }); });
  await login(page, "alice");
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Générer avec l'IA" }).click();
  const form = page.getByRole("dialog", { name: "Générer un brouillon avec l'assistant IA" });
  await form.getByLabel("Nom du brouillon").fill("Essai progression");
  await form.getByLabel("Décrivez en français ce que le workflow doit faire").fill("Un workflow de test");
  await form.getByRole("button", { name: "Générer sans plan" }).click();

  const live = page.getByRole("dialog", { name: "Génération du brouillon en cours" });
  await expect(live.getByRole("progressbar", { name: "Avancement de la génération" })).toHaveAttribute("value", "61");
  await expect(live.getByRole("status")).toContainText("Essai à blanc : exécution des scripts et des conditions");
  await expect(live.getByRole("status")).toContainText("Scénario 2/3");
  const log = live.getByRole("log", { name: "Journal de la génération" });
  await expect(log.getByRole("listitem")).toHaveCount(5);
  await expect(log).toContainText("12 étape(s), 14 liaison(s) construites");
  await expect(log.getByRole("listitem").nth(1)).toContainText("terminé"); // l'état est dit en texte, pas seulement par l'icône
  await expectNoA11yViolations(page);

  const report = page.getByRole("dialog", { name: "Brouillon généré" });
  await expect(report).toContainText("2 étape(s) proposée(s)", { timeout: 15_000 });
  await expect(report).toContainText("Essai à blanc : 2 scénario(s), 2/3 étape(s) exercée(s), 1 problème(s)");
  await report.getByText(/Essai à blanc : 2 scénario/).click(); // le détail se déplie
  await expect(report).toContainText("arrêté (exception « Stop »)");
  await expect(report).toContainText("Aucun jeu d'essai n'est passé par : Branche rare");
  await expect(report).toContainText("12 345 jetons");
  await expectNoA11yViolations(page);
});

test("génération refusée : l'erreur est dite et la demande peut être relancée", async ({ page }) => {
  await page.route("**/ai/policy/", (r) => r.fulfill({ json: { mode: "local_strict", daily_token_budget: 1000000, used_today: 0, modes: {} } }));
  await page.route("**/ai/draft-jobs/", (r) => (r.request().method() === "GET" ? r.fulfill({ json: { results: [] } }) : r.fulfill({ status: 202, json: { id: "job-2", status: "queued" } })));
  await page.route("**/ai/draft-jobs/job-2/", (r) => r.fulfill({ json: { id: "job-2", status: "failed", progress: 6, result: null, events: [], error: { code: "MODEL_OUTPUT_INVALID", message: "Le plan proposé est inexploitable." } } }));
  await login(page, "alice");
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Générer avec l'IA" }).click();
  const form = page.getByRole("dialog", { name: "Générer un brouillon avec l'assistant IA" });
  await form.getByLabel("Décrivez en français ce que le workflow doit faire").fill("Un workflow de test");
  await form.getByRole("button", { name: "Générer sans plan" }).click();
  await expect(form.getByRole("alert")).toContainText("La génération a échoué : Le plan proposé est inexploitable.");
  await expect(form.getByRole("button", { name: "Générer sans plan" })).toBeEnabled();
  await expect(form.getByLabel("Décrivez en français ce que le workflow doit faire")).toHaveValue("Un workflow de test"); // la demande est gardée
});


// ---------------------------------------------------------------------------------------------------- le plan avant génération
const outline = {
  title: "Création d'une VM", summary: "Crée une machine virtuelle après contrôle du nom.",
  inputs: [{ name: "nom_vm", type: "texte", purpose: "nom de la machine" }],
  steps: [{ text: "On vérifie le nom ; sinon exception « Nom invalide »." }, { text: "Approbation « Autoriser la création » ; si refusée, exception « Création refusée »." }],
  uses: [{ kind: "action", name: "Envoyer une notification (simulé)", note: "prévenir le demandeur" }],
  assumptions: ["Le demandeur est prévenu par notification."],
  questions: [{ question: "Que faire si la création échoue ?", proposed: "Ouvrir un incident." }],
};
const planJob = (id: string, o = outline) => ({ id, status: "succeeded", progress: 100, error: null, events: [{ t: t0, phase: "plan", label: "Le modèle rédige le plan du workflow", state: "done", detail: "", pct: 6 }], result: { outline: o, usage: { tokens: 1200, today: 1200, budget: 1000000 } } });

test("plan avant génération : valider (avec un plan modifié à la main) lance la génération avec ce plan", async ({ page }) => {
  const posted: Record<string, unknown>[] = [];
  await page.route("**/ai/policy/", (r) => r.fulfill({ json: { mode: "local_strict", daily_token_budget: 1000000, used_today: 0, modes: {} } }));
  await page.route("**/ai/outline-jobs/", (r) => r.fulfill({ status: 202, json: { id: "plan-1", status: "queued" } }));
  await page.route("**/ai/draft-jobs/plan-1/", (r) => r.fulfill({ json: planJob("plan-1") }));
  await page.route("**/ai/draft-jobs/", (r) => { if (r.request().method() === "GET") return r.fulfill({ json: { results: [] } }); posted.push(r.request().postDataJSON() as Record<string, unknown>); return r.fulfill({ status: 202, json: { id: "job-1", status: "queued" } }); });
  await page.route("**/ai/draft-jobs/job-1/", (r) => r.fulfill({ json: finished }));
  await login(page, "alice");
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Générer avec l'IA" }).click();
  const form = page.getByRole("dialog", { name: "Générer un brouillon avec l'assistant IA" });
  await form.getByLabel("Décrivez en français ce que le workflow doit faire").fill("je veux créer une vm quand on me le demande");
  await form.getByRole("button", { name: "Proposer un plan" }).click();

  const plan = page.getByRole("dialog", { name: /Plan envisagé/ });
  await expect(plan.getByLabel("Nom du workflow")).toHaveValue("Création d'une VM");
  const box = (await plan.boundingBox())!; // au milieu de la page, avec de la place pour lire et répondre
  const view = page.viewportSize()!;
  expect(box.width).toBeGreaterThan(800);
  expect(Math.abs(box.x + box.width / 2 - view.width / 2)).toBeLessThan(10);
  await expect(plan.getByLabel(/Ce que fera le workflow/)).toHaveValue(/Autoriser la création/);
  await expect(plan.getByRole("heading", { name: "Déjà disponible dans votre espace et réutilisé" })).toBeVisible();
  await expect(plan.getByLabel("Que faire si la création échoue ?")).toHaveValue("Ouvrir un incident."); // la réponse proposée est pré-remplie
  await expectNoA11yViolations(page);

  // édition directe : une étape ajoutée, une réponse changée
  const steps = plan.getByLabel(/Ce que fera le workflow/);
  await steps.fill(`${await steps.inputValue()}\nOn prévient le demandeur à la fin.`);
  await plan.getByLabel("Que faire si la création échoue ?").fill("Prévenir l'équipe infrastructure.");
  await plan.getByRole("button", { name: "Valider et générer" }).click();
  await expect(page.getByRole("dialog", { name: "Brouillon généré" })).toContainText("2 étape(s) proposée(s)", { timeout: 15_000 });

  expect(posted).toHaveLength(1);
  const sent = posted[0] as { outline: { steps: { text: string }[]; answers: { answer: string }[] }; name: string };
  expect(sent.outline.steps.map((x) => x.text)).toContain("On prévient le demandeur à la fin.");
  expect(sent.outline.answers[0]?.answer).toBe("Prévenir l'équipe infrastructure.");
  expect(sent.name).toBe("Création d'une VM"); // sans nom saisi : celui du plan
});

test("plan avant génération : consigne libre pour réviser, puis refus qui garde la demande", async ({ page }) => {
  const asked: Record<string, unknown>[] = [];
  let created = 0;
  await page.route("**/ai/policy/", (r) => r.fulfill({ json: { mode: "local_strict", daily_token_budget: 1000000, used_today: 0, modes: {} } }));
  await page.route("**/ai/outline-jobs/", (r) => { asked.push(r.request().postDataJSON() as Record<string, unknown>); created += 1; return r.fulfill({ status: 202, json: { id: `plan-${created}`, status: "queued" } }); });
  await page.route("**/ai/draft-jobs/plan-1/", (r) => r.fulfill({ json: planJob("plan-1") }));
  await page.route("**/ai/draft-jobs/plan-2/", (r) => r.fulfill({ json: planJob("plan-2", { ...outline, steps: [...outline.steps, { text: "On prévient le demandeur à la fin." }] }) }));
  await page.route("**/ai/draft-jobs/", (r) => { if (r.request().method() !== "GET") throw new Error("aucune génération ne doit démarrer avant validation"); return r.fulfill({ json: { results: [] } }); });
  await login(page, "alice");
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Générer avec l'IA" }).click();
  const form = page.getByRole("dialog", { name: "Générer un brouillon avec l'assistant IA" });
  await form.getByLabel("Décrivez en français ce que le workflow doit faire").fill("je veux créer une vm");
  await form.getByRole("button", { name: "Proposer un plan" }).click();
  let plan = page.getByRole("dialog", { name: /Plan envisagé/ });
  await expect(plan.getByLabel(/Ce que fera le workflow/)).toBeVisible();

  const revise = plan.getByRole("button", { name: "Réviser le plan avec cette consigne" });
  await expect(revise).toBeDisabled(); // pas de consigne, pas de révision
  await plan.getByLabel(/Une autre idée/).fill("ajoute une notification à la fin");
  await revise.click();
  plan = page.getByRole("dialog", { name: /Plan envisagé/ });
  await expect(plan.getByLabel(/Ce que fera le workflow/)).toHaveValue(/On prévient le demandeur à la fin/);
  expect(asked[1]).toMatchObject({ request: "je veux créer une vm", feedback: "ajoute une notification à la fin" });
  expect((asked[1] as { previous: { steps: unknown[] } }).previous.steps).toHaveLength(2); // le plan envoyé est celui qu'on voyait

  await plan.getByRole("button", { name: "Refuser ce plan" }).click();
  const back = page.getByRole("dialog", { name: "Générer un brouillon avec l'assistant IA" });
  await expect(back.getByLabel("Décrivez en français ce que le workflow doit faire")).toHaveValue("je veux créer une vm"); // la demande est gardée
});

test("plan avant génération : un plan impossible à préparer est dit, avec la sortie « générer sans plan »", async ({ page }) => {
  await page.route("**/ai/policy/", (r) => r.fulfill({ json: { mode: "local_strict", daily_token_budget: 1000000, used_today: 0, modes: {} } }));
  await page.route("**/ai/outline-jobs/", (r) => r.fulfill({ status: 202, json: { id: "plan-x", status: "queued" } }));
  await page.route("**/ai/draft-jobs/plan-x/", (r) => r.fulfill({ json: { id: "plan-x", status: "failed", progress: 6, result: null, events: [], error: { code: "OUTLINE_EMPTY", message: "L'assistant n'a pas réussi à rédiger un plan : reformulez, ou générez sans plan." } } }));
  await login(page, "alice");
  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Générer avec l'IA" }).click();
  const form = page.getByRole("dialog", { name: "Générer un brouillon avec l'assistant IA" });
  await form.getByLabel("Décrivez en français ce que le workflow doit faire").fill("truc");
  await form.getByRole("button", { name: "Proposer un plan" }).click();
  await expect(page.getByRole("alert")).toContainText("générez sans plan");
  await page.getByRole("button", { name: "Annuler" }).click();
  await expect(page.getByRole("dialog", { name: "Générer un brouillon avec l'assistant IA" }).getByRole("button", { name: "Générer sans plan" })).toBeVisible();
});


// ------------------------------------------------------------------------- suivre et annuler une génération après avoir fermé sa fenêtre
const trayJob = (over: Record<string, unknown>) => ({ id: "t-1", status: "running", progress: 42, name: "Création VM", slug: "creation-vm", phase: "Essai à blanc : exécution des scripts et des conditions", detail: "2/5 scénario(s) terminé(s)", error: null, workflow: null, created_at: "2026-10-27T10:00:00Z", ...over });

test("génération en cours : visible dans l'en-tête sur toutes les pages, suivie et annulable après fermeture de la fenêtre", async ({ page }) => {
  let cancelled = false;
  const cancels: string[] = [];
  await page.route("**/ai/policy/", (r) => r.fulfill({ json: { mode: "local_strict", daily_token_budget: 1000000, used_today: 0, modes: {} } }));
  await page.route("**/ai/draft-jobs/", (r) => r.fulfill({ json: { results: [trayJob(cancelled ? { status: "cancelled", error: "Génération annulée : aucun brouillon n'a été enregistré." } : {})] } }));
  await page.route("**/ai/draft-jobs/t-1/", (r) => r.fulfill({ json: cancelled
    ? { ...running, id: "t-1", status: "cancelled", error: { code: "CANCELLED", message: "Génération annulée : aucun brouillon n'a été enregistré." } }
    : { ...running, id: "t-1" } }));
  await page.route("**/ai/draft-jobs/t-1/cancel/", (r) => { cancelled = true; cancels.push(r.request().method()); return r.fulfill({ json: { id: "t-1", status: "cancelled" } }); });
  await login(page, "alice");
  await goNav(page, "Workflows");
  const tray = page.getByRole("region", { name: "Suivi des générations de workflows" });
  const pill = page.getByRole("button", { name: /^Générations/ }); // dans l'en-tête : il ne recouvre rien
  await expect(pill).toBeVisible();
  await pill.click();
  await expect(tray.getByRole("article", { name: "Génération : Création VM" })).toContainText("Essai à blanc");
  await expect(tray.getByRole("progressbar", { name: /Avancement de la génération Création VM/ })).toHaveAttribute("value", "42");
  await goNav(page, "Accueil"); // le suivi reste visible sur une autre page
  await expect(tray).toBeVisible();
  await expectNoA11yViolations(page);

  await tray.getByRole("button", { name: "Suivre" }).click();
  const live = page.getByRole("dialog", { name: "Génération du brouillon en cours" });
  await expect(live.getByRole("log", { name: "Journal de la génération" })).toBeVisible();
  await live.getByRole("button", { name: "Fermer (la génération continue)" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(tray).toBeVisible(); // la fenêtre est fermée, le suivi reste

  await tray.getByRole("button", { name: "Suivre" }).click();
  await page.getByRole("dialog", { name: "Génération du brouillon en cours" }).getByRole("button", { name: "Annuler la génération" }).click();
  await expect(page.getByRole("dialog", { name: "Génération annulée" })).toContainText("aucun brouillon n'a été enregistré");
  expect(cancels).toEqual(["POST"]);
  await page.getByRole("dialog").locator("footer").getByRole("button", { name: "Fermer" }).click();
  await expect(tray).toHaveCount(0); // une génération annulée n'encombre pas l'écran
});

test("génération terminée pendant qu'on était ailleurs : notification avec le rapport, qu'on peut fermer", async ({ page }) => {
  await page.route("**/ai/policy/", (r) => r.fulfill({ json: { mode: "local_strict", daily_token_budget: 1000000, used_today: 0, modes: {} } }));
  await page.route("**/ai/draft-jobs/", (r) => r.fulfill({ json: { results: [trayJob({ status: "succeeded", progress: 100, workflow: "essai-progression" })] } }));
  await page.route("**/ai/draft-jobs/t-1/", (r) => r.fulfill({ json: { ...finished, id: "t-1" } }));
  await login(page, "alice");
  await goNav(page, "Workflows");
  const tray = page.getByRole("region", { name: "Suivi des générations de workflows" });
  await page.getByRole("button", { name: /^Générations/ }).click();
  await expect(tray).toContainText("Brouillon prêt");
  await tray.getByRole("button", { name: "Voir le rapport" }).click();
  await expect(page.getByRole("dialog", { name: "Brouillon généré" })).toContainText("Essai à blanc : 2 scénario(s)");
  await page.getByRole("dialog").getByRole("button", { name: "Fermer" }).click();
  await tray.getByRole("button", { name: /Fermer la notification de Création VM/ }).click();
  await expect(tray).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: /^Générations/ })).toHaveCount(0); // fermée pour de bon
});
