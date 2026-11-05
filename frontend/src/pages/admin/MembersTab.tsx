import { useMemo, useState } from "react";
import { Avatar, Badge, Confirm, EmptyRow, SearchBox, Section, usePaged } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { useApp } from "../../context";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { Kpi } from "../OpsCharts";

const ROLES: Record<string, string> = { admin: "Administrateur", space_manager: "Responsable d'espace", designer: "Concepteur", operator: "Opérateur", approver: "Approbateur", viewer: "Lecteur", catalog: "Catalogue" };
interface Members { results: { username: string; role: string; is_service: boolean }[] }

export function MembersTab() {
  const { me } = useApp();
  const q = useWsQuery<Members>("members", "/members/");
  const add = useWsMutation<{ username: string; role: string }>("POST", "/members/", ["members"]);
  const change = useWsMutation<{ username: string; role: string }>("PATCH", (i) => `/members/${i.username}/`, ["members"]);
  const remove = useWsMutation<{ username: string }>("DELETE", (i) => `/members/${i.username}/`, ["members"]);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState("viewer");
  const [confirm, setConfirm] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const err = add.error ?? change.error ?? remove.error;
  const all = q.data?.results ?? [];
  const rows = useMemo(() => all.filter((m) => (!roleFilter || m.role === roleFilter) && m.username.toLowerCase().includes(search.trim().toLowerCase())), [all, roleFilter, search]);
  const paged = usePaged(rows, 10);
  const admins = all.filter((m) => m.role === "admin" || m.role === "space_manager").length;
  return (
    <>
      <ul className="kpis" aria-label="Chiffres des membres">
        <Kpi label="Membres" value={all.length} />
        <Kpi label="Administrateurs" value={admins} hint="administrateurs et responsables" />
        <Kpi label="Comptes de service" value={all.filter((m) => m.is_service).length} hint="identités non humaines" tone="info" />
      </ul>
      <Section title="Membres de l'espace" hint="Chaque personne a un rôle ; le rôle décide de ce qu'elle peut voir, lancer, modifier et publier."
        actions={<button type="button" className="primary" onClick={() => { add.reset(); setAdding(true); }}>Ajouter un membre</button>}>
        <div className="adm-filters">
          <SearchBox label="Rechercher un membre" value={search} onChange={setSearch} />
          <div className="field"><label htmlFor="m-rf">Rôle</label>
            <select id="m-rf" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}><option value="">Tous les rôles</option>{Object.entries(ROLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        </div>
        {err && !adding && <p role="alert" className="field-error">Erreur : {errorText(err)}</p>}
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Membres et rôles</caption>
            <thead><tr><th scope="col">Identifiant</th><th scope="col">Rôle</th><th scope="col">Actions</th></tr></thead>
            <tbody>
              {paged.slice.map((m) => (
                <tr key={m.username}>
                  <td><span className="adm-who"><Avatar name={m.username} /><span>{m.username}{m.is_service ? " (compte de service)" : ""}{m.username === me.username ? " — vous" : ""}</span></span></td>
                  <td>
                    <label className="sr-only" htmlFor={`r-${m.username}`}>Rôle de {m.username}</label>
                    <select id={`r-${m.username}`} value={m.role} onChange={(e) => change.mutate({ username: m.username, role: e.target.value })}>
                      {Object.entries(ROLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                  </td>
                  <td><button type="button" onClick={() => setConfirm(m.username)} aria-label={`Retirer ${m.username} de l'espace`}>Retirer</button></td>
                </tr>
              ))}
              {rows.length === 0 && <EmptyRow cols={3}>{q.isLoading ? "Chargement…" : "Aucun membre ne correspond à la recherche."}</EmptyRow>}
            </tbody>
          </table>
        </div>
        {paged.pager("Pagination des membres")}
      </Section>
      {confirm && (
        <Confirm title="Retirer ce membre ?" confirmLabel="Confirmer le retrait" busy={remove.isPending} onCancel={() => setConfirm(null)}
          onConfirm={() => { remove.mutate({ username: confirm }); setConfirm(null); }}>
          <p><strong>{confirm}</strong> perdra l'accès à cet espace. Son compte et ses actions passées sont conservés. <Badge tone="muted">{ROLES[all.find((m) => m.username === confirm)?.role ?? ""] ?? ""}</Badge></p>
        </Confirm>
      )}
      {adding && (
        <Overlay title="Ajouter un membre" description="Le compte doit déjà exister (ou être créé par la connexion OIDC)." onClose={() => setAdding(false)}
          footer={<><button type="button" onClick={() => setAdding(false)}>Annuler</button><button type="submit" form="member-form" className="primary">Ajouter</button></>}>
          <form id="member-form" onSubmit={(e) => { e.preventDefault(); add.mutate({ username, role }, { onSuccess: () => { setUsername(""); setAdding(false); } }); }}>
            <div className="field"><label htmlFor="m-u">Ajouter un compte existant</label><input id="m-u" value={username} onChange={(e) => setUsername(e.target.value)} required /></div>
            <div className="field"><label htmlFor="m-r">Rôle</label>
              <select id="m-r" value={role} onChange={(e) => setRole(e.target.value)}>{Object.entries(ROLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
            {add.error && <p role="alert" className="field-error">Erreur : {errorText(add.error)}</p>}
          </form>
        </Overlay>
      )}
    </>
  );
}
