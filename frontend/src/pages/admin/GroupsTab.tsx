import { useState, type FormEvent } from "react";
import { Confirm, EmptyRow, Section } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { errorText, roleLabel, useWsMutation, useWsQuery } from "../../lib";

interface Group { id: number; name: string; description: string; members: string[]; workflows: string[] }
interface Members { results: { username: string; role: string }[] }
interface Catalog { results: { slug: string; name: string }[] }

/** Groups of people and the catalog items each one may launch. They narrow only the « Catalogue » role: other roles keep their own rights. */
export function GroupsTab() {
  const groups = useWsQuery<{ results: Group[] }>("groups", "/groups/");
  const members = useWsQuery<Members>("members", "/members/");
  const catalog = useWsQuery<Catalog>("groups-catalog", "/catalog/");
  const save = useWsMutation<Partial<Group> & { id?: number }>("POST", (i) => (i.id ? `/groups/${i.id}/` : "/groups/"), ["groups", "catalog"]);
  const patch = useWsMutation<Partial<Group> & { id: number }>("PATCH", (i) => `/groups/${i.id}/`, ["groups", "catalog"]);
  const remove = useWsMutation<{ id: number }>("DELETE", (i) => `/groups/${i.id}/`, ["groups"]);
  const [editing, setEditing] = useState<Group | "new" | null>(null);
  const [toDelete, setToDelete] = useState<Group | null>(null);
  const rows = groups.data?.results ?? [];
  const names = Object.fromEntries((catalog.data?.results ?? []).map((c) => [c.slug, c.name]));
  return (
    <>
      <Section title="Groupes d'utilisateurs" hint="Un groupe réunit des personnes et les automatisations du catalogue qu'elles peuvent lancer. Une personne au rôle « Catalogue » ne voit et ne lance que ce que ses groupes autorisent."
        actions={<button type="button" className="primary" onClick={() => { save.reset(); setEditing("new"); }}>Nouveau groupe</button>}>
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Groupes</caption>
            <thead><tr><th scope="col">Groupe</th><th scope="col">Membres</th><th scope="col">Automatisations autorisées</th><th scope="col">Actions</th></tr></thead>
            <tbody>
              {rows.map((g) => (
                <tr key={g.id}>
                  <td><strong>{g.name}</strong>{g.description && <span className="lst-sub">{g.description}</span>}</td>
                  <td>{g.members.length ? g.members.join(", ") : "—"}</td>
                  <td>{g.workflows.length ? g.workflows.map((s) => names[s] ?? s).join(", ") : "—"}</td>
                  <td className="row-actions">
                    <button type="button" onClick={() => { patch.reset(); setEditing(g); }} aria-label={`Modifier le groupe ${g.name}`}>Modifier</button>
                    <button type="button" onClick={() => setToDelete(g)} aria-label={`Supprimer le groupe ${g.name}`}>Supprimer</button>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && <EmptyRow cols={4}>{groups.isLoading ? "Chargement…" : "Aucun groupe. Créez-en un pour donner accès à des automatisations du catalogue."}</EmptyRow>}
            </tbody>
          </table>
        </div>
      </Section>
      {editing && (
        <GroupDialog group={editing === "new" ? null : editing} people={members.data?.results ?? []} items={catalog.data?.results ?? []}
          busy={save.isPending || patch.isPending} error={errorText(save.error ?? patch.error)}
          onClose={() => setEditing(null)}
          onSave={(d) => (editing === "new" ? save.mutate(d, { onSuccess: () => setEditing(null) }) : patch.mutate({ ...d, id: editing.id }, { onSuccess: () => setEditing(null) }))} />
      )}
      {toDelete && (
        <Confirm title={`Supprimer le groupe « ${toDelete.name} » ?`} confirmLabel="Supprimer le groupe" busy={remove.isPending} onCancel={() => setToDelete(null)}
          onConfirm={() => remove.mutate({ id: toDelete.id }, { onSuccess: () => setToDelete(null) })}>
          <p>Ses {toDelete.members.length} membre(s) perdent les accès qu'il leur donnait au catalogue (sauf ceux d'un autre groupe). Les exécutions déjà lancées sont conservées.</p>
        </Confirm>
      )}
    </>
  );
}

function GroupDialog({ group, people, items, busy, error, onClose, onSave }: {
  group: Group | null; people: Members["results"]; items: Catalog["results"]; busy: boolean; error: string; onClose: () => void;
  onSave: (d: { name: string; description: string; members: string[]; workflows: string[] }) => void;
}) {
  const [name, setName] = useState(group?.name ?? "");
  const [description, setDescription] = useState(group?.description ?? "");
  const [chosen, setChosen] = useState<string[]>(group?.members ?? []);
  const [allowed, setAllowed] = useState<string[]>(group?.workflows ?? []);
  const toggle = (list: string[], set: (v: string[]) => void, v: string) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  return (
    <Overlay title={group ? `Groupe « ${group.name} »` : "Nouveau groupe"} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form="grp-form" className="primary" disabled={!name.trim() || busy}>Enregistrer</button></>}>
      <form id="grp-form" onSubmit={(e: FormEvent) => { e.preventDefault(); onSave({ name: name.trim(), description, members: chosen, workflows: allowed }); }}>
        <div className="field"><label htmlFor="grp-name">Nom du groupe</label><input id="grp-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} autoFocus required /></div>
        <div className="field"><label htmlFor="grp-desc">Description</label><input id="grp-desc" value={description} maxLength={300} onChange={(e) => setDescription(e.target.value)} /></div>
        <fieldset className="grp-pick"><legend>Membres du groupe</legend>
          {people.map((p) => (
            <label key={p.username} className="grp-opt"><input type="checkbox" checked={chosen.includes(p.username)} onChange={() => toggle(chosen, setChosen, p.username)} /> {p.username} <span className="hint">({roleLabel(p.role)})</span></label>
          ))}
        </fieldset>
        <fieldset className="grp-pick"><legend>Automatisations qu'il peut lancer</legend>
          {items.length === 0 && <p className="hint">Aucune automatisation n'est proposée dans le catalogue.</p>}
          {items.map((c) => (
            <label key={c.slug} className="grp-opt"><input type="checkbox" checked={allowed.includes(c.slug)} onChange={() => toggle(allowed, setAllowed, c.slug)} /> {c.name}</label>
          ))}
        </fieldset>
        {error && <p role="alert" className="field-error">Erreur : {error}</p>}
      </form>
    </Overlay>
  );
}
