import { expect, test } from "@playwright/test";
import { expectNoA11yViolations, goNav, login } from "./helpers";

const LOGO_DARK = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40"><rect width="120" height="40" rx="8" fill="#e2e8f0"/><circle cx="20" cy="20" r="9" fill="#0f172a"/></svg>',
);
const LOGO = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 40"><rect width="120" height="40" rx="8" fill="#0b5ed7"/><circle cx="20" cy="20" r="9" fill="#fff"/></svg>',
);

test("Image de marque : nom, logo, couleur et page de connexion, appliqués avant la connexion, puis rétablis", async ({ page, browser }) => {
  await login(page, "admin");
  try {
    await goNav(page, "Plate-forme");
    await page.getByRole("tab", { name: "Image de marque" }).click();
    await expect(page.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
    await expectNoA11yViolations(page);

    await page.getByLabel("Nom du produit").fill("Atelier Bleu");
    await page.getByLabel("Slogan (page de connexion)").fill("Vos flux sous contrôle");
    await page.getByRole("tab", { name: "Connexion", exact: true }).click();
    await page.getByLabel("Message d'accueil").fill("Accès réservé au personnel.");
    await page.getByLabel("Texte du pied de page").fill("Support interne");
    await page.getByLabel("Lien du pied de page (facultatif)").fill("https://support.example.org/aide");
    await page.getByRole("radio", { name: /Dégradé/ }).check();
    await page.getByRole("tab", { name: "Couleurs" }).click();
    await page.getByRole("button", { name: /Azur/ }).click();
    // une teinte trop claire est refusée, avec le contraste, et l'onglet porte un « ! »
    await page.getByLabel("Couleur personnalisée").fill("#38bdf8");
    await expect(page.getByText(/trop claire/)).toBeVisible();
    await expect(page.getByRole("tab", { name: /Couleurs/ })).toContainText("!");
    await expect(page.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
    await page.getByRole("button", { name: "Ajuster automatiquement" }).click();
    await expect(page.getByText(/Tous les contrastes atteignent/)).toBeVisible();
    await page.getByRole("button", { name: /Azur/ }).click();

    // barre du haut sombre, menu de gauche clair : le texte et les états se déduisent des fonds
    await page.getByRole("tab", { name: "Barres" }).click();
    await page
      .getByRole("list", { name: /Fonds proposés : barre du haut/ })
      .getByRole("button", { name: /Nuit/ })
      .click();
    await page
      .getByRole("list", { name: /Fonds proposés : menu de gauche/ })
      .getByRole("button", { name: /Gris clair/ })
      .click();

    await page.getByRole("tab", { name: "Logos" }).click();
    await page.locator('input[type="file"]').first().setInputFiles({ name: "logo.svg", mimeType: "image/svg+xml", buffer: LOGO });
    await expect(page.getByAltText("Logo actuel")).toBeVisible();
    await page.locator('input[type="file"]').nth(1).setInputFiles({ name: "logo-clair.svg", mimeType: "image/svg+xml", buffer: LOGO_DARK });
    await expect(page.getByAltText("Logo pour fond sombre actuel")).toBeVisible();
    await page.getByRole("button", { name: "Enregistrer" }).click();
    await expect(page.getByText(/l'image de marque est appliquée/)).toBeVisible();

    // appliqué à l'application, sans recharger : barre sombre avec le logo pour fond sombre, menu clair
    await expect(page.locator(".topbar .logo-img")).toBeVisible();
    await expect(page.locator(".topbar .logo-img")).toHaveAttribute("src", /assets\/logo_dark\//);
    expect(await page.evaluate(() => document.documentElement.style.getPropertyValue("--accent"))).toBe("#0b5ed7");
    const colors = await page.evaluate(() => {
      const c = (sel: string, prop: "backgroundColor" | "color") => getComputedStyle(document.querySelector(sel) as Element)[prop];
      return [c(".topbar", "backgroundColor"), c(".topbar .who", "color"), c(".sidebar", "backgroundColor"), c(".sidebar a.active", "color")];
    });
    expect(colors[0]).toBe("rgb(15, 23, 42)");
    expect(colors[2]).toBe("rgb(241, 245, 249)");
    expect(colors[1]).not.toBe(colors[0]);
    await expectNoA11yViolations(page); // contrastes réels du texte de la barre sombre et du menu clair
    await page.getByRole("link", { name: "Workflows" }).first().click();
    await expectNoA11yViolations(page);
    await goNav(page, "Plate-forme");
    await page.getByRole("tab", { name: "Image de marque" }).click();
    await expectNoA11yViolations(page);

    // une personne non connectée voit la même image de marque
    const anonContext = await browser.newContext();
    const anon = await anonContext.newPage();
    await anon.goto("/");
    await expect(anon.getByText("Vos flux sous contrôle")).toBeVisible();
    await expect(anon.getByText("Accès réservé au personnel.")).toBeVisible();
    await expect(anon.getByRole("link", { name: /Support interne/ })).toHaveAttribute("href", "https://support.example.org/aide");
    await expect(anon.locator(".login-title .logo-img")).toBeVisible();
    expect(await anon.evaluate(() => getComputedStyle(document.querySelector(".login-shell button.primary") as Element).backgroundColor)).toBe(
      "rgb(11, 94, 215)",
    );
    await expectNoA11yViolations(anon);
    // le logo est servi comme image : jamais exécuté, jamais devinable comme page
    const asset = await anon.request.get(
      await anon
        .locator(".login-title .logo-img")
        .getAttribute("src")
        .then((s) => s ?? ""),
    );
    expect(asset.headers()["content-type"]).toBe("image/svg+xml");
    expect(asset.headers()["content-security-policy"]).toContain("sandbox");
    await anonContext.close();

    // un SVG qui porte un script est refusé par le serveur
    await page.getByRole("tab", { name: "Logos" }).click();
    await page
      .locator('input[type="file"]')
      .nth(2)
      .setInputFiles({
        name: "x.svg",
        mimeType: "image/svg+xml",
        buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script>alert(1)</script></svg>'),
      });
    await expect(page.getByRole("alert").filter({ hasText: /élément interdit/ })).toBeVisible();
  } finally {
    await page.evaluate(async () => {
      const csrf =
        document.cookie
          .split("; ")
          .find((c) => c.startsWith("csrftoken="))
          ?.split("=")[1] ?? "";
      await fetch("/api/v1/platform/branding/reset/", { method: "POST", headers: { "X-CSRFToken": csrf } });
    });
  }
  await page.reload();
  await expect(page.locator(".topbar .logo-name")).toContainText("Flux");
});

test("Image de marque : refusée à une personne qui n'administre pas la plate-forme", async ({ page }) => {
  await login(page, "alice");
  const codes = await page.evaluate(async () => {
    const csrf =
      document.cookie
        .split("; ")
        .find((c) => c.startsWith("csrftoken="))
        ?.split("=")[1] ?? "";
    const get = (await fetch("/api/v1/platform/branding/", { credentials: "same-origin" })).status;
    const patch = (
      await fetch("/api/v1/platform/branding/", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ product_name: "X" }),
      })
    ).status;
    const open = (await fetch("/api/v1/branding/", { credentials: "same-origin" })).status;
    return [get, patch, open];
  });
  expect(codes).toEqual([403, 403, 200]);
});
