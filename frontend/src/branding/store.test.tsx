import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Logo } from "../components/Logo";
import { titleFor } from "../components/useHeadingTitle";
import { DEFAULT_BRANDING, getBranding, initBranding, loadBranding, setBranding, toPublic, type Branding } from "./store";

const custom = (over: Partial<Branding>): Branding => ({ ...DEFAULT_BRANDING, version: 2, ...over });

beforeEach(() => {
  localStorage.clear();
  document.head.innerHTML = '<link rel="icon" href="/favicon.svg">';
  document.documentElement.removeAttribute("style");
  setBranding(DEFAULT_BRANDING, false);
});

describe("image de marque appliquée à la page", () => {
  it("pose les variables de couleur pour un accent personnalisé et les retire au retour à l'origine", () => {
    setBranding(custom({ accent: "#0b5ed7", custom_accent: true }));
    const root = document.documentElement.style;
    expect(root.getPropertyValue("--accent")).toBe("#0b5ed7");
    expect(root.getPropertyValue("--link")).toBe("#0b5ed7");
    expect(root.getPropertyValue("--accent-soft")).toMatch(/^#/);
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute("content")).toBe("#0b5ed7");
    setBranding(custom({ accent: "#0e7490", custom_accent: false }));
    expect(root.getPropertyValue("--accent")).toBe("");
  });
  it("pose les variables et les repères des barres choisies, et les retire au retour au blanc d'origine", () => {
    const root = document.documentElement;
    setBranding(custom({ topbar_bg: "#0f172a", sidebar_bg: "#f1f5f9" }));
    expect(root.hasAttribute("data-topbar") && root.hasAttribute("data-sidebar")).toBe(true);
    expect(root.style.getPropertyValue("--tb-bg")).toBe("#0f172a");
    expect(root.style.getPropertyValue("--tb-fg")).toBe("#ffffff");
    expect(root.style.getPropertyValue("--sb-fg")).toBe("#0f172a");
    setBranding(custom({ topbar_bg: "", sidebar_bg: "#FFFFFF" }));
    expect(root.hasAttribute("data-topbar") || root.hasAttribute("data-sidebar")).toBe(false);
    expect(root.style.getPropertyValue("--tb-bg")).toBe("");
    setBranding(custom({ topbar_bg: "pas une couleur" }));
    expect(root.hasAttribute("data-topbar")).toBe(false);
  });
  it("ne pose rien pour une couleur invalide reçue du serveur", () => {
    setBranding(custom({ accent: "n'importe quoi", custom_accent: true }));
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("");
  });
  it("change l'icône de l'onglet, avec le logo à défaut d'icône", () => {
    const asset = (u: string) => ({ url: u, content_type: "image/png", width: 16, height: 16 });
    setBranding(custom({ logo: asset("/api/v1/branding/assets/logo/?v=a") }));
    expect(document.querySelector('link[rel="icon"]')?.getAttribute("href")).toBe("/api/v1/branding/assets/logo/?v=a");
    setBranding(custom({ logo: asset("/l"), icon: asset("/api/v1/branding/assets/icon/?v=b") }));
    expect(document.querySelector('link[rel="icon"]')?.getAttribute("href")).toBe("/api/v1/branding/assets/icon/?v=b");
    setBranding(DEFAULT_BRANDING);
    expect(document.querySelector('link[rel="icon"]')?.getAttribute("href")).toBe("/favicon.svg");
  });
  it("s'applique avant le premier affichage depuis la dernière image connue, et ignore un cache abîmé", () => {
    localStorage.setItem("fluxonata.branding", JSON.stringify(custom({ name: "Atelier", custom_name: true })));
    initBranding();
    expect(getBranding().name).toBe("Atelier");
    localStorage.setItem("fluxonata.branding", "{pas du json");
    setBranding(DEFAULT_BRANDING, false);
    initBranding();
    expect(getBranding().name).toBe("Fluxonata");
  });
  it("garde l'image connue quand le serveur ne répond pas, et adopte la sienne sinon", async () => {
    setBranding(custom({ name: "Atelier", custom_name: true }), false);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("hors ligne")));
    await loadBranding();
    expect(getBranding().name).toBe("Atelier");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => custom({ name: "Autre", custom_name: true, version: 5 }) }));
    await loadBranding();
    expect(getBranding().name).toBe("Autre");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));
    await loadBranding();
    expect(getBranding().name).toBe("Autre");
    vi.unstubAllGlobals();
  });
  it("ne garde dans le navigateur que la partie publique", () => {
    const admin = { ...custom({}), stored: { accent: "" }, updated_by: "root", limits: {} } as unknown as Branding;
    expect(Object.keys(toPublic(admin)).sort()).not.toContain("updated_by");
    expect(Object.keys(toPublic(admin))).not.toContain("stored");
  });
  it("le titre des pages porte le nom choisi", () => {
    setBranding(custom({ name: "Atelier Bleu", custom_name: true }));
    expect(titleFor("Workflows")).toBe("Workflows — Atelier Bleu");
    expect(titleFor("")).toBe("Atelier Bleu");
  });
});

describe("Logo", () => {
  it("prend le logo pour fond sombre dans la barre du haut seulement quand elle est sombre", () => {
    const asset = (u: string) => ({ url: u, content_type: "image/svg+xml", width: 10, height: 10 });
    setBranding(custom({ name: "A", custom_name: true, logo: asset("/clair"), logo_dark: asset("/sombre"), topbar_bg: "#0f172a" }));
    const { rerender } = render(<Logo onTopbar />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "/sombre");
    rerender(<Logo />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "/clair"); // hors de la barre : toujours le logo normal
    setBranding(custom({ name: "A", custom_name: true, logo: asset("/clair"), logo_dark: asset("/sombre"), topbar_bg: "#f1f5f9" }));
    rerender(<Logo onTopbar />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "/clair");
    setBranding(custom({ name: "A", custom_name: true, logo: asset("/clair"), topbar_bg: "#0f172a" }));
    rerender(<Logo onTopbar />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "/clair"); // pas de logo sombre : repli sur le logo normal
  });
  it("montre la marque d'origine par défaut", () => {
    render(<Logo />);
    expect(screen.getByText("onata")).toBeInTheDocument();
  });
  it("montre une pastille à l'initiale et le nom choisi, sans logo", () => {
    setBranding(custom({ name: "atelier", custom_name: true }));
    render(<Logo />);
    expect(screen.getByText("atelier")).toBeInTheDocument();
    expect(screen.getByText("A")).toBeInTheDocument();
  });
  it("montre le logo téléversé, avec le nom en texte seulement s'il n'y est pas déjà", () => {
    const logo = { url: "/api/v1/branding/assets/logo/?v=x", content_type: "image/png", width: 100, height: 30 };
    setBranding(custom({ name: "Atelier", custom_name: true, logo, logo_has_name: true }));
    const { rerender } = render(<Logo />);
    expect(screen.getByRole("img", { name: "Atelier" })).toHaveAttribute("src", logo.url);
    setBranding(custom({ name: "Atelier", custom_name: true, logo, logo_has_name: false }));
    rerender(<Logo />);
    expect(screen.getByText("Atelier")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Atelier" })).toBeNull(); // image décorative : le nom est écrit à côté
  });
});
