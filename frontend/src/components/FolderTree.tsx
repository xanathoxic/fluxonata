import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";
import { api, type ApiError } from "../api";
import { PHONE, useMediaQuery } from "./useMediaQuery";
import { useApp } from "../context";
import { FolderShareDialog } from "./FolderShare";
import { Overlay } from "./Overlay";

interface Node { name: string; path: string; children: Map<string, Node> }

function buildTree(folders: string[]): Node {
  const root: Node = { name: "", path: "", children: new Map() };
  for (const f of folders.filter(Boolean)) {
    let cur = root;
    f.split("/").forEach((part, i, arr) => {
      const path = arr.slice(0, i + 1).join("/");
      if (!cur.children.has(part)) cur.children.set(part, { name: part, path, children: new Map() });
      cur = cur.children.get(part) as Node;
    });
  }
  return root;
}

const FolderIcon = () => (
  <svg className="tree-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
);
const AllIcon = () => (
  <svg className="tree-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>
);

interface Fold { collapsed: Set<string>; toggle: (path: string, open?: boolean) => void }

function Branch({ node, current, onPick, counts, locked, fold }: { node: Node; current: string; onPick: (p: string) => void; counts: Map<string, number>; locked: Set<string>; fold: Fold }) {
  return (
    <ul className="tree">
      {[...node.children.values()].sort((a, b) => a.name.localeCompare(b.name)).map((c) => {
        const has = c.children.size > 0;
        const open = !fold.collapsed.has(c.path);
        // Same keys as a standard tree: → opens, ← closes (the chevron is the mouse shortcut).
        const key = (e: KeyboardEvent<HTMLButtonElement>) => {
          if (!has) return;
          if (e.key === "ArrowRight" && !open) { e.preventDefault(); fold.toggle(c.path, true); }
          else if (e.key === "ArrowLeft" && open) { e.preventDefault(); fold.toggle(c.path, false); }
        };
        return (
          <li key={c.path}>
            <span className="tree-row">
              {has
                ? <span className={`tree-chev${open ? " open" : ""}`} aria-hidden="true" title={open ? "Replier" : "Déplier"} onClick={() => fold.toggle(c.path)}>▸</span>
                : <span className="tree-chev none" aria-hidden="true" />}
              <button type="button" className="tree-item" aria-current={current === c.path ? "true" : undefined} aria-expanded={has ? open : undefined} onKeyDown={key} onClick={() => onPick(c.path)}>
                <FolderIcon /> {c.name}{locked.has(c.path) && <span title="Dossier privé" aria-label="privé"> 🔒</span>} <span className="hint">({counts.get(c.path) ?? 0})</span>
              </button>
            </span>
            {has && open && <Branch node={c} current={current} onPick={onPick} counts={counts} locked={locked} fold={fold} />}
          </li>
        );
      })}
    </ul>
  );
}

export const inFolder = (itemFolder: string, current: string) => !current || itemFolder === current || itemFolder.startsWith(`${current}/`);

export type FolderKind = "workflow" | "action" | "ai_skill" | "form" | "control";
const parentOf = (p: string) => p.split("/").slice(0, -1).join("/");
const REFRESH = ["folders", "workflows", "actions-lib", "actions", "ai-skills", "ai-skill"];

/** Explicitly created folders (they persist while empty). Items' own folders are merged in by the tree. */
export function useFolders(kind: FolderKind) {
  const { workspace } = useApp();
  return useQuery({ queryKey: ["folders", workspace.slug, kind], queryFn: () => api<{ results: string[]; private?: string[] }>(`/workspaces/${workspace.slug}/folders/?kind=${kind}`) });
}

function useFolderActions(kind: FolderKind, onDone: (path: string) => void) {
  const { workspace } = useApp();
  const qc = useQueryClient();
  const base = `/workspaces/${workspace.slug}/folders/`;
  const done = (path: string) => { for (const k of REFRESH) void qc.invalidateQueries({ queryKey: [k] }); onDone(path); };
  const create = useMutation<unknown, ApiError, string>({ mutationFn: (path) => api(base, { json: { kind, path } }), onSuccess: (_, path) => done(path) });
  const rename = useMutation<unknown, ApiError, { path: string; new_path: string }>({ mutationFn: (i) => api(base, { method: "PATCH", json: { kind, ...i } }), onSuccess: (_, i) => done(i.new_path) });
  const remove = useMutation<unknown, ApiError, string>({ mutationFn: (path) => api(`${base}?kind=${kind}&path=${encodeURIComponent(path)}`, { method: "DELETE" }), onSuccess: (_, path) => done(parentOf(path)) });
  return { create, rename, remove };
}

const ToolIcon = ({ d }: { d: string }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={d} /></svg>
);

function FolderDialog({ title, description, label, initial, submit, error, busy, onSubmit, onClose }: {
  title: string; description?: string; label: string; initial: string; submit: string; error?: string; busy: boolean; onSubmit: (v: string) => void; onClose: () => void;
}) {
  const id = useId();
  const [value, setValue] = useState(initial);
  return (
    <Overlay variant="modal" title={title} description={description} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form={`${id}-f`} className="primary" disabled={busy || !value.trim()}>{submit}</button></>}>
      <form id={`${id}-f`} onSubmit={(e) => { e.preventDefault(); onSubmit(value); }}>
        <div className="field"><label htmlFor={`${id}-i`}>{label}</label><input id={`${id}-i`} value={value} onChange={(e) => setValue(e.target.value)} required maxLength={200} autoComplete="off" /></div>
        {error && <p role="alert" className="field-error">Erreur : {error}</p>}
      </form>
    </Overlay>
  );
}

/** Folder tree built from the "A/B/C" folder of each item (+ folders created explicitly, even empty), with counts.
 *  The same for workflows, actions and AI skills. Editors can create, rename/move and delete (empty only) folders. */
export function FolderTree({ label, kind, folders, current, onPick, canEdit }: {
  label: string; kind: FolderKind; folders: string[]; current: string; onPick: (p: string) => void; canEdit: boolean;
}) {
  const phone = useMediaQuery(PHONE);
  const [foldOpen, setFoldOpen] = useState(false);
  useEffect(() => setFoldOpen(false), [current]);
  const folderQuery = useFolders(kind).data;
  const persisted = folderQuery?.results;
  const locked = useMemo(() => new Set(folderQuery?.private ?? []), [folderQuery]);
  const { can } = useApp();
  const [dialog, setDialog] = useState<"create" | "rename" | "delete" | "share" | null>(null);
  const acts = useFolderActions(kind, (path) => { setDialog(null); onPick(path); });
  const paths = useMemo(() => [...folders, ...(persisted ?? [])], [folders, persisted]);
  const tree = useMemo(() => buildTree(paths), [paths]);
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const f of folders) { const parts = f ? f.split("/") : []; parts.forEach((_, i) => { const p = parts.slice(0, i + 1).join("/"); m.set(p, (m.get(p) ?? 0) + 1); }); }
    return m;
  }, [folders]);
  const { workspace } = useApp();
  const storeKey = `orch.tree.${workspace.slug}.${kind}`;
  const [collapsed, setCollapsed] = useState<Set<string>>(() => { try { return new Set(JSON.parse(localStorage.getItem(storeKey) ?? "[]") as string[]); } catch { return new Set(); } });
  useEffect(() => { try { localStorage.setItem(storeKey, JSON.stringify([...collapsed])); } catch { /* preference only */ } }, [collapsed, storeKey]);
  // The selected folder must stay visible: opening it, or a folder that was just created, unfolds its parents.
  useEffect(() => {
    if (!current) return;
    const parentsPaths = current.split("/").map((_, i, a) => a.slice(0, i + 1).join("/")).slice(0, -1);
    if (parentsPaths.some((p) => collapsed.has(p))) setCollapsed((c) => { const n = new Set(c); parentsPaths.forEach((p) => n.delete(p)); return n; });
  }, [current]); // eslint-disable-line react-hooks/exhaustive-deps
  const fold: Fold = { collapsed, toggle: (path, open) => setCollapsed((c) => { const n = new Set(c); const want = open ?? n.has(path); if (want) n.delete(path); else n.add(path); return n; }) };
  const branches = useMemo(() => { const all: string[] = []; const walk = (n: Node) => n.children.forEach((c) => { if (c.children.size) all.push(c.path); walk(c); }); walk(tree); return all; }, [tree]);
  const open = (d: "create" | "rename" | "delete" | "share") => { acts.create.reset(); acts.rename.reset(); acts.remove.reset(); setDialog(d); };
  const body = (
    <nav aria-label={label} className="tree-nav">
      {canEdit && (
        <div className="tree-tools" role="toolbar" aria-label="Gérer les dossiers">
          <button type="button" className="icon-btn" onClick={() => open("create")} aria-label={current ? `Nouveau dossier dans ${current}` : "Nouveau dossier"} title="Nouveau dossier"><ToolIcon d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10v5M9.5 12.5H14.5" /></button>
          <button type="button" className="icon-btn" onClick={() => open("rename")} disabled={!current} aria-label="Renommer ou déplacer le dossier sélectionné" title="Renommer / déplacer"><ToolIcon d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4" /></button>
          {kind === "workflow" && can("administer") && <button type="button" className="icon-btn" onClick={() => open("share")} disabled={!current} aria-label="Partager le dossier sélectionné (rendre privé)" title="Partage / dossier privé"><ToolIcon d="M7 11V8a5 5 0 0 1 10 0v3M5 11h14v9H5z" /></button>}
          <button type="button" className="icon-btn danger-icon" onClick={() => open("delete")} disabled={!current} aria-label="Supprimer le dossier sélectionné" title="Supprimer"><ToolIcon d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></button>
        </div>
      )}
      <div className="tree-row tree-all">
        <button type="button" className="tree-item" aria-current={current === "" ? "true" : undefined} onClick={() => onPick("")}><AllIcon /> Tous ({folders.length})</button>
        {branches.length > 0 && (
          <span className="tree-fold" role="group" aria-label="Déplier ou replier tout l'arbre">
            <button type="button" className="icon-btn" title="Tout déplier" aria-label="Tout déplier" disabled={collapsed.size === 0} onClick={() => setCollapsed(new Set())}>⊞</button>
            <button type="button" className="icon-btn" title="Tout replier" aria-label="Tout replier" disabled={branches.every((p) => collapsed.has(p))} onClick={() => setCollapsed(new Set(branches))}>⊟</button>
          </span>
        )}
      </div>
      <Branch node={tree} current={current} onPick={onPick} counts={counts} locked={locked} fold={fold} />
      {dialog === "share" && <FolderShareDialog folder={current} onClose={() => setDialog(null)} />}
      {dialog === "create" && (
        <FolderDialog title="Nouveau dossier" description={current ? `Créé dans « ${current} ».` : "Créé à la racine."} label="Nom du dossier" initial="" submit="Créer" busy={acts.create.isPending} error={acts.create.error ? errorMessage(acts.create.error) : undefined}
          onSubmit={(v) => acts.create.mutate(current ? `${current}/${v.trim()}` : v.trim())} onClose={() => setDialog(null)} />
      )}
      {dialog === "rename" && (
        <FolderDialog title={`Renommer ou déplacer « ${current} »`} description="Le contenu et les sous-dossiers suivent. Modifiez le chemin complet pour déplacer le dossier ailleurs." label="Chemin du dossier" initial={current} submit="Enregistrer" busy={acts.rename.isPending} error={acts.rename.error ? errorMessage(acts.rename.error) : undefined}
          onSubmit={(v) => acts.rename.mutate({ path: current, new_path: v })} onClose={() => setDialog(null)} />
      )}
      {dialog === "delete" && (
        <Overlay variant="modal" title={`Supprimer « ${current} » ?`} description="Seul un dossier vide peut être supprimé." onClose={() => setDialog(null)}
          footer={<><button type="button" onClick={() => setDialog(null)}>Annuler</button><button type="button" className="danger" disabled={acts.remove.isPending} onClick={() => acts.remove.mutate(current)}>Supprimer le dossier</button></>}>
          <p>Les sous-dossiers vides seront supprimés avec lui. Si un élément s'y trouve, la suppression est refusée.</p>
          {acts.remove.error && <p role="alert" className="field-error">Erreur : {errorMessage(acts.remove.error)}</p>}
        </Overlay>
      )}
    </nav>
  );
  // On a phone the tree is folded behind « Dossier : … » so the list comes first (it unfolds on demand, and folds again once a folder is picked)
  return phone ? <details className="ft-mobile" open={foldOpen} onToggle={(e) => setFoldOpen((e.currentTarget as HTMLDetailsElement).open)}><summary>Dossier : {current ? current.split("/").join(" / ") : "Tous"}</summary>{body}</details> : body;
}

const errorMessage = (e: ApiError) => {
  const d = e.details as Record<string, unknown> | null;
  const first = d && typeof d === "object" ? Object.values(d)[0] : null;
  return Array.isArray(first) ? String(first[0]) : (typeof first === "string" ? first : e.message);
};
