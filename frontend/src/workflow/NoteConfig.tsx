import { useId } from "react";
import { HelpTip } from "../components/HelpTip";
import { NOTE_COLOR, NOTE_MAX_SIDE, NOTE_MIN_H, NOTE_MIN_OPACITY, NOTE_MIN_W, NOTE_SWATCHES, noteColor, noteOpacity, noteSide, noteStyle } from "./noteStyle";
import type { Note } from "./types";

/** Propriétés d'une note du schéma, en trois parties : le texte, l'apparence (couleur, opacité du fond, aperçu) et la taille. La note se redimensionne aussi à la
 *  souris sur le schéma ; les champs de taille en sont l'équivalent au clavier. Une note est toujours derrière les étapes et les lignes. */
export function NoteConfig({
  note,
  editable,
  onChange,
  onDelete,
  onClose,
}: {
  note: Note;
  editable: boolean;
  onChange: (patch: Partial<Note>) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const id = useId();
  const color = noteColor(note.color);
  const opacity = noteOpacity(note.opacity);
  const w = noteSide(note.ui.w, NOTE_MIN_W);
  const h = noteSide(note.ui.h, NOTE_MIN_H);
  const preview = noteStyle(color, opacity);
  const custom = !NOTE_SWATCHES.some((sw) => sw.value === color);
  const setSize = (next: { w?: number; h?: number }) => {
    const { w: _w, h: _h, ...rest } = note.ui;
    void _w;
    void _h;
    onChange({ ui: { ...rest, ...(next.w ? { w: next.w } : {}), ...(next.h ? { h: next.h } : {}) } });
  };
  const size = (key: "w" | "h", min: number) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value === "" ? undefined : noteSide(Number(e.target.value), min);
    setSize({ w, h, [key]: v });
  };
  const pick = (value: string) => onChange({ color: value === NOTE_COLOR ? undefined : value });
  return (
    <section aria-label="Note" className="node-panel note-panel">
      <div className="note-head">
        <span className="note-dot" style={{ background: preview.background, borderColor: preview.border }} aria-hidden="true" />
        <h2 className="np-title">Note</h2>
      </div>
      <fieldset disabled={!editable} className="bare note-body">
        <section className="np-section" aria-labelledby={`${id}-t`}>
          <h3 id={`${id}-t`}>Texte</h3>
          <div className="field">
            <HelpTip>Les notes sont des annotations visuelles : elles ne sont jamais exécutées, et restent toujours derrière les étapes et les lignes.</HelpTip>
            <label htmlFor={`${id}-text`} className="sr-only">
              Texte de la note
            </label>
            <textarea
              id={`${id}-text`}
              rows={4}
              value={note.text}
              placeholder="Écrivez votre annotation…"
              onChange={(e) => onChange({ text: e.target.value })}
            />
            <p className="hint note-count">
              {note.text.length.toLocaleString("fr-FR")} caractère{note.text.length > 1 ? "s" : ""}
            </p>
          </div>
        </section>

        <section className="np-section" aria-labelledby={`${id}-a`}>
          <h3 id={`${id}-a`}>Apparence</h3>
          <div className="note-colors" role="radiogroup" aria-label="Couleur de la note">
            {NOTE_SWATCHES.map((sw) => (
              // biome-ignore lint/a11y/useSemanticElements: pastille de couleur à choix unique, le nom de la couleur est son libellé
              <button
                key={sw.value}
                type="button"
                role="radio"
                aria-checked={color === sw.value}
                aria-label={sw.name}
                title={sw.name}
                className={`note-swatch${color === sw.value ? " on" : ""}`}
                style={{ background: sw.value }}
                onClick={() => pick(sw.value)}
              />
            ))}
            <label className={`note-swatch note-custom${custom ? " on" : ""}`} title="Autre couleur" style={custom ? { background: color } : undefined}>
              <input type="color" aria-label="Autre couleur" value={color} onChange={(e) => pick(e.target.value)} />
              <span aria-hidden="true">{custom ? "" : "+"}</span>
            </label>
          </div>

          <div className="field note-opacity">
            <label htmlFor={`${id}-opacity`}>
              Opacité du fond : <strong>{Math.round(opacity * 100)} %</strong>
            </label>
            <div className="note-track" style={{ ["--note-color" as string]: color }}>
              <input
                id={`${id}-opacity`}
                type="range"
                min={Math.round(NOTE_MIN_OPACITY * 100)}
                max={100}
                step={5}
                value={Math.round(opacity * 100)}
                onChange={(e) => onChange({ opacity: Number(e.target.value) >= 100 ? undefined : Number(e.target.value) / 100 })}
              />
            </div>
          </div>

          <div className="note-stage">
            <p className="note-preview" style={{ background: preview.background, borderColor: preview.border, color: preview.text }}>
              Aperçu du fond et du texte
            </p>
          </div>
        </section>

        <section className="np-section" aria-labelledby={`${id}-s`}>
          <h3 id={`${id}-s`}>
            Taille
            <HelpTip>
              Glissez les poignées de la note sur le schéma pour la redimensionner, ou saisissez ici sa taille en pixels du schéma (de {NOTE_MIN_W} ×{" "}
              {NOTE_MIN_H} à {NOTE_MAX_SIDE}). Vide : taille automatique.
            </HelpTip>
          </h3>
          <div className="note-size">
            <div className="field">
              <label htmlFor={`${id}-w`}>Largeur</label>
              <span className="note-unit">
                <input
                  id={`${id}-w`}
                  type="number"
                  inputMode="numeric"
                  min={NOTE_MIN_W}
                  max={NOTE_MAX_SIDE}
                  step={8}
                  value={w ?? ""}
                  placeholder="auto"
                  onChange={size("w", NOTE_MIN_W)}
                />
                <span aria-hidden="true">px</span>
              </span>
            </div>
            <div className="field">
              <label htmlFor={`${id}-h`}>Hauteur</label>
              <span className="note-unit">
                <input
                  id={`${id}-h`}
                  type="number"
                  inputMode="numeric"
                  min={NOTE_MIN_H}
                  max={NOTE_MAX_SIDE}
                  step={8}
                  value={h ?? ""}
                  placeholder="auto"
                  onChange={size("h", NOTE_MIN_H)}
                />
                <span aria-hidden="true">px</span>
              </span>
            </div>
            <button type="button" className="btn-s btn-ghost" disabled={!w && !h} onClick={() => setSize({})}>
              Taille automatique
            </button>
          </div>
        </section>
      </fieldset>
      <div className="np-actions">
        <button type="button" className="danger btn-s" disabled={!editable} onClick={onDelete}>
          Supprimer la note
        </button>
        <button type="button" onClick={onClose}>
          Fermer
        </button>
      </div>
    </section>
  );
}
