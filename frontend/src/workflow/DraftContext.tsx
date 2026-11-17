import { NotFoundPage } from "../pages/NotFoundPage";
import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, apiAll, type Action, type ApiError, type WorkflowDetail, type WorkflowSummary } from "../api";
import { useApp } from "../context";
import { clientValidate, emptyGraph, mergeIssues, placeNewNodes } from "./graphOps";
import type { Graph, Issue } from "./types";
import { useHistory } from "./useHistory";

/** `invalid` : le serveur a refusé ce brouillon tel quel (400) ; `error` : échec d'écriture, ou valeur enregistrée différente de celle affichée. */
export type SaveState = "saved" | "dirty" | "saving" | "conflict" | "error" | "invalid";

/** Même contenu JSON, quel que soit l'ordre des clés (PostgreSQL `jsonb` ne conserve pas celui de l'envoi). */
function canonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (v && typeof v === "object")
    return `{${Object.keys(v)
      .sort()
      .filter((k) => (v as Record<string, unknown>)[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${canonical((v as Record<string, unknown>)[k])}`)
      .join(",")}}`;
  return JSON.stringify(v) ?? "null";
}

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
  /** Pourquoi le brouillon n'est pas enregistré (refus du serveur, valeur différente, conflit) ; vide sinon. */
  saveMessage: string;
  /** Écrit le brouillon ; vrai seulement si le serveur a confirmé la valeur stockée (faux : conflit, refus, échec, valeur différente). */
  save: (force?: boolean) => Promise<boolean>;
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

const noop = () => undefined;
const noopAsync = async () => undefined;
const savedAsync = async () => true;

/** A fixed view of another workflow's draft for a preview: nothing is loaded, saved or recovered, and nothing can change. */
export function ReadOnlyDraftProvider({
  slug,
  wf,
  actions,
  workflows,
  children,
}: {
  slug: string;
  wf: WorkflowDetail;
  actions: Action[];
  workflows: WorkflowSummary[];
  children: ReactNode;
}) {
  const graph = useMemo(() => placeNewNodes(wf.draft_graph ?? emptyGraph()), [wf]);
  const value: Draft = {
    slug,
    wf,
    graph,
    setGraph: noop,
    undo: noop,
    redo: noop,
    canUndo: false,
    canRedo: false,
    saveState: "saved",
    saveMessage: "",
    save: savedAsync,
    reload: noopAsync,
    issues: [],
    serverIssues: [],
    setServerIssues: noop,
    validateNow: async () => [],
    editable: false,
    actions,
    workflows,
    recover: null,
    acceptRecovery: noop,
    discardRecovery: noop,
    refetchWorkflow: noopAsync,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
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
  const key = localKey(workspace.slug, slug);
  const {
    data: wf,
    refetch,
    isError,
  } = useQuery({
    queryKey: ["workflow", workspace.slug, slug],
    queryFn: () => api<WorkflowDetail>(`/workspaces/${workspace.slug}/workflows/${slug}/`),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const editable = can("edit") && (!wf?.folder_level || wf.folder_level === "edit"); // a private folder can narrow the role
  const { data: actionsPage } = useQuery({
    queryKey: ["actions", workspace.slug, ""],
    queryFn: () => apiAll<Action>(`/workspaces/${workspace.slug}/actions/?q=`),
  });
  const { data: wfPage } = useQuery({
    queryKey: ["workflows", workspace.slug],
    queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`),
  });
  const actions = actionsPage?.results ?? [];
  const workflows = (wfPage?.results ?? []).filter((w) => w.slug !== slug);

  const history = useHistory<Graph>(emptyGraph());
  const graph = history.value;
  // A draft written by the assistant (or imported) may have no positions. Every consumer sees the laid-out graph, so the
  // first edit (a drag, a new step) starts from what is on screen and saves a position for EVERY node: otherwise only the
  // moved node would keep one and the others would be laid out again around it, differently, at each display.
  const shown = useMemo(() => placeNewNodes(graph), [graph]);
  const [loaded, setLoaded] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [saveMessage, setSaveMessage] = useState("");
  const failedJson = useRef(""); // le contenu que le serveur vient de refuser : on ne le renvoie pas en boucle
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

  // Les écritures sont mises en file : une seule est en vol, chacune part avec la révision laissée par la précédente.
  // Sans cela, un serveur lent (plus de 1,2 s) voyait partir plusieurs écritures avec la même révision : les suivantes
  // étaient refusées (409) par les premières de ce même navigateur, et l'éditeur affichait un conflit imaginaire.
  const chain = useRef<Promise<void>>(Promise.resolve());
  const write = useCallback(
    async (force = false): Promise<boolean> => {
      const json = JSON.stringify(graph);
      if (json === savedJson.current && !force) {
        setSaveState("saved");
        return true;
      } // rien de nouveau depuis l'écriture précédente
      setSaveState("saving");
      setSaveMessage("");
      let overwrite = force; // forcé : en cas de conflit, on reprend la révision courante et on réessaie une fois
      for (;;) {
        try {
          const res = await api<{ revision: number; issues: Issue[]; graph?: Graph }>(`/workspaces/${workspace.slug}/workflows/${slug}/draft/`, {
            method: "PUT",
            json: { graph, revision: revision.current },
          });
          revision.current = res.revision;
          savedJson.current = json;
          failedJson.current = "";
          setServerIssues(res.issues);
          try {
            localStorage.removeItem(key);
          } catch {
            /* ignore */
          }
          // « Enregistré » seulement si le serveur confirme la valeur qu'il a réellement stockée (la réponse la rend ; un ancien serveur ne la rend pas).
          if (res.graph !== undefined && canonical(res.graph) !== canonical(graph)) {
            setSaveMessage("Le serveur a enregistré un contenu différent de celui affiché. Rechargez le brouillon avant de continuer.");
            setSaveState("error");
            return false;
          }
          setSaveState("saved");
          return true;
        } catch (e) {
          const err = e as ApiError;
          if (err.status === 400) {
            failedJson.current = json;
            setSaveMessage(
              typeof err.message === "string" && err.message ? `Brouillon refusé par le serveur : ${err.message}` : "Brouillon refusé par le serveur.",
            );
            setSaveState("invalid");
            return false;
          }
          if (err.status !== 409) {
            setSaveMessage("Le serveur n'a pas pu enregistrer le brouillon ; nouvel essai automatique.");
            setSaveState("error");
            return false;
          }
          const cur = (err.details as { current_revision?: number } | null)?.current_revision;
          if (overwrite && cur) {
            revision.current = cur;
            overwrite = false;
            continue;
          }
          setSaveMessage(
            `Ce brouillon a été modifié ailleurs (révision ${cur ?? "?"} au lieu de ${revision.current}). Rien n'a été écrasé : rechargez la version du serveur ou écrasez-la avec la vôtre.`,
          );
          setSaveState("conflict");
          return false;
        }
      }
    },
    [graph, workspace.slug, slug, key],
  );
  const save = useCallback(
    (force = false) => {
      const run = chain.current.then(() => write(force));
      chain.current = run.then(
        () => undefined,
        () => undefined,
      );
      return run;
    },
    [write],
  );

  useEffect(() => {
    if (!loaded || !editable) return;
    const json = JSON.stringify(graph);
    if (json === savedJson.current) return;
    try {
      localStorage.setItem(key, JSON.stringify({ graph, base: revision.current }));
    } catch {
      /* ignore */
    }
    if (saveState === "saving") return; // une écriture est en vol : on n'affiche pas « modifications en attente » à sa place ; la suite est évaluée à son retour
    if (saveState === "invalid" && json === failedJson.current) return; // déjà refusé tel quel : la prochaine modification réessaie
    setSaveState((s) => (s === "conflict" ? s : "dirty"));
    if (saveState === "conflict") return;
    const t = setTimeout(() => void save(), saveState === "error" ? 5000 : 1200);
    return () => clearTimeout(t);
  }, [graph, loaded, editable, save, key, saveState]);

  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => {
      if (saveState === "dirty" || saveState === "conflict") e.preventDefault();
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [saveState]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (!(e.ctrlKey || e.metaKey) || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.closest(".cm-editor")) return;
      if (e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        history.undo();
      }
      if (e.key === "y" || (e.key === "z" && e.shiftKey)) {
        e.preventDefault();
        history.redo();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [history]);

  const issues = useMemo(() => mergeIssues(clientValidate(graph, actions), serverIssues), [graph, actions, serverIssues]);

  const validateNow = useCallback(async () => {
    if (saveState !== "saved" && !(await save())) return serverIssues; // un brouillon non enregistré ne se valide pas comme s'il l'était
    const res = await api<{ issues: Issue[] }>(`/workspaces/${workspace.slug}/workflows/${slug}/validate/`, { json: {} });
    setServerIssues(res.issues);
    return res.issues;
  }, [save, saveState, serverIssues, workspace.slug, slug]);

  const value: Draft | null = wf
    ? {
        slug,
        wf,
        graph: shown,
        setGraph: history.set,
        undo: history.undo,
        redo: history.redo,
        canUndo: history.canUndo,
        canRedo: history.canRedo,
        saveState,
        saveMessage,
        save,
        issues,
        serverIssues,
        setServerIssues,
        validateNow,
        editable,
        actions,
        workflows,
        recover,
        reload: async () => {
          await refetch();
          setLoaded(false);
          setSaveMessage("");
          setSaveState("saved");
        },
        refetchWorkflow: refetch,
        acceptRecovery: () => {
          if (recover) history.set(recover.graph);
          setRecover(null);
        },
        discardRecovery: () => {
          try {
            localStorage.removeItem(key);
          } catch {
            /* ignore */
          }
          setRecover(null);
        },
      }
    : null;

  if (isError) return <NotFoundPage title="Workflow introuvable" message="Ce workflow n'existe pas, a été supprimé, ou vous n'avez pas le droit de le voir." />;
  if (!value) return <p role="status">Chargement…</p>;
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
