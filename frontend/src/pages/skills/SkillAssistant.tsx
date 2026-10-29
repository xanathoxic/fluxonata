import type { JsonSchema } from "../../api";
import { AssistantPanel, SchemaTable } from "../../components/AssistantPanel";

export interface SkillProposal { slug: string; name: string; description: string; instructions: string; input_schema: JsonSchema; output_schema: JsonSchema; tags: string[] }

const EXAMPLES = [
  "Un skill qui lit un e-mail de client et le classe en réclamation, question ou autre, avec une urgence de 1 à 5.",
  "Un skill qui résume un incident en trois phrases pour une personne non technique.",
  "Un skill qui extrait d'un texte le nom du serveur, la date et la cause probable d'une panne.",
];

/** « Générer avec l'IA » for skills: instructions + typed inputs and answer format, to review in the usual form. */
export function SkillAssistant({ onUse, onClose }: { onUse: (p: SkillProposal) => void; onClose: () => void }) {
  return (
    <AssistantPanel<SkillProposal> endpoint="/ai/propose-skill/" what="le skill" onUse={onUse} onClose={onClose}
      placeholder="Ex. : un skill qui lit un message de client, le classe en réclamation, question ou autre, estime l'urgence et propose une réponse courte." examples={EXAMPLES}
      render={(p) => (
        <>
          <h3>{p.name} <code className="mono">{p.slug}</code></h3>
          <p>{p.description}</p>
          <h4>Instructions données à l'IA</h4><pre className="wrap" tabIndex={0}>{p.instructions}</pre>
          <h4>Entrées</h4><SchemaTable schema={p.input_schema} caption="Entrées proposées" />
          <h4>Champs de la réponse</h4><SchemaTable schema={p.output_schema} caption="Réponse attendue" />
        </>
      )} />
  );
}
