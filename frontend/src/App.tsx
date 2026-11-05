import { AiJobsTray } from "./components/AiJobsTray";
import { GlobalSearch } from "./components/GlobalSearch";
import { PHONE, useMediaQuery } from "./components/useMediaQuery";
import { ToastProvider } from "./components/Toast";
import { CatalogRunDetailPage, CatalogRunsPage } from "./pages/catalog/CatalogRuns";
import { Logo } from "./components/Logo";
import { SkillDetailPage } from "./pages/skills/SkillDetailPage";
import { SkillsPage } from "./pages/skills/SkillsPage";
import { PlatformPage } from "./pages/PlatformPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { AccountPage, ForcedPasswordPage } from "./pages/AccountPage";
import { NavIcon } from "./components/NavIcon";
import { FormIcon } from "./forms/FormIcon";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useParams } from "react-router-dom";
import { api, type ApiError, type Me } from "./api";
import { AppContext } from "./context";
import { t } from "./i18n";
import { roleLabel } from "./lib";
import { ActionDetailPage } from "./pages/ActionDetailPage";
import { CatalogDetailPage, CatalogPage } from "./pages/CatalogPage";
import { ExecutionDetailPage } from "./pages/ExecutionDetailPage";
import { OpsPage } from "./pages/OpsPage";
import { ExecutionsPage } from "./pages/ExecutionsPage";
import { ActionLibraryPage } from "./pages/ActionLibraryPage";
import { AdminPage } from "./pages/AdminPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { HomePage } from "./pages/HomePage";
import { LibraryPage } from "./pages/LibraryPage";
import { LoginPage } from "./pages/LoginPage";
import { CompareRunsPage } from "./pages/CompareRunsPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { TriggersPage } from "./pages/TriggersPage";
import { FormsPage } from "./pages/forms/FormsPage";
import { FormPage } from "./pages/forms/FormPage";
import { ControlsPage } from "./pages/controls/ControlsPage";
import { ControlPage } from "./pages/controls/ControlPage";
import { WorkflowRunPage } from "./pages/WorkflowRunPage";
import { WorkflowsPage } from "./pages/WorkflowsPage";
import { WorkflowPage } from "./workflow/WorkflowPage";

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* preference only: ignore */
  }
}

export function App() {
  const qc = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const phone = useMediaQuery(PHONE);
  const [collapsed, setCollapsed] = useState(stored("orch.sidebar") === "collapsed"); // desktop: icon-only rail
  const location = useLocation();
  useEffect(() => setMenuOpen(false), [location.pathname]);
  const meQuery = useQuery<Me, ApiError>({
    queryKey: ["me"],
    queryFn: () => api<Me>("/auth/me/"),
    retry: false,
  });
  const [wsSlug, setWsSlug] = useState(stored("orch.workspace"));
  const [env, setEnv] = useState(stored("orch.environment") ?? "test");

  useEffect(() => {
    if (wsSlug) store("orch.workspace", wsSlug);
    store("orch.environment", env);
  }, [wsSlug, env]);
  useEffect(() => store("orch.sidebar", collapsed ? "collapsed" : "expanded"), [collapsed]);
  // Tables become cards on a phone: each cell carries its column title (read from the header) for the card layout
  useEffect(() => {
    if (!phone) return;
    const label = () => document.querySelectorAll("table.adm-table").forEach((t) => {
      const heads = [...t.querySelectorAll("thead th")].map((h) => (h.querySelector(".sr-only") ? "" : (h.textContent ?? "").trim()));
      t.querySelectorAll("tbody tr").forEach((tr) => [...tr.children].forEach((td, i) => { if (td instanceof HTMLElement && td.dataset.label !== (heads[i] ?? "")) td.dataset.label = heads[i] ?? ""; }));
    });
    label();
    const mo = new MutationObserver(label);
    mo.observe(document.body, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, [phone]);
  // Ctrl+K opens the global search, unless a screen already used it (the workflow editor keeps « go to a step »: it handles the key first)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && !e.defaultPrevented) { e.preventDefault(); setSearching(true); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const logout = async () => {
    try { await api("/auth/logout/", { method: "POST" }); } finally {
      // whatever the server answered, this browser forgets the session: every cached answer goes, and the login page shows at once
      // (the "me" query stays: the screen is subscribed to it, and clear() would detach it from its observer)
      qc.setQueryData(["me"], null);
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    }
  };

  if (meQuery.isLoading) return <p role="status" className="center">{t("common.loading")}</p>;
  if (meQuery.isError || !meQuery.data) return <LoginPage onLoggedIn={(me) => qc.setQueryData(["me"], me)} />;

  const me = meQuery.data;
  if (me.must_change_password) return <ForcedPasswordPage username={me.username} onLogout={logout} />;
  const workspace = me.workspaces.find((w) => w.slug === wsSlug) ?? me.workspaces[0];
  if (!workspace) {
    return me.is_platform_admin
      ? <main className="platform-only"><div className="actions" style={{ justifyContent: "space-between" }}><Logo size={26} /><button type="button" onClick={logout}>{t("nav.logout")}</button></div><PlatformPage /></main>
      : <p className="center">Aucun espace n'est associé à votre compte.</p>;
  }
  const environment = workspace.environments.some((e) => e.kind === env) ? env : (workspace.environments[0]?.kind ?? "test");
  const isProd = environment === "prod";

  // The « Catalogue » role has nothing but the catalog and its own runs: the menu, the routes and the home page say so (the server enforces it)
  const catalogOnly = workspace.permissions.includes("catalog") && !workspace.permissions.includes("view");
  const items: [string, string][] = catalogOnly ? [["/catalog", "Catalogue"], ["/executions", "Mes exécutions"]] : [["/home", "Accueil"], ["/catalog", "Catalogue"], ["/workflows", "Workflows"], ["/library", "Bibliothèques"], ["/connections", "Connexions"], ["/triggers", "Déclencheurs"], ["/executions", "Mes exécutions"], ["/ops", "Exploitation"], ["/approvals", "Approbations"]];
  if (workspace.permissions.includes("administer")) items.push(["/admin", "Administration"]);
  if (me.is_platform_admin) items.push(["/platform", "Plate-forme"]);
  // The menu by intention: use · design · connect · supervise (the entries a person may not reach are simply absent)
  const INTENTS: [string, string[]][] = catalogOnly ? [["", items.map(([to]) => to)]] : [["Utiliser", ["/home", "/catalog", "/executions", "/approvals"]], ["Concevoir", ["/workflows", "/library", "/triggers"]], ["Intégrer", ["/connections"]], ["Superviser", ["/ops", "/admin", "/platform"]]];
  const groups = INTENTS.map(([title, tos]) => ({ title, entries: items.filter(([to]) => tos.includes(to)) })).filter((g) => g.entries.length > 0);
  const logoutBtn = (
          <button type="button" className="icon-btn" onClick={logout} aria-label={t("nav.logout")} title={t("nav.logout")}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M9 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h4M16 8l4 4-4 4M20 12H9" /></svg>
          </button>
  );
  const ctxFields = (
    <>
          <label className="ctx-field ctx-ws">
            <span className="ctx-label">{t("ctx.workspace")}</span>
            <svg className="ctx-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M3 21V7l9-4 9 4v14M9 21v-6h6v6M8 10h.01M12 10h.01M16 10h.01" /></svg>
            <select value={workspace.slug} onChange={(e) => setWsSlug(e.target.value)}>
              {me.workspaces.map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}
            </select>
          </label>
          <label className={`ctx-field ctx-env ${isProd ? "env-prod" : "env-test"}`}>
            <span className="ctx-label">{t("ctx.environment")}</span>
            <span className="env-dot" aria-hidden="true" />
            <select value={environment} onChange={(e) => setEnv(e.target.value)}>
              {workspace.environments.map((e) => <option key={e.kind} value={e.kind}>{t(`env.${e.kind}` as never)}</option>)}
            </select>
          </label>
    </>
  );
  return (
    <AppContext.Provider
      value={{
        me, workspace, environment, setWorkspace: setWsSlug, setEnvironment: setEnv,
        can: (perm) => workspace.permissions.includes(perm),
      }}
    >
      <ToastProvider>
      <a href="#main" className="skip">{t("app.skip")}</a>
      {phone && <div className={`env-stripe ${isProd ? "env-prod" : "env-test"}`} aria-hidden="true" />}
      <header className="topbar">
        <button type="button" className="icon-btn sidebar-toggle" aria-expanded={!collapsed} aria-controls="sidebar" aria-label={collapsed ? "Déplier la navigation" : "Replier la navigation"} title={collapsed ? "Déplier le menu de gauche" : "Replier le menu de gauche"} onClick={() => setCollapsed(!collapsed)}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" />{collapsed ? <path d="M13.5 10l2 2-2 2" /> : <path d="M16 10l-2 2 2 2" />}</svg>
        </button>
        <button type="button" className="menu-btn" aria-expanded={menuOpen} aria-controls="sidebar" onClick={() => setMenuOpen(!menuOpen)}>Menu</button>
        <strong className="brand"><Logo size={26} /></strong>
        <div className="context">
          {!phone && ctxFields}
          <span className="ctx-sep" aria-hidden="true" />
          {!catalogOnly && <button type="button" className="icon-btn" onClick={() => setSearching(true)} aria-label="Recherche globale (Ctrl+K)" title="Recherche globale (Ctrl+K)"><FormIcon name="search" /></button>}
          <span id="jobs-slot" />
          <NavLink to="/account" className="user-chip" title="Mon compte"><span className="avatar" aria-hidden="true">{me.username.slice(0, 2).toUpperCase()}</span><span className="user-meta"><span className="who">{me.username}</span><span className="role">{roleLabel(workspace.role)}</span></span><span className="sr-only"> — mon compte</span></NavLink>
          {!phone && logoutBtn}
        </div>
      </header>
      {isProd && <p role="note" className="prod-banner">{t("env.prodWarning")}</p>}
      <div className={`shell-body${collapsed ? " shell-collapsed" : ""}`}>
        <nav id="sidebar" aria-label={t("nav.main")} className={`sidebar${menuOpen ? " open" : ""}`}>
          {phone && <div className="drawer-ctx">{ctxFields}<button type="button" onClick={logout}>{t("nav.logout")}</button></div>}
          <ul>{groups.map((g) => (
            <li key={g.title || "all"} className="nav-group">
              {g.title && <span className="nav-head">{g.title}</span>}
              <ul>{g.entries.map(([to, label]) => (
            <li key={to}>
              <NavLink to={to} end={to === "/library"} title={collapsed ? label : undefined} className={to === "/library" && location.pathname.startsWith("/library") ? "in-group" : undefined}><NavIcon to={to} /><span className="nav-label">{label}</span></NavLink>
              {to === "/library" && location.pathname.startsWith("/library") && (
                <ul className="subnav" aria-label="Bibliothèques">
                  <li><NavLink to="/library/actions" title={collapsed ? "Actions" : undefined}><NavIcon to="/library/actions" /><span className="nav-label">Actions</span></NavLink></li>
                  <li><NavLink to="/library/skills" title={collapsed ? "Skills IA" : undefined}><NavIcon to="/library/skills" /><span className="nav-label">Skills IA</span></NavLink></li>
                  <li><NavLink to="/library/forms" title={collapsed ? "Formulaires" : undefined}><NavIcon to="/library/forms" /><span className="nav-label">Formulaires</span></NavLink></li>
                  <li><NavLink to="/library/controls" title={collapsed ? "Contrôles" : undefined}><NavIcon to="/library/controls" /><span className="nav-label">Contrôles</span></NavLink></li>
                </ul>
              )}
            </li>))}</ul>
            </li>
          ))}</ul>
        </nav>
        <main id="main" tabIndex={-1}>
          {catalogOnly ? (
            <Routes>
              <Route path="/catalog" element={<CatalogPage />} />
              <Route path="/catalog/:slug" element={<CatalogDetailPage />} />
              <Route path="/executions" element={<CatalogRunsPage />} />
              <Route path="/executions/:id" element={<CatalogRunDetailPage />} />
              <Route path="/account" element={<AccountPage username={me.username} email={me.email} sso={me.sso_only} />} />
              <Route path="*" element={<Navigate to="/catalog" replace />} />
            </Routes>
          ) : (
          <Routes>
            <Route path="/" element={<Navigate to="/home" replace />} />
            <Route path="/home" element={<HomePage />} />
            <Route path="/catalog" element={<CatalogPage />} />
            <Route path="/catalog/:slug" element={<CatalogDetailPage />} />
            <Route path="/library/actions/:slug/run" element={<ActionDetailPage />} />
            <Route path="/workflows" element={<WorkflowsPage />} />
            <Route path="/workflows/:slug/run" element={<WorkflowRunPage />} />
            <Route path="/workflows/:slug/edit" element={<Navigate to="schema" replace relative="path" />} />
            <Route path="/workflows/:slug/:tab?" element={<WorkflowPage />} />
            <Route path="/runs/compare" element={<CompareRunsPage />} />
            <Route path="/runs/:id" element={<RunDetailPage />} />
            <Route path="/approvals" element={<ApprovalsPage />} />
            <Route path="/library" element={<Navigate to="/library/actions" replace />} />
            <Route path="/library/actions" element={<LibraryPage />} />
            <Route path="/library/skills" element={<SkillsPage />} />
            <Route path="/library/skills/:slug" element={<SkillDetailPage />} />
            <Route path="/library/actions/:slug" element={<ActionLibraryPage />} />
            <Route path="/connections" element={<ConnectionsPage />} />
            <Route path="/triggers" element={<TriggersPage />} />
            <Route path="/library/forms" element={<FormsPage />} />
            <Route path="/library/forms/:slug/:tab?" element={<FormPage />} />
            <Route path="/library/controls" element={<ControlsPage />} />
            <Route path="/library/controls/:slug/:tab?" element={<ControlPage />} />
            <Route path="/forms" element={<Navigate to="/library/forms" replace />} />
            <Route path="/forms/:slug/:tab?" element={<LegacyFormRedirect />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="/account" element={<AccountPage username={me.username} email={me.email} sso={me.sso_only} />} />
            <Route path="/platform" element={me.is_platform_admin ? <PlatformPage /> : <NotFoundPage />} />
            <Route path="/executions" element={<ExecutionsPage />} />
            <Route path="/ops" element={<OpsPage />} />
            <Route path="/executions/:id" element={<ExecutionDetailPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
          )}
        </main>
      </div>
      {!catalogOnly && <AiJobsTray />}
      {searching && !catalogOnly && <GlobalSearch onClose={() => setSearching(false)} />}
      </ToastProvider>
    </AppContext.Provider>
  );
}

/** The forms moved under « Bibliothèques » : old links keep working. */
function LegacyFormRedirect() {
  const { slug = "", tab } = useParams();
  return <Navigate to={`/library/forms/${slug}${tab ? `/${tab}` : ""}`} replace />;
}
