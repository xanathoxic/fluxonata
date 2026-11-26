import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, login } from "./helpers";

test("rôles Ansible : créer, modifier un fichier, publier une version, ancienne version en lecture seule", async ({ page }) => {
  await login(page);
  const name = `r_${Date.now().toString(36)}`;
  const read = () => page.evaluate(async (n) => (await fetch(`/api/v1/workspaces/demo/ansible-roles/${n}/`)).json(), name);

  await page.goto("/library/ansible-roles");
  await expect(page.getByRole("heading", { name: "Rôles Ansible" })).toBeVisible();
  await expectNoA11yViolations(page);
  await page.getByRole("button", { name: /Nouveau rôle/ }).click();
  await page.getByLabel("Nom du rôle").fill(name);
  await page.getByRole("button", { name: "Créer" }).click();
  await expect(page).toHaveURL(new RegExp(`/library/ansible-roles/${name}$`));

  // le fichier d'entrée s'ouvre ; on le modifie, on ajoute un modèle
  const text = page.getByLabel("Contenu de tasks/main.yml");
  await expect(text).toBeVisible();
  await expectNoA11yViolations(page);
  await expect(page.getByRole("button", { name: "Onglet tasks/main.yml" })).toHaveAttribute("aria-current", "true"); // le fichier s'ouvre dans un onglet
  await text.fill("- name: Poser la page\n  ansible.builtin.template:\n    src: page.j2\n    dest: /tmp/page.txt\n");
  await page.getByRole("button", { name: /Ajouter un fichier/ }).click();
  await page.getByLabel("Chemin du fichier").fill("library/x.py");
  await expect(page.getByRole("alert").filter({ hasText: /n'existe pas dans un rôle/ })).toBeVisible();
  await page.getByLabel("Chemin du fichier").fill("templates/page.j2");
  await page.getByRole("dialog").getByRole("button", { name: "Ajouter", exact: true }).click();
  await page.getByLabel("Contenu de templates/page.j2").fill("bonjour {{ message }}\n");
  await expect(page.getByRole("button", { name: "Onglet templates/page.j2" })).toBeVisible();
  await expect(page.getByRole("button", { name: "templates/page.j2" }).getByRole("img", { name: "modifié" })).toBeVisible(); // non publié : pastille dans l'explorateur
  await page.getByRole("button", { name: "Fermer templates/page.j2" }).click();
  await expect(page.getByRole("button", { name: "Onglet templates/page.j2" })).toHaveCount(0);

  // contrat d'usage : une phrase, les variables tirées de defaults/main.yml, un exemple d'appel
  await page.getByRole("tab", { name: /Contrat/ }).click();
  await page.getByLabel(/À quoi sert le rôle/).fill("Pose la page du laboratoire.");
  await page.getByRole("button", { name: "Pré-remplir depuis defaults/main.yml" }).click();
  await expect(page.getByRole("status").filter({ hasText: /variable\(s\) ajoutée\(s\)/ })).toBeVisible();
  await page
    .getByLabel(/Description de /)
    .first()
    .fill("Le texte affiché sur la page.");
  await page.getByRole("button", { name: "Générer depuis les variables" }).click();
  await expect(page.getByLabel("Un playbook qui utilise le rôle")).toContainText(`role: ${name}`);
  await expectNoA11yViolations(page);
  await page.getByRole("tab", { name: "Fichiers" }).click();

  // publication : 1.0.1 proposée
  await page.getByRole("button", { name: "Publier une nouvelle version…" }).click();
  await expect(page.getByLabel("Numéro de version")).toHaveValue("1.0.1");
  await page.getByLabel("Ce qui change (facultatif)").fill("ajout du modèle");
  await page.getByRole("dialog").getByRole("button", { name: "Publier" }).click();
  await expect.poll(async () => (await read()).latest_version).toBe("1.0.1");
  expect((await read()).versions.map((v: { version: string }) => v.version)).toEqual(["1.0.1", "1.0.0"]);

  // le contrat est enregistré avec la version 1.0.1 ; l'ancienne version n'en a pas et se lit sans champ à modifier
  expect((await page.evaluate(async (n) => (await fetch(`/api/v1/workspaces/demo/ansible-roles/${n}/versions/1.0.1/`)).json(), name)).contract.summary).toBe(
    "Pose la page du laboratoire.",
  );
  // l'ancienne version reste telle quelle et en lecture seule
  await page.getByLabel("Version", { exact: true }).selectOption("1.0.0");
  await expect(page.getByText(/Version ancienne : lecture seule/)).toBeVisible();
  await expect(page.getByLabel("Contenu de tasks/main.yml")).toHaveAttribute("aria-readonly", "true");
  await expect(page.getByLabel("Contenu de tasks/main.yml")).not.toContainText("page.j2");
  await expect(page.getByLabel("Barre d'état")).toContainText("lecture seule");
  await page.getByRole("tab", { name: /Contrat/ }).click();
  await expect(page.getByText(/n'a pas de contrat/)).toBeVisible();
  await expect(page.getByLabel(/À quoi sert le rôle/)).toHaveCount(0);

  // un rôle inutilisé se supprime depuis la liste
  await page.goto("/library/ansible-roles");
  await page.getByRole("button", { name: `Supprimer ${name}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Supprimer" }).click();
  await expect(page.getByRole("link", { name, exact: true })).toHaveCount(0);
});

test("fiche d'une action Ansible : étiquettes et rôles figés visibles ; rien de tel pour une action Python", async ({ page }) => {
  await login(page);
  const id = Date.now().toString(36);
  const role = `r_${id}`;
  const call = (method: string, path: string, body: unknown) =>
    page.evaluate(
      async ({ method, path, body }) => {
        const csrf =
          document.cookie
            .split("; ")
            .find((c) => c.startsWith("csrftoken="))
            ?.split("=")[1] ?? "";
        const r = await fetch(`/api/v1/workspaces/demo${path}`, {
          method,
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
          body: JSON.stringify(body),
        });
        return r.status;
      },
      { method, path, body },
    );
  const schemas = {
    input_schema: { type: "object", description: "Cette action n'a aucune entrée.", properties: {} },
    output_schema: { type: "object", properties: { ok: { type: "boolean", title: "Réussi", description: "Vrai si tout va bien." } } },
  };
  expect(
    await call("POST", "/ansible-roles/", {
      name: role,
      description: "essai",
      files: { "tasks/main.yml": "- name: x\n  ansible.builtin.debug:\n    msg: ok\n" },
    }),
  ).toBe(201);
  expect(
    await call("POST", "/library/actions/", {
      slug: `ans-${id}`,
      name: `Ansible ${id}`,
      description: "Action d'essai : écrit un message sur les machines choisies.",
      version: {
        runtime: "ansible",
        source: `- hosts: all\n  gather_facts: false\n  roles: [${role}]\n`,
        required_tags: ["essai-roles"],
        ansible_roles: [{ role, version: "latest" }],
        ...schemas,
      },
    }),
  ).toBe(201);
  expect(
    await call("POST", "/library/actions/", {
      slug: `py-${id}`,
      name: `Python ${id}`,
      description: "Action d'essai : écrit un message sur les machines choisies.",
      version: { runtime: "python", source: 'def run(inputs):\n    return {"ok": True}\n', ...schemas },
    }),
  ).toBe(201);

  await page.goto(`/library/actions/ans-${id}?onglet=code`);
  const block = page.getByRole("region", { name: "Exécution Ansible" });
  await expect(block).toContainText("essai-roles");
  await expect(block.getByRole("link", { name: role })).toBeVisible();
  await page.goto(`/library/actions/ans-${id}?onglet=versions`);
  await expect(page.getByRole("columnheader", { name: "Rôles" })).toBeVisible();
  await expect(page.getByRole("cell", { name: new RegExp(`${role} 1\\.0\\.0`) })).toBeVisible();

  // nouvelle version à partir de la dernière : les rôles sont repris, le type de changement fixe le numéro
  await page.goto(`/library/actions/ans-${id}`);
  await page.getByRole("button", { name: "Nouvelle version" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Étiquettes de runner exigées (séparées par des virgules)")).toHaveValue("essai-roles");
  await expect(dialog.getByRole("list", { name: "Rôles de l'action" }).getByText(role, { exact: true })).toBeVisible();
  await expect(dialog.getByLabel(`Version du rôle ${role}`)).toHaveValue("1.0.0");
  await expect(dialog.getByRole("checkbox")).toHaveCount(0); // seuls les rôles choisis sont affichés, pas toute la bibliothèque
  await dialog.getByLabel(/Ajouter un rôle/).fill(role.slice(0, 6));
  await expect(dialog.getByRole("button", { name: `Ajouter le rôle ${role}` })).toHaveCount(0); // déjà choisi
  await expectNoA11yViolations(page);
  await dialog.getByLabel("Type de changement").selectOption("patch");
  await dialog.getByLabel("Ce qui change (facultatif)").fill("correctif");
  await dialog.getByRole("tab", { name: "Entrées", exact: true }).click();
  // contrat édité par champs (le JSON reste dans « Avancé »)
  await dialog.getByRole("button", { name: "Ajouter une entrée" }).click();
  await expect(dialog.getByLabel("Identifiant de l'entrée 1")).toHaveValue("champ_1");
  await dialog.getByLabel("Titre de champ_1").fill("Essai");
  await dialog.getByRole("button", { name: "Supprimer l'entrée champ_1" }).click();
  await expect(dialog.getByLabel("Identifiant de l'entrée 1")).toHaveCount(0);
  await dialog.getByText("Avancé : JSON du schéma des entrées").click();
  await expect(dialog.getByLabel("Schéma des entrées (JSON Schema)")).toBeVisible();
  await dialog.getByRole("tab", { name: "Sorties", exact: true }).click();
  await expect(dialog.getByLabel("Identifiant de la sortie 1")).toBeVisible();
  await dialog.getByRole("tab", { name: "Playbook" }).click();
  await dialog.getByRole("button", { name: "Ouvrir l'éditeur avancé" }).click();
  await expect(dialog.getByRole("button", { name: /Essayer dans le bac à sable/ })).toBeVisible();
  await expect(dialog.getByLabel(/Code de l'action \(Ansible\)/)).toBeVisible();
  await dialog.getByRole("button", { name: "Retour au formulaire" }).click();
  await dialog.getByRole("button", { name: "Publier la nouvelle version" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.goto(`/library/actions/ans-${id}?onglet=versions`);
  await expect(page.getByRole("cell", { name: /1\.0\.1 — correctif/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: new RegExp(`${role} 1\\.0\\.0`) })).toHaveCount(2);

  // éditeur avancé : essai du brouillon dans le bac à sable, sans publier
  await page.goto(`/library/actions/py-${id}`);
  await page.getByRole("button", { name: "Nouvelle version" }).click();
  const py = page.getByRole("dialog");
  await py.getByRole("tab", { name: "Code" }).click();
  await py.getByRole("button", { name: "Ouvrir l'éditeur avancé" }).click();
  // le panneau d'essai se replie pour laisser toute la largeur au code, et se rouvre
  const panel = py.getByRole("complementary", { name: "Essai" });
  await py.getByRole("button", { name: /Masquer le panneau d'essai/ }).click();
  await expect(panel).toBeHidden();
  await py.getByRole("button", { name: /Afficher le panneau d'essai/ }).click();
  await expect(panel).toBeVisible();
  await py.getByRole("button", { name: /Masquer le panneau d'essai/ }).click(); // replié au lancement : il se rouvre tout seul pour montrer le journal
  await py.getByRole("button", { name: /Essayer dans le bac à sable/ }).click();
  await expect(py.getByRole("status").filter({ hasText: "Dernier essai réussi" })).toBeVisible({ timeout: 90_000 });
  await expect(panel).toBeVisible();
  await expectNoA11yViolations(page);
  await py.getByRole("tab", { name: "Résultat" }).click();
  await expect(py.getByText(/"ok": true/)).toBeVisible();
  await py.getByRole("button", { name: "Retour au formulaire" }).click();
  await page.keyboard.press("Escape");
  await page.goto(`/library/actions/py-${id}?onglet=versions`);
  await expect(page.getByRole("cell", { name: /^1\.0\.0/ })).toHaveCount(1);
  await expect(page.getByRole("cell", { name: /^0\.0\./ })).toHaveCount(0); // l'essai n'a rien publié

  await page.goto(`/library/actions/py-${id}?onglet=code`);
  await expect(page.getByRole("region", { name: "Exécution Ansible" })).toHaveCount(0);
  await page.goto(`/library/actions/py-${id}?onglet=versions`);
  await expect(page.getByRole("columnheader", { name: "Rôles" })).toHaveCount(0);

  // la démo reste propre : on retire ce que le test a créé (les actions d'abord, le rôle ensuite)
  await call("DELETE", `/actions/ans-${id}/delete/`, undefined);
  await call("DELETE", `/actions/py-${id}/delete/`, undefined);
  expect(await call("DELETE", `/ansible-roles/${role}/`, undefined)).toBe(204);
});

test("rôles et inventaires ont leur arborescence de dossiers : créer un dossier, y ranger, déplacer, filtrer", async ({ page }) => {
  await login(page);
  const id = Date.now().toString(36);
  const dossier = `Essai${id}`;
  const role = `r_${id}`;
  const call = (method: string, path: string, body?: unknown) =>
    page.evaluate(
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
        return r.status;
      },
      { method, path, body },
    );
  const task = "- name: x\n  ansible.builtin.debug:\n    msg: ok\n";
  expect(await call("POST", "/ansible-roles/", { name: role, description: "essai", files: { "tasks/main.yml": task } })).toBe(201);
  expect(await call("POST", "/inventories/", { slug: `inv-${id}`, name: `Inventaire ${id}`, hosts: [], groups: [], variables: {} })).toBe(201);
  expect(await call("POST", "/folders/", { kind: "ansible_role", path: dossier })).toBe(201);
  expect(await call("POST", "/folders/", { kind: "inventory", path: dossier })).toBe(201);

  // rôle : à la racine, puis déplacé dans le dossier ; le filtre par dossier suit
  await page.goto("/library/actions?tab=roles");
  const tree = page.getByRole("navigation", { name: "Dossiers de rôles" });
  await expect(tree.getByRole("button", { name: new RegExp(dossier) })).toBeVisible();
  await expect(page.getByRole("link", { name: role, exact: true })).toBeVisible();
  await page.getByRole("button", { name: `Déplacer le rôle ${role} dans un dossier` }).click();
  await page.getByLabel("Dossier", { exact: true }).fill(dossier);
  await page.getByRole("dialog").getByRole("button", { name: "Déplacer" }).click();
  await expect(page.getByRole("link", { name: role, exact: true })).toHaveCount(0); // plus à la racine
  await tree.getByRole("button", { name: new RegExp(dossier) }).click();
  await expect(page.getByRole("link", { name: role, exact: true })).toBeVisible();

  // inventaire : même parcours
  await page.goto("/library/actions?tab=inventories");
  const treeInv = page.getByRole("navigation", { name: "Dossiers d'inventaires" });
  await page.getByRole("button", { name: `Déplacer l'inventaire Inventaire ${id} dans un dossier` }).click();
  await page.getByLabel("Dossier", { exact: true }).fill(dossier);
  await page.getByRole("dialog").getByRole("button", { name: "Déplacer" }).click();
  await expect(page.getByRole("link", { name: `Inventaire ${id}`, exact: true })).toHaveCount(0);
  await treeInv.getByRole("button", { name: new RegExp(dossier) }).click();
  await expect(page.getByRole("link", { name: `Inventaire ${id}`, exact: true })).toBeVisible();
  await expectNoA11yViolations(page);

  // la démo reste propre
  await call("DELETE", `/inventories/inv-${id}/`);
  await call("DELETE", `/ansible-roles/${role}/`);
  await call("DELETE", `/folders/?kind=ansible_role&path=${dossier}`);
  await call("DELETE", `/folders/?kind=inventory&path=${dossier}`);
});
