import { useMutation } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api, type ApiError } from "../api";
import { HelpTip } from "../components/HelpTip";
import { useApp } from "../context";
import type { ControlPrefill, ControlSpec } from "./controls";
import { FormIcon, IconLabel, type FormIconName } from "./FormIcon";
import type { DataSource } from "./types";

type Kind = "options" | "prefill";
type Reading = DataSource | ControlPrefill;
interface Spot { key: keyof DataSource; label: string; icon: FormIconName; placeholder: string; help: string; kinds: Kind[] }

/** Where to read in the reply: one short label, an icon and an example; the explanation is in the « ? » balloon, not on the page. */
const SPOTS: Spot[] = [
  { key: "items_path", label: "Liste", icon: "list2", placeholder: "data.rows", help: "Où se trouve la liste dans la réponse (ex. data.rows). Vide : la réponse est elle-même la liste.", kinds: ["options"] },
  { key: "value_path", label: "Valeur", icon: "tag", placeholder: "id", help: "Champ de chaque élément qui donne la valeur transmise au workflow.", kinds: ["options"] },
  { key: "label_path", label: "Libellé", icon: "paragraph", placeholder: "name", help: "Champ affiché à la personne. Vide : la valeur.", kinds: ["options"] },
  { key: "group_path", label: "Groupe", icon: "layers", placeholder: "region", help: "Facultatif : les éléments qui ont la même valeur sont rangés sous ce titre.", kinds: ["options"] },
  { key: "disabled_path", label: "Grisée si", icon: "eyeOff", placeholder: "off", help: "Facultatif : champ vrai/faux ; vrai = option visible mais non sélectionnable.", kinds: ["options"] },
  { key: "value_path", label: "Où lire", icon: "braces", placeholder: "order.lines", help: "Où se trouve la valeur dans la réponse (ex. order.lines). Vide : toute la réponse.", kinds: ["prefill"] },
];
const toList = (v: string): string[] => v.split(",").map((x) => x.trim()).filter(Boolean).slice(0, 5);

function Labelled({ id, icon, label, help, children, wide }: { id: string; icon: FormIconName; label: string; help: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`field sv-f${wide ? " wide" : ""}`}>
      <div className="sv-h"><label htmlFor={id}><FormIcon name={icon} size={15} /> {label}</label><HelpTip label={`Aide : ${label}`}>{help}</HelpTip></div>
      {children}
    </div>
  );
}

/** The reading of a web service by a FORM field (not by the control, which is reused by forms that each read their own service). */
export function ServiceReading({ idPrefix, kind, value, connections, onChange }: { idPrefix: string; kind: Kind; value: Reading; connections: string[]; onChange: (v: Reading) => void }) {
  const rec = value as unknown as Record<string, string>;
  const put = (key: string, text: string, optional: boolean) => {
    const next = { ...rec, [key]: text } as Record<string, unknown>;
    if (text === "" && optional) delete next[key];
    onChange(next as unknown as Reading);
  };
  const id = (k: string) => `${idPrefix}-${k}`;
  return (
    <div className="sv-form">
      <datalist id={id("conns")}>{connections.map((c) => <option key={c} value={c} />)}</datalist>
      <section className="sv-card" aria-label="Appel">
        <h3><FormIcon name="link" size={16} /> Appel</h3>
        <div className="sv-grid">
          <Labelled id={id("connection")} icon="lock" label="Connexion" help="Connexion HTTP de l'espace : le serveur appelle le service avec elle (aucun secret côté navigateur).">
            <input id={id("connection")} list={id("conns")} value={rec.connection ?? ""} placeholder="demo-api" maxLength={300} aria-invalid={!rec.connection || undefined} onChange={(e) => put("connection", e.target.value, false)} />
          </Labelled>
          <Labelled id={id("params")} icon="braces" label="Champs lus" help="Noms des champs du formulaire utilisés dans le chemin, séparés par des virgules (ex. recherche, marque).">
            <input id={id("params")} defaultValue={(value.params ?? []).join(", ")} key={(value.params ?? []).join(",")} placeholder="recherche"
              onBlur={(e) => { const params = toList(e.target.value); const { params: _old, ...rest } = value; onChange((params.length ? { ...rest, params } : rest) as Reading); }} />
          </Labelled>
          <Labelled id={id("path")} icon="source" label="Chemin" wide help="Commence par « / ». {champ} est remplacé par la valeur d'un autre champ du formulaire : c'est ce qui permet les listes en cascade.">
            <input id={id("path")} value={rec.path ?? ""} placeholder="/marques?q={recherche}" maxLength={300} aria-invalid={!rec.path?.startsWith("/") || undefined} onChange={(e) => put("path", e.target.value, false)} />
          </Labelled>
        </div>
      </section>
      <section className="sv-card" aria-label="Dans la réponse">
        <h3><FormIcon name="search" size={16} /> Dans la réponse</h3>
        <div className="sv-grid">
          {SPOTS.filter((s) => s.kinds.includes(kind)).map((s) => (
            <Labelled key={`${kind}-${s.key}`} id={id(s.key)} icon={s.icon} label={s.label} help={s.help} wide={kind === "prefill"}>
              <input id={id(s.key)} value={rec[s.key] ?? ""} placeholder={s.placeholder} maxLength={300} onChange={(e) => put(s.key, e.target.value, true)} />
            </Labelled>
          ))}
        </div>
      </section>
    </div>
  );
}

/** « Essai » : the server reads a sample reply with this very reading (no call to the service) and says what the control would get. */
export function ServiceTry({ kind, spec, config }: { kind: Kind; spec: ControlSpec; config: Reading }) {
  const { workspace } = useApp();
  const [sample, setSample] = useState("");
  const [bad, setBad] = useState("");
  const run = useMutation<{ options?: { value: string; label?: string; group?: string; disabled?: boolean }[]; value?: unknown; problem?: string; problems?: Record<string, string> }, ApiError>({
    mutationFn: () => {
      let parsed: unknown;
      try { parsed = JSON.parse(sample); } catch { return Promise.reject(new Error("Réponse d'exemple : JSON invalide.")); }
      return api(`/workspaces/${workspace.slug}/control-library/source-preview/`, { json: { kind, spec, config, sample: parsed } });
    },
    onMutate: () => setBad(""),
    onError: (e) => setBad(e.message),
  });
  const r = run.data;
  const problem = bad || r?.problem || (r?.problems ? Object.values(r.problems)[0] : "");
  return (
    <section className="sv-card sv-try" aria-label="Essai">
      <h3><FormIcon name="play" size={16} /> Essai <HelpTip label="Aide : essai">Collez une réponse JSON du service : le serveur la lit avec ces réglages, sans appeler le service, et montre ce que le champ obtiendrait.</HelpTip></h3>
      <div className="field"><label htmlFor={`try-${kind}`} className="sr-only">Réponse d'exemple du service (JSON)</label>
        <textarea id={`try-${kind}`} className="mono" rows={7} value={sample} onChange={(e) => setSample(e.target.value)} placeholder={'{ "data": { "rows": [ { "id": "ford", "name": "Ford" } ] } }'} /></div>
      <button type="button" onClick={() => run.mutate()} disabled={!sample.trim() || run.isPending}><IconLabel icon="play">Lire cette réponse</IconLabel></button>
      {problem && <p role="alert" className="field-error">{problem}</p>}
      {!problem && r?.options && (
        <ul aria-label="Options lues" className="sv-result">
          {r.options.slice(0, 8).map((o) => <li key={o.value}><FormIcon name={o.disabled ? "eyeOff" : "check"} size={14} />{o.group ? `${o.group} › ` : ""}{o.label || o.value}{o.disabled ? " (grisée)" : ""}</li>)}
          {r.options.length > 8 && <li>… et {r.options.length - 8} autre(s)</li>}
          {r.options.length === 0 && <li>Aucune option dans cette réponse.</li>}
        </ul>
      )}
      {!problem && r && "value" in r && <pre className="ctl-json" aria-label="Valeur lue">{JSON.stringify(r.value ?? null, null, 2)}</pre>}
    </section>
  );
}
