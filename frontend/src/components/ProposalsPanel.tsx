import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, apiAll, type ApiError } from "../api";
import { useApp } from "../context";
import { Badge } from "./AdminUi";

interface Proposal { slug: string; name: string; description: string; status: string; latest_version?: { version: string } | null }

/** « Propositions d'un assistant » (ADR 0025) : actions et skills préparées par un assistant externe. Rien n'est exécutable ni publiable
 *  avant qu'une personne les ait relues et activées ; le serveur applique la règle, cette liste n'est que le point de contrôle. */
export function ProposalsPanel({ kind }: { kind: "action" | "skill" }) {
  const { can, workspace } = useApp();
  const qc = useQueryClient();
  const base = `/workspaces/${workspace.slug}/${kind === "action" ? "actions" : "ai/skills"}`;
  const page = kind === "action" ? "/library/actions" : "/library/skills";
  const noun = kind === "action" ? "action" : "skill";
  const q = useQuery({ queryKey: ["proposals", kind, workspace.slug], enabled: can("edit"), queryFn: () => apiAll<Proposal>(`${base}/?status=proposed`) });
  const done = () => { void qc.invalidateQueries(); };
  const activate = useMutation<unknown, ApiError, string>({ mutationFn: (slug) => api(`${base}/${slug}/activate/`, { method: "POST", json: {} }), onSuccess: done });
  const reject = useMutation<unknown, ApiError, string>({ mutationFn: (slug) => api(`${base}/${slug}/deprecate/`, { method: "POST", json: { reason: "Proposition rejetée après relecture" } }), onSuccess: done });
  // le serveur ne renvoie les propositions qu'à qui peut modifier ; on filtre aussi ici pour ne jamais présenter une ressource active comme une proposition
  const rows = (q.data?.results ?? []).filter((p) => p.status === "proposed");
  if (!can("edit") || rows.length === 0) return null;
  const error = activate.error ?? reject.error;
  return (
    <section className="adm-card" aria-labelledby={`prop-${kind}`}>
      <h2 id={`prop-${kind}`}>Propositions d'un assistant ({rows.length})</h2>
      <p className="hint">Ces {noun}s ont été {kind === "action" ? "préparées" : "préparés"} par un assistant externe. Tant que vous ne les avez pas relus et activés, ils ne peuvent ni s'exécuter ni entrer dans un workflow publié.</p>
      <div className="table-wrap">
        <table className="adm-table">
          <caption className="sr-only">Propositions d'un assistant</caption>
          <thead><tr><th scope="col">{kind === "action" ? "Action" : "Skill"}</th><th scope="col">Description</th><th scope="col">Version</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.slug}>
                <td><Link className="lst-title" to={`${page}/${p.slug}`}>{p.name}</Link> <Badge tone="warn">À relire</Badge></td>
                <td>{p.description || "—"}</td>
                <td>{p.latest_version ? <Badge tone="info">v{p.latest_version.version}</Badge> : "—"}</td>
                <td>
                  <Link className="button" to={`${page}/${p.slug}`} aria-label={`Relire ${p.name}`}>Relire</Link>
                  {can("publish") && <>
                    <button type="button" className="primary" disabled={activate.isPending || reject.isPending} onClick={() => activate.mutate(p.slug)} aria-label={`Activer ${p.name} après relecture`}>Activer</button>
                    <button type="button" disabled={activate.isPending || reject.isPending} onClick={() => reject.mutate(p.slug)} aria-label={`Rejeter ${p.name}`}>Rejeter</button>
                  </>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {error && <p role="alert" className="field-error">Erreur : {error.message}</p>}
    </section>
  );
}
