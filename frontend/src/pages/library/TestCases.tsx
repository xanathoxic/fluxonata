import { useState } from "react";
import type { JsonSchema } from "../../api";
import { Badge, Confirm, Section } from "../../components/AdminUi";
import { Fields } from "../../components/Fields";
import { Overlay } from "../../components/Overlay";
import { defaultsFor, SchemaForm, validateValues } from "../../components/SchemaForm";
import { errorText, useWsMutation, useWsQuery } from "../../lib";

export interface TestCase { id: number; name: string; inputs: Record<string, unknown>; valid: boolean; created_by: string | null; updated_at: string }
type Suggestion = { name: string; inputs: Record<string, unknown> };

/** Saved test sets of an action (shared by the action sheet and the "Lancer un essai" page). */
export function useTestCases(slug: string) {
  return useWsQuery<{ results: TestCase[] }>(`testcases-${slug}`, `/actions/${slug}/tests/`);
}

const summary = (inputs: Record<string, unknown>) => {
  const s = JSON.stringify(inputs);
  return s.length > 90 ? `${s.slice(0, 90)}…` : s;
};

export function TestCasesSection({ slug, schema }: { slug: string; schema: JsonSchema }) {
  const cases = useTestCases(slug);
  const save = useWsMutation<{ name: string; inputs: Record<string, unknown> }>("POST", `/actions/${slug}/tests/`, [`testcases-${slug}`]);
  const update = useWsMutation<{ id: number; name: string; inputs: Record<string, unknown> }>("PATCH", (i) => `/actions/${slug}/tests/${i.id}/`, [`testcases-${slug}`]);
  const remove = useWsMutation<{ id: number }>("DELETE", (i) => `/actions/${slug}/tests/${i.id}/`, [`testcases-${slug}`]);
  const suggest = useWsMutation<{ provider?: string }, { tests: Suggestion[] }>("POST", `/actions/${slug}/ai/suggest-tests/`);
  const [edit, setEdit] = useState<{ id?: number; name: string; inputs: Record<string, unknown> } | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState<TestCase | null>(null);
  const list = cases.data?.results ?? [];
  const saved = new Set(list.map((c) => c.name));
  const proposals = (suggest.data?.tests ?? []).filter((t) => !saved.has(t.name));

  const open = (c?: TestCase) => { setErrors({}); update.reset(); save.reset(); setEdit(c ? { id: c.id, name: c.name, inputs: c.inputs } : { name: "", inputs: defaultsFor(schema) }); };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    const problems = validateValues(schema, edit.inputs);
    if (!edit.name.trim()) problems._name = "Donnez un nom au jeu de test.";
    setErrors(problems);
    if (Object.keys(problems).length) return;
    const done = { onSuccess: () => setEdit(null) };
    if (edit.id) update.mutate({ id: edit.id, name: edit.name, inputs: edit.inputs }, done);
    else save.mutate({ name: edit.name, inputs: edit.inputs }, done);
  };
  const failure = save.error ?? update.error;

  return (
    <Section title="Jeux de test" hint="Des entrées prêtes à l'emploi, proposées à tous ceux qui lancent un essai de cette action. Ne mettez jamais de secret dedans : utilisez une connexion."
      actions={<><button type="button" onClick={() => suggest.mutate({})} disabled={suggest.isPending}>{suggest.isPending ? "Réflexion…" : "Suggérer avec l'IA"}</button><button type="button" className="primary" onClick={() => open()}>Ajouter un jeu de test</button></>}>
      {cases.isLoading ? <p role="status">Chargement…</p> : list.length === 0 ? <p className="hint">Aucun jeu de test enregistré. Ajoutez-en un, ou demandez des suggestions à l'assistant.</p> : (
        <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Jeux de test de l'action</caption>
          <thead><tr><th scope="col">Nom</th><th scope="col">Entrées</th><th scope="col">État</th><th scope="col">Actions</th></tr></thead>
          <tbody>{list.map((c) => (
            <tr key={c.id}><th scope="row">{c.name}</th><td className="mono tc-sum">{summary(c.inputs)}</td>
              <td>{c.valid ? <Badge tone="ok">Valide</Badge> : <Badge tone="warn">Contrat modifié</Badge>}</td>
              <td className="tc-actions"><button type="button" onClick={() => open(c)} aria-label={`Modifier le jeu de test ${c.name}`}>Modifier</button>
                <button type="button" className="danger" onClick={() => setConfirm(c)} aria-label={`Supprimer le jeu de test ${c.name}`}>Supprimer</button></td></tr>))}
          </tbody></table></div>)}
      {suggest.error && <p role="alert" className="field-error">{errorText(suggest.error)}</p>}
      {proposals.length > 0 && (
        <div className="tc-proposals"><h3>Suggestions de l'assistant <span className="hint">(rien n'est enregistré tant que vous ne le décidez pas)</span></h3>
          {proposals.map((t) => (
            <div key={t.name} className="notice tc-proposal"><div><strong>{t.name}</strong><Fields data={t.inputs} /></div>
              <button type="button" onClick={() => save.mutate({ name: t.name, inputs: t.inputs })} disabled={save.isPending} aria-label={`Enregistrer la suggestion ${t.name}`}>Enregistrer</button></div>))}
          {save.error && !edit && <p role="alert" className="field-error">Erreur : {errorText(save.error)}</p>}
        </div>)}
      {edit && (
        <Overlay title={edit.id ? "Modifier le jeu de test" : "Nouveau jeu de test"} description="Les valeurs doivent respecter le contrat de la dernière version de l'action." onClose={() => setEdit(null)}
          footer={<><button type="button" onClick={() => setEdit(null)}>Annuler</button><button type="submit" form="tc-form" className="primary" disabled={save.isPending || update.isPending}>Enregistrer</button></>}>
          <form id="tc-form" onSubmit={submit} noValidate>
            <div className="field"><label htmlFor="tc-name">Nom du jeu de test</label>
              <input id="tc-name" value={edit.name} maxLength={100} onChange={(e) => setEdit({ ...edit, name: e.target.value })} aria-invalid={errors._name ? true : undefined} aria-describedby={errors._name ? "tc-name-e" : undefined} />
              {errors._name && <p id="tc-name-e" className="field-error">{errors._name}</p>}</div>
            <SchemaForm schema={schema} values={edit.inputs} onChange={(v) => setEdit({ ...edit, inputs: v })} errors={errors} />
            {failure && <p role="alert" className="field-error">Erreur : {errorText(failure)}</p>}
          </form>
        </Overlay>)}
      {confirm && (
        <Confirm title="Supprimer ce jeu de test ?" confirmLabel="Supprimer" busy={remove.isPending} onCancel={() => setConfirm(null)}
          onConfirm={() => remove.mutate({ id: confirm.id }, { onSuccess: () => setConfirm(null) })}>
          <p>Le jeu « {confirm.name} » ne sera plus proposé quand on lance un essai.</p>
          {remove.error && <p role="alert" className="field-error">Erreur : {errorText(remove.error)}</p>}
        </Confirm>)}
    </Section>
  );
}
