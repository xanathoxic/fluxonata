import type { ReactNode } from "react";
import { EMPTY_SECRET, SecretSource, secretPayload, secretReady, type SecretDraft } from "./SecretSource";

export type SecretChoiceMode = "keep" | "existing" | "new";
export interface SecretChoiceDraft { mode: SecretChoiceMode; existing: string; newName: string; src: SecretDraft }
export interface SecretRow { name: string; provider: string; used_by: number }

export const emptyChoice = (mode: SecretChoiceMode = "new"): SecretChoiceDraft => ({ mode, existing: "", newName: "", src: EMPTY_SECRET });

/** What the API takes: nothing to keep the current secret, the name of an existing one, or the value (or external reference) of a new one. */
export function secretChoicePayload(c: SecretChoiceDraft): Record<string, unknown> {
  if (c.mode === "keep") return {};
  if (c.mode === "existing") return { secret: c.existing };
  const p = secretPayload(c.src);
  return { secret_provider: p.provider, ...(p.value ? { secret_value: p.value } : {}), ...("vault" in p ? { vault: p.vault } : {}), ...(c.newName.trim() ? { new_secret_name: c.newName.trim() } : {}) };
}

export const secretChoiceReady = (c: SecretChoiceDraft): boolean => (c.mode === "keep" ? true : c.mode === "existing" ? c.existing !== "" : secretReady(c.src));

const providerLabel = (p: string) => (p === "vault" ? "Vault" : "coffre de Fluxonata");

/** « Which secret does this connection use? » Keep the current one (when editing), pick one that already exists, or create a new one. A secret is never shown again. */
export function SecretChoice({ value, onChange, secrets, current, allowDynamic = false, multiline = false, instructions, internalLabel, idBase = "sc" }: {
  value: SecretChoiceDraft; onChange: (c: SecretChoiceDraft) => void; secrets: SecretRow[]; current?: string | null;
  allowDynamic?: boolean; multiline?: boolean; instructions?: ReactNode; internalLabel?: string; idBase?: string;
}) {
  const set = (patch: Partial<SecretChoiceDraft>) => onChange({ ...value, ...patch });
  const none = secrets.length === 0;
  const option = (mode: SecretChoiceMode, label: string, hint?: ReactNode, disabled = false) => (
    <label className={`ss-card${value.mode === mode ? " on" : ""}${disabled ? " off" : ""}`}>
      <input type="radio" name={`${idBase}-mode`} checked={value.mode === mode} disabled={disabled} onChange={() => set({ mode })} />
      <span><strong>{label}</strong>{hint && <span className="hint">{hint}</span>}</span>
    </label>
  );
  return (
    <div className="secret-choice">
      <fieldset className="ss-choice">
        <legend>Secret de la connexion</legend>
        {current !== undefined && option("keep", `Conserver le secret actuel${current ? ` (${current})` : ""}`, "Rien ne change côté secret.")}
        {option("existing", "Choisir un secret existant", none ? "Aucun secret dans cet espace : créez-en un nouveau." : "Plusieurs connexions peuvent partager un secret : le remplacer ailleurs les change toutes.", none)}
        {option("new", "Créer un nouveau secret", current !== undefined ? "L'ancien secret reste en place (il n'est pas supprimé)." : undefined)}
      </fieldset>
      {value.mode === "existing" && (
        <div className="field"><label htmlFor={`${idBase}-existing`}>Secret existant</label>
          <select id={`${idBase}-existing`} value={value.existing} onChange={(e) => set({ existing: e.target.value })}>
            <option value="">Choisir…</option>
            {secrets.map((s) => <option key={s.name} value={s.name}>{s.name} — {providerLabel(s.provider)}, {s.used_by} connexion(s)</option>)}
          </select>
          <p className="hint">Seul le nom est montré : la valeur n'est jamais réaffichée.</p></div>
      )}
      {value.mode === "new" && (
        <>
          <div className="field"><label htmlFor={`${idBase}-new-name`}>Nom du nouveau secret (facultatif)</label>
            <input id={`${idBase}-new-name`} value={value.newName} pattern="[A-Za-z0-9_-]*" maxLength={60} placeholder="généré à partir de la connexion si vide" onChange={(e) => set({ newName: e.target.value })} /></div>
          <SecretSource value={value.src} onChange={(src) => set({ src })} allowDynamic={allowDynamic} multiline={multiline} {...(instructions ? { instructions } : {})} {...(internalLabel ? { internalLabel } : {})} name={`${idBase}-src`} />
        </>
      )}
    </div>
  );
}
