import { useEffect, useRef, useState } from "react";
import { api, type ApiError, type Execution, type StepRun } from "../../api";
import type { CodeEditorHandle } from "../../components/CodeEditor";
import { LazyCodeEditor as CodeEditor } from "../../components/LazyCodeEditor";
import { ReadRegion } from "../../components/ReadRegion";
import { HelpTip } from "../../components/HelpTip";
import { Tabs } from "../../components/Tabs";
import { useApp } from "../../context";
import { useWsQuery } from "../../lib";
import { AnsibleTab, ansibleResult, hasAnsibleLog } from "../AnsibleLog";

type ConsoleTab = "essai" | "journal" | "resultat";
type EditorLanguage = "python" | "javascript" | "typescript" | "yaml" | "shell" | "powershell";

const PYTHON_FILE = { language: "python" as EditorLanguage, name: "action.py", label: "Python" };
const FILE: Record<string, { language: EditorLanguage; name: string; label: string }> = {
  python: PYTHON_FILE,
  node: { language: "javascript", name: "action.js", label: "Node.js" },
  typescript: { language: "typescript", name: "action.ts", label: "TypeScript" },
  shell: { language: "shell", name: "action.sh", label: "Shell" },
  powershell: { language: "powershell", name: "action.ps1", label: "PowerShell" },
  ansible: { language: "yaml", name: "playbook.yml", label: "Ansible" },
};

/** Des valeurs d'essai plausibles tirées du schéma d'entrées : la valeur par défaut, sinon le « zéro » du type. */
export function sampleFromSchema(schemaText: string): string {
  let props: Record<string, { type?: string; default?: unknown }> = {};
  try {
    const s = JSON.parse(schemaText) as { properties?: typeof props };
    props = s.properties ?? {};
  } catch {
    return "{}";
  }
  const zero = (t?: string) => (t === "integer" || t === "number" ? 0 : t === "boolean" ? false : t === "array" ? [] : t === "object" ? {} : "");
  return JSON.stringify(Object.fromEntries(Object.entries(props).map(([k, p]) => [k, p.default ?? zero(p.type)])), null, 2);
}

/** Les résultats d'un essai Ansible se lisent comme ceux d'une étape : on les présente avec le même composant. */
function asStep(ex: Execution): StepRun {
  return {
    node_id: "essai",
    name: "Essai",
    node_type: "action",
    status: ex.status,
    status_label: ex.status_label,
    attempt: ex.attempt_count,
    output: ex.result,
    error: ex.error,
    available_at: null,
    started_at: ex.started_at ?? ex.created_at,
    finished_at: ex.finished_at,
    approval_id: null,
    execution_id: ex.id,
    child_run_ids: [],
  };
}

interface Props {
  runtime: string;
  source: string;
  onSource: (v: string) => void;
  slug: string;
  inputSchema: string;
  /** Le brouillon complet de la version (champs de l'API), ou la raison pour laquelle il n'est pas utilisable. */
  buildDraft: () => { draft: Record<string, unknown> } | { error: string };
  onBack: () => void;
  /** Ailleurs que dans une version d'action (l'étape « Playbook » d'un workflow) : l'adresse de l'essai, ce qui lui est envoyé, les valeurs d'essai et les connexions de départ. */
  trial?: {
    path: string;
    body: (draft: Record<string, unknown>, inputs: unknown, connections: Record<string, string>) => unknown;
    sample?: string;
    connections?: Record<string, string>;
    backLabel?: string;
  };
}

/** Éditeur avancé du code d'une version : colorisation, complétion, valeurs d'essai et essai dans le bac à sable (le même que pour une exécution réelle), sans rien publier. */
export function ActionIde({ runtime, source, onSource, slug, inputSchema, buildDraft, onBack, trial }: Props) {
  const { workspace } = useApp();
  const file = FILE[runtime] ?? PYTHON_FILE;
  const editor = useRef<CodeEditorHandle>(null);
  const [cursor, setCursor] = useState({ line: 1, col: 1, lines: source.split("\n").length });
  const [tab, setTab] = useState<ConsoleTab>("essai");
  const [panel, setPanel] = useState(true); // le panneau d'essai (à droite) se replie pour donner toute la largeur au code
  const [sample, setSample] = useState(() => trial?.sample ?? sampleFromSchema(inputSchema));
  const [run, setRun] = useState<{ state: "idle" | "running" | "done"; exec?: Execution; error?: string }>({ state: "idle" });
  const [conns, setConns] = useState<Record<string, string>>(trial?.connections ?? {});
  const available = useWsQuery<{ results: { name: string }[] }>("connections", "/connections/");
  const built0 = buildDraft();
  const slots =
    "draft" in built0 && Array.isArray(built0.draft.secrets_required)
      ? (built0.draft.secrets_required as { slot?: string }[]).map((x) => x.slot ?? "").filter(Boolean)
      : [];
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const test = async () => {
    const built = buildDraft();
    if ("error" in built) {
      setRun({ state: "done", error: built.error });
      setTab("resultat");
      return;
    }
    let inputs: unknown;
    try {
      inputs = JSON.parse(sample || "{}");
    } catch {
      setRun({ state: "done", error: "Les valeurs d'essai ne sont pas du JSON valide." });
      setTab("resultat");
      return;
    }
    setRun({ state: "running" });
    setTab("journal");
    setPanel(true); // on veut voir le journal
    try {
      const chosen = Object.fromEntries(Object.entries(conns).filter(([, v]) => v));
      const start = await api<{ execution_id: string }>(`/workspaces/${workspace.slug}${trial?.path ?? `/actions/${slug}/trial/`}`, {
        json: trial ? trial.body(built.draft, inputs, chosen) : { version: built.draft, inputs, connections: chosen },
      });
      const end = Date.now() + 300_000;
      while (alive.current && Date.now() < end) {
        const ex = await api<Execution>(`/workspaces/${workspace.slug}/executions/${start.execution_id}/`);
        if (["succeeded", "failed", "cancelled"].includes(ex.status)) {
          setRun({ state: "done", exec: ex });
          setTab(ex.status === "succeeded" ? "resultat" : "journal");
          return;
        }
        setRun({ state: "running", exec: ex });
        await new Promise((r) => setTimeout(r, 700));
      }
      if (alive.current) setRun({ state: "done", error: "L'essai n'a pas terminé dans le délai imparti." });
    } catch (e) {
      const err = e as ApiError;
      const fields =
        err.details && typeof err.details === "object"
          ? Object.entries(err.details as Record<string, unknown>)
              .map(([k, v]) => `${k} : ${Array.isArray(v) ? v.join(" ") : String(v)}`)
              .join(" · ")
          : "";
      setRun({ state: "done", error: fields || err.message });
      setTab("resultat");
    }
  };

  const logs =
    run.exec?.attempts
      ?.map((a) => a.logs)
      .filter(Boolean)
      .join("\n") ?? "";
  const status =
    run.state === "running"
      ? { tone: "warn", text: "Essai en cours…" }
      : run.error || (run.exec && run.exec.status !== "succeeded")
        ? { tone: "bad", text: "✖ Dernier essai en échec" }
        : run.exec
          ? { tone: "ok", text: "✔ Dernier essai réussi" }
          : { tone: "muted", text: "Pas encore essayé" };
  const step = run.exec ? asStep(run.exec) : null;

  return (
    <section
      aria-label="Éditeur avancé"
      className="script-dialog sd2 sd2-embedded"
      onKeyDown={(e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && run.state !== "running") {
          e.preventDefault();
          void test();
        }
      }}
    >
      <header className="sd-head sd2-head">
        <div className="sd2-title">
          <h3>Éditeur avancé — {file.label}</h3>
          <p className="hint">
            Essai dans un conteneur isolé, sans rien publier{" "}
            <span className={`adm-badge adm-badge-${status.tone}`} role="status">
              {status.text}
            </span>
          </p>
        </div>
        <div className="sd-tools">
          <button type="button" className="primary" onClick={() => void test()} disabled={run.state === "running"} title="Ctrl + Entrée">
            {run.state === "running" ? "Essai en cours…" : "▶ Essayer dans le bac à sable"}
          </button>
          <button type="button" onClick={onBack}>
            {trial?.backLabel ?? "Retour au formulaire"}
          </button>
        </div>
      </header>
      <div className={`sd2-split${panel ? "" : " panel-closed"}`}>
        <section className="sd2-left" aria-label="Code">
          <div className="sd2-bar">
            <span className="sd2-file">
              <span aria-hidden="true">{file.name.split(".").pop()?.toUpperCase()}</span> {file.name}
            </span>
            <button type="button" className="sd2-toggle" aria-expanded={panel} aria-controls="ide-panel" onClick={() => setPanel((v) => !v)}>
              {panel ? "Masquer le panneau d'essai ▸" : "◂ Afficher le panneau d'essai"}
            </button>
            <span className="sd2-pos" aria-live="off">
              Ligne {cursor.line}, col. {cursor.col} · {cursor.lines} ligne(s)
            </span>
          </div>
          <CodeEditor
            ref={editor}
            fill
            value={source}
            onChange={onSource}
            language={file.language}
            onCursor={setCursor}
            ariaLabel={`Code de l'action (${file.label})`}
            minHeight="200px"
          />
        </section>
        <aside id="ide-panel" className="sd2-right" aria-label="Essai" hidden={!panel}>
          <Tabs
            label="Console d'essai"
            active={tab}
            onChange={(id) => setTab(id as ConsoleTab)}
            tabs={[
              {
                id: "essai",
                label: "Valeurs d'essai",
                content: (
                  <div className="sd-pane sd-samples">
                    <div className="field">
                      <label htmlFor="ide-si">Entrées de l'essai (JSON)</label>
                      <textarea id="ide-si" rows={10} className="mono" value={sample} onChange={(e) => setSample(e.target.value)} spellCheck={false} />
                    </div>
                    {slots.map((slot) => (
                      <div className="field" key={slot}>
                        <label htmlFor={`ide-c-${slot}`}>Connexion pour « {slot} »</label>
                        <select id={`ide-c-${slot}`} value={conns[slot] ?? ""} onChange={(e) => setConns((cur) => ({ ...cur, [slot]: e.target.value }))}>
                          <option value="">— aucune —</option>
                          {(available.data?.results ?? []).map((c) => (
                            <option key={c.name} value={c.name}>
                              {c.name}
                            </option>
                          ))}
                        </select>
                      </div>
                    ))}
                    <p className="help-row">
                      <HelpTip caption="À savoir">
                        L'essai joue le code tel qu'il est écrit ici, avec les paramètres du formulaire (réseau, étiquettes de runner, rôles Ansible figés comme
                        à la publication), dans l'environnement de recette. Il ne crée aucune version.
                        {runtime === "ansible" ? " Un playbook ne part que sur un runner qui porte les étiquettes déclarées." : ""}
                      </HelpTip>
                    </p>
                  </div>
                ),
              },
              {
                id: "journal",
                label: "Journal",
                content: (
                  <div className="sd-pane">
                    <ReadRegion role="log" label="Journal de l'essai">
                      {run.state === "idle" ? "Lancez un essai pour voir le journal." : logs || (run.state === "running" ? "En cours…" : "(journal vide)")}
                    </ReadRegion>
                  </div>
                ),
              },
              {
                id: "resultat",
                label: "Résultat",
                content: (
                  <div className="sd-pane" role="status" aria-live="polite">
                    {run.state === "idle" && <p>Aucun essai lancé.</p>}
                    {run.error && <p className="field-error">Erreur : {run.error}</p>}
                    {run.exec?.status === "succeeded" && (
                      <p>
                        <strong>✔ L'essai s'est terminé avec succès.</strong>
                      </p>
                    )}
                    {run.exec && run.exec.status !== "succeeded" && run.exec.error && (
                      <p className="field-error">
                        <strong>✖ {run.exec.error.message}</strong> {run.exec.error.next_step}
                      </p>
                    )}
                    {step && hasAnsibleLog(step) && ansibleResult(step) && <AnsibleTab steps={[step]} />}
                    {run.exec?.status === "succeeded" && <ReadRegion label="Résultat de l'essai">{JSON.stringify(run.exec.result, null, 2)}</ReadRegion>}
                  </div>
                ),
              },
            ]}
          />
        </aside>
      </div>
    </section>
  );
}
