import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError } from "../../api";
import { Confirm } from "../../components/AdminUi";
import { Badge } from "../../components/AdminUi";
import { Tabs } from "../../components/Tabs";
import { useApp } from "../../context";
import { errorText, useWsQuery } from "../../lib";
import { fieldsFromInputs } from "../../forms/DefinitionEditor";
import { DefinitionForm } from "../../forms/DefinitionForm";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import { slugify, type FormCard, type FormFull } from "../../forms/library";
import { problemsOf, type InputMap } from "../../forms/inputMap";
import type { FormDefinition } from "../../forms/types";
import { useDraft } from "../DraftContext";
import { FormChooser } from "./FormChooser";
import { InputMapping } from "./InputMapping";

/** The designed input form of a workflow: which library form (and version) it uses, with a preview. */
export function LibraryPicker() {
  const { graph, setGraph, editable, wf } = useDraft();
  const { workspace, can } = useApp();
  const qc = useQueryClient();
  const input = graph.input_form;
  const ref = input?.ref ?? { form: "", version: null };
  const embedded = input !== null && input !== undefined && input.sections !== undefined && input.ref === undefined;
  const lib = useWsQuery<{ results: FormCard[] }>("form-library", "/form-library/");
  const choices = (lib.data?.results ?? []).filter((f) => !f.is_template && f.latest_version);
  const chosen = choices.find((f) => f.slug === ref.form);
  const shownVersion = ref.version ?? chosen?.latest_version?.version ?? null;
  const preview = useQuery({ queryKey: ["form-ver", workspace.slug, ref.form, shownVersion], enabled: ref.form !== "" && shownVersion !== null, queryFn: () => api<{ definition: FormDefinition }>(`/workspaces/${workspace.slug}/form-library/${ref.form}/versions/${shownVersion}/`) });
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [confirmMove, setConfirmMove] = useState(false);
  const [tab, setTab] = useState("map");
  const [chooser, setChooser] = useState(false);
  const [pick, setPick] = useState(ref.form);
  const pickDetail = useQuery({ queryKey: ["form-lib", workspace.slug, pick], enabled: chooser && pick !== "", queryFn: () => api<FormFull>(`/workspaces/${workspace.slug}/form-library/${pick}/`) });
  const map: InputMap | undefined = input?.map && Object.keys(input.map).length > 0 ? input.map : undefined;
  // the correspondence belongs to a form: it is kept when only the version changes, dropped for another form
  const set = (form: string, version: string | null) => setGraph({ ...graph, input_form: { ref: { form, version }, ...(form === ref.form && map ? { map } : {}) } });
  const setMap = (next: InputMap | undefined) => setGraph({ ...graph, input_form: { ref, ...(next ? { map: next } : {}) } });

  // quick creation: a published form with one field per input of this workflow, attached right away
  const quick = useMutation<{ slug: string }, ApiError, FormDefinition | null>({
    mutationFn: async (definition) => {
      const name = `Formulaire de ${wf.name}`;
      const slugBase = slugify(name) || "formulaire";
      const body = { slug: `${slugBase}-${Date.now().toString().slice(-4)}`, name, definition: definition ?? { version: 1, sections: [{ id: "s1", title: "Informations", fields: fieldsFromInputs(graph.inputs) }] } };
      const f = await api<FormCard>(`/workspaces/${workspace.slug}/form-library/`, { json: body });
      await api(`/workspaces/${workspace.slug}/form-library/${f.slug}/publish/`, { json: { changelog: definition ? "Repris du workflow" : "Un champ par entrée du workflow" } });
      return f;
    },
    onSuccess: (f) => { set(f.slug, null); setChooser(false); void qc.invalidateQueries({ queryKey: ["form-library"] }); },
  });

  if (embedded) {
    return (
      <>
        <p role="note" className="notice">Ce workflow porte un formulaire intégré (ancien format). Déplacez-le vers la bibliothèque pour le réutiliser, le versionner et le rattacher ailleurs.</p>
        {editable && can("publish") && <p><button type="button" onClick={() => setConfirmMove(true)}><IconLabel icon="send">Déplacer vers la bibliothèque</IconLabel></button></p>}
        {confirmMove && <Confirm title="Déplacer le formulaire vers la bibliothèque ?" confirmLabel="Déplacer" danger={false} busy={quick.isPending} onConfirm={() => { quick.mutate(input as FormDefinition); setConfirmMove(false); }} onCancel={() => setConfirmMove(false)}>Un formulaire de la bibliothèque est créé et publié (1.0.0), puis utilisé par ce workflow.</Confirm>}
        {quick.error && <p role="alert" className="field-error">Erreur : {errorText(quick.error)}</p>}
        <DefinitionForm definition={input as FormDefinition} values={values} onChange={setValues} />
      </>
    );
  }
  const problems = preview.data ? problemsOf(preview.data.definition, graph.inputs, map) : {};
  const bad = Object.keys(problems).length;
  const status = !preview.data ? null : bad > 0 ? <Badge tone="warn"><IconLabel icon="warn">{bad} problème(s) de correspondance</IconLabel></Badge> : <Badge tone="ok"><IconLabel icon="check">Correspondance valide</IconLabel></Badge>;
  return (
    <>
      {!ref.form ? (
        <div className="lp-empty">
          <span className="lp-tile" aria-hidden="true"><FormIcon name="design" size={26} /></span>
          <h3>Aucun formulaire choisi</h3>
          <p className="hint">Le formulaire de la bibliothèque devient la page de lancement de ce workflow (catalogue, page « Lancer », formulaires publics).</p>
          {editable && <p className="lp-btns"><button type="button" className="primary" onClick={() => setChooser(true)} aria-haspopup="dialog"><IconLabel icon="list">Choisir un formulaire</IconLabel></button>{" "}
            {can("publish") && <button type="button" onClick={() => quick.mutate(null)} disabled={quick.isPending}><IconLabel icon="wand">Créer à partir des entrées</IconLabel></button>}</p>}
        </div>
      ) : (
        <>
          <header className="lp-head">
            <span className="lp-tile" aria-hidden="true"><FormIcon name="design" size={24} /></span>
            <div className="lp-id">
              <h3>{chosen?.name ?? ref.form}{!chosen && <span className="hint"> (introuvable ou non publié)</span>}</h3>
              <p className="lp-chips">
                <Badge tone="info">{ref.version ? `Version ${ref.version}` : `Dernière publiée (${chosen?.latest_version?.version ?? "…"})`}</Badge>
                <Badge tone="info">{map ? "Correspondance personnalisée" : "Correspondance automatique"}</Badge>
                {status}
              </p>
            </div>
            <div className="lp-actions">
              {editable && <button type="button" onClick={() => setChooser(true)} aria-haspopup="dialog"><IconLabel icon="edit">Changer</IconLabel></button>}
              <Link className="icon-btn" to={`/library/forms/${ref.form}`} aria-label="Ouvrir dans le concepteur" title="Ouvrir dans le concepteur"><FormIcon name="design" /></Link>
              <Link className="icon-btn" to="/library/forms" aria-label="Tous les formulaires" title="Tous les formulaires"><FormIcon name="list" /></Link>
            </div>
          </header>
          <Tabs label="Formulaire d'entrée" active={tab} onChange={setTab} tabs={[
            { id: "map", label: <IconLabel icon="link">Correspondance{bad > 0 && <span className="lp-count" aria-label={`${bad} problème(s)`}>{bad}</span>}</IconLabel>,
              content: preview.data ? <InputMapping definition={preview.data.definition} schema={graph.inputs} map={map} editable={editable} onChange={setMap} /> : <p className="hint">Chargement…</p> },
            { id: "preview", label: <IconLabel icon="eye">Aperçu</IconLabel>,
              content: preview.data ? <DefinitionForm definition={preview.data.definition} values={values} onChange={setValues} /> : <p className="hint">Chargement…</p> },
          ]} />
        </>
      )}
      {quick.error && <p role="alert" className="field-error">Erreur : {errorText(quick.error)}</p>}
      {chooser && (
        <FormChooser choices={choices} current={ref} versions={pickDetail.data?.versions ?? []} canCreate={can("publish")} creating={quick.isPending} onPickForm={setPick} onClose={() => setChooser(false)}
          onApply={(form, version) => { set(form, version); setChooser(false); }} onCreate={() => quick.mutate(null)} />
      )}
    </>
  );
}
