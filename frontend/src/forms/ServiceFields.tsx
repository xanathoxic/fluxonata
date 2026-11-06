import { useMutation } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api, type ApiError } from "../api";
import { HelpTip } from "../components/HelpTip";
import { useApp } from "../context";
import type { ControlPrefill, ControlSpec } from "./controls";
import { FormIcon, IconLabel, type FormIconName } from "./FormIcon";
import { choicesToText, textToChoices, withChooser } from "./serviceParams";
import type { DataSource } from "./types";

type Kind = "options" | "prefill";
type Reading = DataSource | ControlPrefill;
interface Spot { key: keyof DataSource; label: string; icon: FormIconName; placeholder: string; help: string; kinds: Kind[] }

/** Where to read in the reply: one short label, an icon and an example; the explanation is in the « ? » balloon, not on the page. */
const SPOTS: Spot[] = [
  { key: "items_path", label: "Liste", icon: "list2", placeholder: "data.rows", help: "Où se trouve la liste dans la réponse (ex. data.rows). Vide : la réponse est elle-même la liste. {champ} est remplacé par la valeur d'un autre champ du formulaire (ex. groups.{groupe}) : un point dans cette valeur ne change pas de niveau.", kinds: ["options"] },
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

/** How the reply becomes options when it is not a tidy list: an object whose keys are the choices, a filter, a computed label, a sort. */
export function ShapingFields({ idPrefix, value, onChange }: { idPrefix: string; value: DataSource; onChange: (v: DataSource) => void }) {
  const put = (patch: Partial<DataSource>) => {
    const next = { ...value, ...patch } as Record<string, unknown>;
    for (const k of Object.keys(patch)) if (next[k] === "" || next[k] === "list") delete next[k];
    onChange(next as unknown as DataSource);
  };
  const id = (k: string) => `${idPrefix}-${k}`;
  return (
    <>
      <Labelled id={id("items_as")} icon="braces" label="Forme" help="« Liste » : la réponse contient une liste d'éléments. « Objet » : chaque clé de l'objet est un choix (ex. des groupes) ; chaque élément porte alors « key » (la clé) et « value » (son contenu). La valeur et le libellé sont la clé tant que vous n'en indiquez pas d'autre.">
        <select id={id("items_as")} value={value.items_as ?? "list"} onChange={(e) => put({ items_as: e.target.value as DataSource["items_as"] })}>
          <option value="list">Liste</option>
          <option value="entries">Objet (clés)</option>
        </select>
      </Labelled>
      <Labelled id={id("sort")} icon="sort" label="Tri" help="Trie les options par libellé avant de garder les 200 premières.">
        <select id={id("sort")} value={value.sort ?? ""} onChange={(e) => put({ sort: e.target.value as DataSource["sort"] })}>
          <option value="">Ordre reçu</option>
          <option value="label">Libellé A → Z</option>
          <option value="label_desc">Libellé Z → A</option>
        </select>
      </Labelled>
      <Labelled id={id("where")} icon="search" label="Garder si" wide help="Facultatif. Expression sur « item » ; seuls les éléments pour lesquels elle est vraie sont gardés (ex. starts_with(item.key, 'group:') ou item.actif). Un élément qu'elle ne peut pas lire est écarté.">
        <input id={id("where")} value={value.where ?? ""} placeholder="starts_with(item.key, 'group:')" maxLength={300} onChange={(e) => put({ where: e.target.value })} />
      </Labelled>
      <Labelled id={id("label_expr")} icon="paragraph" label="Libellé calculé" wide help="Facultatif. Expression sur « item » qui construit le libellé (ex. item.prenom + ' ' + upper(item.nom)). Si elle échoue pour un élément, le libellé habituel est utilisé.">
        <input id={id("label_expr")} value={value.label_expr ?? ""} placeholder="item.prenom + ' ' + upper(item.nom)" maxLength={300} onChange={(e) => put({ label_expr: e.target.value })} />
      </Labelled>
    </>
  );
}

/** The « Mise en forme » section: the shape of the reply and what is done with its items. */
export function ShapingCard({ idPrefix, value, onChange }: { idPrefix: string; value: DataSource; onChange: (v: DataSource) => void }) {
  return (
    <section className="sv-card" aria-label="Mise en forme">
      <h3><FormIcon name="sort" size={16} /> Mise en forme</h3>
      <div className="sv-grid"><ShapingFields idPrefix={idPrefix} value={value} onChange={onChange} /></div>
    </section>
  );
}

const putter = (value: Reading, onChange: (v: Reading) => void) => (key: string, text: string, optional: boolean) => {
  const next = { ...(value as unknown as Record<string, unknown>), [key]: text };
  if (text === "" && optional) delete next[key];
  onChange(next as unknown as Reading);
};

/** The « Appel » section: which connection, which path, which fields of the form the path reads. With `choices`, the connection can depend
 * on the value of a field of the form, among a CLOSED list written here: the person filling the form never names a connection. */
export function CallCard({ idPrefix, value, connections, onChange, choices = false }: { idPrefix: string; value: Reading; connections: string[]; onChange: (v: Reading) => void; choices?: boolean }) {
  const rec = value as unknown as Record<string, string>;
  const source = value as DataSource;
  const put = putter(value, onChange);
  const id = (k: string) => `${idPrefix}-${k}`;
  const byField = choices && source.connection_from !== undefined;
  const [lines, setLines] = useState(() => choicesToText(source.connections));
  const toggle = (on: boolean) => {
    const { connection_from: _f, connections: _c, ...rest } = source;
    onChange((on ? { ...rest, connection: "", connection_from: "", connections: {} } : { ...rest, connection: connections[0] ?? "" }) as Reading);
    if (!on) setLines("");
  };
  return (
    <section className="sv-card" aria-label="Appel">
      <datalist id={id("conns")}>{connections.map((c) => <option key={c} value={c} />)}</datalist>
      <h3><FormIcon name="link" size={16} /> Appel</h3>
      <div className="sv-grid">
        {!byField && (
          <Labelled id={id("connection")} icon="lock" label="Connexion" help="Connexion HTTP de l'espace : le serveur appelle le service avec elle (aucun secret côté navigateur).">
            <input id={id("connection")} list={id("conns")} value={rec.connection ?? ""} placeholder="demo-api" maxLength={300} aria-invalid={!rec.connection || undefined} onChange={(e) => put("connection", e.target.value, false)} />
          </Labelled>
        )}
        <Labelled id={id("params")} icon="braces" label="Champs lus" help="Noms des champs du formulaire utilisés dans le chemin ou dans la liste, séparés par des virgules (ex. recherche, marque).">
          <input id={id("params")} defaultValue={(value.params ?? []).join(", ")} key={(value.params ?? []).join(",")} placeholder="recherche"
            onBlur={(e) => { const params = toList(e.target.value); const { params: _old, ...rest } = value; onChange((params.length ? { ...rest, params } : rest) as Reading); }} />
        </Labelled>
        <Labelled id={id("path")} icon="source" label="Chemin" wide help="Commence par « / ». {champ} est remplacé par la valeur d'un autre champ du formulaire (listes en cascade), ex. /acl/{champ1}/org/{champ2} : déclarez chaque champ dans « Champs lus ». La liste attend que les champs placés avant le « ? » soient renseignés ; après le « ? » ils peuvent rester vides.">
          <input id={id("path")} value={rec.path ?? ""} placeholder="/marques?q={recherche}" maxLength={300} aria-invalid={!rec.path?.startsWith("/") || undefined} onChange={(e) => put("path", e.target.value, false)} />
        </Labelled>
        {choices && (
          <div className="field field-inline sv-f wide">
            <input id={id("by-field")} type="checkbox" checked={byField} onChange={(e) => toggle(e.target.checked)} />
            <label htmlFor={id("by-field")}>La connexion dépend d'un champ du formulaire</label>
            <HelpTip label="Aide : connexion selon un champ">Pour lire un autre service selon un choix fait plus haut (ex. un environnement). Vous fixez la liste des connexions permises : la personne ne peut choisir que parmi elles, toute autre valeur est refusée.</HelpTip>
          </div>
        )}
        {byField && (
          <>
            <Labelled id={id("from")} icon="braces" label="Champ qui choisit" help="Clé d'un champ placé avant dans le formulaire. Sa valeur choisit la connexion dans la liste ci-dessous. Elle est ajoutée aux champs lus (elle doit être envoyée au serveur).">
              <input id={id("from")} value={source.connection_from ?? ""} placeholder="environnement" maxLength={64} aria-invalid={!source.connection_from || undefined}
                onChange={(e) => onChange({ ...source, connection_from: e.target.value.trim() } as Reading)}
                onBlur={() => onChange(withChooser(source) as Reading)} />
            </Labelled>
            <Labelled id={id("allowed")} icon="lock" label="Connexions permises" wide help="Une ligne par choix : valeur du champ | nom de la connexion (10 au plus). Les valeurs sont celles des options du champ qui choisit. Une valeur qui n'est pas dans la liste est refusée : aucun appel n'est fait.">
              <textarea id={id("allowed")} rows={4} className="mono" value={lines} placeholder={"prod | tailnet-prod\ntest | tailnet-test"} aria-invalid={Object.keys(source.connections ?? {}).length === 0 || undefined}
                onChange={(e) => { setLines(e.target.value); onChange({ ...source, connections: textToChoices(e.target.value) } as Reading); }} />
            </Labelled>
          </>
        )}
      </div>
    </section>
  );
}

/** The « Dans la réponse » section: where each part is read in the reply. */
export function SpotsCard({ idPrefix, kind, value, onChange }: { idPrefix: string; kind: Kind; value: Reading; onChange: (v: Reading) => void }) {
  const rec = value as unknown as Record<string, string>;
  const put = putter(value, onChange);
  const id = (k: string) => `${idPrefix}-${k}`;
  return (
    <section className="sv-card" aria-label="Dans la réponse">
      <h3><FormIcon name="search" size={16} /> Dans la réponse</h3>
      <div className="sv-grid">
        {SPOTS.filter((s) => s.kinds.includes(kind)).map((s) => (
          <Labelled key={`${kind}-${s.key}`} id={id(s.key)} icon={s.icon} label={s.label} help={s.help} wide={kind === "prefill" || s.key === "items_path"}>
            <input id={id(s.key)} value={rec[s.key] ?? ""} placeholder={s.placeholder} maxLength={300} onChange={(e) => put(s.key, e.target.value, true)} />
          </Labelled>
        ))}
      </div>
    </section>
  );
}

/** The reading of a web service by a FORM field (not by the control, which is reused by forms that each read their own service). */
export function ServiceReading({ idPrefix, kind, value, connections, onChange }: { idPrefix: string; kind: Kind; value: Reading; connections: string[]; onChange: (v: Reading) => void }) {
  return (
    <div className="sv-form">
      <CallCard idPrefix={idPrefix} value={value} connections={connections} onChange={onChange} choices={kind === "options"} />
      <SpotsCard idPrefix={idPrefix} kind={kind} value={value} onChange={onChange} />
      {kind === "options" && <ShapingCard idPrefix={idPrefix} value={value as DataSource} onChange={(v) => onChange(v)} />}
    </div>
  );
}

/** « Essai » : the server reads a sample reply with this very reading (no call to the service) and says what the control would get. */
export function ServiceTry({ kind, spec, config }: { kind: Kind; spec: ControlSpec; config: Reading }) {
  const { workspace } = useApp();
  const [sample, setSample] = useState("");
  const [bad, setBad] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const wanted = kind === "options" ? [...new Set([...(String((config as DataSource).items_path ?? "").matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g))].map((m) => m[1] ?? "").filter(Boolean))] : [];
  const run = useMutation<{ options?: { value: string; label?: string; group?: string; disabled?: boolean }[]; value?: unknown; problem?: string; problems?: Record<string, string> }, ApiError>({
    mutationFn: () => {
      let parsed: unknown;
      try { parsed = JSON.parse(sample); } catch { return Promise.reject(new Error("Réponse d'exemple : JSON invalide.")); }
      return api(`/workspaces/${workspace.slug}/control-library/source-preview/`, { json: { kind, spec, config, sample: parsed, params: Object.fromEntries(wanted.map((k) => [k, values[k] ?? ""])) } });
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
      {wanted.map((k) => (
        <div className="field" key={k}><label htmlFor={`try-${kind}-${k}`}>Valeur d'essai de {`{${k}}`}</label>
          <input id={`try-${kind}-${k}`} value={values[k] ?? ""} onChange={(e) => setValues({ ...values, [k]: e.target.value })} placeholder="group:dev" /></div>
      ))}
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
