import { useEffect, useState } from "react";
import type { JsonSchema } from "../api";
import { Overlay } from "../components/Overlay";
import { ReadRegion } from "../components/ReadRegion";
import { useWsQuery } from "../lib";
import { ActionIde } from "../pages/library/ActionIde";
import { AnsibleFields } from "../pages/library/AnsibleFields";
import type { InvSummary } from "../pages/inventories/inventory";
import { ParamEditor } from "./ParamEditor";
import type { OutputField } from "./graphOps";
import type { GraphNode } from "./types";

interface StepSchema {
  input_schema: JsonSchema;
  output_schema: JsonSchema;
}

/** Les paramètres de l'étape, tels que le serveur les connaît (une seule source) : l'éditeur construit ses champs avec. */
export function useAnsibleSchema() {
  return useWsQuery<StepSchema>("ansible-step", "/ansible-step/");
}

const isExpr = (v: unknown): v is string => typeof v === "string" && v.includes("{{");
const PARAM_ORDER = ["user", "check", "diff", "limit", "tags", "skip_tags", "become", "become_user", "forks", "timeout", "extra_vars", "fail_on_error"];

/** Les rôles de l'étape sous la forme que sait éditer `AnsibleFields` : nom → version choisie (« » = la dernière, figée à la publication). */
const rolesRecord = (node: GraphNode): Record<string, string> =>
  Object.fromEntries((node.roles ?? []).map((r) => [r.role, r.version === "latest" ? "" : r.version]));
const rolesList = (rec: Record<string, string>) => Object.entries(rec).map(([role, version]) => ({ role, version: version || "latest" }));

/** Onglet « Playbook » : le code (éditeur avancé avec essai), les étiquettes de runner et les rôles de la bibliothèque. */
export function PlaybookTab({
  node,
  editable,
  onUpdate,
  onOpen,
}: {
  node: GraphNode;
  editable: boolean;
  onUpdate: (p: Partial<GraphNode>) => void;
  onOpen: () => void;
}) {
  const [tags, setTags] = useState((node.required_tags ?? []).join(", "));
  const setRoles = (next: React.SetStateAction<Record<string, string>>) =>
    onUpdate({ roles: rolesList(typeof next === "function" ? next(rolesRecord(node)) : next) });
  return (
    <>
      {node.ai_review && (
        <div role="note" className="notice">
          <p>
            <strong>Playbook écrit par un assistant, non relu.</strong> Lisez-le, vérifiez ses rôles et la cible (inventaire, connexion), testez-le en mode
            vérification, puis confirmez. Tant que ce n'est pas fait, le workflow ne peut pas être publié.
          </p>
          <button type="button" className="primary" disabled={!editable} onClick={() => onUpdate({ ai_review: false })}>
            J'ai relu ce playbook
          </button>
        </div>
      )}
      <button type="button" className="primary" onClick={onOpen}>
        {editable ? "Ouvrir l'éditeur de playbook" : "Voir le playbook"}
      </button>
      <ReadRegion className="script-preview" label="Aperçu du playbook">
        {(node.playbook ?? "").split("\n").slice(0, 14).join("\n")}
      </ReadRegion>
      <AnsibleFields
        runnerTags={tags}
        setRunnerTags={(v) => {
          setTags(v);
          onUpdate({
            required_tags: v
              .split(",")
              .map((t) => t.trim())
              .filter(Boolean),
          });
        }}
        roles={rolesRecord(node)}
        setRoles={setRoles}
        idPrefix="ap"
      />
      <p className="hint">
        Les rôles choisis sont disponibles par leur nom dans le playbook (<code>roles:</code> ou <code>include_role</code>) ; leur version est figée à la
        publication du workflow.
      </p>
    </>
  );
}

/** Onglet « Cible et accès » : l'inventaire, la connexion SSH, l'élévation de privilèges et les clés d'hôtes. */
export function TargetTab({
  node,
  variables,
  onUpdate,
  schema,
}: {
  node: GraphNode;
  variables: OutputField[];
  onUpdate: (p: Partial<GraphNode>) => void;
  schema?: StepSchema;
}) {
  const inventories = useWsQuery<InvSummary[]>("inventories", "/inventories/");
  const connections = useWsQuery<{ results: { name: string; kind: string }[] }>("connections", "/connections/");
  const props = schema?.input_schema.properties ?? {};
  const inputs = node.inputs ?? {};
  const dynamic = !!inputs.inventory && !inputs.inventory_ref;
  const setInput = (key: string, value: unknown) => {
    const next = { ...inputs };
    if (value === undefined || value === "") delete next[key];
    else next[key] = value;
    onUpdate({ inputs: next });
  };
  const conn = (slot: string, name: string) => {
    const next = { ...(node.connections ?? {}) };
    if (name) next[slot] = name;
    else delete next[slot];
    onUpdate({ connections: next });
  };
  const all = connections.data?.results ?? [];
  return (
    <>
      <fieldset className="field">
        <legend>Inventaire</legend>
        <div className="field-inline">
          <input
            id="ap-inv-lib"
            type="radio"
            name="ap-inv"
            checked={!dynamic}
            onChange={() => onUpdate({ inputs: { ...inputs, inventory: undefined, inventory_ref: inputs.inventory_ref ?? "" } })}
          />
          <label htmlFor="ap-inv-lib">De la bibliothèque</label>
          <input
            id="ap-inv-dyn"
            type="radio"
            name="ap-inv"
            checked={dynamic}
            onChange={() => onUpdate({ inputs: { ...inputs, inventory_ref: undefined, inventory: "{{ }}" } })}
          />
          <label htmlFor="ap-inv-dyn">Construit par une étape</label>
        </div>
        {!dynamic ? (
          <div className="field">
            <label htmlFor="ap-inv">Inventaire</label>
            <select id="ap-inv" value={String(inputs.inventory_ref ?? "")} onChange={(e) => setInput("inventory_ref", e.target.value)}>
              <option value="">— choisir —</option>
              {(inventories.data ?? []).map((i) => (
                <option key={i.slug} value={i.slug}>
                  {i.name} ({i.hosts} machine(s))
                </option>
              ))}
            </select>
            <p className="hint">Les machines sont lues au démarrage de l'étape : l'inventaire peut changer sans republier le workflow.</p>
          </div>
        ) : (
          props.inventory && (
            <ParamEditor
              name="inventory"
              schema={props.inventory}
              required={false}
              value={inputs.inventory}
              variables={variables}
              onChange={(v) => setInput("inventory", v)}
            />
          )
        )}
      </fieldset>
      <div className="field">
        <label htmlFor="ap-ssh">Connexion SSH</label>
        <select id="ap-ssh" value={node.connections?.ssh ?? ""} onChange={(e) => conn("ssh", e.target.value)}>
          <option value="">— aucune —</option>
          {all.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
        <p className="hint">Le secret (clé ou mot de passe) n'est jamais écrit dans le workflow : seul le nom de la connexion y figure.</p>
      </div>
      <div className="field">
        <label htmlFor="ap-auth">Secret de la connexion</label>
        <select id="ap-auth" value={node.auth ?? "key"} onChange={(e) => onUpdate({ auth: e.target.value as "key" | "password" })}>
          <option value="key">Clé privée SSH (encodée en base64)</option>
          <option value="password">Mot de passe SSH</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="ap-become">Connexion pour l'élévation de privilèges (facultatif)</label>
        <select id="ap-become" value={node.connections?.become ?? ""} onChange={(e) => conn("become", e.target.value)}>
          <option value="">— aucune (sudo sans mot de passe) —</option>
          {all.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
      </div>
      {props.known_hosts && (
        <ParamEditor
          name="known_hosts"
          schema={props.known_hosts}
          required
          value={inputs.known_hosts}
          variables={variables}
          onChange={(v) => setInput("known_hosts", v)}
        />
      )}
    </>
  );
}

/** Onglet « Paramètres » : le reste des paramètres du playbook (compte, vérification, limites, variables…), chacun avec le choix d'une variable du workflow. */
export function ParamsTab({
  node,
  variables,
  onUpdate,
  schema,
}: {
  node: GraphNode;
  variables: OutputField[];
  onUpdate: (p: Partial<GraphNode>) => void;
  schema?: StepSchema;
}) {
  const props = schema?.input_schema.properties ?? {};
  const inputs = node.inputs ?? {};
  return (
    <>
      {!schema && <p role="status">Chargement…</p>}
      {PARAM_ORDER.filter((k) => props[k]).map((k) => (
        <ParamEditor
          key={k}
          name={k}
          schema={props[k] as JsonSchema}
          required={false}
          value={inputs[k]}
          variables={variables}
          onChange={(v) => onUpdate({ inputs: { ...inputs, [k]: v } })}
        />
      ))}
      <div className="field">
        <label htmlFor="ap-timeout">Durée maximale de l'étape (secondes)</label>
        <input
          id="ap-timeout"
          type="number"
          min={30}
          max={3600}
          value={node.timeout_seconds ?? 300}
          onChange={(e) => onUpdate({ timeout_seconds: Number(e.target.value) })}
        />
      </div>
      <div className="field-inline">
        <input id="ap-idem" type="checkbox" checked={!!node.idempotent} onChange={(e) => onUpdate({ idempotent: e.target.checked })} />
        <label htmlFor="ap-idem">Le playbook est idempotent (peut être rejoué automatiquement après un échec)</label>
      </div>
    </>
  );
}

/** Les valeurs d'essai de départ : ce que l'étape donne en clair (pas les expressions, qui n'ont pas de valeur hors d'une exécution). */
export function sampleOf(node: GraphNode): string {
  const sample: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(node.inputs ?? {})) if (v !== "" && v !== undefined && !isExpr(v)) sample[k] = v;
  if (sample.check === undefined) sample.check = true; // un essai ne modifie rien tant qu'on ne le demande pas
  return JSON.stringify(sample, null, 2);
}

/** L'éditeur du playbook de l'étape : le même éditeur avancé que pour une action, avec essai sur les machines désignées (mode vérification par défaut). */
export function AnsiblePlaybookDialog({
  node,
  slug,
  schema,
  editable,
  onUpdate,
  onClose,
}: {
  node: GraphNode;
  slug: string;
  schema?: StepSchema;
  editable: boolean;
  onUpdate: (p: Partial<GraphNode>) => void;
  onClose: () => void;
}) {
  const [source, setSource] = useState(node.playbook ?? "");
  useEffect(() => setSource(node.playbook ?? ""), [node.playbook]);
  const slots = Object.keys({ ssh: 1, ...(node.connections ?? {}) });
  const stepFields = {
    playbook: source,
    roles: node.roles ?? [],
    required_tags: node.required_tags ?? [],
    auth: node.auth ?? "key",
    connections: node.connections ?? {},
    timeout_seconds: node.timeout_seconds ?? 300,
    idempotent: !!node.idempotent,
  };
  return (
    <Overlay
      wide
      variant="modal"
      className="overlay-version"
      title={`Playbook — ${node.name}`}
      description="Le playbook est enregistré avec le workflow ; l'essai le joue sur les machines désignées, avec ces valeurs."
      onClose={onClose}
    >
      <ActionIde
        runtime="ansible"
        source={source}
        onSource={(v) => {
          setSource(v);
          if (editable) onUpdate({ playbook: v });
        }}
        slug={slug}
        inputSchema={JSON.stringify(schema?.input_schema ?? { type: "object", properties: {} })}
        buildDraft={() => ({ draft: { secrets_required: slots.map((slot) => ({ slot })) } })}
        onBack={onClose}
        trial={{
          path: `/workflows/${slug}/test-ansible/`,
          body: (_draft, inputs, connections) => ({ node: stepFields, inputs, connections }),
          sample: sampleOf(node),
          connections: node.connections ?? {},
          backLabel: "Fermer l'éditeur",
        }}
      />
    </Overlay>
  );
}
