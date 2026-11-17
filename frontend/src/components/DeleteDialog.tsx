import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError } from "../api";
import { useApp } from "../context";
import { errorText } from "../lib";
import { ConfirmDialog } from "./ConfirmDialog";
import { Overlay } from "./Overlay";

export interface Usage {
  used_by: { slug: string; name: string; where: string[] }[];
  hidden: number;
  total: number;
  /** Exécutions qui partiraient avec l'élément (exécutions de workflow, ou d'action), dont celles encore en cours. */
  executions?: number;
  running?: number;
  /** Déclencheurs du workflow (planifications, webhooks, formulaires publics, interrogations), par nature. */
  triggers?: Record<string, number>;
}
const TRIGGER_NAMES: Record<string, [string, string]> = {
  schedules: ["planification", "planifications"],
  webhooks: ["webhook", "webhooks"],
  forms: ["formulaire public", "formulaires publics"],
  polls: ["interrogation", "interrogations"],
};
const plural = (n: number, one: string, many: string) => `${n} ${n > 1 ? many : one}`;
const triggerText = (t: Record<string, number>) =>
  Object.entries(t)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => plural(n, ...(TRIGGER_NAMES[k] ?? [k, k])))
    .join(", ");
const PATH = { action: "actions", workflow: "workflows" } as const;
const NOUN = { action: "l'action", workflow: "le workflow" } as const;

/** Supprimer une action ou un workflow. Tant qu'un AUTRE workflow l'appelle (brouillon ou version publiée), rien n'est supprimé : une fenêtre
 *  liste ces workflows. Sinon : une confirmation, puis la suppression, définitive. L'usage est lu AVANT, et relu si le serveur refuse (409). */
export function DeleteDialog({
  kind,
  slug,
  name,
  onClose,
  onDeleted,
}: {
  kind: "action" | "workflow";
  slug: string;
  name: string;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const { workspace } = useApp();
  const qc = useQueryClient();
  const base = `/workspaces/${workspace.slug}/${PATH[kind]}/${slug}`;
  const usage = useQuery({ queryKey: ["usage", kind, workspace.slug, slug], queryFn: () => api<Usage>(`${base}/usage/`), gcTime: 0 });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [withHistory, setWithHistory] = useState(false);
  const [withTriggers, setWithTriggers] = useState(false);
  const [typed, setTyped] = useState("");

  const remove = async () => {
    setBusy(true);
    setProblem("");
    try {
      const flags = [withHistory && "with_history=1", withTriggers && "with_triggers=1"].filter(Boolean).join("&");
      await api(`${base}/delete/${flags ? `?${flags}` : ""}`, { method: "DELETE" });
      void qc.invalidateQueries();
      onDeleted();
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 409)
        void usage.refetch(); // quelqu'un vient de l'utiliser : la liste s'affiche
      else setProblem(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  if (usage.isLoading)
    return (
      <Overlay
        variant="modal"
        title={`Supprimer « ${name} » ?`}
        onClose={onClose}
        footer={
          <button type="button" onClick={onClose}>
            Annuler
          </button>
        }
      >
        <p role="status">Recherche des workflows qui l'utilisent…</p>
      </Overlay>
    );
  if (usage.isError)
    return (
      <Overlay
        variant="modal"
        title={`Supprimer « ${name} » ?`}
        onClose={onClose}
        footer={
          <button type="button" onClick={onClose}>
            Fermer
          </button>
        }
      >
        <p role="alert" className="field-error">
          Impossible de vérifier si {NOUN[kind]} est utilisé : {errorText(usage.error)}
        </p>
      </Overlay>
    );
  const u = usage.data;
  if (u && u.total > 0) {
    return (
      <Overlay
        variant="modal"
        title={`« ${name} » est utilisé`}
        description={`Suppression impossible : ${u.total} workflow${u.total > 1 ? "s" : ""} ${u.total > 1 ? "appellent" : "appelle"} ${NOUN[kind]}.`}
        onClose={onClose}
        footer={
          <button type="button" className="primary" onClick={onClose}>
            Fermer
          </button>
        }
      >
        <p>Retirez ou remplacez {NOUN[kind]} dans ces workflows (brouillon et versions publiées), puis recommencez.</p>
        <ul aria-label="Workflows qui l'utilisent" className="usage-list">
          {u.used_by.map((w) => (
            <li key={w.slug}>
              <Link to={`/workflows/${w.slug}/schema`} onClick={onClose}>
                {w.name}
              </Link>{" "}
              <span className="hint">({w.where.join(", ")})</span>
            </li>
          ))}
        </ul>
        {u.hidden > 0 && (
          <p className="hint">
            {u.hidden} autre{u.hidden > 1 ? "s" : ""} workflow{u.hidden > 1 ? "s" : ""} que vous n'avez pas le droit de voir{" "}
            {u.hidden > 1 ? "l'utilisent" : "l'utilise"} aussi.
          </p>
        )}
      </Overlay>
    );
  }
  const executions = u?.executions ?? 0;
  const running = u?.running ?? 0;
  const triggers = u?.triggers ?? {};
  const triggerCount = Object.values(triggers).reduce((a, b) => a + b, 0);
  const ready = (executions === 0 || withHistory) && (triggerCount === 0 || withTriggers) && (!withHistory || typed.trim() === name);

  if (running > 0) {
    return (
      <Overlay
        variant="modal"
        title={`Supprimer « ${name} » ?`}
        description={`Suppression impossible : ${plural(running, "exécution est encore en cours", "exécutions sont encore en cours")}.`}
        onClose={onClose}
        footer={
          <button type="button" className="primary" onClick={onClose}>
            Fermer
          </button>
        }
      >
        <p>Annulez {running > 1 ? "ces exécutions" : "cette exécution"} (page Exécutions), puis recommencez : on ne supprime jamais ce qui tourne.</p>
      </Overlay>
    );
  }
  return (
    <ConfirmDialog title={`Supprimer « ${name} » ?`} confirmLabel="Supprimer" busy={busy || !ready} onCancel={onClose} onConfirm={() => void remove()}>
      <p>
        Aucun workflow n'utilise {NOUN[kind]}.{" "}
        {executions > 0 || triggerCount > 0 ? (
          "Mais des éléments en dépendent : sans votre accord exprès, ils le protègent."
        ) : (
          <>
            La suppression est <strong>définitive</strong>.
          </>
        )}
      </p>
      {executions > 0 && (
        <div className="field field-inline">
          <input id="del-history" type="checkbox" checked={withHistory} onChange={(e) => setWithHistory(e.target.checked)} />
          <label htmlFor="del-history">
            Supprimer aussi l'historique : {plural(executions, "exécution", "exécutions")}, avec leurs journaux, tentatives et fichiers. Définitif.
          </label>
        </div>
      )}
      {triggerCount > 0 && (
        <div className="field field-inline">
          <input id="del-triggers" type="checkbox" checked={withTriggers} onChange={(e) => setWithTriggers(e.target.checked)} />
          <label htmlFor="del-triggers">
            Supprimer aussi les déclencheurs : {triggerText(triggers)}. Ce qui les appelle (planification, système externe) cessera de fonctionner.
          </label>
        </div>
      )}
      {withHistory && (
        <div className="field">
          <label htmlFor="del-typed">Pour confirmer, saisissez le nom exact : {name}</label>
          <input id="del-typed" value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} />
        </div>
      )}
      {(executions > 0 || triggerCount > 0) && (
        <p className="hint">Déprécier {NOUN[kind]} le retire du catalogue sans rien perdre : fermez cette fenêtre pour le faire à la place.</p>
      )}
      {problem && (
        <p role="alert" className="field-error">
          {problem}
        </p>
      )}
    </ConfirmDialog>
  );
}
