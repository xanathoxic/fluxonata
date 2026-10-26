import { NotFoundPage } from "../pages/NotFoundPage";
import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, apiAll, type Action, type ApiError, type WorkflowDetail, type WorkflowSummary } from "../api";
import { useApp } from "../context";
import { clientValidate, emptyGraph, mergeIssues } from "./graphOps";
import type { Graph, Issue } from "./types";
import { useHistory } from "./useHistory";

export type SaveState = "saved" | "dirty" | "saving" | "conflict" | "error";

export interface Draft {
  slug: string;
  wf: WorkflowDetail;
  graph: Graph;
  setGraph: (g: Graph | ((p: Graph) => Graph)) => void;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  saveState: SaveState;
  save: (force?: boolean) => Promise<void>;
  reload: () => Promise<void>;
  issues: Issue[];
  serverIssues: Issue[];
  setServerIssues: (i: Issue[]) => void;
  validateNow: () => Promise<Issue[]>;
  editable: boolean;
  actions: Action[];
  workflows: WorkflowSummary[];
  recover: { graph: Graph; base: number } | null;
  acceptRecovery: () => void;
  discardRecovery: () => void;
  refetchWorkflow: () => Promise<unknown>;
}

const Ctx = createContext<Draft | null>(null);
export const useDraft = (): Draft => {
  const d = useContext(Ctx);
  if (!d) throw new Error("DraftProvider missing");
  return d;
};

const localKey = (ws: string, slug: string) => `orch.draft.${ws}.${slug}`;
function readLocal(key: string): { graph: Graph; base: number } | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as { graph: Graph; base: number }) : null;
  } catch {
    return null;
  }
}

/** Loads the draft once, then keeps it: history (undo/redo), debounced autosave with optimistic revision, conflict
 * handling and recovery of an unsaved local draft after a disconnection. Shared by every tab of the workflow page. */
export function DraftProvider({ slug, children }: { slug: string; children: ReactNode }) {
  const { workspace, can } = useApp();
  const editable = can("edit");
  const key = localKey(workspace.slug, slug);
  const { data: wf, refetch, isError } = useQuery({
    queryKey: ["workflow", workspace.slug, slug],
    queryFn: () => api<WorkflowDetail>(`/workspaces/${workspace.slug}/workflows/${slug}/`),
    staleTime: Infinity, refetchOnWindowFocus: false,
  });
  const { data: actionsPage } = useQuery({ queryKey: ["actions", workspace.slug, ""], queryFn: () => apiAll<Action>(`/workspaces/${workspace.slug}/actions/?q=`) });
  const { data: wfPage } = useQuery({ queryKey: ["workflows", workspace.slug], queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`) });
  const actions = actionsPage?.results ?? [];
  const workflows = (wfPage?.results ?? []).filter((w) => w.slug !== slug);

  const history = useHistory<Graph>(emptyGraph());
  const graph = history.value;
  const [loaded, setLoaded] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [serverIssues, setServerIssues] = useState<Issue[]>([]);
  const [recover, setRecover] = useState<{ graph: Graph; base: number } | null>(null);
  const revision = useRef(1);
  const savedJson = useRef("");

  useEffect(() => {
    if (!wf || loaded) return;
    const g = { ...emptyGraph(), ...wf.draft_graph };
    history.replace(g);
    revision.current = wf.draft_revision;
    savedJson.current = JSON.stringify(g);
    const local = readLocal(key);
    if (local && JSON.stringify(local.graph) !== savedJson.current) setRecover(local);
    setLoaded(true);
  }, [wf, loaded, history, key]);

  const save = useCallback(async (force = false) => {
    const json = JSON.stringify(graph);
    setSaveState("saving");
    try {
      const res = await api<{ revision: number; issues: Issue[] }>(`/workspaces/${workspace.slug}/workflows/${slug}/draft/`, { method: "PUT", json: { graph, revision: revision.current } });
      revision.current = res.revision;
      savedJson.current = json;
      setServerIssues(res.issues);
      try { localStorage.removeItem(key); } catch { /* ignore */ }
      setSaveState(JSON.stringify(graph) === json ? "saved" : "dirty");
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 409) {
        const cur = (err.details as { current_revision?: number } | null)?.current_revision;
        if (force && cur) { revision.current = cur; void save(false); return; }
        setSaveState("conflict");
      } else setSaveState("error");
    }
  }, [graph, workspace.slug, slug, key]);

  useEffect(() => {
    if (!loaded || !editable) return;
    const json = JSON.stringify(graph);
    if (json === savedJson.current) return;
    try { localStorage.setItem(key, JSON.stringify({ graph, base: revision.current })); } catch { /* ignore */ }
    setSaveState((s) => (s === "conflict" ? s : "dirty"));
    if (saveState === "conflict") return;
    const t = setTimeout(() => void save(), saveState === "error" ? 5000 : 1200);
    return () => clearTimeout(t);
  }, [graph, loaded, editable, save, key, saveState]);

  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => { if (saveState === "dirty" || saveState === "conflict") e.preventDefault(); };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [saveState]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (!(e.ctrlKey || e.metaKey) || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.closest(".cm-editor")) return;
      if (e.key === "z" && !e.shiftKey) { e.preventDefault(); history.undo(); }
      if (e.key === "y" || (e.key === "z" && e.shiftKey)) { e.preventDefault(); history.redo(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [history]);

  const issues = useMemo(() => mergeIssues(clientValidate(graph, actions), serverIssues), [graph, actions, serverIssues]);

  const validateNow = useCallback(async () => {
    if (saveState !== "saved") await save();
    const res = await api<{ issues: Issue[] }>(`/workspaces/${workspace.slug}/workflows/${slug}/validate/`, { json: {} });
    setServerIssues(res.issues);
    return res.issues;
  }, [save, saveState, workspace.slug, slug]);

  const value: Draft | null = wf ? {
    slug, wf, graph, setGraph: history.set, undo: history.undo, redo: history.redo, canUndo: history.canUndo, canRedo: history.canRedo,
    saveState, save, issues, serverIssues, setServerIssues, validateNow, editable, actions, workflows, recover,
    reload: async () => { await refetch(); setLoaded(false); setSaveState("saved"); },
    refetchWorkflow: refetch,
    acceptRecovery: () => { if (recover) history.set(recover.graph); setRecover(null); },
    discardRecovery: () => { try { localStorage.removeItem(key); } catch { /* ignore */ } setRecover(null); },
  } : null;

  if (isError) return <NotFoundPage title="Workflow introuvable" message="Ce workflow n'existe pas, a été supprimé, ou vous n'avez pas le droit de le voir." />;
  if (!value) return <p role="status">Chargement…</p>;
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
