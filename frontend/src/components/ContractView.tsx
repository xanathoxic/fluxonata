import type { Contract, ContractField } from "../api";

const EFFECT: Record<string, string> = { none: "Aucun effet externe : l'action ne modifie rien en dehors de la plateforme.", external: "Effet externe : l'action agit sur un système extérieur." };
const NETWORK: Record<string, string> = { none: "Aucun accès réseau", "test-service": "Service de test local uniquement" };

function Table({ label, rows, none, note }: { label: string; rows: ContractField[]; none: string; note: string }) {
  if (rows.length === 0) return <p><strong>{none}.</strong>{note && note !== `${none}.` ? <> {note}</> : null}</p>;
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">{label}</caption>
        <thead><tr><th scope="col">Nom</th><th scope="col">Type</th><th scope="col">{label === "Entrées" ? "Obligatoire" : "Toujours présent"}</th><th scope="col">À quoi ça sert</th><th scope="col">Valeur par défaut et limites</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <th scope="row"><code>{r.name}</code>{r.title && <><br /><span className="hint">{r.title}</span></>}</th>
              <td>{r.type}</td>
              <td>{r.required ? "Oui" : "Non"}</td>
              <td>{r.description || <span className="field-error">Non décrit</span>}</td>
              <td>{[r.has_default ? `par défaut : ${JSON.stringify(r.default)}` : "", r.limits].filter(Boolean).join(" ; ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The contract of an action: what it does, every input and output (or the statement that there is none), its effects. */
export function ContractView({ contract }: { contract: Contract }) {
  return (
    <section aria-label="Contrat de l'action" className="card">
      {!contract.complete && (
        <div role="note" className="notice">
          <strong>⚠ Contrat incomplet.</strong> Publiez une nouvelle version pour le compléter :
          <ul>{contract.problems.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
      <h3>Ce que fait l'action</h3>
      <p>{contract.summary || <span className="field-error">Non décrit</span>}</p>
      <h3>Entrées</h3>
      <Table label="Entrées" rows={contract.inputs} none="Aucune entrée" note={contract.inputs_note} />
      <h3>Sorties</h3>
      <Table label="Sorties" rows={contract.outputs} none="Aucune sortie" note={contract.outputs_note} />
      <h3>Effets et conditions d'exécution</h3>
      <ul>
        <li>{EFFECT[contract.effects.side_effects] ?? contract.effects.side_effects}{contract.effects.side_effects === "external" && !contract.effects.idempotent ? " Elle n'est pas idempotente : elle n'est jamais relancée à l'aveugle si son issue est incertaine." : ""}</li>
        <li>{NETWORK[contract.effects.network_policy] ?? contract.effects.network_policy}</li>
        <li>{contract.effects.needs_connections.length ? `Connexion(s) nécessaire(s) : ${contract.effects.needs_connections.join(", ")}` : "Aucune connexion (secret) nécessaire"}</li>
      </ul>
    </section>
  );
}
