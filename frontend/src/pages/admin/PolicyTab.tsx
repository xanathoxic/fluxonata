import { useEffect, useState } from "react";
import { errorText, useWsMutation, useWsQuery } from "../../lib";

interface Policy { version: number; rules: Record<string, boolean> }
const LABELS: Record<string, string> = {
  prod_requires_promotion: "Une version doit être promue avant de s'exécuter en production",
  promotion_requires_review: "La promotion en production doit être revue par une autre personne",
  forbid_self_review: "Interdire de réviser sa propre demande de promotion",
  require_signed_packages: "N'importer que des paquets signés par un éditeur de confiance",
  forbid_self_approval_in_prod: "Interdire l'auto-approbation des étapes humaines en production",
};

export function PolicyTab() {
  const q = useWsQuery<Policy>("policy", "/policy/");
  const save = useWsMutation<{ rules: Record<string, boolean> }>("PUT", "/policy/", ["policy"]);
  const [rules, setRules] = useState<Record<string, boolean>>({});
  useEffect(() => { if (q.data) setRules(q.data.rules); }, [q.data]);
  return (
    <>
      <h2>Politiques de gouvernance</h2>
      <p className="hint">Chaque modification crée une nouvelle version de la politique ; chaque exécution conserve la version qui l'a gouvernée. Version actuelle : {q.data?.version ?? "—"}.</p>
      <form onSubmit={(e) => { e.preventDefault(); save.mutate({ rules }); }}>
        {Object.entries(LABELS).map(([k, label]) => (
          <div className="field field-inline" key={k}><input id={`p-${k}`} type="checkbox" checked={rules[k] ?? false} onChange={(e) => setRules({ ...rules, [k]: e.target.checked })} /><label htmlFor={`p-${k}`}>{label}</label></div>
        ))}
        <button type="submit" className="primary" disabled={save.isPending}>Publier une nouvelle version de la politique</button>
        {save.isSuccess && <p role="status">Politique enregistrée.</p>}
        {save.error && <p role="alert" className="field-error">Erreur : {errorText(save.error)}</p>}
      </form>
    </>
  );
}
