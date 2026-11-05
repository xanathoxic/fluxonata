import type { ReactNode } from "react";
import { FormIcon, type FormIconName } from "../forms/FormIcon";
import type { AutosaveState } from "./useAutosave";

const WORDING: Record<AutosaveState, { icon: FormIconName; text: string; tone: string }> = {
  saved: { icon: "check", text: "Brouillon enregistré", tone: "ok" },
  dirty: { icon: "edit", text: "Modifications en attente", tone: "warn" },
  saving: { icon: "save", text: "Enregistrement…", tone: "warn" },
  conflict: { icon: "warn", text: "Conflit : modifié ailleurs", tone: "bad" },
  error: { icon: "warn", text: "Enregistrement impossible, nouvel essai", tone: "bad" },
  invalid: { icon: "warn", text: "Non enregistré : brouillon refusé", tone: "bad" },
};
const hhmm = (d: Date) => d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

/** The single status line of an editor: « v1.0.0 publiée · brouillon enregistré à 16:16 · valide ». One `role=status` region, same words everywhere. */
export function StatusLine({ published, state, savedAt, problems, extra }: {
  /** « v1.0.0 publiée », « Jamais publié », « Modèle »… */
  published?: ReactNode;
  state?: AutosaveState;
  savedAt?: Date | null;
  /** Number of problems found by the server's checks; `null` while unknown. */
  problems?: number | null;
  extra?: ReactNode;
}) {
  const w = state ? WORDING[state] : null;
  return (
    <p className="es-status" role="status" aria-live="polite">
      {published !== undefined && <span className="es-part">{published}</span>}
      {w && <span className={`es-part es-${w.tone}`}><FormIcon name={w.icon} size={14} />{w.text}{state === "saved" && savedAt ? ` à ${hhmm(savedAt)}` : ""}</span>}
      {problems !== undefined && problems !== null && <span className={`es-part es-${problems ? "bad" : "ok"}`}><FormIcon name={problems ? "warn" : "check"} size={14} />{problems ? `${problems} problème(s)` : "Valide"}</span>}
      {extra && <span className="es-part">{extra}</span>}
    </p>
  );
}
