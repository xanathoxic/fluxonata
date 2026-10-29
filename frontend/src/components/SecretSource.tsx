import { HelpTip } from "./HelpTip";
import { Badge } from "./AdminUi";
import { useWsQuery } from "../lib";

export type SecretMode = "managed" | "reference" | "dynamic";
export interface SecretDraft { provider: "internal" | "vault"; mode: SecretMode; value: string; mount: string; path: string; key: string; version: string }
export const EMPTY_SECRET: SecretDraft = { provider: "internal", mode: "managed", value: "", mount: "", path: "", key: "", version: "" };

export interface WorkspaceVault { available: boolean; mount?: string; own_prefix?: string; grants?: { pattern: string; writable: boolean }[] }

/** What the API takes for a secret: the person's choice of where it lives (Fluxonata's own vault or HashiCorp Vault). */
export function secretPayload(d: SecretDraft) {
  if (d.provider !== "vault") return { provider: "internal" as const, value: d.value };
  const vault = d.mode === "managed" ? { mode: d.mode } : { mode: d.mode, mount: d.mount.trim(), path: d.path.trim(), key: d.key.trim(), version: d.version ? Number(d.version) : null };
  return { provider: "vault" as const, value: d.mode === "managed" ? d.value : "", vault };
}

/** True when the draft holds everything the chosen source needs. */
export function secretReady(d: SecretDraft): boolean {
  if (d.provider !== "vault") return d.value !== "";
  if (d.mode === "managed") return d.value !== "";
  return d.path.trim() !== "" && (d.mode !== "dynamic" || d.mount.trim() !== "");
}

export const MODE_LABEL: Record<SecretMode, string> = { managed: "Écrit par Fluxonata dans Vault", reference: "Secret existant dans Vault", dynamic: "Identifiants dynamiques (Vault)" };

/** « Where is the secret kept? » The choice shared by connections, AI providers' keys and every other secret. */
export function SecretSource({ value, onChange, allowDynamic = false, internalLabel = "Secret (jeton, mot de passe…)", name = "secret" }: {
  value: SecretDraft; onChange: (d: SecretDraft) => void; allowDynamic?: boolean; internalLabel?: string; name?: string;
}) {
  const info = useWsQuery<WorkspaceVault>("vault-info", "/vault/");
  const vault = info.data;
  const set = (patch: Partial<SecretDraft>) => onChange({ ...value, ...patch });
  const external = value.provider === "vault";
  return (
    <div className="secret-source">
      <fieldset className="ss-choice">
        <legend>Où conserver le secret ?</legend>
        <label className={`ss-card${!external ? " on" : ""}`}>
          <input type="radio" name={`${name}-provider`} checked={!external} onChange={() => set({ provider: "internal" })} />
          <span><strong>Coffre de Fluxonata</strong><span className="hint">Chiffré dans la base de Fluxonata, relu seulement par les actions autorisées.</span></span>
        </label>
        <label className={`ss-card${external ? " on" : ""}${vault && !vault.available ? " off" : ""}`}>
          <input type="radio" name={`${name}-provider`} checked={external} disabled={!vault?.available} onChange={() => set({ provider: "vault", mode: "managed" })} />
          <span><strong>Vault (HashiCorp)</strong><span className="hint">{vault?.available ? "La valeur reste dans votre Vault : Fluxonata n'en garde qu'une référence." : "Non disponible : un administrateur de la plate-forme doit configurer et activer le coffre externe."}</span></span>
        </label>
      </fieldset>

      {!external && (
        <div className="field"><label htmlFor={`${name}-value`}>{internalLabel}</label>
          <input id={`${name}-value`} type="password" autoComplete="off" value={value.value} onChange={(e) => set({ value: e.target.value })} />
          <HelpTip>Il sera chiffré puis ne pourra plus être relu, seulement remplacé.</HelpTip></div>
      )}

      {external && vault?.available && (
        <>
          <fieldset className="ss-modes">
            <legend>Mode Vault</legend>
            {(Object.keys(MODE_LABEL) as SecretMode[]).filter((m) => allowDynamic || m !== "dynamic").map((m) => (
              <label key={m} className={`ss-mode${value.mode === m ? " on" : ""}`}>
                <input type="radio" name={`${name}-mode`} checked={value.mode === m} onChange={() => set({ mode: m })} />{MODE_LABEL[m]}
              </label>
            ))}
          </fieldset>
          {value.mode === "managed" && (
            <div className="field"><label htmlFor={`${name}-vvalue`}>Valeur à écrire dans Vault</label>
              <input id={`${name}-vvalue`} type="password" autoComplete="off" value={value.value} onChange={(e) => set({ value: e.target.value })} />
              <HelpTip>Fluxonata l'écrit dans Vault, à <code>{vault.mount}/{vault.own_prefix}…</code>, puis ne la garde pas : elle ne pourra plus être relue ici, seulement remplacée.</HelpTip></div>
          )}
          {value.mode !== "managed" && (
            <div className="adm-two">
              <div className="field"><label htmlFor={`${name}-mount`}>Point de montage{value.mode === "reference" ? " (facultatif)" : ""}</label>
                <input id={`${name}-mount`} value={value.mount} placeholder={value.mode === "dynamic" ? "database" : vault.mount} onChange={(e) => set({ mount: e.target.value })} /></div>
              <div className="field"><label htmlFor={`${name}-path`}>Chemin dans Vault</label>
                <input id={`${name}-path`} className="mono" value={value.path} placeholder={value.mode === "dynamic" ? "creds/mon-role" : `${vault.own_prefix}mon-secret`} onChange={(e) => set({ path: e.target.value })} /></div>
              {value.mode === "reference" && <>
                <div className="field"><label htmlFor={`${name}-key`}>Clé du secret (champ à lire)</label><input id={`${name}-key`} value={value.key} placeholder="value" onChange={(e) => set({ key: e.target.value })} /></div>
                <div className="field"><label htmlFor={`${name}-ver`}>Version (facultatif)</label><input id={`${name}-ver`} type="number" min={1} value={value.version} placeholder="dernière" onChange={(e) => set({ version: e.target.value })} /></div>
              </>}
              {value.mode === "dynamic" && <div className="field"><label htmlFor={`${name}-key`}>Champ utilisé comme secret</label><input id={`${name}-key`} value={value.key} placeholder="password" onChange={(e) => set({ key: e.target.value })} />
                <HelpTip>Vault fournit un identifiant et un mot de passe temporaires à chaque bail ; l'identifiant (<code>username</code>) est utilisé automatiquement pour se connecter.</HelpTip></div>}
            </div>
          )}
          <p className="ss-allowed hint">Accessible à cet espace : <Badge tone="info">{vault.mount}/{vault.own_prefix}*</Badge> {(vault.grants ?? []).map((g) => <Badge key={g.pattern} tone={g.writable ? "warn" : "muted"}>{g.pattern}{g.writable ? " (écriture)" : ""}</Badge>)}</p>
        </>
      )}
    </div>
  );
}
