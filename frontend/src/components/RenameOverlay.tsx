import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { api, type ApiError } from "../api";
import { useApp } from "../context";
import { Overlay } from "./Overlay";
import { RowIcon } from "./RowIcon";

/** Ce qu'on peut renommer, avec le point d'API qui accepte `{ name }` (le nom affiché change, jamais l'identifiant). */
export type RenameKind = "workflow" | "action" | "skill" | "form" | "control";

const ENDPOINT: Record<RenameKind, (slug: string) => string> = {
  workflow: (s) => `/workflows/${s}/meta/`,
  action: (s) => `/actions/${s}/meta/`,
  skill: (s) => `/ai/skills/${s}/`,
  form: (s) => `/form-library/${s}/`,
  control: (s) => `/control-library/${s}/`,
};
const WHAT: Record<RenameKind, string> = { workflow: "le workflow", action: "l'action", skill: "le skill", form: "le formulaire", control: "le contrôle" };
/** Droit nécessaire côté interface ; le serveur revérifie toujours. */
export const RENAME_RIGHT: Record<RenameKind, "edit" | "publish"> = { workflow: "edit", action: "publish", skill: "publish", form: "edit", control: "edit" };
export const MAX_NAME = 160;

/** Fenêtre « Renommer » : le nom affiché dans les listes et les pages ; l'identifiant technique ne change pas, aucune référence n'est touchée. */
export function RenameOverlay({
  kind,
  slug,
  name,
  onClose,
  onDone,
}: {
  kind: RenameKind;
  slug: string;
  name: string;
  onClose: () => void;
  onDone?: (name: string) => void;
}) {
  const { workspace } = useApp();
  const qc = useQueryClient();
  const id = useId();
  const [value, setValue] = useState(name);
  const field = useRef<HTMLInputElement>(null);
  // fenêtre modale : le champ est prêt à être remplacé dès l'ouverture
  useEffect(() => {
    field.current?.focus();
    field.current?.select();
  }, []);
  const clean = value.trim();
  const save = useMutation<unknown, ApiError, string>({
    mutationFn: (n) => api(`/workspaces/${workspace.slug}${ENDPOINT[kind](slug)}`, { method: "PATCH", json: { name: n } }),
    onSuccess: (_, n) => {
      void qc.invalidateQueries();
      onDone?.(n);
      onClose();
    },
  });
  const tooLong = clean.length > MAX_NAME;
  const unchanged = clean === name;
  return (
    <Overlay
      variant="modal"
      title={`Renommer ${WHAT[kind]} « ${name} »`}
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose}>
            Annuler
          </button>
          <button type="submit" form={`${id}-f`} className="primary" disabled={save.isPending || clean === "" || tooLong || unchanged}>
            Renommer
          </button>
        </>
      }
    >
      <form
        id={`${id}-f`}
        onSubmit={(e) => {
          e.preventDefault();
          if (clean && !tooLong && !unchanged) save.mutate(clean);
        }}
      >
        <div className="field">
          <label htmlFor={`${id}-n`}>Nouveau nom</label>
          <input
            id={`${id}-n`}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            maxLength={MAX_NAME + 20}
            ref={field}
            aria-invalid={clean === "" || tooLong}
          />
          <p className="hint">Seul le nom affiché change : l'identifiant (« {slug} »), les liens et l'historique restent les mêmes.</p>
        </div>
        {tooLong && (
          <p role="alert" className="field-error">
            Le nom fait {MAX_NAME} caractères au plus.
          </p>
        )}
        {save.error && (
          <p role="alert" className="field-error">
            Erreur : {save.error.message}
          </p>
        )}
      </form>
    </Overlay>
  );
}

/** Bouton « Renommer » d'une ligne de liste (icône + fenêtre). Masqué sans le droit de modification. */
export function RenameButton({ kind, slug, name }: { kind: RenameKind; slug: string; name: string }) {
  const { can } = useApp();
  const [open, setOpen] = useState(false);
  if (!can(RENAME_RIGHT[kind])) return null;
  return (
    <>
      <button
        type="button"
        className="icon-btn"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-label={`Renommer ${WHAT[kind]} ${name}`}
        title="Renommer"
      >
        <RowIcon name="rename" />
      </button>
      {open && <RenameOverlay kind={kind} slug={slug} name={name} onClose={() => setOpen(false)} />}
    </>
  );
}
