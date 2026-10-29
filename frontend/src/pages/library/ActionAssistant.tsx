import { useState } from "react";
import type { JsonSchema } from "../../api";
import { AssistantPanel, SchemaTable } from "../../components/AssistantPanel";
import { SourceViewer } from "../../components/SourceViewer";

export interface ActionProposal {
  slug: string; name: string; description: string; input_schema: JsonSchema; output_schema: JsonSchema; source: string;
  runtime: string; side_effects: string; network_policy: string; examples: Record<string, unknown>[];
}

const EXAMPLES = [
  "Une action qui reçoit une liste de montants et renvoie le total, la moyenne et le plus grand.",
  "Une action qui reçoit un nom de serveur et vérifie qu'il respecte la règle « srv-» + 3 chiffres.",
  "Une action qui transforme un texte en identifiant sans accents ni espaces.",
];

/** « Générer avec l'IA » for actions: a proposal (code + contract) to review in the usual creation form. */
export function ActionAssistant({ onUse, onClose }: { onUse: (p: ActionProposal) => void; onClose: () => void }) {
  const [runtime, setRuntime] = useState("python");
  return (
    <>
      <div className="field"><label htmlFor="asst-rt">Langage</label>
        <select id="asst-rt" value={runtime} onChange={(e) => setRuntime(e.target.value)}><option value="python">Python</option><option value="node">Node.js</option></select>
        <p className="hint">L'action s'exécutera sans réseau, sans fichier et sans secret : elle calcule, transforme ou contrôle des données reçues. Pour appeler un service, utilisez plutôt une étape « Requête HTTP » ou une intégration.</p></div>
      <AssistantPanel<ActionProposal> endpoint="/ai/propose-action/" extra={{ runtime }} what="l'action" onUse={onUse} onClose={onClose}
        placeholder="Ex. : une action qui reçoit un texte et renvoie le nombre de mots, le mot le plus long et la phrase en majuscules." examples={EXAMPLES}
        render={(p) => (
          <>
            <h3>{p.name} <code className="mono">{p.slug}</code></h3>
            <p>{p.description}</p>
            <h4>Entrées</h4><SchemaTable schema={p.input_schema} caption="Entrées proposées" />
            <h4>Sorties</h4><SchemaTable schema={p.output_schema} caption="Sorties proposées" />
            <h4>Code</h4><SourceViewer source={p.source} runtime={p.runtime} name={p.slug} />
            {p.examples[0] && <><h4>Exemple d'entrées</h4><pre className="wrap">{JSON.stringify(p.examples[0], null, 2)}</pre></>}
          </>
        )} />
    </>
  );
}
