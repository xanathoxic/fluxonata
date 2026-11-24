import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ClipboardEvent, type DragEvent, type KeyboardEvent, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api";
import { Overlay } from "../components/Overlay";
import { HelpTip } from "../components/HelpTip";
import { useApp } from "../context";
import { errorText } from "../lib";
import { applyFormat, type Edit, type FormatKind, imageMarkdown, insertText, variableMarkdown } from "./markdownEdit";
import type { GraphNode } from "./types";

export interface ModelImage {
  id: string;
  name: string;
  content_type: string;
  width: number;
  height: number;
  bytes: number | null;
  url: string;
  reference: string;
}
interface Preview {
  html: string;
  missing_images: string[];
}
export interface ModelVariable {
  path: string;
  label: string;
}

const TOOLS: { kind: FormatKind; label: string; short: string; key?: string }[] = [
  { kind: "h1", label: "Titre 1", short: "Titre 1" },
  { kind: "h2", label: "Titre 2", short: "Titre 2" },
  { kind: "h3", label: "Titre 3", short: "Titre 3" },
  { kind: "bold", label: "Gras (Ctrl+B)", short: "G", key: "b" },
  { kind: "italic", label: "Italique (Ctrl+I)", short: "I", key: "i" },
  { kind: "strike", label: "Barré", short: "S" },
  { kind: "code", label: "Code", short: "<>" },
  { kind: "link", label: "Lien (Ctrl+K)", short: "Lien", key: "k" },
  { kind: "ul", label: "Liste à puces", short: "Liste à puces" },
  { kind: "ol", label: "Liste numérotée", short: "Liste numérotée" },
  { kind: "quote", label: "Citation", short: "Citation" },
  { kind: "table", label: "Tableau", short: "Tableau" },
  { kind: "rule", label: "Séparateur", short: "—" },
];

const sizeLabel = (n: number | null) => (n === null ? "" : n < 1024 ? `${n} o` : `${Math.round(n / 1024)} Ko`);

/** L'aperçu : du HTML produit par le moteur du serveur (ADR 0036) ; texte échappé, liens http/https/mailto, images de l'espace seulement. */
function PreviewHtml({ html, labelledBy }: { html: string; labelledBy: string }) {
  // biome-ignore lint/a11y/useSemanticElements: rôle explicite conservé ; l'élément natif changerait la mise en forme
  // biome-ignore lint/a11y/noNoninteractiveTabindex: zone défilante atteignable au clavier pour être lue
  // biome-ignore lint/security/noDangerouslySetInnerHtml: HTML du moteur du serveur, jamais du HTML saisi (échappé côté serveur)
  return <div className="md-preview" role="region" aria-labelledby={labelledBy} tabIndex={0} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** L'éditeur de modèle de document : écriture en Markdown à gauche, aperçu HTML à droite (rendu par le serveur, le même moteur que le fichier et l'e-mail),
 *  barre de mise en forme, images de l'espace (envoi, collage, glisser-déposer) et insertion de variables. Travaille sur un brouillon : rien n'est
 *  appliqué à l'étape avant « Appliquer ». */
export function ModelEditorDialog({
  nodeName,
  initial,
  variables,
  readOnly,
  onApply,
  onClose,
}: {
  nodeName: string;
  initial: { markdown: string; title: string };
  variables: ModelVariable[];
  readOnly: boolean;
  onApply: (v: { markdown: string; title: string }) => void;
  onClose: () => void;
}) {
  const { workspace } = useApp();
  const slug = workspace.slug;
  const id = useId();
  const qc = useQueryClient();
  const area = useRef<HTMLTextAreaElement>(null);
  const pending = useRef<Edit | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [markdown, setMarkdown] = useState(initial.markdown);
  const [title, setTitle] = useState(initial.title);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const dirty = markdown !== initial.markdown || title !== initial.title;

  const images = useQuery({ queryKey: ["model-images", slug], queryFn: () => api<ModelImage[]>(`/workspaces/${slug}/model-images/`) });
  const upload = useMutation<ModelImage, unknown, File>({
    mutationFn: (file) => {
      const form = new FormData();
      form.append("file", file);
      return api<ModelImage>(`/workspaces/${slug}/model-images/`, { method: "POST", form });
    },
    onSuccess: (img) => {
      setNotice(null);
      void qc.invalidateQueries({ queryKey: ["model-images", slug] });
      insert(imageMarkdown(img.id, img.name.replace(/\.[^.]+$/, "")));
    },
    onError: (e) => setNotice(errorText(e)),
  });
  const remove = useMutation<unknown, unknown, string>({
    mutationFn: (imageId) => api(`/workspaces/${slug}/model-images/${imageId}/`, { method: "DELETE" }),
    onSuccess: () => {
      setNotice(null);
      void qc.invalidateQueries({ queryKey: ["model-images", slug] });
    },
    onError: (e) => setNotice(errorText(e)),
  });

  // Aperçu : le serveur rend le Markdown (au plus une requête toutes les 350 ms de frappe) ; une réponse périmée est ignorée.
  useEffect(() => {
    let current = true;
    const t = setTimeout(() => {
      api<Preview>(`/workspaces/${slug}/model-preview/`, { method: "POST", json: { markdown } })
        .then((p) => {
          if (current) {
            setPreview(p);
            setPreviewError(null);
          }
        })
        .catch((e) => current && setPreviewError(errorText(e)));
    }, 350);
    return () => {
      current = false;
      clearTimeout(t);
    };
  }, [markdown, slug]);

  // La sélection est rétablie après le rendu qui suit une mise en forme.
  useLayoutEffect(() => {
    const p = pending.current;
    const el = area.current;
    if (p && el) {
      el.focus();
      el.setSelectionRange(p.start, p.end);
      pending.current = null;
    }
  });

  const commit = (e: Edit) => {
    pending.current = e;
    setMarkdown(e.value);
  };
  const selection = (): [number, number] => [area.current?.selectionStart ?? markdown.length, area.current?.selectionEnd ?? markdown.length];
  const format = (kind: FormatKind) => {
    const [s, e] = selection();
    commit(applyFormat(markdown, s, e, kind));
  };
  function insert(text: string) {
    const [s, e] = selection();
    commit(insertText(markdown, s, e, text));
  }
  const sendImage = (files: FileList | File[] | undefined | null) => {
    const f = Array.from(files ?? []).find((x) => x.type.startsWith("image/"));
    if (f && !readOnly) upload.mutate(f);
    return Boolean(f);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const tool = TOOLS.find((t) => t.key === e.key.toLowerCase());
    if (tool) {
      e.preventDefault();
      format(tool.kind);
    }
  };
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    if (sendImage(e.clipboardData.files)) e.preventDefault();
  };
  const onDrop = (e: DragEvent<HTMLTextAreaElement>) => {
    if (sendImage(e.dataTransfer.files)) e.preventDefault();
  };

  return (
    <Overlay
      variant="modal"
      wide
      className="overlay-preview"
      title={`Modèle de document : ${nodeName}`}
      description="Écrivez en Markdown, l'aperçu se met à jour à droite. Ce modèle sert ensuite aux fichiers (Word, PDF, HTML, PowerPoint) et aux e-mails du workflow."
      onClose={onClose}
      footer={
        <>
          <span className="hint" role="status">
            {dirty ? "Modifications non appliquées" : "Aucune modification"} · {markdown.length.toLocaleString("fr-FR")} caractères
          </span>
          <button type="button" onClick={onClose}>
            {readOnly ? "Fermer" : "Annuler"}
          </button>
          {!readOnly && (
            <button type="button" className="primary" disabled={!dirty} onClick={() => onApply({ markdown, title })}>
              Appliquer
            </button>
          )}
        </>
      }
    >
      <div className="md-editor">
        <div className="field md-title">
          <label htmlFor={`${id}-title`}>Titre du document (facultatif)</label>
          <input
            id={`${id}-title`}
            value={title}
            maxLength={200}
            readOnly={readOnly}
            placeholder="Rapport de {{ inputs.mois }}"
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        {!readOnly && (
          <div className="md-toolbar" role="toolbar" aria-label="Mise en forme">
            {TOOLS.map((t) => (
              <button key={t.kind} type="button" className="btn-s" aria-label={t.label} title={t.label} onClick={() => format(t.kind)}>
                {t.short}
              </button>
            ))}
            <button type="button" className="btn-s" onClick={() => fileInput.current?.click()} disabled={upload.isPending} aria-label="Ajouter une image">
              Image…
            </button>
            <input
              ref={fileInput}
              type="file"
              accept="image/png,image/jpeg,image/gif"
              hidden
              aria-hidden="true"
              tabIndex={-1}
              onChange={(e) => {
                sendImage(e.target.files);
                e.target.value = "";
              }}
            />
            <label className="sr-only" htmlFor={`${id}-var`}>
              Insérer une variable
            </label>
            <select id={`${id}-var`} value="" onChange={(e) => e.target.value && insert(variableMarkdown(e.target.value))}>
              <option value="">Insérer une variable…</option>
              {variables.map((v) => (
                <option key={v.path} value={v.path}>
                  {v.label}
                </option>
              ))}
            </select>
            <HelpTip>
              Collez ou glissez une image dans le texte pour l'ajouter (PNG, JPEG ou GIF, 1 Mo). Une variable s'écrit <code>{"{{ inputs.nom }}"}</code> : sa
              valeur est du texte, jamais de la mise en forme. Seules les images ajoutées ici sont affichées : jamais d'adresse distante.
            </HelpTip>
          </div>
        )}
        {notice && (
          <p className="field-error" role="alert">
            {notice}
          </p>
        )}
        <div className="md-panes">
          <div className="md-pane">
            <label htmlFor={`${id}-src`}>Texte (Markdown)</label>
            <textarea
              id={`${id}-src`}
              ref={area}
              value={markdown}
              readOnly={readOnly}
              spellCheck
              onChange={(e) => setMarkdown(e.target.value)}
              onKeyDown={onKeyDown}
              onPaste={onPaste}
              onDrop={onDrop}
              placeholder={"# Titre\n\nBonjour **{{ inputs.nom }}**,\n\n- un point\n- un autre"}
            />
          </div>
          <div className="md-pane">
            <h3 id={`${id}-prev`}>Aperçu</h3>
            {previewError ? (
              <p className="field-error" role="alert">
                {previewError}
              </p>
            ) : (
              <>
                {(preview?.missing_images.length ?? 0) > 0 && (
                  <p className="field-error">{preview?.missing_images.length} image(s) introuvable(s) dans cet espace : réinsérez-les.</p>
                )}
                <PreviewHtml labelledBy={`${id}-prev`} html={preview?.html ?? ""} />
              </>
            )}
          </div>
        </div>
        <details className="md-images">
          <summary>Images de l'espace ({images.data?.length ?? 0})</summary>
          {(images.data?.length ?? 0) === 0 ? (
            <p className="hint">Aucune image : utilisez « Image… » ou collez-en une dans le texte.</p>
          ) : (
            <ul>
              {images.data?.map((img) => (
                <li key={img.id}>
                  <img src={img.url} alt="" width={48} height={48} />
                  <span>
                    {img.name}{" "}
                    <span className="hint">
                      ({img.width}×{img.height}, {sizeLabel(img.bytes)})
                    </span>
                  </span>
                  {!readOnly && (
                    <>
                      <button type="button" className="btn-s" onClick={() => insert(imageMarkdown(img.id, img.name.replace(/\.[^.]+$/, "")))}>
                        Insérer
                      </button>
                      <button type="button" className="btn-s danger" aria-label={`Supprimer ${img.name}`} onClick={() => remove.mutate(img.id)}>
                        Supprimer
                      </button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </details>
      </div>
    </Overlay>
  );
}

/** Ce que montre l'onglet « Modèle » de l'étape : un résumé et le bouton qui ouvre l'éditeur au centre de l'écran. */
export function ModelConfig({
  node,
  variables,
  editable,
  onUpdate,
}: {
  node: GraphNode;
  variables: ModelVariable[];
  editable: boolean;
  onUpdate: (patch: Partial<GraphNode>) => void;
}) {
  const [open, setOpen] = useState(false);
  const text = typeof node.markdown === "string" ? node.markdown : "";
  const title = node.title ?? "";
  const first = text.split("\n").find((l) => l.trim()) ?? "";
  const images = (text.match(/\]\(image:/g) ?? []).length;
  return (
    <>
      <p className="hint">
        Un modèle de document est un texte Markdown (titres, listes, tableaux, images, variables) que l'on rédige une fois et que d'autres étapes réutilisent :{" "}
        <em>Créer un fichier</em> (Word, PDF, HTML, PowerPoint) et <em>Envoyer un e-mail</em>, par <code>steps.{node.id}.output</code>.
      </p>
      <div className="md-summary">
        <p>
          {text.trim() ? (
            <>
              <strong>{title || first.replace(/^#+\s*/, "").slice(0, 80) || "Modèle sans titre"}</strong>
              <br />
              <span className="hint">
                {text.length.toLocaleString("fr-FR")} caractères · {text.split("\n").length} lignes · {images} image(s)
              </span>
            </>
          ) : (
            <span className="hint">Le modèle est vide.</span>
          )}
        </p>
        <button type="button" className="primary" onClick={() => setOpen(true)}>
          {editable ? "Ouvrir l'éditeur" : "Voir le modèle"}
        </button>
      </div>
      {open && (
        <ModelEditorDialog
          nodeName={node.name}
          initial={{ markdown: text, title }}
          variables={variables}
          readOnly={!editable}
          onClose={() => setOpen(false)}
          onApply={(v) => {
            onUpdate({ markdown: v.markdown, title: v.title || undefined });
            setOpen(false);
          }}
        />
      )}
    </>
  );
}
