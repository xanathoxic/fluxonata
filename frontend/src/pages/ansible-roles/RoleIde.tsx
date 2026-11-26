import { useState } from "react";
import { LazyCodeEditor as CodeEditor } from "../../components/LazyCodeEditor";
import { FormIcon } from "../../forms/FormIcon";
import { DIRS, grouped } from "./roles";

/** Le langage d'un fichier de rôle, pour la coloration et la barre d'état : le YAML des tâches et des variables, le reste en texte (modèles Jinja…). */
export function languageOf(path: string): { editor: "yaml" | "text"; label: string; ext: string } {
  const ext = path.includes(".") ? (path.split(".").pop() ?? "") : "";
  if (ext === "yml" || ext === "yaml") return { editor: "yaml", label: "YAML", ext: "yml" };
  if (ext === "j2") return { editor: "text", label: "Jinja2", ext: "j2" };
  return { editor: "text", label: "Texte brut", ext: ext || "txt" };
}

interface Props {
  roleName: string;
  version: string | null;
  files: Record<string, string>;
  original: Record<string, string>;
  tabs: string[];
  selected: string | null;
  canEdit: boolean;
  old: boolean;
  onOpen: (path: string) => void;
  onClose: (path: string) => void;
  onChange: (path: string, text: string) => void;
  onDelete: (path: string) => void;
  onAdd: () => void;
  onAddFolder: () => void;
  /** Recharge les fichiers de la version et abandonne les modifications non publiées. */
  onRefresh: () => void;
}

const Svg = ({ children }: { children: React.ReactNode }) => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    {children}
  </svg>
);
const NewFileIcon = () => (
  <Svg>
    <path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z" />
    <path d="M13 3v6h6M12 12v6M9 15h6" />
  </Svg>
);
const NewFolderIcon = () => (
  <Svg>
    <path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    <path d="M12 10v6M9 13h6" />
  </Svg>
);
const RefreshIcon = () => (
  <Svg>
    <path d="M20 12a8 8 0 1 1-2.5-5.8M20 4v5h-5" />
  </Svg>
);
const CollapseIcon = () => (
  <Svg>
    <rect x="4" y="4" width="16" height="16" rx="2" />
    <path d="M8 12h8" />
  </Svg>
);

function FileGlyph({ path }: { path: string }) {
  const ext = languageOf(path).ext.slice(0, 3);
  return <span className={`vsc-ico vsc-ico-${ext}`} data-ext={ext} aria-hidden="true" />; // l'extension est dessinée par le style : elle ne fait pas partie du texte du bouton
}

/** L'éditeur des fichiers d'un rôle, à la manière d'un éditeur de code : explorateur des dossiers à gauche, onglets des fichiers ouverts, éditeur coloré, barre d'état. */
export function RoleIde({
  roleName,
  version,
  files,
  original,
  tabs,
  selected,
  canEdit,
  old,
  onOpen,
  onClose,
  onChange,
  onDelete,
  onAdd,
  onAddFolder,
  onRefresh,
}: Props) {
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [cursor, setCursor] = useState({ line: 1, col: 1, lines: 1 });
  const lang = selected ? languageOf(selected) : null;
  const help = selected ? DIRS.find((d) => d.name === selected.split("/")[0])?.help : undefined;
  const modified = (p: string) => !(p in original) || original[p] !== files[p];
  const changed = Object.keys(files).filter(modified).length + Object.keys(original).filter((p) => !(p in files)).length;
  const toggle = (dir: string) =>
    setFolded((cur) => {
      const next = new Set(cur);
      if (!next.delete(dir)) next.add(dir);
      return next;
    });
  return (
    <div className="vsc">
      <aside className="vsc-side" aria-label="Explorateur">
        <div className="vsc-side-head">
          <span>Explorateur</span>
          <span className="vsc-tools">
            {canEdit && (
              <>
                <button type="button" className="vsc-btn" onClick={onAdd} title="Nouveau fichier" aria-label="Ajouter un fichier">
                  <NewFileIcon />
                </button>
                <button type="button" className="vsc-btn" onClick={onAddFolder} title="Nouveau dossier" aria-label="Ajouter un dossier">
                  <NewFolderIcon />
                </button>
              </>
            )}
            <button
              type="button"
              className="vsc-btn"
              onClick={onRefresh}
              title={canEdit ? "Actualiser : recharge la version et abandonne les modifications non publiées" : "Actualiser"}
              aria-label="Actualiser les fichiers"
            >
              <RefreshIcon />
            </button>
            <button
              type="button"
              className="vsc-btn"
              onClick={() => setFolded(new Set(grouped(files).map((g) => g.dir)))}
              title="Tout réduire"
              aria-label="Réduire tous les dossiers"
            >
              <CollapseIcon />
            </button>
          </span>
        </div>
        <nav aria-label="Fichiers du rôle" className="vsc-tree">
          <div className="vsc-root">{roleName}</div>
          {grouped(files).map((g) => {
            const open = !folded.has(g.dir);
            return (
              <div key={g.dir} className="vsc-dir">
                <button
                  type="button"
                  className="vsc-dirname"
                  aria-expanded={open}
                  onClick={() => toggle(g.dir)}
                  title={DIRS.find((d) => d.name === g.dir)?.help}
                >
                  <span className={`vsc-chev${open ? " open" : ""}`} aria-hidden="true">
                    ▸
                  </span>{" "}
                  {g.dir}/
                </button>
                {open && (
                  <ul>
                    {g.paths.map((p) => (
                      <li key={p} className={p === selected ? "active" : undefined}>
                        <button type="button" className="vsc-file" aria-label={p} aria-current={p === selected ? "true" : undefined} onClick={() => onOpen(p)}>
                          <FileGlyph path={p} />
                          <span className="vsc-fname">{p.slice(g.dir.length + 1)}</span>
                          {modified(p) && <span className="vsc-dot" role="img" aria-label="modifié" title="Modifié, non publié" />}
                        </button>
                        {canEdit && (
                          <button
                            type="button"
                            className="vsc-btn vsc-del"
                            aria-label={`Supprimer le fichier ${p}`}
                            title="Supprimer"
                            onClick={() => onDelete(p)}
                          >
                            <FormIcon name="trash" />
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </nav>
      </aside>
      <section className="vsc-main" aria-label="Éditeur">
        <nav aria-label="Fichiers ouverts" className="vsc-tabs">
          {tabs
            .filter((p) => p in files)
            .map((p) => (
              <span key={p} className={`vsc-tab${p === selected ? " active" : ""}`}>
                <button
                  type="button"
                  aria-current={p === selected ? "true" : undefined}
                  aria-label={`Onglet ${p}`}
                  className="vsc-tabname"
                  onClick={() => onOpen(p)}
                >
                  <FileGlyph path={p} /> {p}
                  {modified(p) && <span className="vsc-dot" aria-hidden="true" />}
                </button>
                <button type="button" className="vsc-btn vsc-x" aria-label={`Fermer ${p}`} title="Fermer" onClick={() => onClose(p)}>
                  ×
                </button>
              </span>
            ))}
        </nav>
        {selected ? (
          <>
            <nav className="vsc-crumb" aria-label="Fil d'Ariane">
              {[roleName, ...selected.split("/")].join("  ›  ")}
            </nav>
            <div className="vsc-editor">
              <CodeEditor
                key={selected}
                fill
                value={files[selected] ?? ""}
                onChange={(v) => onChange(selected, v)}
                language={lang?.editor ?? "text"}
                readOnly={!canEdit}
                onCursor={setCursor}
                ariaLabel={`Contenu de ${selected}`}
                minHeight="200px"
              />
            </div>
          </>
        ) : (
          <div className="vsc-empty">
            <p>Aucun fichier ouvert.</p>
            <p className="hint">Choisissez un fichier dans l'explorateur.</p>
          </div>
        )}
      </section>
      <section className="vsc-status" aria-label="Barre d'état">
        <span>
          {version ? `v${version}` : "—"}
          {old ? " · lecture seule" : ""}
        </span>
        {changed > 0 && <span>{changed} fichier(s) modifié(s)</span>}
        {help && (
          <span className="vsc-help" title={help}>
            {help}
          </span>
        )}
        <span className="vsc-spacer" />
        {selected && (
          <span aria-live="off">
            Ln {cursor.line}, Col {cursor.col}
          </span>
        )}
        {lang && <span>{lang.label}</span>}
        <span>UTF-8</span>
      </section>
    </div>
  );
}
