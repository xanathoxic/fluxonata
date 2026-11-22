import { useEffect, useRef, useState } from "react";
import { api, type ApiError } from "../../api";
import { useApp } from "../../context";
import { FormIcon } from "../FormIcon";
import { CssCodeEditor, type CssEditorHandle, type CssField, type CssProblem } from "./CssCodeEditor";
import { CLASSES, SNIPPETS } from "./cssVocabulary";

const MIN_H = 140;
const STEP = 70;

/** Petit menu déroulant de la barre d'outils : s'ouvre sous le bouton, se ferme à Échap ou au clic ailleurs. */
function Menu({ label, icon, children }: { label: string; icon: "plus" | "design"; children: (close: () => void) => React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        root.current?.querySelector<HTMLElement>("button")?.focus();
      }
    };
    document.addEventListener("pointerdown", down);
    document.addEventListener("keydown", esc);
    root.current?.querySelector<HTMLElement>(".cs-tbmenu button")?.focus();
    return () => {
      document.removeEventListener("pointerdown", down);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <span className="cs-tbwrap" ref={root}>
      <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <FormIcon name={icon} size={14} /> {label}
      </button>
      {open && (
        <div className="cs-tbmenu" role="menu" aria-label={label}>
          {children(() => setOpen(false))}
        </div>
      )}
    </span>
  );
}

/** Le CSS du formulaire dans un vrai éditeur : coloration, suggestions de ce que le serveur accepte, erreurs à la ligne, pastilles de
 * couleur, recherche, exemples et cibles à insérer. Ce qu'on tape est vérifié par le serveur (même grammaire que le concepteur) :
 * tant que le texte est refusé, le dernier CSS valide reste appliqué et le texte tapé reste ici.
 * La hauteur se règle à la poignée du rebord (ou avec les flèches haut et bas quand elle a le focus) ; réduite au maximum,
 * il ne reste que le titre. */
export function CssLive({ text, edit, onCss, fields }: { text: string; edit: boolean; onCss: (css: string | undefined) => void; fields: CssField[] }) {
  const { workspace } = useApp();
  const [draft, setDraft] = useState(text);
  const [problems, setProblems] = useState<CssProblem[]>([]);
  const [fail, setFail] = useState("");
  const [busy, setBusy] = useState(false);
  const [canonical, setCanonical] = useState("");
  const [pos, setPos] = useState({ line: 1, col: 1 });
  const [h, setH] = useState(260);
  const [folded, setFolded] = useState(false);
  const ed = useRef<CssEditorHandle>(null);
  const box = useRef<HTMLDivElement>(null);
  const seq = useRef(0);
  // un réglage fait ailleurs (concepteur, annuler/rétablir) met le texte à jour, sauf pendant la saisie ici
  useEffect(() => {
    if (!ed.current?.hasFocus()) {
      setDraft(text);
      setProblems([]);
      setFail("");
    }
  }, [text]);

  // poignée : on tire vers le haut pour agrandir, vers le bas pour réduire (souris, stylet ou doigt) ; flèches au clavier
  const drag = useRef<{ y: number; h: number } | null>(null);
  const max = () => Math.round(window.innerHeight * 0.75);
  const current = () => (folded ? 0 : (box.current?.offsetHeight ?? h));
  const size = (to: number) => {
    if (to < MIN_H - 20) {
      setFolded(true);
      return;
    }
    setFolded(false);
    setH(Math.max(MIN_H, Math.min(max(), to)));
  };
  const grab = (e: React.PointerEvent<HTMLDivElement>) => {
    drag.current = { y: e.clientY, h: current() };
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  };
  const move = (e: React.PointerEvent) => {
    if (drag.current) size(drag.current.h + drag.current.y - e.clientY);
  };
  const drop = () => {
    drag.current = null;
  };
  const handleKey = (e: React.KeyboardEvent) => {
    const d = e.key === "ArrowUp" ? STEP / 2 : e.key === "ArrowDown" ? -STEP / 2 : 0;
    if (d) {
      e.preventDefault();
      size(current() + d);
    }
  };

  const check = (value: string) => {
    setDraft(value);
    const mine = ++seq.current;
    setBusy(true);
    window.setTimeout(() => {
      if (mine !== seq.current) return;
      api<{ css: { css: string; problems: CssProblem[] } }>(`/workspaces/${workspace.slug}/form-library/check/`, { json: { css: value } })
        .then((r) => {
          if (mine !== seq.current) return;
          setBusy(false);
          setFail("");
          setProblems(r.css.problems);
          if (r.css.problems.length > 0) return;
          setCanonical(r.css.css);
          onCss(r.css.css || undefined);
        })
        .catch((e: ApiError) => {
          if (mine !== seq.current) return;
          setBusy(false);
          setFail(
            e?.status === 401 || e?.status === 403
              ? "Votre session a expiré : reconnectez-vous (le texte reste dans cette page)."
              : "Vérification impossible pour le moment : le texte reste dans cette page.",
          );
        });
    }, 450);
  };
  const lines = draft ? draft.split("\n").length : 0;
  const bad = problems.length > 0 || fail !== "";
  const tidy = !bad && !busy && canonical !== "" && canonical !== draft;
  const state =
    problems.length > 0
      ? `${problems.length} problème(s) : le dernier CSS valide reste appliqué`
      : fail
        ? fail
        : busy
          ? "Vérification…"
          : draft.trim()
            ? "CSS valide et appliqué"
            : "";
  return (
    <details className="cs-gen" open>
      {/* biome-ignore lint/a11y/noStaticElementInteractions: <summary> est interactif en natif (clavier compris) ; on remplace seulement son effet */}
      <summary
        onClick={(e) => {
          // un clic sur le titre replie ou déplie le bloc ; il reste ouvert pour que la poignée du rebord reste visible
          e.preventDefault();
          setFolded(!folded);
        }}
      >
        <FormIcon name="braces" size={16} /> CSS généré en direct <small>{lines} ligne(s)</small>
        {folded && bad && <small className="cs-genstate bad"> · CSS refusé</small>}
      </summary>
      {/* biome-ignore lint/a11y/useSemanticElements: séparateur réglable (focalisable, déplaçable) : un <hr> ne l’est pas */}
      <div
        className="cs-grip"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Hauteur de la zone de CSS : tirer ou utiliser les flèches haut et bas"
        aria-valuenow={Math.round(folded ? 0 : h)}
        aria-valuemin={0}
        aria-valuemax={max()}
        tabIndex={0}
        title="Tirez pour agrandir ou réduire"
        onPointerDown={grab}
        onPointerMove={move}
        onPointerUp={drop}
        onPointerCancel={drop}
        onKeyDown={handleKey}
      >
        <span />
      </div>
      {!folded && (
        <div className="cs-live" ref={box} style={{ height: h }}>
          <div className="cs-genbar">
            <button type="button" onClick={() => ed.current?.undo()} disabled={!edit} aria-label="Annuler la saisie" title="Annuler (Ctrl+Z)">
              <FormIcon name="undo" size={14} />
            </button>
            <button type="button" onClick={() => ed.current?.redo()} disabled={!edit} aria-label="Rétablir la saisie" title="Rétablir (Ctrl+Y)">
              <FormIcon name="redo" size={14} />
            </button>
            {edit && (
              <Menu label="Exemples" icon="plus">
                {(close) =>
                  SNIPPETS.map((s) => (
                    <button
                      key={s.label}
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        ed.current?.insert(`${draft.trim() ? "\n" : ""}${s.css}\n`);
                        close();
                      }}
                    >
                      {s.label}
                    </button>
                  ))
                }
              </Menu>
            )}
            {edit && (
              <Menu label="Cibles" icon="design">
                {(close) => (
                  <>
                    <p className="cs-mh">Éléments du formulaire</p>
                    {CLASSES.map(([sel, what]) => (
                      <button
                        key={sel}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          ed.current?.addRule(sel);
                          close();
                        }}
                      >
                        <code>{sel}</code> <small>{what}</small>
                      </button>
                    ))}
                    {fields.length > 0 && <p className="cs-mh">Champs</p>}
                    {fields.map((f) => (
                      <button
                        key={f.key}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          ed.current?.addRule(`[data-key="${f.key}"]`);
                          close();
                        }}
                      >
                        <code>{`[data-key="${f.key}"]`}</code> <small>{f.label || f.key}</small>
                      </button>
                    ))}
                  </>
                )}
              </Menu>
            )}
            {edit && (
              <button
                type="button"
                onClick={() => setDraft(canonical)}
                disabled={!tidy}
                title="Écrire le CSS sous sa forme normalisée (celle qui est enregistrée)"
              >
                <FormIcon name="wand" size={14} /> Mettre en forme
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard?.writeText(draft);
              }}
              disabled={!draft}
            >
              <FormIcon name="copy" size={14} /> Copier
            </button>
            <span className={`cs-genstate${bad ? " bad" : ""}`} role="status">
              {state}
            </span>
            <span className="cs-pos" aria-hidden="true">
              Ln {pos.line}, col {pos.col}
            </span>
          </div>
          <CssCodeEditor
            ref={ed}
            value={draft}
            onChange={check}
            readOnly={!edit}
            ariaLabel="CSS généré"
            fields={fields}
            problems={problems}
            onCursor={setPos}
          />
        </div>
      )}
      {!folded && problems.length > 0 && (
        <ul role="alert" className="field-error cs-genproblem">
          {problems.slice(0, 4).map((p) => (
            <li key={`${p.line}:${p.message}`}>
              <strong>Ligne {p.line}</strong> : {p.message}
            </li>
          ))}
          {problems.length > 4 && <li>… et {problems.length - 4} autre(s)</li>}
        </ul>
      )}
    </details>
  );
}
