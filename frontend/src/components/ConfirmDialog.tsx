import type { ReactNode } from "react";
import { Overlay } from "./Overlay";

interface Props {
  /** What is acted on, by name (« Supprimer « Demande d'accès » ? »). */
  title: string;
  /** The consequence, in a sentence. */
  children: ReactNode;
  /** Label of the confirming button: the verb of the action (« Supprimer », « Annuler l'exécution »), never « OK ». */
  confirmLabel: string;
  /** Red button for what cannot be undone (the default); `false` for a plain confirmation. */
  danger?: boolean;
  busy?: boolean;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/** The one confirmation dialog (also exported as `Confirm` from AdminUi): names the object, states the consequence, puts the verb on the button.
 *  Focus stays inside; Escape cancels. */
export function ConfirmDialog({ title, children, confirmLabel, danger = true, busy, cancelLabel = "Annuler", onConfirm, onCancel }: Props) {
  return (
    <Overlay variant="modal" title={title} onClose={onCancel}
      footer={<><button type="button" onClick={onCancel}>{cancelLabel}</button><button type="button" className={danger ? "danger" : "primary"} disabled={busy} onClick={onConfirm}>{confirmLabel}</button></>}>
      {children}
    </Overlay>
  );
}
