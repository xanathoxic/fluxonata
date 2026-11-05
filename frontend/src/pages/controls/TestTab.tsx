import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../../api";
import { HelpTip } from "../../components/HelpTip";
import { useApp } from "../../context";
import { ControlInput } from "../../forms/ControlInput";
import { detailsOf, processControl, type ControlItem, type ControlSpec } from "../../forms/controls";
import { exampleOptions, exampleValue } from "../../forms/controlDocs";
import type { LookupFn } from "../../forms/DefinitionForm";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import { isBlock, type FormField } from "../../forms/types";

interface Reply { options?: ControlItem[]; value?: unknown; problem?: string; problems?: Record<string, string>; sent?: number }
const IDENTITY = { connection: "essai", path: "/", value_path: "value", label_path: "label", group_path: "group", disabled_path: "disabled" };

const withData = (o: ControlItem[]) => o.filter((x) => x.data).length;
const pretty = (v: unknown) => JSON.stringify(v, null, 2);

/** « Test » : what a form (or a service behind it) can send to the control, tried against the draft. The options go through the same reading as in a
 * form (the server keeps the valid ones: value, label, group, disabled), the value through the control's own checks (the server's `process`);
 * what is accepted fills the control below, with its children. Nothing is saved and no service is called. */
export function TestTab({ name, slug, spec: draft }: { name: string; slug: string; spec: ControlSpec }) {
  const { workspace } = useApp();
  const listy = draft.mode === "items" || draft.mode === "choice";
  const dynamic = listy; // options can always be tried here, whether or not the control lets forms supply them (that is a setting of the control)
  const spec = useMemo<ControlSpec>(() => { if (!listy) return draft; const { items_editable: _e, ...rest } = draft; return { ...rest, dynamic: true }; }, [draft, listy]);
  const modelKeys = detailsOf(spec).filter((p) => !isBlock(p.type) && !p.compute).map((p) => p.key);
  const [optText, setOptText] = useState("");
  const [valText, setValText] = useState("");
  const [options, setOptions] = useState<{ items: ControlItem[]; sent: number } | null>(null);
  const [value, setValue] = useState<unknown>(undefined);
  const [notice, setNotice] = useState<{ zone: "options" | "value"; ok: boolean; text: string } | null>(null);

  const post = useMutation<Reply, Error, { kind: "options" | "prefill"; text: string }>({
    mutationFn: ({ kind, text }) => {
      let sample: unknown;
      try { sample = JSON.parse(text); } catch { return Promise.reject(new Error("JSON invalide.")); }
      return api<Reply>(`/workspaces/${workspace.slug}/control-library/source-preview/`, { json: { kind, spec, config: kind === "options" ? IDENTITY : { connection: "essai", path: "/" }, sample } })
        .then((r) => ({ ...r, sent: Array.isArray(sample) ? sample.length : 0 }));
    },
  });
  const unknownKeys = (text: string): string[] => {
    try {
      const v: unknown = JSON.parse(text);
      const keys = new Set((Array.isArray(v) ? v : []).flatMap((o) => (o && typeof o === "object" ? Object.keys(o) : [])));
      return [...keys].filter((k) => !["value", "label", "group", "disabled"].includes(k) && !modelKeys.includes(k));
    } catch { return []; }
  };
  const failure = (r: Reply) => r.problem || (r.problems ? Object.entries(r.problems).map(([k, v]) => `${k.replace(/^(spec|config)\.?/, "") || "contrôle"} : ${v}`)[0] : "");
  const sendOptions = () => post.mutate({ kind: "options", text: optText }, {
    onSuccess: (r) => {
      const bad = failure(r);
      if (bad || !r.options) { setNotice({ zone: "options", ok: false, text: bad || "Réponse illisible." }); return; }
      const dropped = (r.sent ?? 0) - r.options.length;
      setOptions({ items: r.options, sent: r.sent ?? 0 });
      const extra = unknownKeys(optText);
      setNotice({ zone: "options", ok: true, text: `${r.options.length} option(s) retenue(s)${withData(r.options) ? ` (dont ${withData(r.options)} avec des valeurs pour les champs enfants)` : ""}${dropped > 0 ? `, ${dropped} ignorée(s) (valeur absente, vide ou en double)` : ""}.${extra.length ? ` Clé(s) sans champ enfant correspondant, donc non utilisée(s) : ${extra.join(", ")}. Créez dans « Modèle d'une option » des champs ayant ces clés pour qu'elles pré-remplissent.` : ""}` });
    },
    onError: (e) => setNotice({ zone: "options", ok: false, text: e.message }),
  });
  const sendValue = () => post.mutate({ kind: "prefill", text: valText }, {
    onSuccess: (r) => {
      const bad = failure(r);
      if (bad) { setNotice({ zone: "value", ok: false, text: bad }); return; }
      setValue(r.value);
      setNotice({ zone: "value", ok: true, text: "Valeur acceptée : le contrôle est rempli ci-dessous, enfants compris." });
    },
    onError: (e) => setNotice({ zone: "value", ok: false, text: e.message }),
  });

  const known = options?.items ?? (spec.items ?? []);
  const lookup = useMemo<LookupFn | undefined>(() => (dynamic && options ? async () => options.items : undefined), [dynamic, options]);
  const field: FormField = useMemo(() => ({
    key: "essai", label: name, type: "custom", control: { ref: slug, version: "0.0.0", name, spec },
    ...(dynamic && options ? { source: { connection: "essai", path: "/" } } : {}),
    ...(spec.mode === "items" && spec.items_editable ? { options: spec.items ?? [] } : {}),
  }), [name, slug, spec, dynamic, options]);
  const result = processControl(field, value === undefined ? (spec.mode === "group" ? {} : spec.mode === "choice" ? undefined : []) : value);
  const note = (zone: "options" | "value") => (notice?.zone === zone ? <p role={notice.ok ? "status" : "alert"} className={notice.ok ? "hint" : "field-error"}>{notice.text}</p> : null);

  return (
    <section className="fb-panel fb-scroll ctl-preview" aria-label="Test du contrôle">
      <p className="hint">Envoyez du JSON au contrôle comme le ferait un formulaire : les options à proposer, puis la valeur à pré-remplir (champs enfants compris). Le brouillon actuel est utilisé ; rien n'est enregistré et aucun service n'est appelé.</p>
      <div className="ct-grid">
        {listy && (
        <div className="cd-sec">
          <h3><FormIcon name="list2" size={16} /> Options envoyées <HelpTip label="Aide : options envoyées">Une liste d'objets avec « value » (obligatoire), « label », « group » et « disabled ». Ajoutez, à plat, les clés des champs enfants (par exemple « nom », « prenom ») : choisir l'option les pré-remplit. Les éléments sans valeur, vides ou en double sont ignorés.</HelpTip></h3>
          {listy ? (
            <>
              {!draft.dynamic && <p className="hint" role="note"><FormIcon name="info" size={14} /> Dans ce test, vous pouvez envoyer des options ; pour que les formulaires puissent en recevoir d'un service, activez « Options fournies par le formulaire » (section Options).</p>}
              {modelKeys.length === 0 && <p className="hint" role="note"><FormIcon name="warn" size={14} /> Le modèle d'une option n'a aucun champ enfant : choisir une option n'ouvre rien. Ajoutez-en dans la section « Modèle d'une option ».</p>}
              <div className="field"><label htmlFor="ct-opt" className="sr-only">Options (JSON)</label>
                <textarea id="ct-opt" className="mono" rows={9} value={optText} onChange={(e) => setOptText(e.target.value)} placeholder={pretty(exampleOptions(spec))} /></div>
              <div className="ct-btns">
                <button type="button" className="primary" onClick={sendOptions} disabled={!optText.trim() || post.isPending}><IconLabel icon="send">Envoyer les options</IconLabel></button>
                <button type="button" onClick={() => setOptText(pretty(exampleOptions(spec)))}><IconLabel icon="wand">Exemple</IconLabel></button>
                {options && <button type="button" onClick={() => { setOptions(null); setNotice(null); }}><IconLabel icon="undo">Oublier les options</IconLabel></button>}
              </div>
              {note("options")}
            </>
          ) : null}
        </div>
        )}
        <div className="cd-sec">
          <h3><FormIcon name="design" size={16} /> Valeur envoyée <HelpTip label="Aide : valeur envoyée">La valeur à pré-remplir, au format que le contrôle remet au workflow. Elle est vérifiée par les mêmes règles que celles du serveur : refusée en entier si elle ne les respecte pas.</HelpTip></h3>
          <div className="field"><label htmlFor="ct-val" className="sr-only">Valeur (JSON)</label>
            <textarea id="ct-val" className="mono" rows={9} value={valText} onChange={(e) => setValText(e.target.value)} placeholder={pretty(exampleValue(spec, known) ?? {})} /></div>
          <div className="ct-btns">
            <button type="button" className="primary" onClick={sendValue} disabled={!valText.trim() || post.isPending}><IconLabel icon="send">Envoyer la valeur</IconLabel></button>
            <button type="button" onClick={() => setValText(pretty(exampleValue(spec, known) ?? {}))}><IconLabel icon="wand">Exemple</IconLabel></button>
            <button type="button" onClick={() => { setValue(undefined); setNotice(null); }}><IconLabel icon="undo">Vider le contrôle</IconLabel></button>
          </div>
          {note("value")}
        </div>
      </div>

      <h3><FormIcon name="eye" size={16} /> Le contrôle rempli</h3>
      <div className="field fb-field"><span className="fb-label" id="fb-test-l">{name}</span>
        <ControlInput field={field} id="fb-test" value={value} lookup={lookup} error={result.problem || undefined} onChange={setValue} /></div>
      {result.problem ? <p role="status" className="field-error">{result.problem}</p> : <p role="status" className="hint">Valeur acceptée.</p>}
      <h3>Valeur remise au workflow</h3>
      <pre className="ctl-json" aria-label="Valeur produite par le test">{pretty(result.value ?? null)}</pre>
    </section>
  );
}
