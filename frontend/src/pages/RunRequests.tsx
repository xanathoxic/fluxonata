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

interface AutoRun {
  enabled: boolean;
  unrestricted: boolean;
  enabled_by: string | null;
  enabled_at: string | null;
  can_change: boolean;
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

/** Validation automatique : l'assistant n'attend plus une confirmation ; seulement dev et recette ; réservée aux administrateurs de l'espace. */
function AutoRunSwitch() {
  const q = useWsQuery<AutoRun>("run-requests-auto", "/run-requests/auto/");
  const set = useWsMutation<{ enabled: boolean; unrestricted?: boolean }>("PUT", "/run-requests/auto/", ["run-requests-auto"]);
  const [asking, setAsking] = useState(false);
  const [full, setFull] = useState(false); // « débridage total » choisi dans la fenêtre d'avertissement
  const auto = q.data && !Array.isArray(q.data) ? q.data : null;
  const enabled = auto?.enabled ?? false;
  const canChange = auto?.can_change ?? false;
  const unrestricted = enabled && (auto?.unrestricted ?? false);
  const ask = () => {
    setFull(unrestricted);
    setAsking(true);
  };
  return (
    <div className="rr-auto">
      <label className="rr-auto-label">
        <input
          type="checkbox"
          checked={enabled}
          disabled={!canChange || set.isPending}
          onChange={(e) => (e.target.checked ? ask() : set.mutate({ enabled: false }))}
        />
        <span>Valider automatiquement les lancements demandés par un assistant</span>
      </label>
      <p className="hint">
        {enabled
          ? `Activée${auto?.enabled_by ? ` par ${auto.enabled_by}` : ""}${auto?.enabled_at ? ` le ${formatDate(auto.enabled_at)}` : ""} : les demandes en développement et en recette partent sans confirmation.${unrestricted ? " Débridage total actif : l'assistant publie et active lui-même." : ""}`
          : canChange
            ? "Désactivée : chaque demande attend votre confirmation."
            : "Désactivée. Seul un administrateur de l'espace peut l'activer."}
      </p>
      {enabled && canChange && (
        <p className="hint">
          <button type="button" className="linklike" onClick={ask}>
            Changer le niveau d'autonomie
          </button>
        </p>
      )}
      {set.error && (
        <p role="alert" className="field-error">
          Erreur : {errorText(set.error)}
        </p>
      )}
      {asking && (
        <Overlay
          variant="modal"
          title={enabled ? "Niveau d'autonomie de l'assistant" : "Activer la validation automatique ?"}
          description="Un assistant pourra lancer des workflows sans qu'une personne les confirme."
          onClose={() => setAsking(false)}
          footer={
            <>
              <button type="button" onClick={() => setAsking(false)}>
                Annuler
              </button>
              <button
                type="button"
                className="danger"
                disabled={set.isPending}
                onClick={() => set.mutate({ enabled: true, unrestricted: full }, { onSuccess: () => setAsking(false) })}
              >
                {full ? "Activer avec débridage total" : "Activer la validation automatique"}
              </button>
            </>
          }
        >
          <div role="alert" className="rr-warning">
            <strong>Attention : plus aucune confirmation humaine.</strong>
          </div>
          <ul className="rr-warning-list">
            <li>
              Toute demande valide d'un assistant est <strong>lancée immédiatement</strong>, sans que vous ayez examiné les entrées.
            </li>
            <li>
              Elle s'exécute au nom du <strong>propriétaire du jeton</strong> de l'assistant, avec ses droits.
            </li>
            <li>
              Seuls le <strong>développement</strong> et la <strong>recette</strong> sont concernés : la production reste toujours refusée.
            </li>
            <li>Les actions et étapes du workflow s'exécutent réellement (SSH, HTTP, messages…), selon le mode de chaque étape.</li>
            <li>Chaque lancement automatique est inscrit à l'audit, avec votre nom comme personne qui a activé l'option.</li>
          </ul>
          <div className={`rr-full${full ? " on" : ""}`}>
            <label className="rr-auto-label">
              <input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />
              <span>Débridage total</span>
            </label>
            <p className="hint">L'assistant devient autonome sur le développement et les tests d'un workflow dans son ensemble.</p>
            <ul className="rr-warning-list">
              <li>
                Il <strong>publie lui-même</strong> les workflows, actions, formulaires, contrôles et skills : chaque version est utilisable tout de suite.
              </li>
              <li>
                Il <strong>active ses propres propositions</strong> et considère comme relues les étapes qu'il a écrites (scripts, playbooks).
              </li>
              <li>Il enchaîne conception, publication, lancements et essais en développement et en recette, sans aucune relecture humaine.</li>
              <li>
                Reste interdit : la <strong>production</strong> et les promotions, les décisions d'approbation, les suppressions, les connexions, les secrets et
                les utilisateurs.
              </li>
            </ul>
            {full && (
              <p role="alert" className="rr-warning">
                <strong>Débridage total :</strong> du code écrit par l'assistant peut être publié et exécuté sur de vraies machines de développement et de
                recette sans que personne ne l'ait lu.
              </p>
            )}
          </div>
          <p className="hint">Vous pouvez la désactiver à tout moment depuis cette page.</p>
        </Overlay>
      )}
    </div>
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
          entrées puis confirmez, ou refusez. Un administrateur peut activer la validation automatique : les demandes partent alors sans confirmation.
        </HelpTip>
      </h2>
      <AutoRunSwitch />
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
