import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ApiError } from "../../api";
import { Badge, Confirm } from "../../components/AdminUi";
import { HelpTip } from "../../components/HelpTip";
import { RowIcon } from "../../components/RowIcon";
import { errorText } from "../../lib";
import { SsoEditor } from "./SsoEditor";
import { KIND_LABEL, type Kind, type Listing, type Provider, type Space } from "./ssoTypes";
import "./sso.css";

const P = "/platform/sso";

/** Authentification unique : les fournisseurs d'identité (OpenID Connect, SAML 2.0), leurs règles groupe → droit et leurs adresses.
 * Réservé aux administrateurs de la plate-forme ; le serveur le revérifie et audite chaque changement. */
export function SsoTab() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["platform", "sso"], queryFn: () => api<Listing>(`${P}/providers/`) });
  const spaces = useQuery({ queryKey: ["platform", "spaces"], queryFn: () => api<{ results: Space[] }>("/platform/workspaces/") });
  const [edit, setEdit] = useState<{ provider: Provider | null; kind: Kind } | null>(null);
  const [remove, setRemove] = useState<Provider | null>(null);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["platform", "sso"] });
    void qc.invalidateQueries({ queryKey: ["platform", "audit"] });
  };
  const toggle = useMutation<unknown, ApiError, Provider>({
    mutationFn: (p) => api(`${P}/providers/${p.slug}/`, { method: "PATCH", json: { enabled: !p.enabled } }),
    onSuccess: refresh,
  });
  const del = useMutation<unknown, ApiError, Provider>({
    mutationFn: (p) => api(`${P}/providers/${p.slug}/`, { method: "DELETE" }),
    onSuccess: () => {
      setRemove(null);
      refresh();
    },
  });
  const data = list.data;
  const rows = data?.results ?? [];
  return (
    <>
      <div className="toolbar-row">
        <span style={{ flex: 1 }}>
          <HelpTip caption="À savoir">
            Les personnes se connectent avec le compte de leur entreprise (annuaire, Keycloak, Entra ID, AD FS…). Leurs droits viennent de leurs groupes :
            chaque règle dit « les membres de ce groupe ont ce rôle dans cet espace ». Le mot de passe local reste possible pour les comptes qui en ont un.
          </HelpTip>
        </span>
        {rows.length > 0 && (
          <>
            <button type="button" onClick={() => setEdit({ provider: null, kind: "oidc" })}>
              Ajouter OpenID Connect
            </button>
            <button type="button" className="primary" onClick={() => setEdit({ provider: null, kind: "saml" })}>
              Ajouter SAML
            </button>
          </>
        )}
      </div>
      {list.isError && (
        <p role="alert" className="field-error">
          Impossible de charger les fournisseurs.
        </p>
      )}
      {data?.environment && (
        <section className="adm-card" aria-label="Fournisseur défini par l'environnement">
          <div className="adm-card-h">
            <div>
              <h2>Fournisseur défini par le serveur</h2>
              <p className="hint">
                Réglé par les variables <code>ORCH_OIDC_*</code> : visible ici, non modifiable. Ses droits viennent de <code>ORCH_OIDC_GROUP_ROLES</code>
                {data.environment.group_roles ? "" : " (non réglé)"}.
              </p>
            </div>
            <Badge tone="info">Environnement</Badge>
          </div>
          <p className="sso-meta">
            <span>
              <strong>Émetteur</strong> {data.environment.issuer}
            </span>
            <span>
              <strong>Client</strong> {data.environment.client_id}
            </span>
          </p>
        </section>
      )}
      {data && rows.length === 0 && (
        <section className="adm-card" aria-label="Premier fournisseur">
          <div className="adm-card-h">
            <div>
              <h2>Aucun fournisseur d'identité</h2>
              <p className="hint">Branchez l'annuaire de l'entreprise. Il n'est pas obligatoire : les comptes locaux continuent de fonctionner.</p>
            </div>
          </div>
          <div className="sso-start">
            <button type="button" onClick={() => setEdit({ provider: null, kind: "oidc" })}>
              <strong>OpenID Connect</strong>
              <span>Keycloak, Entra ID (Azure AD), Okta, Authentik, Google Workspace, AD FS récent…</span>
            </button>
            <button type="button" onClick={() => setEdit({ provider: null, kind: "saml" })}>
              <strong>SAML 2.0</strong>
              <span>AD FS, Entra ID, Keycloak, Shibboleth, annuaires d'entreprise plus anciens.</span>
            </button>
          </div>
        </section>
      )}
      <div className="sso-grid">
        {rows.map((p) => (
          <section key={p.slug} className="adm-card sso-card" aria-label={p.name}>
            <h3>
              <span className={`sso-ico${p.enabled ? " on" : ""}`}>
                <RowIcon name="shield" />
              </span>
              {p.name}
              <Badge tone={p.enabled ? "ok" : "muted"}>{p.enabled ? "Actif" : "Désactivé"}</Badge>
              <Badge tone="info">{KIND_LABEL[p.kind]}</Badge>
            </h3>
            <p className="sso-meta">
              <span>
                <strong>{p.rules}</strong> règle(s) de droits
              </span>
              <span>
                <strong>{p.identities}</strong> personne(s) liée(s)
              </span>
              <span>{p.unmapped === "deny" ? "Refuse les personnes sans groupe reconnu" : "Accepte les personnes sans groupe reconnu (sans accès)"}</span>
            </p>
            <div className="sso-foot">
              <button type="button" className="primary sso-conf" onClick={() => setEdit({ provider: p, kind: p.kind })} aria-label={`Configurer ${p.name}`}>
                <RowIcon name="sliders" /> Configurer
              </button>
              <label className="sso-toggle" title={p.enabled ? "Désactiver ce fournisseur" : "Activer ce fournisseur"}>
                <input
                  type="checkbox"
                  className="adm-switch"
                  checked={p.enabled}
                  disabled={toggle.isPending}
                  onChange={() => toggle.mutate(p)}
                  aria-label={`${p.name} : actif`}
                />
                <span aria-hidden="true">{p.enabled ? "Actif" : "Inactif"}</span>
              </label>
              <button
                type="button"
                className="icon-btn sso-del"
                onClick={() => {
                  del.reset();
                  setRemove(p);
                }}
                aria-label={`Supprimer ${p.name}`}
                title="Supprimer ce fournisseur"
              >
                <RowIcon name="trash" />
              </button>
            </div>
          </section>
        ))}
      </div>
      {toggle.error && (
        <p role="alert" className="field-error">
          {errorText(toggle.error)}
        </p>
      )}
      {edit && data && (
        <SsoEditor
          key={edit.provider?.slug ?? `new-${edit.kind}`}
          provider={edit.provider}
          kind={edit.kind}
          listing={data}
          spaces={spaces.data?.results ?? []}
          onClose={() => setEdit(null)}
        />
      )}
      {remove && (
        <Confirm title={`Supprimer « ${remove.name} » ?`} confirmLabel="Supprimer" onCancel={() => setRemove(null)} onConfirm={() => del.mutate(remove)}>
          <p>
            Plus personne ne pourra se connecter par ce fournisseur. Les droits qu'il a donnés sont conservés, comme des droits donnés à la main : ils ne
            changeront plus à la connexion. Les comptes ne sont pas supprimés.
          </p>
          {del.error && (
            <p role="alert" className="field-error">
              Erreur : {errorText(del.error)}
            </p>
          )}
        </Confirm>
      )}
    </>
  );
}
