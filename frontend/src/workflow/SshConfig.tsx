import { useEffect, useState } from "react";
import { useApp } from "../context";
import { useWsQuery } from "../lib";
import { HelpTip } from "../components/HelpTip";
import type { GraphNode, SshCommand, SshJump, SshPty, SshResponse, SshStep } from "./types";

interface Conn { name: string; label: string; kind: string; config: { host?: string; host_key_sha256?: string } }

/** Jumps as objects for editing (a plain name is a jump whose host comes from its connection). */
export const asJumps = (value: unknown): SshJump[] =>
  Array.isArray(value) ? value.flatMap((j): SshJump[] => (typeof j === "string" ? [{ connection: j }] : j && typeof j === "object" ? [{ ...(j as SshJump), connection: String((j as SshJump).connection ?? "") }] : [])) : [];

/** Back to what is stored: a plain name when the step names no host for that jump (older workflows and the MCP keep working unchanged). */
export const fromJumps = (jumps: SshJump[]): (string | SshJump)[] =>
  jumps.map((j) => (j.host || j.port || j.host_key_sha256 ? j : j.connection));

const MAX_JUMPS = 5;
const EOLS: [string, string][] = [["lf", "Entrée (\\n)"], ["cr", "Retour chariot (\\r)"], ["crlf", "\\r\\n"], ["none", "Aucune"]];

/** Moves item `i` by `d` positions (returns a new array; unchanged when it would leave the list). */
export function move<T>(items: T[], i: number, d: -1 | 1): T[] {
  const j = i + d;
  if (j < 0 || j >= items.length) return items;
  const next = [...items];
  [next[i], next[j]] = [next[j] as T, next[i] as T];
  return next;
}

let rowSeq = 0;
const newKey = () => `row${rowSeq++}`;

/** Stable React keys for a list the user can reorder: the key follows the row (not its index), so focus and typing survive a move. */
function useRowKeys(count: number) {
  const [keys, setKeys] = useState<string[]>(() => Array.from({ length: count }, newKey));
  const synced = keys.length === count ? keys : Array.from({ length: count }, (_, i) => keys[i] ?? newKey());
  useEffect(() => {
    if (keys.length !== count) setKeys(synced); // the list changed from outside (undo, import): adopt the keys once
  }, [keys.length, count, synced]);
  return {
    keys: synced,
    add: () => setKeys([...synced, newKey()]),
    remove: (i: number) => setKeys(synced.filter((_, k) => k !== i)),
    move: (i: number, d: -1 | 1) => setKeys(move(synced, i, d)),
  };
}

/** A pattern field as a list: the server accepts one text as well as a list (an assistant or an import may write either). */
export const asList = (value: unknown): string[] => (Array.isArray(value) ? value.filter((x): x is string => typeof x === "string") : typeof value === "string" && value !== "" ? [value] : []);

/** Commands as objects: a plain text is a command `{ run }` (and anything else is ignored rather than crashing the editor). */
export const asCommands = (value: unknown): SshCommand[] =>
  Array.isArray(value) ? value.flatMap((c): SshCommand[] => (typeof c === "string" ? [{ run: c }] : c && typeof c === "object" ? [{ ...(c as SshCommand), run: String((c as SshCommand).run ?? "") }] : [])) : [];

const lines = (text: string): string[] | undefined => {
  const out = text.split("\n").map((l) => l.trim()).filter(Boolean);
  return out.length ? out : undefined;
};

function RowButtons({ label, i, count, onMove, onRemove }: { label: string; i: number; count: number; onMove: (d: -1 | 1) => void; onRemove: () => void }) {
  return (
    <span className="row-actions">
      <button type="button" className="btn-link" aria-label={`Monter ${label}`} disabled={i === 0} onClick={() => onMove(-1)}>↑</button>
      <button type="button" className="btn-link" aria-label={`Descendre ${label}`} disabled={i === count - 1} onClick={() => onMove(1)}>↓</button>
      <button type="button" className="btn-link" aria-label={`Retirer ${label}`} onClick={onRemove}>Retirer</button>
    </span>
  );
}

const sendChoice = (s: { send?: string; send_secret?: string }) => (s.send_secret !== undefined ? "secret" : s.send !== undefined ? "text" : "none");

function SendEditor({ aliasNames, idBase, label, item, patch }: { aliasNames: string[]; idBase: string; label: string; item: SshStep | SshResponse; patch: (p: Record<string, unknown>) => void }) {
  const choice = sendChoice(item);
  return (
    <>
      <div className="field">
        <label htmlFor={`${idBase}-kind`}>{label}</label>
        <select id={`${idBase}-kind`} value={choice} onChange={(e) => patch({ send: e.target.value === "text" ? "" : undefined, send_secret: e.target.value === "secret" ? (aliasNames[0] ?? "") : undefined })}>
          <option value="none">Ne rien envoyer (attendre seulement)</option>
          <option value="text">Un texte</option>
          <option value="secret" disabled={aliasNames.length === 0}>Un secret référencé{aliasNames.length === 0 ? " (déclarez d'abord un secret ci-dessous)" : ""}</option>
        </select>
      </div>
      {choice === "text" && <div className="field"><label htmlFor={`${idBase}-text`}>Texte envoyé</label><input id={`${idBase}-text`} value={item.send ?? ""} onChange={(e) => patch({ send: e.target.value })} /></div>}
      {choice === "secret" && <div className="field"><label htmlFor={`${idBase}-secret`}>Alias du secret envoyé</label>
        <select id={`${idBase}-secret`} value={item.send_secret ?? ""} onChange={(e) => patch({ send_secret: e.target.value })}>{aliasNames.map((a) => <option key={a} value={a}>{a}</option>)}</select>
        <p className="hint">La valeur n'apparaît ni dans le workflow, ni dans les résultats, ni dans les journaux.</p></div>}
      {choice !== "none" && <div className="field"><label htmlFor={`${idBase}-eol`}>Fin de ligne ajoutée</label>
        <select id={`${idBase}-eol`} value={item.eol ?? "lf"} onChange={(e) => patch({ eol: e.target.value })}>{EOLS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>}
    </>
  );
}

function PatternBox({ id, label, hint, value, onChange }: { id: string; label: string; hint: string; value: string | string[] | undefined; onChange: (v: string[] | undefined) => void }) {
  return (
    <div className="field"><label htmlFor={id}>{label}</label>
      <textarea id={id} rows={2} spellCheck={false} value={asList(value).join("\n")} onChange={(e) => onChange(lines(e.target.value))} aria-describedby={`${id}-h`} />
      <p className="hint" id={`${id}-h`}>{hint}</p></div>
  );
}

function StepEditor({ i, step: s, count, aliasNames, last, onChange, onMove, onRemove }: { i: number; step: SshStep; count: number; aliasNames: string[]; last: boolean; onChange: (p: Partial<SshStep>) => void; onMove: (d: -1 | 1) => void; onRemove: () => void }) {
  const responses: SshResponse[] = Array.isArray(s.responses) ? s.responses.filter((r): r is SshResponse => !!r && typeof r === "object") : [];
  const rk = useRowKeys(responses.length);
  return (
    <details className="card" open={last}>
      <summary>Étape {i + 1}{s.id ? ` (${s.id})` : ""}</summary>
      <div className="field"><label htmlFor={`ssh-s${i}-id`}>Identifiant (facultatif)</label><input id={`ssh-s${i}-id`} value={s.id ?? ""} pattern="[A-Za-z0-9_-]*" maxLength={40} onChange={(e) => onChange({ id: e.target.value || undefined })} /></div>
      <SendEditor aliasNames={aliasNames} idBase={`ssh-s${i}`} label="Envoyer" item={s} patch={(p) => onChange(p as Partial<SshStep>)} />
      <PatternBox id={`ssh-s${i}-expect`} label="Attendre (un motif par ligne)" hint="Termine l'étape dès qu'un motif apparaît dans ce qui est reçu." value={s.expect} onChange={(v) => onChange({ expect: v })} />
      <PatternBox id={`ssh-s${i}-fail`} label="Motifs d'échec (un par ligne)" hint="Prioritaires : si l'un apparaît, l'étape échoue." value={s.failure} onChange={(v) => onChange({ failure: v })} />
      <PatternBox id={`ssh-s${i}-ok`} label="Motifs de succès (un par ligne)" hint="Si renseignés, l'un doit apparaître dans ce que l'étape a capturé." value={s.success} onChange={(v) => onChange({ success: v })} />
      <div className="field"><label htmlFor={`ssh-s${i}-t`}>Délai de l'étape (secondes, facultatif)</label><input id={`ssh-s${i}-t`} type="number" min={1} max={300} value={s.timeout_seconds ?? ""} onChange={(e) => onChange({ timeout_seconds: e.target.value ? Number(e.target.value) : undefined })} /></div>
      <div className="field-inline"><input id={`ssh-s${i}-sens`} type="checkbox" checked={!!s.sensitive} onChange={(e) => onChange({ sensitive: e.target.checked })} /><label htmlFor={`ssh-s${i}-sens`}>Étape sensible (rien n'est conservé de ce qui est envoyé ni reçu)</label></div>
      <div className="field-inline"><input id={`ssh-s${i}-ign`} type="checkbox" checked={!!s.ignore_error} onChange={(e) => onChange({ ignore_error: e.target.checked })} /><label htmlFor={`ssh-s${i}-ign`}>Ignorer l'échec de cette étape</label></div>
      <fieldset className="field">
        <legend>Interactions (réponses automatiques)</legend>
        {responses.map((r, k) => {
          const setResp = (p: Record<string, unknown>) => onChange({ responses: responses.map((x, m) => (m === k ? { ...x, ...p } : x)) });
          return (
            <div key={rk.keys[k]} className="card">
              <div className="field"><label htmlFor={`ssh-s${i}-r${k}-e`}>Quand ce motif apparaît</label><input id={`ssh-s${i}-r${k}-e`} className="mono" value={r.expect} onChange={(e) => setResp({ expect: e.target.value })} /></div>
              <SendEditor aliasNames={aliasNames} idBase={`ssh-s${i}-r${k}`} label="Répondre" item={r} patch={setResp} />
              <div className="field"><label htmlFor={`ssh-s${i}-r${k}-m`}>Au plus (fois)</label><input id={`ssh-s${i}-r${k}-m`} type="number" min={1} max={10} value={r.max ?? 1} onChange={(e) => setResp({ max: Number(e.target.value) })} /></div>
              <div className="field-inline"><input id={`ssh-s${i}-r${k}-s`} type="checkbox" checked={!!r.sensitive} onChange={(e) => setResp({ sensitive: e.target.checked })} /><label htmlFor={`ssh-s${i}-r${k}-s`}>Réponse sensible (texte non conservé)</label></div>
              <button type="button" className="btn-link" aria-label={`Retirer l'interaction ${k + 1} de l'étape ${i + 1}`} onClick={() => { rk.remove(k); onChange({ responses: responses.filter((_, m) => m !== k) }); }}>Retirer l'interaction</button>
            </div>
          );
        })}
        <button type="button" className="btn" disabled={responses.length >= 10} onClick={() => { rk.add(); onChange({ responses: [...responses, { expect: "", send: "" }] }); }}>Ajouter une interaction à l'étape {i + 1}</button>
      </fieldset>
      <RowButtons label={`l'étape ${i + 1}`} i={i} count={count} onMove={onMove} onRemove={onRemove} />
    </details>
  );
}

/** Commands over SSH: direct or through ordered jump hosts; independent commands (exec) or one persistent session (shell). */
export function SshConfig({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const { can } = useApp();
  const conns = (useWsQuery<{ results: Conn[] }>("connections", "/connections/").data?.results ?? []).filter((c) => c.kind === "ssh");
  const secrets = useWsQuery<{ results: { name: string }[] }>("secrets", "/secrets/", { enabled: can("manage_connections") }).data?.results ?? [];
  const jk = useRowKeys(Array.isArray(node.jumps) ? node.jumps.length : 0);
  const ck = useRowKeys(asCommands(node.commands).length);
  const sk = useRowKeys(Array.isArray(node.steps) ? node.steps.length : 0);
  const ak = useRowKeys(Object.keys(node.secrets ?? {}).length);
  const [mocksText, setMocksText] = useState(() => (node.mocks?.length ? JSON.stringify(node.mocks, null, 2) : ""));
  const [mocksError, setMocksError] = useState("");
  const session = node.session ?? "exec";
  const jumps: SshJump[] = asJumps(node.jumps);
  const dynamicList = typeof node.commands === "string";
  const commands: SshCommand[] = asCommands(node.commands);
  const steps: SshStep[] = Array.isArray(node.steps) ? node.steps.filter((s): s is SshStep => !!s && typeof s === "object") : [];
  const pty: SshPty = node.pty ?? {};
  const aliases = Object.entries(node.secrets ?? {});
  const aliasNames = aliases.map(([a]) => a);
  // a connection that has its own host can appear once in the chain; an identity (no host) can serve several hosts
  const used = new Set([node.connection ?? "", ...jumps.map((j) => j.connection)]);
  const optionsFor = (current: string) => conns.filter((c) => c.name === current || !!c.config.host === false || !used.has(c.name));
  const target = conns.find((c) => c.name === node.connection);
  const hostless = (name: string) => { const c = conns.find((x) => x.name === name); return !!c && !c.config.host; };
  const setJump = (i: number, patch: Partial<SshJump>) => onUpdate({ jumps: fromJumps(jumps.map((x, k) => (k === i ? { ...x, ...patch } : x))) });
  const hostFields = (idBase: string, who: string, v: { host?: string; port?: number | string; host_key_sha256?: string }, set: (p: { host?: string; port?: number | string; host_key_sha256?: string }) => void) => (
    <div className="card">
      <p className="hint">Cette connexion est une identité sans serveur : indiquez ici {who}. L'empreinte de sa clé est obligatoire (aucune clé inconnue n'est acceptée). Seule une clé privée peut servir ainsi, jamais un mot de passe.</p>
      <div className="field"><label htmlFor={`${idBase}-host`}>Hôte : adresse IP ou nom DNS</label><input id={`${idBase}-host`} value={v.host ?? ""} placeholder="srv1.exemple.org ou 10.0.0.5" onChange={(e) => set({ host: e.target.value || undefined })} /></div>
      <div className="field"><label htmlFor={`${idBase}-port`}>Port (22 par défaut)</label><input id={`${idBase}-port`} value={v.port ?? ""} inputMode="numeric" placeholder="22" onChange={(e) => set({ port: e.target.value || undefined })} /></div>
      <div className="field"><label htmlFor={`${idBase}-fp`}>Empreinte de la clé du serveur (SHA256:…)</label><input id={`${idBase}-fp`} className="mono" value={v.host_key_sha256 ?? ""} placeholder="SHA256:…" onChange={(e) => set({ host_key_sha256: e.target.value || undefined })} /></div>
    </div>
  );

  const setCommand = (i: number, patch: Partial<SshCommand>) => onUpdate({ commands: commands.map((c, k) => (k === i ? { ...c, ...patch } : c)) });
  const setStep = (i: number, patch: Partial<SshStep>) => onUpdate({ steps: steps.map((s, k) => (k === i ? { ...s, ...patch } : s)) });
  const setAlias = (oldName: string, name: string, secret: string) => {
    if (name !== oldName && aliasNames.includes(name)) return; // two aliases cannot share a name (the second would silently replace the first)
    const next: Record<string, string> = {};
    for (const [a, s] of aliases) next[a === oldName ? name : a] = a === oldName ? secret : s;
    onUpdate({ secrets: next });
  };

  return (
    <>
      <div className="field">
        <HelpTip>{target ? (target.config.host_key_sha256 ? `Serveur ${target.config.host} : clé vérifiée contre l'empreinte enregistrée.` : "Cette connexion n'a pas d'empreinte de clé : elle sera refusée.") : "Menu Connexions, type « Serveur SSH » : l'empreinte de la clé du serveur est obligatoire (aucune clé inconnue n'est acceptée)."}</HelpTip>
        <label htmlFor="ssh-conn">Connexion cible (serveur SSH)</label>
        <select id="ssh-conn" value={node.connection ?? ""} onChange={(e) => onUpdate({ connection: e.target.value })}>
          <option value="">Choisir…</option>{optionsFor(node.connection ?? "").map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}</select>
      </div>
      {node.connection && hostless(node.connection) && hostFields("ssh-target", "l'hôte de la cible", { host: node.host, port: node.port, host_key_sha256: node.host_key_sha256 }, (p) => onUpdate(p))}
      {node.connection && !hostless(node.connection) && target && <p className="hint">Hôte défini par la connexion « {target.label || target.name} » : l'étape n'en indique pas.</p>}

      <fieldset className="field">
        <legend>Rebonds (facultatif, dans l'ordre)</legend>
        <p className="hint">Le premier rebond est joint depuis la plateforme, puis chaque serveur est joint depuis le précédent, la cible en dernier. La clé de chaque serveur est vérifiée ; les noms après le premier saut sont résolus par le rebond précédent. {MAX_JUMPS} rebonds au plus ; une même identité (connexion sans serveur) peut servir pour plusieurs serveurs, jamais pour le même serveur deux fois.</p>
        {jumps.length === 0 && <p className="hint">Connexion directe.</p>}
        {jumps.map((j, i) => (
          <div key={jk.keys[i]}>
            <div className="inline-row">
              <label htmlFor={`ssh-jump-${i}`} className="sr-only">Rebond {i + 1}</label>
              <select id={`ssh-jump-${i}`} value={j.connection} onChange={(e) => setJump(i, { connection: e.target.value, host: undefined, port: undefined, host_key_sha256: undefined })}>
                <option value="">Choisir…</option>{optionsFor(j.connection).map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}</select>
              <RowButtons label={`le rebond ${i + 1}`} i={i} count={jumps.length} onMove={(d) => { jk.move(i, d); onUpdate({ jumps: fromJumps(move(jumps, i, d)) }); }} onRemove={() => { jk.remove(i); onUpdate({ jumps: fromJumps(jumps.filter((_, k) => k !== i)) }); }} />
            </div>
            {j.connection && hostless(j.connection) && hostFields(`ssh-jump-${i}`, `l'hôte du rebond ${i + 1}`, j, (p) => setJump(i, p))}
          </div>
        ))}
        <button type="button" className="btn" disabled={jumps.length >= MAX_JUMPS} onClick={() => { jk.add(); onUpdate({ jumps: fromJumps([...jumps, { connection: "" }]) }); }}>Ajouter un rebond</button>
      </fieldset>

      <fieldset className="field">
        <legend>Type de session</legend>
        <div className="field-inline"><input id="ssh-exec" type="radio" name="ssh-session" checked={session === "exec"} onChange={() => onUpdate({ session: "exec", commands: node.commands ?? [{ run: "" }] })} /><label htmlFor="ssh-exec">Commandes indépendantes (exec)</label></div>
        <div className="field-inline"><input id="ssh-shell" type="radio" name="ssh-session" checked={session === "shell"} onChange={() => onUpdate({ session: "shell", steps: node.steps?.length ? node.steps : [{ expect: [] }] })} /><label htmlFor="ssh-shell">Session interactive (shell)</label></div>
        <p className="hint">{session === "exec" ? "Chaque commande s'exécute sur son propre canal : elles ne partagent ni répertoire courant ni variables." : "Une seule session persistante pendant l'exécution de l'étape : on envoie du texte et on attend des motifs. Un prompt reconnu ne prouve pas qu'une commande a réussi ; le code retour est toujours vide."}</p>
      </fieldset>

      {session === "exec" && (
        <fieldset className="field">
          <legend>Commandes</legend>
          <div className="field-inline"><input id="ssh-dyn" type="checkbox" checked={dynamicList} onChange={(e) => onUpdate({ commands: e.target.checked ? "" : [{ run: "" }] })} /><label htmlFor="ssh-dyn">Liste produite par une étape précédente (expression)</label></div>
          {dynamicList ? (
            <div className="field"><label htmlFor="ssh-cmds-expr">Expression de la liste</label><input id="ssh-cmds-expr" value={String(node.commands ?? "")} placeholder="{{ steps.preparer.output.commandes }}" onChange={(e) => onUpdate({ commands: e.target.value })} /></div>
          ) : (
            <>
              {commands.map((c, i) => (
                <div key={ck.keys[i]} className="inline-row">
                  <label htmlFor={`ssh-cmd-${i}`} className="sr-only">Commande {i + 1}</label>
                  <input id={`ssh-cmd-${i}`} className="mono" spellCheck={false} value={c.run} placeholder="commande, avec {{ expressions }}" onChange={(e) => setCommand(i, { run: e.target.value })} />
                  <span className="field-inline"><input id={`ssh-cs-${i}`} type="checkbox" checked={!!c.sensitive} onChange={(e) => setCommand(i, { sensitive: e.target.checked })} /><label htmlFor={`ssh-cs-${i}`}>Sensible</label></span>
                  <span className="field-inline"><input id={`ssh-ci-${i}`} type="checkbox" checked={!!c.ignore_error} onChange={(e) => setCommand(i, { ignore_error: e.target.checked })} /><label htmlFor={`ssh-ci-${i}`}>Ignorer l'échec</label></span>
                  <RowButtons label={`la commande ${i + 1}`} i={i} count={commands.length} onMove={(d) => { ck.move(i, d); onUpdate({ commands: move(commands, i, d) }); }} onRemove={() => { ck.remove(i); onUpdate({ commands: commands.filter((_, k) => k !== i) }); }} />
                </div>
              ))}
              <button type="button" className="btn" disabled={commands.length >= 50} onClick={() => { ck.add(); onUpdate({ commands: [...commands, { run: "" }] }); }}>Ajouter une commande</button>
              <p className="hint">« Sensible » : le texte, la sortie et l'erreur de cette commande ne sont conservés ni dans les résultats, ni pour la branche d'erreur, ni dans les journaux (le code retour reste visible).</p>
            </>
          )}
        </fieldset>
      )}

      {session === "shell" && (
        <>
          <fieldset className="field">
            <legend>Terminal (PTY)</legend>
            <div className="field-inline"><input id="ssh-pty" type="checkbox" checked={!!pty.enabled} onChange={(e) => onUpdate({ pty: { ...pty, enabled: e.target.checked } })} /><label htmlFor="ssh-pty">Demander un pseudo-terminal</label></div>
            <p className="hint">Avec un PTY, le serveur fusionne la sortie standard et la sortie d'erreur en un seul flux (le résultat l'indique) et renvoie l'écho de ce qui est envoyé.</p>
            {pty.enabled && (
              <div className="grid-3">
                <div className="field"><label htmlFor="ssh-term">Type de terminal</label><input id="ssh-term" value={pty.term ?? "xterm"} maxLength={32} onChange={(e) => onUpdate({ pty: { ...pty, term: e.target.value } })} /></div>
                <div className="field"><label htmlFor="ssh-cols">Colonnes (20 à 500)</label><input id="ssh-cols" type="number" min={20} max={500} value={pty.cols ?? 80} onChange={(e) => onUpdate({ pty: { ...pty, cols: Number(e.target.value) } })} /></div>
                <div className="field"><label htmlFor="ssh-rows">Lignes (5 à 200)</label><input id="ssh-rows" type="number" min={5} max={200} value={pty.rows ?? 24} onChange={(e) => onUpdate({ pty: { ...pty, rows: Number(e.target.value) } })} /></div>
              </div>
            )}
          </fieldset>

          <fieldset className="field">
            <legend>Étapes de la session</legend>
            <p className="hint">Dans l'ordre. Une étape qui envoie du texte écarte d'abord ce qui a été reçu avant l'envoi : un ancien prompt ne peut pas satisfaire son attente. Une correspondance consomme le tampon jusqu'au motif. Priorité des motifs : échec, puis interactions, puis attente. Motifs : expressions régulières (500 caractères au plus, répétitions imbriquées refusées).</p>
            {steps.map((s, i) => (
              <StepEditor key={sk.keys[i]} i={i} step={s} count={steps.length} aliasNames={aliasNames} last={i === steps.length - 1}
                onChange={(patch) => setStep(i, patch)} onMove={(d) => { sk.move(i, d); onUpdate({ steps: move(steps, i, d) }); }} onRemove={() => { sk.remove(i); onUpdate({ steps: steps.filter((_, k) => k !== i) }); }} />
            ))}
            <button type="button" className="btn" disabled={steps.length >= 100} onClick={() => { sk.add(); onUpdate({ steps: [...steps, { expect: [] }] }); }}>Ajouter une étape</button>
          </fieldset>
        </>
      )}

      <fieldset className="field">
        <legend>Secrets référencés (facultatif)</legend>
        <p className="hint">Pour répondre à une demande de mot de passe : déclarez un alias vers un secret de l'espace de travail, puis choisissez-le dans une étape ou une interaction. Seul le nom du secret est enregistré dans le workflow.</p>
        {aliases.map(([a, s], i) => (
          <div key={ak.keys[i]} className="inline-row">
            <label htmlFor={`ssh-al-${i}`} className="sr-only">Alias {i + 1}</label>
            <input id={`ssh-al-${i}`} value={a} pattern="[A-Za-z0-9_-]*" maxLength={40} placeholder="alias" onChange={(e) => setAlias(a, e.target.value, s)} />
            <label htmlFor={`ssh-as-${i}`} className="sr-only">Secret de l'alias {i + 1}</label>
            <input id={`ssh-as-${i}`} list="ssh-secret-names" value={s} placeholder="nom du secret" onChange={(e) => setAlias(a, a, e.target.value)} />
            <button type="button" className="btn-link" aria-label={`Retirer l'alias ${a || i + 1}`} onClick={() => { ak.remove(i); onUpdate({ secrets: Object.fromEntries(aliases.filter((_, k) => k !== i)) }); }}>Retirer</button>
          </div>
        ))}
        <datalist id="ssh-secret-names">{secrets.map((s) => <option key={s.name} value={s.name} />)}</datalist>
        <button type="button" className="btn" disabled={aliases.length >= 10} onClick={() => { ak.add(); onUpdate({ secrets: { ...(node.secrets ?? {}), [`secret${aliases.length + 1}`]: "" } }); }}>Ajouter un secret</button>
      </fieldset>

      <details className="field">
        <summary>Délais, limites et erreurs</summary>
        <div className="field"><label htmlFor="ssh-to">Délai global (secondes, 1 à 600)</label><input id="ssh-to" type="number" min={1} max={600} value={node.timeout_seconds ?? 60} onChange={(e) => onUpdate({ timeout_seconds: Number(e.target.value) })} /></div>
        <div className="field"><label htmlFor="ssh-cto">Délai par commande ou attente (secondes, 1 à 300)</label><input id="ssh-cto" type="number" min={1} max={300} value={node.command_timeout_seconds ?? 30} onChange={(e) => onUpdate({ command_timeout_seconds: Number(e.target.value) })} /></div>
        <div className="field"><label htmlFor="ssh-max">Sortie conservée par flux et par commande (octets, jusqu'à 524288)</label><input id="ssh-max" type="number" min={1} max={524288} value={node.max_output_bytes ?? 65536} onChange={(e) => onUpdate({ max_output_bytes: Number(e.target.value) })} /></div>
        <div className="field-inline"><input id="ssh-soe" type="checkbox" checked={node.stop_on_error !== false} onChange={(e) => onUpdate({ stop_on_error: e.target.checked })} /><label htmlFor="ssh-soe">S'arrêter à la première erreur</label></div>
        <p className="hint">Sans arrêt, les éléments suivants s'exécutent ; l'étape échoue si au moins un élément non ignoré a échoué, avec tous les résultats disponibles.</p>
        <div className="field"><label htmlFor="ssh-rt">Nouvelles tentatives de connexion (0 à 3)</label><input id="ssh-rt" type="number" min={0} max={3} value={node.connect_retries ?? 0} onChange={(e) => onUpdate({ connect_retries: Number(e.target.value) })} />
          <p className="hint">Seulement pour l'établissement de la connexion, avant toute commande, et seulement pour une erreur de connexion ou un délai. Jamais pour un refus d'authentification, une clé serveur différente ou une commande déjà envoyée.</p></div>
      </details>

      <div className="field">
        <HelpTip>Sorties : <code>steps.{node.id}.output.results</code> (une entrée par commande ou étape : <code>stdout</code>, <code>stderr</code>, <code>exit_status</code> ou vide, <code>status</code>), <code>.status</code>, <code>.failed_step</code>, <code>.error_type</code>, <code>.uncertain</code>. La branche « En cas d'erreur » reçoit les résultats partiels. Après une interruption, une commande distante a pu se poursuivre : elle n'est jamais rejouée automatiquement.</HelpTip>
        <label htmlFor="ssh-mode">Mode</label>
        <select id="ssh-mode" value={node.mode ?? "test"} onChange={(e) => onUpdate({ mode: e.target.value as "test" | "reel" })}>
          <option value="test">Test : aucune connexion, aucun nom résolu, aucun secret lu (sortie simulée)</option>
          <option value="reel">Réel : les commandes sont exécutées sur le serveur</option></select>
      </div>

      {(node.mode ?? "test") === "test" && (
        <div className="field">
          <label htmlFor="ssh-mocks">Résultats simulés (JSON, facultatif)</label>
          <textarea id="ssh-mocks" rows={4} spellCheck={false} className="mono" value={mocksText} aria-invalid={mocksError ? true : undefined} aria-describedby="ssh-mocks-h"
            onChange={(e) => {
              setMocksText(e.target.value);
              if (!e.target.value.trim()) { setMocksError(""); onUpdate({ mocks: undefined }); return; }
              try {
                const parsed: unknown = JSON.parse(e.target.value);
                if (!Array.isArray(parsed)) throw new Error("liste attendue");
                setMocksError(""); onUpdate({ mocks: parsed });
              } catch { setMocksError("JSON invalide : une liste d'objets {id, stdout, stderr, exit_status} est attendue."); }
            }} />
          <p className="hint" id="ssh-mocks-h">Ex. <code>[{"{"}"id": "c1", "stdout": "ok", "exit_status": 0{"}"}]</code> : l'id est celui de la commande (c1, c2…) ou de l'étape (s1, s2…). Un statut non nul simule un échec pour essayer la branche d'erreur.</p>
          {mocksError && <p role="alert" className="error">{mocksError}</p>}
        </div>
      )}
    </>
  );
}
