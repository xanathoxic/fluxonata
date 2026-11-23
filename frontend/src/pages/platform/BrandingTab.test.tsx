import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_BRANDING, getBranding, setBranding } from "../../branding/store";
import { BrandingTab } from "./BrandingTab";

const api = vi.fn();
vi.mock("../../api", async (orig) => ({ ...(await orig<typeof import("../../api")>()), api: (...a: unknown[]) => api(...a) }));

const base = {
  ...DEFAULT_BRANDING,
  version: 1,
  defaults: { name: "Fluxonata", accent: "#0e7490" },
  stored: { product_name: "", accent: "", topbar_bg: "", sidebar_bg: "" },
  updated_by: "",
  limits: { image_bytes: 262144, side: 2048, formats: ["PNG", "JPEG", "WebP", "SVG"] },
};
let server: typeof base & Record<string, unknown>;

beforeEach(() => {
  server = { ...base };
  setBranding(DEFAULT_BRANDING, false);
  api.mockReset();
  api.mockImplementation(async (path: string, opts?: { method?: string; json?: Record<string, unknown>; form?: FormData }) => {
    if (path === "/platform/branding/" && opts?.method === "PATCH") {
      const j = opts.json as { product_name: string; accent: string };
      server = {
        ...server,
        ...opts.json,
        name: j.product_name || "Fluxonata",
        custom_name: !!j.product_name,
        accent: j.accent || "#0e7490",
        custom_accent: !!j.accent,
        footer: { text: "", url: "" },
        stored: {
          product_name: j.product_name,
          accent: j.accent,
          topbar_bg: (opts.json as Record<string, string>).topbar_bg,
          sidebar_bg: (opts.json as Record<string, string>).sidebar_bg,
        },
        version: 2,
      } as typeof server;
      return server;
    }
    if (path === "/platform/branding/reset/") {
      server = { ...base, version: 3 };
      return server;
    }
    if (path.startsWith("/platform/branding/assets/logo/") && opts?.method === "PUT") {
      server = {
        ...server,
        logo: { url: "/api/v1/branding/assets/logo/?v=n", content_type: "image/png", width: 10, height: 10, bytes: 2048 },
      } as typeof server;
      return server;
    }
    return server;
  });
});

async function openTab(name: string) {
  await userEvent.click(await screen.findByRole("tab", { name }));
}

function show() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BrandingTab />
    </QueryClientProvider>,
  );
}

describe("onglet Image de marque", () => {
  it("n'enregistre rien tant que rien n'a changé", async () => {
    show();
    expect(await screen.findByRole("button", { name: "Enregistrer" })).toBeDisabled();
  });

  it("enregistre le nom et une couleur conforme, et les applique aussitôt à l'application", async () => {
    show();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Nom du produit"), "Atelier Bleu");
    await openTab("Couleurs");
    await user.click(screen.getByRole("button", { name: /Azur/ }));
    expect(screen.getByText(/Tous les contrastes atteignent 4.5:1/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/platform/branding/",
        expect.objectContaining({ method: "PATCH", json: expect.objectContaining({ product_name: "Atelier Bleu", accent: "#0b5ed7" }) }),
      ),
    );
    await waitFor(() => expect(getBranding().name).toBe("Atelier Bleu"));
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#0b5ed7");
    expect(await screen.findByText(/l'image de marque est appliquée/)).toBeInTheDocument();
  });

  it("refuse une couleur trop claire avec les contrastes, puis l'ajuste automatiquement", async () => {
    show();
    const user = userEvent.setup();
    await openTab("Couleurs");
    const hex = await screen.findByLabelText("Couleur personnalisée");
    await user.type(hex, "#38bdf8");
    expect(screen.getByText(/trop claire : 1\.95:1/)).toBeInTheDocument();
    expect(screen.getAllByText(/✗/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Ajuster automatiquement" }));
    expect(screen.getByText(/Tous les contrastes atteignent/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeEnabled();
  });

  it("signale une couleur mal écrite", async () => {
    show();
    const user = userEvent.setup();
    await openTab("Couleurs");
    await user.type(await screen.findByLabelText("Couleur personnalisée"), "bleu");
    expect(screen.getByText(/doit s'écrire #rrggbb/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
  });

  it("envoie un logo dès qu'il est choisi, et refuse tout de suite un fichier trop gros", async () => {
    show();
    const user = userEvent.setup();
    await openTab("Logos");
    const [logoInput, , iconInput] = Array.from(document.querySelectorAll<HTMLInputElement>('input[type="file"]'));
    if (!logoInput || !iconInput) throw new Error("champs de fichier absents");
    await user.upload(logoInput, new File([new Uint8Array(10)], "logo.png", { type: "image/png" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/platform/branding/assets/logo/", expect.objectContaining({ method: "PUT" })));
    expect(await screen.findByAltText("Logo actuel")).toHaveAttribute("src", expect.stringContaining("/branding/assets/logo/"));
    expect(getBranding().logo?.url).toContain("v=n");
    api.mockClear();
    await user.upload(iconInput, new File([new Uint8Array(262145)], "gros.png", { type: "image/png" }));
    expect(await screen.findByText(/dépasse 256 Ko/)).toBeInTheDocument();
    expect(api).not.toHaveBeenCalledWith("/platform/branding/assets/icon/", expect.anything());
  });

  it("rétablit l'image d'origine après confirmation", async () => {
    server = { ...base, name: "Atelier", custom_name: true, stored: { product_name: "Atelier", accent: "", topbar_bg: "", sidebar_bg: "" } } as typeof server;
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Rétablir l'image d'origine" }));
    await user.click(screen.getByRole("button", { name: "Rétablir" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/platform/branding/reset/", expect.objectContaining({ method: "POST" })));
    await waitFor(() => expect(getBranding().name).toBe("Fluxonata"));
  });

  it("range les réglages dans des onglets et garde ce qui a été saisi quand on change d'onglet", async () => {
    show();
    const user = userEvent.setup();
    const tabs = (await screen.findAllByRole("tab")).map((t) => t.textContent);
    expect(tabs).toEqual(expect.arrayContaining(["Identité", "Logos", "Couleurs", "Barres", "Connexion", "Aperçu : application", "Aperçu : connexion"]));
    expect(screen.queryByLabelText("Couleur personnalisée")).toBeNull(); // un seul volet à la fois
    await user.type(screen.getByLabelText("Nom du produit"), "Atelier");
    await openTab("Connexion");
    expect(screen.getByLabelText("Message d'accueil")).toBeInTheDocument();
    await openTab("Identité");
    expect(screen.getByLabelText("Nom du produit")).toHaveValue("Atelier");
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeEnabled();
  });

  it("règle le fond de la barre du haut et du menu de gauche, avec le texte déduit et ses contrastes", async () => {
    show();
    const user = userEvent.setup();
    await openTab("Barres");
    const bar = screen.getByRole("list", { name: /Fonds proposés : barre du haut/ });
    await user.click(within(bar).getByRole("button", { name: /Nuit/ }));
    expect(screen.getAllByText(/Le texte \(blanc\)/).length).toBe(1);
    await user.click(within(screen.getByRole("list", { name: /Fonds proposés : menu de gauche/ })).getByRole("button", { name: /Gris clair/ }));
    expect(screen.getAllByText(/Le texte \(foncé\)/).length).toBe(1);
    expect(screen.getAllByText(/:1 ✓/).length).toBe(8);
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/platform/branding/",
        expect.objectContaining({ method: "PATCH", json: expect.objectContaining({ topbar_bg: "#0f172a", sidebar_bg: "#f1f5f9" }) }),
      ),
    );
  });

  it("copie la couleur de la barre du haut vers le menu de gauche, et signale une couleur mal écrite par un « ! » sur l'onglet", async () => {
    show();
    const user = userEvent.setup();
    await openTab("Barres");
    await user.type(screen.getByLabelText("Couleur de fond", { selector: "#br-topbar" }), "#334155");
    await user.click(screen.getByRole("button", { name: /Appliquer la couleur de la barre du haut/ }));
    expect(screen.getByLabelText("Couleur de fond", { selector: "#br-sidebar" })).toHaveValue("#334155");
    await user.clear(screen.getByLabelText("Couleur de fond", { selector: "#br-sidebar" }));
    await user.type(screen.getByLabelText("Couleur de fond", { selector: "#br-sidebar" }), "bleu");
    expect(screen.getByRole("tab", { name: /Barres/ })).toHaveTextContent("!");
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
  });
});
