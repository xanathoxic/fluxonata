import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, type ApiError } from "../api";
import { Avatar, Badge, Confirm, EmptyRow, SearchBox, usePaged } from "../components/AdminUi";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { RowIcon } from "../components/RowIcon";
import { Tabs } from "../components/Tabs";
import { Kpi } from "./OpsCharts";
import { VaultTab } from "./platform/VaultTab";
import { RunnersPanel } from "./runners/RunnersPanel";
import { UsersPanel } from "./users/UsersPanel";
import { formatDate } from "../i18n";
import { HelpTip } from "../components/HelpTip";

interface Space { slug: string; name: string; organization: string; members: number; admins: string[]; environments: string[]; archived: boolean }
interface Proc { kind: string; name: string; hostname: string; pid: number; last_seen_at: string; alive: boolean }
interface AuditRow { id: number; created_at: string; actor: string; action: string; workspace: string | null; data: Record<string, unknown> }
const P = "/platform";
const ACTION_LABEL: Record<string, string> = {
  "platform.workspace_create": "Espace créé", "platform.workspace_rename": "Espace renommé", "platform.workspace_archive": "Espace archivé", "platform.workspace_restore": "Espace restauré",
  "platform.workspace_admin_grant": "Administrateur d'espace ajouté", "platform.workers_purge": "Processus arrêtés oubliés", "platform.admin_grant": "Administrateur de plate-forme ajouté", "platform.admin_revoke": "Administrateur de plate-forme retiré",
  "platform.vault_configure": "Coffre externe configuré", "platform.vault_test": "Coffre externe testé", "platform.vault_grants": "Accès au coffre externe modifiés",
};
const msg = (e: unknown) => ((e as ApiError | null)?.message ?? "");

function useList<T>(key: string, path: string, refetchInterval?: number) {
  return useQuery({ queryKey: ["platform", key], queryFn: () => api<{ results: T[] }>(`${P}${path}`), refetchInterval });
}
function useAction<I = void>(fn: (i: I) => Promise<unknown>, keys: string[]) {
  const qc = useQueryClient();
  return useMutation<unknown, ApiError, I>({ mutationFn: fn, onSuccess: () => { for (const k of keys) void qc.invalidateQueries({ queryKey: ["platform", k] }); } });
}

function SpacesTab() {
  const list = useList<Space>("spaces", "/workspaces/");
  const [creating, setCreating] = useState(false);
  const [edit, setEdit] = useState<Space | null>(null);
  const [grant, setGrant] = useState<Space | null>(null);
  const [f, setF] = useState({ name: "", slug: "", admin_username: "" });
  const [name, setName] = useState("");
  const [user, setUser] = useState("");
  const create = useAction(() => api(`${P}/workspaces/`, { json: f }), ["spaces", "audit"]);
  const rename = useAction((s: Space) => api(`${P}/workspaces/${s.slug}/`, { method: "PATCH", json: { name } }), ["spaces", "audit"]);
  const archive = useAction((s: { slug: string; archived: boolean }) => api(`${P}/workspaces/${s.slug}/`, { method: "PATCH", json: { archived: s.archived } }), ["spaces", "audit"]);
  const addAdmin = useAction((s: Space) => api(`${P}/workspaces/${s.slug}/admins/`, { json: { username: user } }), ["spaces", "audit"]);
  const [confirm, setConfirm] = useState<Space | null>(null);
  const [search, setSearch] = useState("");
  const [state, setState] = useState("");
  const rows = useMemo(() => (list.data?.results ?? []).filter((s) => (!state || (state === "archived") === s.archived) && `${s.name} ${s.slug} ${s.admins.join(" ")}`.toLowerCase().includes(search.trim().toLowerCase())), [list.data, search, state]);
  const paged = usePaged(rows, 10);
  return (
    <>
      <div className="toolbar-row">
        <span style={{ flex: 1 }}><HelpTip caption="À savoir">Un espace regroupe des workflows, actions, connexions et membres, avec ses environnements. L'archiver le rend inaccessible et arrête ses planifications et webhooks, sans rien supprimer.</HelpTip></span>
        <button type="button" className="primary" onClick={() => { create.reset(); setF({ name: "", slug: "", admin_username: "" }); setCreating(true); }}>Nouvel espace</button>
      </div>
      <section className="adm-card">
        <div className="adm-filters">
          <SearchBox label="Rechercher un espace" value={search} onChange={setSearch} placeholder="Nom, identifiant ou administrateur" />
          <div className="field"><label htmlFor="sp-state">Statut</label>
            <select id="sp-state" value={state} onChange={(e) => setState(e.target.value)}><option value="">Tous</option><option value="active">Actifs</option><option value="archived">Archivés</option></select></div>
        </div>
        <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Espaces de travail</caption>
          <thead><tr><th scope="col">Espace</th><th scope="col">Identifiant</th><th scope="col">Membres</th><th scope="col">Administrateurs</th><th scope="col">Statut</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{paged.slice.map((s) => (
            <tr key={s.slug}>
              <td>{s.name}</td><td className="mono">{s.slug}</td><td>{s.members}</td>
              <td>{s.admins.length ? <span className="adm-who">{s.admins.slice(0, 3).map((a) => <Avatar key={a} name={a} />)}<span>{s.admins.join(", ")}</span></span> : <span className="field-error">Aucun — ajoutez-en un</span>}</td>
              <td>{s.archived ? <Badge tone="muted">⏸ Archivé</Badge> : <Badge tone="ok">✔ Actif</Badge>}</td>
              <td>
                <button type="button" className="icon-btn" aria-label={`Renommer l'espace ${s.name}`} title="Renommer" onClick={() => { rename.reset(); setName(s.name); setEdit(s); }}><RowIcon name="edit" /></button>
                <button type="button" className="icon-btn" aria-label={`Ajouter un administrateur à l'espace ${s.name}`} title="Ajouter un administrateur" onClick={() => { addAdmin.reset(); setUser(""); setGrant(s); }}><RowIcon name="plus" /></button>
                <button type="button" onClick={() => setConfirm(s)} aria-label={`${s.archived ? "Restaurer" : "Archiver"} l'espace ${s.name}`}>{s.archived ? "Restaurer" : "Archiver"}</button>
              </td>
            </tr>))}
            {rows.length === 0 && <EmptyRow cols={6}>{list.isLoading ? "Chargement…" : "Aucun espace ne correspond."}</EmptyRow>}</tbody></table></div>
        {paged.pager("Pagination des espaces")}
      </section>
      {confirm && (
        <Confirm title={confirm.archived ? "Restaurer cet espace ?" : "Archiver cet espace ?"} confirmLabel={confirm.archived ? "Confirmer la restauration" : "Confirmer l'archivage"} danger={!confirm.archived} busy={archive.isPending}
          onCancel={() => setConfirm(null)} onConfirm={() => { archive.mutate({ slug: confirm.slug, archived: !confirm.archived }); setConfirm(null); }}>
          <p>{confirm.archived ? <>L'espace <strong>{confirm.name}</strong> redevient accessible à ses {confirm.members} membre(s).</> : <>L'espace <strong>{confirm.name}</strong> devient inaccessible à ses {confirm.members} membre(s) et ses planifications et webhooks s'arrêtent. Rien n'est supprimé : vous pourrez le restaurer.</>}</p>
        </Confirm>
      )}
      {archive.error && <p role="alert" className="field-error">Erreur : {msg(archive.error)}</p>}
      {creating && (
        <Overlay title="Nouvel espace" description="Les environnements Développement, Recette et Production sont créés automatiquement." onClose={() => setCreating(false)}
          footer={<><button type="button" onClick={() => setCreating(false)}>Annuler</button><button type="submit" form="space-form" className="primary" disabled={create.isPending}>Créer l'espace</button></>}>
          <form id="space-form" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined, { onSuccess: () => setCreating(false) }); }}>
            <div className="field"><label htmlFor="sp-n">Nom de l'espace</label><input id="sp-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required maxLength={120} /></div>
            <div className="field">
<HelpTip>Utilisé dans les adresses ; il ne pourra plus être modifié.</HelpTip><label htmlFor="sp-s">Identifiant (lettres minuscules, chiffres, tirets)</label><input id="sp-s" className="mono" value={f.slug} onChange={(e) => setF({ ...f, slug: e.target.value })} pattern="[a-z0-9-]+" required maxLength={50} /></div>
            <div className="field">
<HelpTip>Un compte existant. Être administrateur de la plate-forme ne donne pas accès aux données d'un espace.</HelpTip><label htmlFor="sp-a">Premier administrateur de l'espace</label><input id="sp-a" value={f.admin_username} onChange={(e) => setF({ ...f, admin_username: e.target.value })} placeholder="vous-même si vide" /></div>
            {create.error && <p role="alert" className="field-error">Erreur : {msg(create.error)} {JSON.stringify((create.error as ApiError).details ?? "")}</p>}
          </form>
        </Overlay>
      )}
      {edit && (
        <Overlay variant="modal" title="Renommer l'espace" onClose={() => setEdit(null)}
          footer={<><button type="button" onClick={() => setEdit(null)}>Annuler</button><button type="submit" form="rename-form" className="primary">Renommer</button></>}>
          <form id="rename-form" onSubmit={(e) => { e.preventDefault(); rename.mutate(edit, { onSuccess: () => setEdit(null) }); }}>
            <div className="field"><label htmlFor="rn-n">Nom de l'espace</label><input id="rn-n" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} /></div>
            {rename.error && <p role="alert" className="field-error">Erreur : {msg(rename.error)}</p>}
          </form>
        </Overlay>
      )}
      {grant && (
        <Overlay variant="modal" title={`Ajouter un administrateur — ${grant.name}`} description="Le compte reçoit le rôle Administrateur dans cet espace. L'opération est journalisée." onClose={() => setGrant(null)}
          footer={<><button type="button" onClick={() => setGrant(null)}>Annuler</button><button type="submit" form="grant-form" className="primary">Ajouter</button></>}>
          <form id="grant-form" onSubmit={(e) => { e.preventDefault(); addAdmin.mutate(grant, { onSuccess: () => setGrant(null) }); }}>
            <div className="field"><label htmlFor="gr-u">Identifiant du compte</label><input id="gr-u" value={user} onChange={(e) => setUser(e.target.value)} required /></div>
            {addAdmin.error && <p role="alert" className="field-error">Erreur : {msg(addAdmin.error)}</p>}
          </form>
        </Overlay>
      )}
    </>
  );
}

function WorkersTab() {
  const list = useList<Proc>("workers", "/workers/", 5000);
  const [showStopped, setShowStopped] = useState(false);
  const purge = useAction(() => api<{ removed: number }>(`${P}/workers/purge/`, { method: "POST" }), ["workers", "audit"]);
  const rows = list.data?.results ?? [];
  const stopped = rows.filter((w) => !w.alive).length;
  const shown = rows.filter((w) => showStopped || w.alive);
  const paged = usePaged(shown, 10);
  return (
    <>
      <p className="help-row"><HelpTip caption="À savoir">Les workers et l'orchestrateur sont des processus partagés par tous les espaces. Les démarrer, les arrêter ou en ajouter relève de l'exploitation (Docker Compose, systemd) : voir <code>docs/ADMIN.md</code>.</HelpTip></p>
      <div className="toolbar-row">
        <div className="field field-inline"><input type="checkbox" id="show-stopped" checked={showStopped} onChange={(e) => setShowStopped(e.target.checked)} /><label htmlFor="show-stopped">Afficher les {stopped} processus arrêtés</label></div>
        {stopped > 0 && <button type="button" onClick={() => purge.mutate()} disabled={purge.isPending}>Oublier les processus arrêtés</button>}
        {purge.isSuccess && <p role="status" className="hint">Liste nettoyée.</p>}
      </div>
      <section className="adm-card"><div className="table-wrap"><table className="adm-table"><caption className="sr-only">Workers et orchestrateur</caption>
        <thead><tr><th scope="col">Rôle</th><th scope="col">Nom</th><th scope="col">Machine</th><th scope="col">Dernier signe de vie</th><th scope="col">État</th></tr></thead>
        <tbody>{paged.slice.map((w) => (
          <tr key={w.name}><td>{w.kind === "worker" ? "Worker" : "Orchestrateur"}</td><td className="mono">{w.name}</td><td>{w.hostname} ({w.pid})</td><td>{formatDate(w.last_seen_at)}</td><td>{w.alive ? <Badge tone="ok">✔ actif</Badge> : <Badge tone="bad">✖ arrêté ou injoignable</Badge>}</td></tr>))}
          {rows.filter((w) => w.alive).length === 0 && !showStopped && <tr><td colSpan={5} className="field-error">Aucun processus actif : les exécutions n'avancent pas.</td></tr>}</tbody></table></div>
      {paged.pager("Pagination des processus")}</section>
    </>
  );
}

function AdminsTab() {
  const list = useList<{ username: string; you: boolean }>("admins", "/admins/");
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState("");
  const add = useAction(() => api(`${P}/admins/`, { json: { username: user } }), ["admins", "audit"]);
  const remove = useAction((u: string) => api(`${P}/admins/${encodeURIComponent(u)}/`, { method: "DELETE" }), ["admins", "audit"]);
  return (
    <>
      <div className="toolbar-row">
        <span style={{ flex: 1 }}><HelpTip caption="À savoir">Les administrateurs de la plate-forme créent les espaces et voient les workers. Ils n'ont aucun accès aux données des espaces dont ils ne sont pas membres. Il en reste toujours au moins un.</HelpTip></span>
        <button type="button" className="primary" onClick={() => { add.reset(); setUser(""); setOpen(true); }}>Ajouter un administrateur</button>
      </div>
      <section className="adm-card"><div className="table-wrap"><table className="adm-table"><caption className="sr-only">Administrateurs de la plate-forme</caption>
        <thead><tr><th scope="col">Identifiant</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{list.data?.results.map((a) => (
          <tr key={a.username}><td><span className="adm-who"><Avatar name={a.username} /><span>{a.username}{a.you ? " — vous" : ""}</span></span></td>
            <td><button type="button" onClick={() => remove.mutate(a.username)} aria-label={`Retirer ${a.username} des administrateurs de la plate-forme`}>Retirer</button></td></tr>))}</tbody></table></div></section>
      {remove.error && <p role="alert" className="field-error">Erreur : {msg(remove.error)}</p>}
      {open && (
        <Overlay variant="modal" title="Ajouter un administrateur de la plate-forme" onClose={() => setOpen(false)}
          footer={<><button type="button" onClick={() => setOpen(false)}>Annuler</button><button type="submit" form="padmin-form" className="primary">Ajouter</button></>}>
          <form id="padmin-form" onSubmit={(e) => { e.preventDefault(); add.mutate(undefined, { onSuccess: () => setOpen(false) }); }}>
            <div className="field"><label htmlFor="pa-u">Identifiant du compte</label><input id="pa-u" value={user} onChange={(e) => setUser(e.target.value)} required /></div>
            {add.error && <p role="alert" className="field-error">Erreur : {msg(add.error)}</p>}
          </form>
        </Overlay>
      )}
    </>
  );
}

function AuditTab() {
  const list = useList<AuditRow>("audit", "/audit/");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<AuditRow | null>(null);
  const rows = useMemo(() => (list.data?.results ?? []).filter((e) => !search.trim() || `${e.actor} ${ACTION_LABEL[e.action] ?? e.action} ${e.workspace ?? ""}`.toLowerCase().includes(search.trim().toLowerCase())), [list.data, search]);
  const paged = usePaged(rows, 25);
  return (
    <section className="adm-card">
      <div className="adm-filters"><SearchBox label="Rechercher dans le journal" value={search} onChange={setSearch} placeholder="Qui, événement ou espace" /></div>
      <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Journal de la plate-forme</caption>
        <thead><tr><th scope="col">Date</th><th scope="col">Qui</th><th scope="col">Événement</th><th scope="col">Espace</th><th scope="col">Détails</th></tr></thead>
        <tbody>{paged.slice.map((e) => (
          <tr key={e.id}><td>{formatDate(e.created_at)}</td><td>{e.actor || "—"}</td><td>{ACTION_LABEL[e.action] ?? e.action}</td><td className="mono">{e.workspace ?? "—"}</td>
            <td>{Object.keys(e.data).length ? <button type="button" onClick={() => setOpen(e)} aria-label={`Voir le détail : ${ACTION_LABEL[e.action] ?? e.action}, ${formatDate(e.created_at)}`}>Voir</button> : "—"}</td></tr>))}
          {rows.length === 0 && <EmptyRow cols={5}>{list.isLoading ? "Chargement…" : "Aucun événement."}</EmptyRow>}</tbody></table></div>
      {paged.pager("Pagination du journal")}
      {open && (
        <Overlay title="Détail de l'événement" description={`${ACTION_LABEL[open.action] ?? open.action} — ${formatDate(open.created_at)}`} onClose={() => setOpen(null)} footer={<button type="button" onClick={() => setOpen(null)}>Fermer</button>}>
          <dl className="adm-dl"><dt>Qui</dt><dd>{open.actor || "—"}</dd><dt>Espace</dt><dd className="mono">{open.workspace ?? "—"}</dd></dl>
          <pre className="adm-json">{JSON.stringify(open.data, null, 2)}</pre>
        </Overlay>
      )}
    </section>
  );
}

/** Platform administration. Independent of any workspace (no useApp): it must keep working when every space is archived. */
export function PlatformPage() {
  const [tab, setTab] = useState("spaces");
  const spaces = useList<Space>("spaces", "/workspaces/");
  const workers = useList<Proc>("workers", "/workers/", 5000);
  const admins = useList<{ username: string }>("admins", "/admins/");
  const sp = spaces.data?.results ?? [];
  const alive = (workers.data?.results ?? []).filter((w) => w.alive);
  return (
    <>
      <PageHeader title="Plate-forme" description="Espaces de travail, utilisateurs, runners, processus partagés et administrateurs de la plate-forme." />
      <ul className="kpis" aria-label="Chiffres de la plate-forme">
        <Kpi label="Espaces actifs" value={sp.filter((x) => !x.archived).length} tone="ok" hint={`${sp.filter((x) => x.archived).length} archivé(s)`} />
        <Kpi label="Membres (cumul)" value={sp.reduce((n, x) => n + x.members, 0)} />
        <Kpi label="Processus actifs" value={alive.length} tone={workers.data && alive.length === 0 ? "bad" : "info"} hint={`${alive.filter((w) => w.kind === "worker").length} worker(s), ${alive.filter((w) => w.kind !== "worker").length} orchestrateur(s)`} />
        <Kpi label="Administrateurs" value={admins.data?.results.length ?? 0} hint="de la plate-forme" />
      </ul>
      <Tabs label="Administration de la plate-forme" active={tab} onChange={setTab} tabs={[
        { id: "spaces", label: "Espaces", content: <SpacesTab /> },
        { id: "users", label: "Utilisateurs", content: <UsersPanel /> },
        { id: "runners", label: "Runners", content: <RunnersPanel mode="platform" /> },
        { id: "vault", label: "Coffre externe", content: <VaultTab /> },
        { id: "workers", label: "Processus", content: <WorkersTab /> },
        { id: "admins", label: "Administrateurs", content: <AdminsTab /> },
        { id: "audit", label: "Journal", content: <AuditTab /> },
      ]} />
    </>
  );
}
