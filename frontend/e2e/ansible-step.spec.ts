import { expect, test, type Page } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const uniq = () => `${Date.now()}`.slice(-7);
const addTile = async (page: Page, label: string) => {
  await page.getByLabel("Rechercher un élément").fill(label);
  await page.getByRole("button", { name: `Ajouter : ${label}`, exact: true }).click();
  await page.getByLabel("Rechercher un élément").fill("");
};
const panelTab = (page: Page, name: string) => page.getByRole("tab", { name, exact: true }).click();

async function api(page: Page, method: string, path: string, body?: unknown) {
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
      return r.status;
    },
    { method, path, body },
  );
}

test("étape « Playbook Ansible » : ajoutée depuis la palette, rôles et cible choisis, éditeur avec essai, erreurs expliquées", async ({ page }) => {
  await login(page);
  const id = uniq();
  const role = `r_pb${id}`;
  const inventory = `inv-pb${id}`;
  expect(
    await api(page, "POST", "/ansible-roles/", { name: role, description: "essai", files: { "tasks/main.yml": "- ansible.builtin.debug: {msg: ok}\n" } }),
  ).toBe(201);
  expect(await api(page, "POST", "/inventories/", { slug: inventory, name: `Inventaire ${id}`, hosts: [], groups: [], variables: {} })).toBe(201);

  await goNav(page, "Workflows");
  await page.getByRole("button", { name: "Nouveau workflow" }).click();
  await page.getByLabel("Nom du nouveau workflow").fill(`Playbook E2E ${id}`);
  await page.getByRole("button", { name: "Créer et ouvrir l'éditeur" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(`Playbook E2E ${id}`);

  await addTile(page, "Playbook Ansible");
  await panelTab(page, "Playbook");

  // Playbook : étiquette de runner puis un rôle de la bibliothèque, trouvé par la recherche
  await page.getByLabel(/Étiquettes de runner exigées/).fill("ansible-essai");
  await page.getByLabel(/Ajouter un rôle/).fill(role.slice(0, 8));
  await page.getByRole("button", { name: new RegExp(`Ajouter le rôle ${role}`) }).click();
  await expect(page.getByRole("list", { name: "Rôles de l'action" }).getByText(role, { exact: true })).toBeVisible();
  await expectNoA11yViolations(page);

  // Cible et accès : l'inventaire de la bibliothèque
  await panelTab(page, "Cible et accès");
  await page.getByLabel("Inventaire", { exact: true }).selectOption(inventory);
  await expectNoA11yViolations(page);

  // Paramètres : le mode vérification est proposé et lié à une valeur
  await panelTab(page, "Paramètres");
  await expect(page.getByLabel("Mode vérification", { exact: true })).toBeVisible();

  // l'étape signale ce qu'il manque : clés d'hôtes et connexion SSH
  await panelTab(page, "Général");
  await expect(page.getByRole("list", { name: "Problèmes de cette étape" })).toContainText(/clés d'hôtes|known_hosts/);

  // l'éditeur de playbook s'ouvre (éditeur avancé, essai dans la fenêtre)
  await panelTab(page, "Playbook");
  await page.getByRole("button", { name: "Ouvrir l'éditeur de playbook" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: /Essayer dans le bac à sable/ })).toBeVisible();
  await expect(dialog.getByLabel(/Code de l'action \(Ansible\)/)).toBeVisible();
  await dialog.getByRole("button", { name: "Fermer l'éditeur" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // la démo reste propre
  await api(page, "DELETE", `/inventories/${inventory}/`);
  await api(page, "DELETE", `/ansible-roles/${role}/`);
});
