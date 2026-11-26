import { useState } from "react";
import { Link } from "react-router-dom";
import { usePaged } from "../components/AdminUi";
import { Fields } from "../components/Fields";
import { HelpTip } from "../components/HelpTip";
import { Overlay } from "../components/Overlay";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { errorText, useWsMutation, useWsQuery } from "../lib";

export interface RunRequest {
  id: string;
  workflow: { slug: string; name: string; version: string };
  environment: "dev" | "test" | "prod";
  inputs: Record<string, unknown>;
  reason: string;
  client: string;
  status: "pending" | "launched" | "refused" | "expired";
  requested_by: string | null;
  created_at: string;
  expires_at: string;
  can_decide: boolean;
}

const ENV: Record<string, string> = { dev: "Développement", test: "Recette", prod: "Production" };

function Row({ req }: { req: RunRequest }) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");
  const invalidate = ["run-requests", "workflow-runs"];
  const confirm = useWsMutation<{ comment: string }>("POST", `/run-requests/${req.id}/confirm/`, invalidate);
  const refuse = useWsMutation<{ comment: string }>("POST", `/run-requests/${req.id}/refuse/`, invalidate);
  const busy = confirm.isPending || refuse.isPending;
  const error = confirm.error ?? refuse.error;
  return (
    <li className="approval-row">
      <div className="ar-main">
        <strong>
          {req.workflow.name} — version {req.workflow.version}
        </strong>
        <span className="hint">
          {ENV[req.environment]} · demandé par un assistant au nom de {req.requested_by ?? "—"} le {formatDate(req.created_at)}
        </span>
      </div>
      <button type="button" className="primary" onClick={() => setOpen(true)} aria-label={`Examiner le lancement de ${req.workflow.name}`}>
        Examiner
      </button>
      {open && (
        <Overlay
          title={`Lancer « ${req.workflow.name} » ?`}
          description={`Demandé par un assistant externe au nom de ${req.requested_by ?? "—"} — rien ne s'exécute sans votre confirmation.`}
          variant="modal"
          onClose={() => setOpen(false)}
          footer={
            <>
              <button
                type="button"
                className="danger"
                disabled={!req.can_decide || busy}
                onClick={() => refuse.mutate({ comment }, { onSuccess: () => setOpen(false) })}
              >
                Refuser
              </button>
              <button
                type="button"
                className="primary"
                disabled={!req.can_decide || busy}
                onClick={() => confirm.mutate({ comment }, { onSuccess: () => setOpen(false) })}
              >
                {`Confirmer et lancer en ${(ENV[req.environment] ?? req.environment).toLowerCase()}`}
              </button>
            </>
          }
        >
          <dl className="ap-meta">
            <div>
              <dt>Workflow</dt>
              <dd>
                {req.workflow.name} (version {req.workflow.version})
              </dd>
            </div>
            <div>
              <dt>Environnement</dt>
              <dd>{ENV[req.environment]}</dd>
            </div>
            <div className="ap-subject">
              <dt>Motif donné par l'assistant</dt>
              <dd>{req.reason || "—"}</dd>
            </div>
            <div>
              <dt>Valable jusqu'à</dt>
              <dd>{formatDate(req.expires_at)}</dd>
            </div>
          </dl>
          <h3>Entrées qui seront utilisées</h3>
          <Fields data={req.inputs} />
          {req.can_decide ? (
            <div className="field">
              <label htmlFor={`rr-${req.id}`}>Commentaire (facultatif)</label>
              <input id={`rr-${req.id}`} value={comment} maxLength={300} onChange={(e) => setComment(e.target.value)} />
            </div>
          ) : (
            <p className="hint">Votre rôle ne permet pas de lancer ce workflow.</p>
          )}
          {error && (
            <p role="alert" className="field-error">
              Erreur : {errorText(error)}
            </p>
          )}
        </Overlay>
      )}
    </li>
  );
}

/** Les lancements qu'un assistant (MCP) a DEMANDÉS : rien ne part sans qu'une personne les confirme ici. */
export function RunRequests() {
  const { can } = useApp();
  const q = useWsQuery<RunRequest[]>("run-requests", "/run-requests/?status=pending", { refetchInterval: 5000 });
  const rows = Array.isArray(q.data) ? q.data : [];
  const paged = usePaged(rows, 5);
  if (rows.length === 0 && !can("run")) return null;
  return (
    <section aria-labelledby="run-requests" className="card">
      <h2 id="run-requests">
        Lancements demandés par un assistant{" "}
        <HelpTip>
          Un assistant connecté par MCP peut demander de lancer un workflow en développement ou en recette. Il ne lance jamais lui-même : vous vérifiez les
          entrées puis confirmez, ou refusez.
        </HelpTip>
      </h2>
      {rows.length === 0 && <p>Aucune demande en attente.</p>}
      <ul className="approval-list">
        {paged.slice.map((r) => (
          <Row key={r.id} req={r} />
        ))}
      </ul>
      {paged.pager("Pagination des lancements demandés")}
      {rows.length > 0 && (
        <p className="hint">
          Après confirmation, suivez l'exécution dans <Link to="/executions">Mes exécutions</Link>.
        </p>
      )}
    </section>
  );
}
