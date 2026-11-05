import { useState } from "react";
import { MAX_ITEMS, MAX_ROWS, producesOf, type ControlSpec } from "../../forms/controls";
import { exampleOptions, exampleValue, fieldRows, interactions, optionSchema, valueSchema } from "../../forms/controlDocs";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import { Card } from "./DesignerUi";

const pretty = (v: unknown) => JSON.stringify(v, null, 2);
const PRODUCES: Record<string, string> = { object: "un objet", array: "une liste", string: "un texte" };

function Code({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => { try { void navigator.clipboard.writeText(text).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); }); } catch { /* clipboard unavailable */ } };
  return (
    <div className="dc-code">
      <button type="button" className="dc-copy" onClick={copy} aria-label={`Copier : ${label}`}><IconLabel icon={copied ? "check" : "copy"}>{copied ? "Copié" : "Copier"}</IconLabel></button>
      <pre className="ctl-json" aria-label={label} tabIndex={0}>{text}</pre>
    </div>
  );
}

/** The documentation of the control, written from the design as it stands: what it hands to the workflow (as a schema and as an example), its
 * sub-fields, the options it accepts, and how the form and the workflow reach each part. Nothing here is typed by hand. */
export function DocsPanel({ spec }: { spec: ControlSpec }) {
  const listy = spec.mode === "items" || spec.mode === "choice";
  const rows = fieldRows(spec.parts ?? []);
  const known = spec.items ?? [];
  const example = exampleValue(spec, known.length ? known : exampleOptions(spec));
  const access = interactions(spec);
  const produced = PRODUCES[producesOf(spec)] ?? "une valeur";
  return (
    <div className="dc">
      <Card icon="info" title="En bref">
        <ul className="dc-facts">
          <li>Le contrôle remet au workflow <strong>{produced}</strong>{spec.mode === "repeat" ? ` (de ${spec.min ?? 0} à ${spec.max ?? MAX_ROWS} lignes)` : spec.mode === "items" ? ` (de ${spec.min ?? 0} à ${spec.max ?? MAX_ITEMS} choix)` : ""}.</li>
          <li>{rows.length} champ(s) {spec.mode === "repeat" ? "par ligne" : listy ? "de détail par option" : "dans le groupe"}{rows.some((r) => r.required) ? `, dont ${rows.filter((r) => r.required).length} obligatoire(s)` : ""}.</li>
          {listy && <li>{spec.dynamic ? "Les options sont fournies par le formulaire (un service)" : `${known.length} option(s) fixe(s)`}{spec.items_editable ? ", la liste est modifiable par chaque formulaire" : ""}.</li>}
        </ul>
      </Card>

      <Card icon="braces" title="Valeur remise au workflow" help="Schéma JSON (draft 2020-12) de ce que le contrôle produit et de ce qu'il accepte en pré-remplissage. Il se met à jour à chaque modification du contrôle.">
        <h4>Schéma</h4>
        <Code label="Schéma JSON de la valeur" text={pretty(valueSchema(spec))} />
        <h4>Exemple</h4>
        <Code label="Exemple de valeur" text={pretty(example ?? null)} />
      </Card>

      <Card icon="design" title={spec.mode === "repeat" ? "Champs d'une ligne" : listy ? "Champs de détail d'une option" : "Champs du groupe"}>
        {rows.length === 0 ? <p className="hint">Aucun champ pour le moment.</p> : (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Champs du contrôle">
            <table className="adm-table dc-table">
              <caption className="sr-only">Champs du contrôle</caption>
              <thead><tr><th scope="col">Clé</th><th scope="col">Libellé</th><th scope="col">Contrôle</th><th scope="col">Valeur</th><th scope="col">Obligatoire</th><th scope="col">Précisions</th></tr></thead>
              <tbody>{rows.map((r) => <tr key={r.key}><td><code>{r.key}</code></td><td>{r.label}</td><td>{r.typeLabel}</td><td>{r.json}</td><td>{r.required ? "oui" : "non"}</td><td>{r.notes.join(" · ") || "—"}</td></tr>)}</tbody>
            </table>
          </div>
        )}
      </Card>

      {listy && (
        <Card icon="list2" title="Options reçues" help="Le format qu'un service (ou le test) peut envoyer pour fournir les options. Les clés des champs enfants, à plat, pré-remplissent ces champs quand l'option est choisie.">
          {!spec.dynamic && <p className="hint" role="note"><FormIcon name="info" size={14} /> Les options de ce contrôle sont fixes. Activez « Options fournies par le formulaire » (section Options) pour que les formulaires puissent en recevoir d'un service.</p>}
          <h4>Schéma d'une option</h4>
          <Code label="Schéma JSON d'une option" text={pretty(optionSchema(spec))} />
          <h4>Exemple de réponse</h4>
          <Code label="Exemple d'options" text={pretty(exampleOptions(spec))} />
        </Card>
      )}

      <Card icon="link" title="Interactions avec le formulaire et le workflow" help="« <clé> » est la clé que le formulaire donne au champ qui porte ce contrôle (propriétés du champ).">
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Accès au contrôle et à ses champs">
          <table className="adm-table dc-table">
            <caption className="sr-only">Comment accéder au contrôle et à ses champs</caption>
            <thead><tr><th scope="col">Je veux…</th><th scope="col">Où</th><th scope="col">Comment</th></tr></thead>
            <tbody>{access.map((a, i) => <tr key={i}><td>{a.goal}</td><td>{a.where}</td><td><code>{a.syntax}</code></td></tr>)}</tbody>
          </table>
        </div>
        <p className="hint">Le serveur revérifie la valeur à l'envoi avec les mêmes règles : une valeur forgée ou hors schéma est refusée.</p>
      </Card>
    </div>
  );
}
