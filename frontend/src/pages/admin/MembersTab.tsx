import { useState } from "react";
import { Overlay } from "../../components/Overlay";
import { useApp } from "../../context";
import { errorText, useWsMutation, useWsQuery } from "../../lib";

const ROLES: Record<string, string> = { admin: "Administrateur", space_manager: "Responsable d'espace", designer: "Concepteur", operator: "Opérateur", approver: "Approbateur", viewer: "Lecteur" };
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
  const err = add.error ?? change.error ?? remove.error;
  return (
    <>
      <div className="toolbar-row"><h2 style={{ flex: 1, margin: 0 }}>Membres de l'espace</h2><button type="button" className="primary" onClick={() => { add.reset(); setAdding(true); }}>Ajouter un membre</button></div>
      {err && !adding && <p role="alert" className="field-error">Erreur : {errorText(err)}</p>}
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Membres et rôles</caption>
          <thead><tr><th scope="col">Identifiant</th><th scope="col">Rôle</th><th scope="col">Actions</th></tr></thead>
          <tbody>
            {q.data?.results.map((m) => (
              <tr key={m.username}>
                <td>{m.username}{m.is_service ? " (compte de service)" : ""}{m.username === me.username ? " — vous" : ""}</td>
                <td>
                  <label className="sr-only" htmlFor={`r-${m.username}`}>Rôle de {m.username}</label>
                  <select id={`r-${m.username}`} value={m.role} onChange={(e) => change.mutate({ username: m.username, role: e.target.value })}>
                    {Object.entries(ROLES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </td>
                <td>
                  {confirm === m.username ? (
                    <><button type="button" className="danger" onClick={() => { remove.mutate({ username: m.username }); setConfirm(null); }}>Confirmer le retrait</button> <button type="button" onClick={() => setConfirm(null)}>Annuler</button></>
                  ) : <button type="button" onClick={() => setConfirm(m.username)} aria-label={`Retirer ${m.username} de l'espace`}>Retirer</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
