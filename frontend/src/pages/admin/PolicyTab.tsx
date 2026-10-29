import { useEffect, useState } from "react";
import { Badge, Section } from "../../components/AdminUi";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { HelpTip } from "../../components/HelpTip";

interface Policy { version: number; rules: Record<string, boolean> }
const RULES: { key: string; label: string; why: string; group: string }[] = [
  { key: "prod_requires_promotion", label: "Une version doit être promue avant de s'exécuter en production", why: "Une version n'atteint la production qu'après une promotion explicite.", group: "Production" },
  { key: "promotion_requires_review", label: "La promotion en production doit être revue par une autre personne", why: "Deux personnes au moins interviennent avant la production.", group: "Production" },
  { key: "forbid_self_review", label: "Interdire de réviser sa propre demande de promotion", why: "Le demandeur ne peut pas valider sa propre demande.", group: "Production" },
  { key: "forbid_self_approval_in_prod", label: "Interdire l'auto-approbation des étapes humaines en production", why: "La personne qui lance une exécution ne peut pas approuver ses propres étapes.", group: "Approbations" },
  { key: "require_signed_packages", label: "N'importer que des paquets signés par un éditeur de confiance", why: "Les paquets d'intégration non signés sont refusés à l'import.", group: "Paquets" },
];

export function PolicyTab() {
  const q = useWsQuery<Policy>("policy", "/policy/");
  const save = useWsMutation<{ rules: Record<string, boolean> }>("PUT", "/policy/", ["policy"]);
  const [rules, setRules] = useState<Record<string, boolean>>({});
  useEffect(() => { if (q.data) setRules(q.data.rules); }, [q.data]);
  const dirty = q.data ? RULES.some((r) => (rules[r.key] ?? false) !== (q.data?.rules[r.key] ?? false)) : false;
  const active = RULES.filter((r) => rules[r.key]).length;
  const groups = [...new Set(RULES.map((r) => r.group))];
  return (
    <Section title="Politiques de gouvernance"
      hint={<>Chaque modification crée une nouvelle version de la politique ; chaque exécution conserve la version qui l'a gouvernée. Version actuelle : {q.data?.version ?? "—"}.</>}
      actions={<Badge tone={active === RULES.length ? "ok" : "info"}>{active} règle(s) sur {RULES.length} actives</Badge>}>
      <form onSubmit={(e) => { e.preventDefault(); save.mutate({ rules }); }}>
        {groups.map((g) => (
          <fieldset key={g} className="adm-rules">
            <legend>{g}</legend>
            {RULES.filter((r) => r.group === g).map((r) => (
              <div className="adm-rule" key={r.key}>
                <input id={`p-${r.key}`} className="adm-switch" type="checkbox" checked={rules[r.key] ?? false} onChange={(e) => setRules({ ...rules, [r.key]: e.target.checked })} />
                <div><label htmlFor={`p-${r.key}`}>{r.label}</label><HelpTip>{r.why}</HelpTip></div>
              </div>
            ))}
          </fieldset>
        ))}
        <div className="adm-savebar">
          <button type="submit" className="primary" disabled={save.isPending || !dirty}>Publier une nouvelle version de la politique</button>
          {dirty && <span className="hint" role="status">Modifications non publiées.</span>}
          {save.isSuccess && !dirty && <span role="status" className="ok-text">✔ Politique enregistrée.</span>}
        </div>
        {save.error && <p role="alert" className="field-error">Erreur : {errorText(save.error)}</p>}
      </form>
    </Section>
  );
}
