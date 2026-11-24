import { useState } from "react";
import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";
import { ModelPicker } from "./ModelPicker";

export type FileFormat = "text" | "csv" | "xlsx" | "pdf" | "docx" | "pptx" | "html" | "zip";
export const FILE_FORMATS: { value: FileFormat; label: string; ext: string }[] = [
  { value: "text", label: "Texte (.txt, .md, .json…)", ext: ".txt" },
  { value: "csv", label: "Tableau CSV", ext: ".csv" },
  { value: "xlsx", label: "Tableur Excel (.xlsx)", ext: ".xlsx" },
  { value: "pdf", label: "Document PDF", ext: ".pdf" },
  { value: "docx", label: "Document Word (.docx)", ext: ".docx" },
  { value: "pptx", label: "Présentation PowerPoint (.pptx)", ext: ".pptx" },
  { value: "html", label: "Page web HTML (.html)", ext: ".html" },
  { value: "zip", label: "Archive ZIP", ext: ".zip" },
];
const TABLES: FileFormat[] = ["csv", "xlsx"];
const TEXTS: FileFormat[] = ["text", "pdf", "docx", "pptx", "html"];
const MODEL_NOTES: Partial<Record<FileFormat, string>> = {
  html: "Page autonome, images intégrées.",
  docx: "Mise en forme complète, images et tableaux compris.",
  pdf: "Mise en page complète : titres, tableaux, liens, images PNG et JPEG.",
  pptx: "Une diapositive par bloc séparé par une ligne ---, avec mise en forme, tableaux et images.",
  text: "Le texte Markdown, tel quel.",
};

const asText = (v: unknown): string => (typeof v === "string" ? v : v === undefined || v === null ? "" : JSON.stringify(v));

/** Saisie d'une liste : le texte tapé reste tel quel (virgules, espaces, retours à la ligne) ; seule la liste nettoyée est enregistrée. */
function ListInput({
  id,
  items,
  separator,
  multiline,
  rows,
  placeholder,
  onChange,
}: {
  id: string;
  items?: string[];
  separator: RegExp;
  multiline?: boolean;
  rows?: number;
  placeholder: string;
  onChange: (items: string[] | undefined) => void;
}) {
  const [text, setText] = useState((items ?? []).join(multiline ? "\n" : ", "));
  const change = (value: string) => {
    setText(value);
    const list = value
      .split(separator)
      .map((c) => c.trim())
      .filter(Boolean);
    onChange(list.length ? list : undefined);
  };
  return multiline ? (
    <textarea id={id} rows={rows} value={text} placeholder={placeholder} onChange={(e) => change(e.target.value)} />
  ) : (
    <input id={id} value={text} placeholder={placeholder} onChange={(e) => change(e.target.value)} />
  );
}

/** Réglage de l'étape « Créer un fichier » : le format, le nom, puis ce qui va dedans. Le fichier se télécharge ensuite depuis la page de l'exécution (onglet Fichiers). */
export function FileConfig({
  node,
  onUpdate,
  models = [],
}: {
  node: GraphNode;
  onUpdate: (patch: Partial<GraphNode>) => void;
  models?: Pick<GraphNode, "id" | "name">[];
}) {
  const format = (node.format ?? "text") as FileFormat;
  const ext = FILE_FORMATS.find((f) => f.value === format)?.ext ?? "";
  return (
    <>
      <div className="field">
        <label htmlFor="fi-format">Format du fichier</label>
        <select id="fi-format" value={format} onChange={(e) => onUpdate({ format: e.target.value as FileFormat })}>
          {FILE_FORMATS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <HelpTip>
          Le nom peut contenir des expressions, ex. <code>rapport-{"{{ inputs.mois }}"}</code>. L'extension <code>{ext}</code> est ajoutée si elle manque.
        </HelpTip>
        <label htmlFor="fi-name">Nom du fichier</label>
        <input
          id="fi-name"
          list="expr-paths"
          value={node.filename ?? ""}
          placeholder={`rapport${ext}`}
          onChange={(e) => onUpdate({ filename: e.target.value })}
        />
      </div>
      {(format === "pdf" || format === "docx" || format === "pptx") && (
        <div className="field">
          <label htmlFor="fi-title">{format === "pptx" ? "Titre du diaporama (facultatif)" : "Titre (facultatif)"}</label>
          <input id="fi-title" value={node.title ?? ""} onChange={(e) => onUpdate({ title: e.target.value })} />
        </div>
      )}
      {TEXTS.includes(format) && (
        <ModelPicker
          value={node.model}
          models={models}
          onChange={(model) => onUpdate({ model })}
          hint={`${MODEL_NOTES[format] ?? ""} Le contenu ci-dessous n'est alors pas utilisé.`.trim()}
        />
      )}
      {TEXTS.includes(format) && !node.model && (
        <div className="field">
          <HelpTip>
            {format === "pptx" ? (
              <>
                Une diapositive par bloc, séparés par une ligne <code>---</code> : la première ligne est le titre, les suivantes sont des puces.
              </>
            ) : format === "docx" ? (
              <>
                Une ligne = un paragraphe ; <code># </code> et <code>## </code> font des intertitres, <code>- </code> une puce.
              </>
            ) : format === "pdf" ? (
              <>Texte mis en page (Markdown accepté, retours à la ligne conservés), 200 pages au plus. Les expressions sont remplacées par leur valeur.</>
            ) : (
              <>Le contenu du fichier, tel quel. Les expressions sont remplacées par leur valeur.</>
            )}{" "}
            Exemple d'expression : <code>{"{{ steps.lire.output.resultat }}"}</code>.
          </HelpTip>
          <label htmlFor="fi-content">{format === "pptx" ? "Diapositives" : "Contenu"}</label>
          <textarea id="fi-content" rows={8} value={asText(node.content)} onChange={(e) => onUpdate({ content: e.target.value })} />
        </div>
      )}
      {TABLES.includes(format) && (
        <>
          <div className="field">
            <HelpTip>
              Une expression qui donne une liste d'objets, ex. <code>{"{{ steps.annuaire.output.rows }}"}</code> (50 000 lignes au plus). Les cellules de texte
              qui commencent par <code>=</code> ne sont jamais exécutées comme des formules.
            </HelpTip>
            <label htmlFor="fi-rows">Lignes du tableau</label>
            <input
              id="fi-rows"
              list="expr-paths"
              value={node.rows ?? ""}
              placeholder="{{ steps.lire.output.rows }}"
              onChange={(e) => onUpdate({ rows: e.target.value })}
            />
          </div>
          <div className="field">
            <HelpTip>Les colonnes à écrire, dans l'ordre, séparées par des virgules. Vide : toutes les colonnes trouvées.</HelpTip>
            <label htmlFor="fi-cols">Colonnes (facultatif)</label>
            <ListInput id="fi-cols" items={node.columns} separator={/,/} placeholder="nom, service, date" onChange={(columns) => onUpdate({ columns })} />
          </div>
          {format === "xlsx" && (
            <div className="field">
              <label htmlFor="fi-sheet">Nom de la feuille (facultatif)</label>
              <input id="fi-sheet" value={node.sheet ?? ""} placeholder="Feuille1" maxLength={31} onChange={(e) => onUpdate({ sheet: e.target.value })} />
            </div>
          )}
        </>
      )}
      {format === "zip" && (
        <div className="field">
          <HelpTip>
            Un fichier par ligne, donné par sa référence : la sortie <code>file</code> d'une étape précédente, ex.{" "}
            <code>{"{{ steps.rapport.output.file }}"}</code>. Une expression qui donne une liste de fichiers convient aussi. 100 fichiers et 10 Mo au plus.
          </HelpTip>
          <label htmlFor="fi-src">Fichiers à archiver</label>
          <ListInput
            id="fi-src"
            items={node.sources}
            separator={/\n/}
            multiline
            rows={4}
            placeholder={"{{ steps.rapport.output.file }}\n{{ steps.tableau.output.file }}"}
            onChange={(sources) => onUpdate({ sources: sources ?? [] })}
          />
        </div>
      )}
      <p className="hint">
        Le fichier est créé quand l'étape s'exécute et gardé avec l'exécution (10 Mo au plus). Il se <strong>télécharge depuis la page de l'exécution</strong>{" "}
        (onglet
        <em> Fichiers</em>) et se passe aux autres étapes par <code>steps.{node.id}.output.file</code>.
      </p>
    </>
  );
}
