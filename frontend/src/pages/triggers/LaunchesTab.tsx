import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Confirm, EmptyRow, Section, usePaged, type Tone } from "../../components/AdminUi";
import { HelpTip } from "../../components/HelpTip";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";

export interface Launch {
  id: string;
  workflow: string;
  workflow_name: string;
  version: string;
  environment: string;
  run_at: string;
  timezone: string;
  status: "pending" | "fired" | "cancelled" | "refused";
  note: string;
  requested_by: string;
  source_run: string | null;
  source_workflow: string;
  fired_run: string | null;
  fired_at: string | null;
}

const STATUS: Record<Launch["status"], [string, Tone]> = {
  pending: ["Programmé", "info"],
  fired: ["Lancé", "ok"],
  cancelled: ["Annulé", "muted"],
  refused: ["Refusé", "bad"],
};
const ENV: Record<string, string> = { dev: "Développement", test: "Test", prod: "Production" };

/** Les lancements programmés par une étape « Programmer » : ce qui attend (annulable), et l'historique récent (lancé, annulé, refusé et pourquoi). */
export function LaunchesTab() {
  const { can } = useApp();
  const list = useWsQuery<{ count: number; results: Launch[] }>("scheduled-launches", "/scheduled-launches/", { refetchInterval: 15000 });
  const cancel = useWsMutation<{ id: string }>("DELETE", (i) => `/scheduled-launches/${i.id}/`, ["scheduled-launches"]);
  const [ask, setAsk] = useState<Launch | null>(null);
  const [status, setStatus] = useState("");
  const all = list.data?.results ?? [];
  const rows = all.filter((r) => !status || r.status === status);
  const paged = usePaged(rows, 10);
  const canCancel = can("run");
  return (
    <>
      <Section
        title="Lancements programmés"
        info={
          <HelpTip caption="À savoir">
            Une étape « Programmer » d'un workflow programme le lancement d'un autre workflow à une date et une heure. Il part tout seul à l'heure dite, avec
            l'identité de la personne qui avait lancé le workflow programmateur. Si la plate-forme était arrêtée à ce moment-là, il part dès qu'elle repart.
          </HelpTip>
        }
      >
        <div className="adm-filters">
          <label htmlFor="la-status" className="sr-only">
            État
          </label>
          <select id="la-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Tous les états</option>
            {Object.entries(STATUS).map(([k, [label]]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          <span className="lst-count" role="status">
            {rows.length} lancement(s)
          </span>
        </div>
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Lancements programmés</caption>
            <thead>
              <tr>
                <th scope="col">Workflow</th>
                <th scope="col">Quand</th>
                <th scope="col">État</th>
                <th scope="col">Programmé par</th>
                <th scope="col">Environnement</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {paged.slice.map((r) => (
                <tr key={r.id}>
                  <td>
                    <strong>{r.workflow_name || r.workflow}</strong>
                    {r.version !== "latest" && <span className="hint"> v{r.version}</span>}
                    {r.source_workflow && <div className="hint">par {r.source_workflow}</div>}
                  </td>
                  <td>
                    {formatDate(r.run_at)}
                    <div className="hint">{r.timezone}</div>
                  </td>
                  <td>
                    <Badge tone={STATUS[r.status][1]}>{STATUS[r.status][0]}</Badge>
                    {r.note && <div className="hint">{r.note}</div>}
                    {r.fired_run && (
                      <div>
                        <Link to={`/executions/${r.fired_run}`}>Voir l'exécution</Link>
                      </div>
                    )}
                  </td>
                  <td>{r.requested_by || "—"}</td>
                  <td>{ENV[r.environment] ?? r.environment}</td>
                  <td className="row-actions">
                    {r.status === "pending" && canCancel && (
                      <button
                        type="button"
                        className="danger"
                        onClick={() => setAsk(r)}
                        aria-label={`Annuler le lancement de ${r.workflow_name || r.workflow} prévu le ${formatDate(r.run_at)}`}
                      >
                        Annuler
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <EmptyRow cols={6}>
                  {list.isLoading
                    ? "Chargement…"
                    : all.length === 0
                      ? "Aucun lancement programmé : ajoutez une étape « Programmer » à un workflow."
                      : "Aucun lancement dans cet état."}
                </EmptyRow>
              )}
            </tbody>
          </table>
        </div>
        {paged.pager("Pagination des lancements programmés")}
        {list.error && (
          <p role="alert" className="field-error">
            Erreur : {errorText(list.error)}
          </p>
        )}
      </Section>
      {ask && (
        <Confirm
          title="Annuler ce lancement ?"
          confirmLabel="Annuler le lancement"
          busy={cancel.isPending}
          onCancel={() => setAsk(null)}
          onConfirm={() => {
            cancel.mutate({ id: ask.id });
            setAsk(null);
          }}
        >
          <p>
            <strong>{ask.workflow_name || ask.workflow}</strong> ne sera pas lancé le {formatDate(ask.run_at)}. Le workflow qui l'avait programmé n'est pas
            modifié.
          </p>
        </Confirm>
      )}
      {cancel.error && (
        <p role="alert" className="field-error">
          Erreur : {errorText(cancel.error)}
        </p>
      )}
    </>
  );
}
