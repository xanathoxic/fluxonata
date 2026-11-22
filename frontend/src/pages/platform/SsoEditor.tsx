import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ApiError } from "../../api";
import { HelpTip } from "../../components/HelpTip";
import { Overlay } from "../../components/Overlay";
import { SegmentedControl } from "../../components/SegmentedControl";
import { errorText } from "../../lib";
import { fieldErrors, KIND_LABEL, type Kind, type Listing, type Provider, type Rule, type Simulation, type Space } from "./ssoTypes";

const P = "/platform/sso";
const CLAIMS: [string, string, string][] = [
  ["username", "Identifiant", "preferred_username"],
  ["email", "Adresse e-mail", "email"],
  ["name", "Nom affiché", "name"],
  ["groups", "Groupes", "groups"],
];
const ATTRS: [string, string, string][] = [
  ["username", "Identifiant", "uid"],
  ["email", "Adresse e-mail", "mail"],
  ["name", "Nom affiché", "displayName"],
  ["groups", "Groupes", "memberOf"],
];
const obj = (v: unknown): Record<string, string> => (v && typeof v === "object" ? (v as Record<string, string>) : {});
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);

function splitCerts(text: string): string[] {
  return text
    .split(/(?<=-----END CERTIFICATE-----)/)
    .map((c) => c.trim())
    .filter((c) => c.includes("BEGIN CERTIFICATE"));
}

function Err({ at, errors }: { at: string; errors: Record<string, string> }) {
  return errors[at] ? (
    <p role="alert" className="sso-error">
      {errors[at]}
    </p>
  ) : null;
}

interface Props {
  provider: Provider | null;
  kind: Kind;
  listing: Listing;
  spaces: Space[];
  onClose: () => void;
}

/** La fenêtre d'un fournisseur : connexion, comptes et droits (groupes → rôles, avec simulation), adresses à donner au fournisseur. */
export function SsoEditor({ provider, kind: startKind, listing, spaces, onClose }: Props) {
  const qc = useQueryClient();
  const [saved, setSaved] = useState<Provider | null>(provider);
  const kind = saved?.kind ?? startKind;
  const cfg = saved?.config ?? {};
  const [tab, setTab] = useState<"connection" | "rights" | "integration">("connection");
  const [name, setName] = useState(saved?.name ?? "");
  const [slug, setSlug] = useState(saved?.slug ?? "");
  const [secret, setSecret] = useState("");
  const [clearSecret, setClearSecret] = useState(false);
  const [issuer, setIssuer] = useState(str(cfg.issuer));
  const [clientId, setClientId] = useState(str(cfg.client_id));
  const [scopes, setScopes] = useState(str(cfg.scopes, "openid profile email"));
  const [tokenAuth, setTokenAuth] = useState(str(cfg.token_auth, "basic"));
  const [pkce, setPkce] = useState(cfg.pkce !== false);
  const [mapping, setMapping] = useState<Record<string, string>>(obj(kind === "oidc" ? cfg.claims : cfg.attributes));
  const [domains, setDomains] = useState(Array.isArray(cfg.allowed_domains) ? (cfg.allowed_domains as string[]).join(", ") : "");
  const [idpEntity, setIdpEntity] = useState(str(cfg.idp_entity_id));
  const [idpUrl, setIdpUrl] = useState(str(cfg.idp_sso_url));
  const [certs, setCerts] = useState(Array.isArray(cfg.idp_certs) ? (cfg.idp_certs as string[]).join("\n") : "");
  const [spEntity, setSpEntity] = useState(str(cfg.sp_entity_id));
  const [nameIdFormat, setNameIdFormat] = useState(str(cfg.name_id_format));
  const [forceAuthn, setForceAuthn] = useState(cfg.force_authn === true);
  const [skew, setSkew] = useState(String(typeof cfg.clock_skew === "number" ? cfg.clock_skew : 60));
  const [metaUrl, setMetaUrl] = useState("");
  const [metaXml, setMetaXml] = useState("");
  const [autoCreate, setAutoCreate] = useState(saved?.auto_create ?? true);
  const [unmapped, setUnmapped] = useState<"allow" | "deny">(saved?.unmapped ?? "allow");

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["platform", "sso"] });
    void qc.invalidateQueries({ queryKey: ["platform", "audit"] });
  };
  const domainList = domains
    .split(/[\s,;]+/)
    .map((d) => d.trim())
    .filter(Boolean);
  const claimsOut = Object.fromEntries(Object.entries(mapping).filter(([, v]) => v.trim()));
  const config =
    kind === "oidc"
      ? {
          issuer: issuer.trim(),
          client_id: clientId.trim(),
          scopes: scopes.trim(),
          token_auth: tokenAuth,
          pkce,
          claims: claimsOut,
          allowed_domains: domainList,
        }
      : {
          idp_entity_id: idpEntity.trim(),
          idp_sso_url: idpUrl.trim(),
          idp_certs: splitCerts(certs),
          sp_entity_id: spEntity.trim(),
          name_id_format: nameIdFormat,
          force_authn: forceAuthn,
          clock_skew: Number(skew),
          attributes: claimsOut,
          allowed_domains: domainList,
        };

  const save = useMutation<Provider, ApiError>({
    mutationFn: () =>
      saved
        ? api<Provider>(`${P}/providers/${saved.slug}/`, {
            method: "PATCH",
            json: { name, config, ...(secret ? { secret } : {}), ...(clearSecret ? { clear_secret: true } : {}) },
          })
        : api<Provider>(`${P}/providers/`, { json: { slug, name, kind, config, secret: secret || undefined } }),
    onSuccess: (p) => {
      const first = !saved;
      setSaved(p);
      setSecret("");
      setClearSecret(false);
      refresh();
      if (first) setTab("rights");
    },
  });
  const errors = fieldErrors(save.error);
  const test = useMutation<{ ok: boolean; message?: string; detail?: Record<string, unknown> }, ApiError>({
    mutationFn: () => api(`${P}/providers/${saved?.slug}/test/`, { json: {} }),
  });
  const importMeta = useMutation<{ idp_entity_id: string; idp_sso_url: string; idp_certs: string[]; name_id_format: string }, ApiError>({
    mutationFn: () => api(`${P}/saml/import-metadata/`, { json: metaUrl.trim() ? { url: metaUrl.trim() } : { xml: metaXml } }),
    onSuccess: (m) => {
      setIdpEntity(m.idp_entity_id);
      setIdpUrl(m.idp_sso_url);
      setCerts(m.idp_certs.join("\n"));
      if (m.name_id_format) setNameIdFormat(m.name_id_format);
    },
  });

  return (
    <Overlay
      variant="drawer"
      wide
      className="sso-overlay"
      title={saved ? `Fournisseur « ${saved.name} »` : `Nouveau fournisseur ${KIND_LABEL[kind]}`}
      onClose={onClose}
      description={saved ? `${KIND_LABEL[kind]} · ${saved.enabled ? "actif" : "désactivé"}` : "Renseignez la connexion, puis réglez les droits."}
      footer={
        <button type="button" onClick={onClose}>
          Fermer
        </button>
      }
    >
      <SegmentedControl
        label="Réglages du fournisseur"
        active={tab}
        onChange={(t) => setTab(t as typeof tab)}
        segments={[
          { id: "connection", label: "Connexion", icon: "link" },
          ...(saved
            ? [
                { id: "rights", label: "Comptes et droits", icon: "lock" as const },
                { id: "integration", label: "Adresses à donner", icon: "list" as const },
              ]
            : []),
        ]}
      />
      {tab === "connection" && (
        <form
          className="sso-form"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <div className="sso-two">
            <div className="field">
              <label htmlFor="sso-name">Nom affiché sur le bouton de connexion</label>
              <input id="sso-name" value={name} maxLength={80} required onChange={(e) => setName(e.target.value)} placeholder="Annuaire de l'entreprise" />
              <Err at="name" errors={errors} />
            </div>
            <div className="field">
              <label htmlFor="sso-slug">Identifiant</label>
              <input
                id="sso-slug"
                value={slug}
                disabled={Boolean(saved)}
                required
                pattern="[a-z0-9][a-z0-9\-]{1,38}[a-z0-9]"
                onChange={(e) => setSlug(e.target.value.toLowerCase())}
                placeholder="entreprise"
              />
              <p className="hint">Fait partie des adresses de connexion ; non modifiable ensuite.</p>
              <Err at="slug" errors={errors} />
            </div>
          </div>
          {kind === "oidc" ? (
            <section className="sso-sec" aria-label="Fournisseur OpenID Connect">
              <h3>OpenID Connect</h3>
              <div className="field">
                <label htmlFor="sso-issuer">Adresse du fournisseur (issuer)</label>
                <input
                  id="sso-issuer"
                  value={issuer}
                  required
                  onChange={(e) => setIssuer(e.target.value)}
                  placeholder="https://keycloak.exemple.fr/realms/entreprise"
                />
                <p className="hint">
                  L'adresse de découverte est déduite : <code>…/.well-known/openid-configuration</code>. Keycloak, Entra ID, Okta, Authentik, ADFS…
                </p>
                <Err at="issuer" errors={errors} />
              </div>
              <div className="sso-two">
                <div className="field">
                  <label htmlFor="sso-cid">Identifiant du client (client_id)</label>
                  <input id="sso-cid" value={clientId} required onChange={(e) => setClientId(e.target.value)} />
                  <Err at="client_id" errors={errors} />
                </div>
                <div className="field">
                  <label htmlFor="sso-secret">Secret du client</label>
                  <input
                    id="sso-secret"
                    type="password"
                    autoComplete="new-password"
                    value={secret}
                    onChange={(e) => {
                      setSecret(e.target.value);
                      setClearSecret(false);
                    }}
                    placeholder={saved?.has_secret ? "•••••••• (enregistré, inchangé)" : "Facultatif avec PKCE"}
                  />
                  <p className="hint">
                    Chiffré dans le coffre, jamais renvoyé ni affiché.
                    {saved?.has_secret && (
                      <>
                        {" "}
                        <label className="field-inline">
                          <input type="checkbox" checked={clearSecret} onChange={(e) => setClearSecret(e.target.checked)} /> Retirer le secret enregistré
                        </label>
                      </>
                    )}
                  </p>
                </div>
              </div>
              <div className="sso-two">
                <div className="field">
                  <label htmlFor="sso-scopes">Scopes</label>
                  <input id="sso-scopes" value={scopes} onChange={(e) => setScopes(e.target.value)} />
                  <Err at="scopes" errors={errors} />
                </div>
                <div className="field">
                  <label htmlFor="sso-auth">Authentification du client</label>
                  <select id="sso-auth" value={tokenAuth} onChange={(e) => setTokenAuth(e.target.value)}>
                    <option value="basic">client_secret_basic (en-tête)</option>
                    <option value="post">client_secret_post (corps)</option>
                  </select>
                </div>
              </div>
              <div className="field field-inline">
                <input id="sso-pkce" type="checkbox" checked={pkce} onChange={(e) => setPkce(e.target.checked)} />
                <label htmlFor="sso-pkce">Utiliser PKCE (recommandé ; à désactiver seulement si le fournisseur le refuse)</label>
              </div>
            </section>
          ) : (
            <section className="sso-sec" aria-label="Fournisseur SAML">
              <h3>SAML 2.0</h3>
              <div className="field">
                <label htmlFor="sso-murl">Importer les métadonnées du fournisseur</label>
                <div className="sso-copy">
                  <input
                    id="sso-murl"
                    value={metaUrl}
                    onChange={(e) => setMetaUrl(e.target.value)}
                    placeholder="https://adfs.exemple.fr/FederationMetadata/2007-06/FederationMetadata.xml"
                  />
                  <button type="button" disabled={!metaUrl.trim() || importMeta.isPending} onClick={() => importMeta.mutate()}>
                    Importer
                  </button>
                </div>
                <details>
                  <summary>… ou coller le XML</summary>
                  <textarea
                    aria-label="XML des métadonnées"
                    className="sso-cert"
                    value={metaXml}
                    onChange={(e) => setMetaXml(e.target.value)}
                    placeholder="<EntityDescriptor …>"
                  />
                  <button
                    type="button"
                    disabled={!metaXml.trim() || importMeta.isPending}
                    onClick={() => {
                      setMetaUrl("");
                      importMeta.mutate();
                    }}
                  >
                    Lire le XML
                  </button>
                </details>
                {importMeta.isSuccess && (
                  <p role="status" className="hint">
                    Métadonnées lues : les champs ci-dessous sont remplis. Vérifiez-les puis enregistrez.
                  </p>
                )}
                {importMeta.error && (
                  <p role="alert" className="sso-error">
                    {errorText(importMeta.error)}
                  </p>
                )}
              </div>
              <div className="field">
                <label htmlFor="sso-ent">Identifiant du fournisseur (entityID)</label>
                <input id="sso-ent" value={idpEntity} required onChange={(e) => setIdpEntity(e.target.value)} />
                <Err at="idp_entity_id" errors={errors} />
              </div>
              <div className="field">
                <label htmlFor="sso-url">Adresse de connexion (SSO URL, liaison HTTP-Redirect)</label>
                <input id="sso-url" value={idpUrl} required onChange={(e) => setIdpUrl(e.target.value)} />
                <Err at="idp_sso_url" errors={errors} />
              </div>
              <div className="field">
                <label htmlFor="sso-cert">Certificat(s) de signature du fournisseur (PEM)</label>
                <textarea
                  id="sso-cert"
                  className="sso-cert"
                  value={certs}
                  onChange={(e) => setCerts(e.target.value)}
                  placeholder={"-----BEGIN CERTIFICATE-----\n…\n-----END CERTIFICATE-----"}
                />
                <p className="hint">
                  Certificats publics, un à quatre (le deuxième sert au changement de certificat sans coupure). Une réponse non signée par l'un d'eux est
                  refusée.
                </p>
                <Err at="idp_certs" errors={errors} />
              </div>
              <div className="sso-two">
                <div className="field">
                  <label htmlFor="sso-fmt">Format de l'identifiant (NameID)</label>
                  <select id="sso-fmt" value={nameIdFormat} onChange={(e) => setNameIdFormat(e.target.value)}>
                    {listing.name_id_formats.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="sso-skew">Tolérance d'horloge (secondes)</label>
                  <input id="sso-skew" type="number" min={0} max={300} value={skew} onChange={(e) => setSkew(e.target.value)} />
                  <Err at="clock_skew" errors={errors} />
                </div>
              </div>
              <div className="field">
                <label htmlFor="sso-sp">Identifiant de ce service (facultatif)</label>
                <input
                  id="sso-sp"
                  value={spEntity}
                  onChange={(e) => setSpEntity(e.target.value)}
                  placeholder={saved?.endpoints.entity_id ?? "Par défaut : l'adresse des métadonnées"}
                />
              </div>
              <div className="field field-inline">
                <input id="sso-force" type="checkbox" checked={forceAuthn} onChange={(e) => setForceAuthn(e.target.checked)} />
                <label htmlFor="sso-force">Redemander le mot de passe à chaque connexion (ForceAuthn)</label>
              </div>
            </section>
          )}
          <section className="sso-sec" aria-label="Correspondance des attributs">
            <h3>{kind === "oidc" ? "Revendications (claims)" : "Attributs SAML"}</h3>
            <div className="sso-two">
              {(kind === "oidc" ? CLAIMS : ATTRS).map(([key, label, def]) => (
                <div className="field" key={key}>
                  <label htmlFor={`sso-map-${key}`}>{label}</label>
                  <input
                    id={`sso-map-${key}`}
                    value={mapping[key] ?? ""}
                    placeholder={def}
                    onChange={(e) => setMapping({ ...mapping, [key]: e.target.value })}
                  />
                  <Err at={`${kind === "oidc" ? "claims" : "attributes"}.${key}`} errors={errors} />
                </div>
              ))}
            </div>
            <div className="field">
              <label htmlFor="sso-dom">Domaines d'e-mail autorisés (facultatif)</label>
              <input id="sso-dom" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="exemple.fr, filiale.exemple.fr" />
              <p className="hint">Vide = tous. Sinon, une personne dont l'adresse est d'un autre domaine est refusée.</p>
              <Err at="allowed_domains" errors={errors} />
            </div>
          </section>
          {save.error && !Object.keys(errors).length && (
            <p role="alert" className="sso-error">
              {errorText(save.error)}
            </p>
          )}
          <div className="sso-actions">
            <button type="submit" className="primary" disabled={save.isPending}>
              {saved ? "Enregistrer" : "Créer le fournisseur"}
            </button>
            {saved && (
              <button type="button" disabled={test.isPending} onClick={() => test.mutate()}>
                Tester la configuration enregistrée
              </button>
            )}
            {save.isSuccess && (
              <span role="status" className="hint">
                Enregistré.
              </span>
            )}
          </div>
          {test.data && (
            <div role="status" className={`sso-result ${test.data.ok ? "ok" : "bad"}`}>
              {test.data.ok ? (
                <TestDetail kind={kind} detail={test.data.detail ?? {}} />
              ) : (
                <>
                  <strong>Échec :</strong> {test.data.message}
                </>
              )}
            </div>
          )}
        </form>
      )}
      {tab === "rights" && saved && (
        <Rights
          provider={saved}
          listing={listing}
          spaces={spaces}
          autoCreate={autoCreate}
          setAutoCreate={setAutoCreate}
          unmapped={unmapped}
          setUnmapped={setUnmapped}
          onSaved={(p) => {
            setSaved(p);
            refresh();
          }}
        />
      )}
      {tab === "integration" && saved && <Integration provider={saved} listing={listing} />}
    </Overlay>
  );
}

function TestDetail({ kind, detail }: { kind: Kind; detail: Record<string, unknown> }) {
  if (kind === "oidc") {
    return (
      <>
        <strong>Le fournisseur répond correctement.</strong>
        <ul>
          <li>
            Émetteur : <code>{String(detail.issuer)}</code>
          </li>
          <li>{String(detail.signing_keys)} clé(s) de signature publiée(s)</li>
          <li>PKCE (S256) : {detail.pkce_supported ? "pris en charge" : "non annoncé par le fournisseur"}</li>
        </ul>
      </>
    );
  }
  const certs = (detail.certificates as { subject: string; days_left: number; expired: boolean; sha256: string }[]) ?? [];
  return (
    <>
      <strong>{certs.some((c) => c.expired) ? "Un certificat est expiré." : "Les certificats sont lisibles et valides."}</strong>
      <ul>
        {certs.map((c) => (
          <li key={c.sha256}>
            {c.subject} — {c.expired ? "expiré" : `valide encore ${c.days_left} jour(s)`}
            <br />
            <small>
              SHA-256 : <code>{c.sha256}</code>
            </small>
          </li>
        ))}
      </ul>
      <p className="hint">Un essai complet se fait par une vraie connexion : le fournisseur ne peut pas être appelé sans une personne.</p>
    </>
  );
}

function CopyLine({ label, value }: { label: string; value: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="field">
      <span className="hint">{label}</span>
      <div className="sso-copy">
        <code>{value}</code>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(value);
            setDone(true);
            window.setTimeout(() => setDone(false), 1500);
          }}
          aria-label={`Copier : ${label}`}
        >
          {done ? "Copié" : "Copier"}
        </button>
      </div>
    </div>
  );
}

function Integration({ provider, listing }: { provider: Provider; listing: Listing }) {
  const e = provider.endpoints;
  return (
    <div className="sso-form">
      {!listing.public_url_configured && (
        <p role="note" className="sso-result bad">
          L'adresse publique de la plate-forme n'est pas réglée (<code>ORCH_PUBLIC_URL</code>) : les adresses ci-dessous viennent de la requête actuelle (
          {listing.base_url}). Derrière un proxy, réglez-la avant de les donner au fournisseur.
        </p>
      )}
      <section className="sso-sec" aria-label="Adresses à donner au fournisseur d'identité">
        <h3>À saisir chez le fournisseur d'identité</h3>
        {provider.kind === "oidc" ? (
          <>
            <CopyLine label="URI de redirection (Redirect URI / Valid redirect URIs)" value={e.callback ?? ""} />
            <ol className="sso-steps">
              <li>Créez un client « confidentiel » (ou public avec PKCE) de type OpenID Connect.</li>
              <li>Collez l'URI de redirection ci-dessus.</li>
              <li>Ajoutez aux jetons la revendication des groupes (Keycloak : mapper « Group Membership », Entra ID : revendication « groups »).</li>
            </ol>
          </>
        ) : (
          <>
            <CopyLine label="Identifiant du service (Entity ID / Audience)" value={e.entity_id ?? ""} />
            <CopyLine label="Adresse de réponse (ACS URL / Reply URL, liaison HTTP-POST)" value={e.acs ?? ""} />
            <CopyLine label="Métadonnées du service (à importer chez le fournisseur)" value={e.metadata ?? ""} />
            <ol className="sso-steps">
              <li>Chez le fournisseur, créez une « Relying Party » / application SAML avec les adresses ci-dessus.</li>
              <li>Faites signer l'assertion (ou la réponse) en SHA-256.</li>
              <li>Envoyez l'attribut des groupes (AD FS : règle « Send Group Membership as a Claim »).</li>
            </ol>
          </>
        )}
      </section>
      <section className="sso-sec" aria-label="Adresse de connexion directe">
        <h3>Connexion directe</h3>
        <CopyLine label="Lien de connexion (à partager ou mettre en favori)" value={e.login ?? ""} />
        <p className="hint">Les boutons de la page de connexion apparaissent dès que le fournisseur est actif.</p>
      </section>
    </div>
  );
}

const blank = (): Rule => ({ group: "", match: "exact", grant: "workspace", workspace: null, role: "viewer", user_group: "" });

interface RightsProps {
  provider: Provider;
  listing: Listing;
  spaces: Space[];
  autoCreate: boolean;
  setAutoCreate: (v: boolean) => void;
  unmapped: "allow" | "deny";
  setUnmapped: (v: "allow" | "deny") => void;
  onSaved: (p: Provider) => void;
}

function Rights({ provider, listing, spaces, autoCreate, setAutoCreate, unmapped, setUnmapped, onSaved }: RightsProps) {
  const loaded = useQuery({
    queryKey: ["platform", "sso", "rules", provider.slug],
    queryFn: () => api<{ results: Rule[] }>(`${P}/providers/${provider.slug}/rules/`),
  });
  const [rules, setRules] = useState<Rule[] | null>(null);
  const rows = rules ?? loaded.data?.results ?? [];
  const live = spaces.filter((s) => !s.archived);
  const set = (i: number, patch: Partial<Rule>) => setRules(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const [groups, setGroups] = useState("");
  const sim = useMutation<Simulation, ApiError>({
    mutationFn: () =>
      api(`${P}/providers/${provider.slug}/simulate/`, {
        json: {
          groups: groups
            .split("\n")
            .map((g) => g.trim())
            .filter(Boolean),
        },
      }),
  });
  const save = useMutation<unknown, ApiError>({
    mutationFn: async () => {
      const p = await api<Provider>(`${P}/providers/${provider.slug}/`, { method: "PATCH", json: { auto_create: autoCreate, unmapped } });
      await api(`${P}/providers/${provider.slug}/rules/`, {
        method: "PUT",
        json: { rules: rows.map((r) => ({ ...r, workspace: r.grant === "workspace" ? r.workspace : null })) },
      });
      onSaved(p);
      setRules(null);
      void loaded.refetch();
    },
  });
  const errors = fieldErrors(save.error);
  const toggle = useMutation<Provider, ApiError>({
    mutationFn: () => api<Provider>(`${P}/providers/${provider.slug}/`, { method: "PATCH", json: { enabled: !provider.enabled } }),
    onSuccess: onSaved,
  });
  return (
    <div className="sso-form">
      <section className="sso-sec" aria-label="Activation">
        <h3>État</h3>
        <label className="sso-toggle sso-toggle-lg">
          <input
            type="checkbox"
            className="adm-switch"
            checked={provider.enabled}
            disabled={toggle.isPending}
            onChange={() => toggle.mutate()}
            aria-label="Fournisseur actif"
          />
          <span>{provider.enabled ? "Actif : le bouton est sur la page de connexion" : "Désactivé : personne ne peut se connecter par ce fournisseur"}</span>
        </label>
        {toggle.error && (
          <p role="alert" className="sso-error">
            {errorText(toggle.error)}
          </p>
        )}
        <p className="hint">Désactiver ne retire aucun droit : les personnes ne peuvent simplement plus se connecter par ce fournisseur.</p>
      </section>
      <section className="sso-sec" aria-label="Politique des comptes">
        <h3>Comptes</h3>
        <div className="field field-inline">
          <input id="sso-auto" type="checkbox" checked={autoCreate} onChange={(e) => setAutoCreate(e.target.checked)} />
          <label htmlFor="sso-auto">Créer le compte à la première connexion</label>
        </div>
        <p className="hint">
          Sinon, seules les personnes déjà liées à ce fournisseur peuvent se connecter. Le compte est lié à l'identité du fournisseur, jamais à l'adresse
          e-mail.
        </p>
        <fieldset className="field" style={{ border: 0, padding: 0 }}>
          <legend>Quand aucun groupe de la personne ne correspond à une règle</legend>
          <div className="field-inline">
            <input id="sso-un-allow" type="radio" name="sso-unmapped" checked={unmapped === "allow"} onChange={() => setUnmapped("allow")} />
            <label htmlFor="sso-un-allow">Autoriser la connexion, sans aucun accès</label>
          </div>
          <div className="field-inline">
            <input id="sso-un-deny" type="radio" name="sso-unmapped" checked={unmapped === "deny"} onChange={() => setUnmapped("deny")} />
            <label htmlFor="sso-un-deny">Refuser la connexion (aucun compte n'est créé)</label>
          </div>
        </fieldset>
      </section>
      <section className="sso-sec" aria-label="Règles groupe vers droit">
        <h3>Groupes du fournisseur → droits</h3>
        <HelpTip caption="Comment ça marche">
          À chaque connexion, les groupes de la personne sont comparés à ces règles. Un espace n'est géré par ce fournisseur que s'il apparaît dans une règle :
          ses droits sont alors mis à jour (ajoutés, changés, retirés) à chaque connexion, mais seulement ceux que ce fournisseur a donnés. Un droit donné à la
          main n'est jamais modifié. Si plusieurs règles s'appliquent à un même espace, le rôle le plus élevé gagne.
        </HelpTip>
        <div className="sso-rule-list">
          {rows.map((r, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: lignes éditées sur place, sans identifiant stable avant l'enregistrement
            <fieldset key={i} className="sso-rule">
              <legend>Règle {i + 1}</legend>
              <div className="field">
                <label htmlFor={`sso-r${i}-g`}>Groupe du fournisseur</label>
                <input id={`sso-r${i}-g`} value={r.group} placeholder="orch-concepteurs" onChange={(e) => set(i, { group: e.target.value })} />
                <Err at={`rules.${i}.group`} errors={errors} />
              </div>
              <div className="field">
                <label htmlFor={`sso-r${i}-m`}>Correspondance</label>
                <select id={`sso-r${i}-m`} value={r.match} onChange={(e) => set(i, { match: e.target.value as Rule["match"] })}>
                  <option value="exact">Nom exact</option>
                  <option value="glob">Motif (* et ?)</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor={`sso-r${i}-t`}>Droit donné</label>
                <select id={`sso-r${i}-t`} value={r.grant} onChange={(e) => set(i, { grant: e.target.value as Rule["grant"] })}>
                  <option value="workspace">Rôle dans un espace</option>
                  <option value="platform_admin">Admin plate-forme</option>
                </select>
              </div>
              {r.grant === "workspace" ? (
                <>
                  <div className="field">
                    <label htmlFor={`sso-r${i}-w`}>Espace</label>
                    <select id={`sso-r${i}-w`} value={r.workspace ?? ""} onChange={(e) => set(i, { workspace: e.target.value || null })}>
                      <option value="">Choisir…</option>
                      {live.map((sp) => (
                        <option key={sp.slug} value={sp.slug}>
                          {sp.name}
                        </option>
                      ))}
                    </select>
                    <Err at={`rules.${i}.workspace`} errors={errors} />
                  </div>
                  <div className="field">
                    <label htmlFor={`sso-r${i}-r`}>Rôle</label>
                    <select id={`sso-r${i}-r`} value={r.role} onChange={(e) => set(i, { role: e.target.value })}>
                      {listing.roles.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field sso-span2">
                    <label htmlFor={`sso-r${i}-u`}>Groupe d'utilisateurs de l'espace</label>
                    <input
                      id={`sso-r${i}-u`}
                      value={r.user_group}
                      placeholder="Facultatif : créé s'il n'existe pas"
                      onChange={(e) => set(i, { user_group: e.target.value })}
                    />
                  </div>
                </>
              ) : (
                <p className="hint sso-rule-na">
                  Accès à l'administration de toute la plate-forme, retiré à la connexion suivante si le groupe disparaît. Les administrateurs ne sont jamais
                  tous retirés.
                </p>
              )}
              <div className="sso-rule-x">
                <button type="button" aria-label={`Supprimer la règle ${i + 1}`} onClick={() => setRules(rows.filter((_, k) => k !== i))}>
                  Supprimer la règle
                </button>
              </div>
            </fieldset>
          ))}
          {rows.length === 0 && <p className="hint">Aucune règle : personne ne reçoit de droit par ce fournisseur.</p>}
        </div>
        <div className="sso-actions">
          <button type="button" onClick={() => setRules([...rows, blank()])}>
            Ajouter une règle
          </button>
          <button type="button" className="primary" disabled={save.isPending} onClick={() => save.mutate()}>
            Enregistrer les droits
          </button>
          {save.isSuccess && (
            <span role="status" className="hint">
              Enregistré.
            </span>
          )}
        </div>
        {save.error && !Object.keys(errors).length && (
          <p role="alert" className="sso-error">
            {errorText(save.error)}
          </p>
        )}
        <details>
          <summary>Rôles disponibles</summary>
          <ul className="sso-roles">
            <li>
              <strong>Administrateur, Responsable d'espace</strong> : tout dans l'espace, y compris ses membres.
            </li>
            <li>
              <strong>Concepteur</strong> : crée, modifie, publie, lance ; gère les connexions.
            </li>
            <li>
              <strong>Opérateur</strong> : lance et consulte.
            </li>
            <li>
              <strong>Approbateur</strong> : consulte et approuve.
            </li>
            <li>
              <strong>Lecteur</strong> : consulte.
            </li>
            <li>
              <strong>Catalogue</strong> : lance seulement ce que ses groupes d'utilisateurs autorisent.
            </li>
          </ul>
        </details>
      </section>
      <section className="sso-sec sso-sim" aria-label="Simulation">
        <h3>Simuler</h3>
        <label htmlFor="sso-groups">Groupes d'une personne (un par ligne)</label>
        <textarea id="sso-groups" value={groups} onChange={(e) => setGroups(e.target.value)} placeholder={"orch-concepteurs\norch-exploitation"} />
        <div className="sso-actions">
          <button type="button" disabled={sim.isPending} onClick={() => sim.mutate()}>
            Simuler avec les règles enregistrées
          </button>
        </div>
        {sim.data && <SimOut sim={sim.data} roles={listing.roles} />}
        {sim.error && (
          <p role="alert" className="sso-error">
            {errorText(sim.error)}
          </p>
        )}
      </section>
    </div>
  );
}

function SimOut({ sim, roles }: { sim: Simulation; roles: { id: string; label: string }[] }) {
  const roleName = (id: string | null) => (id ? (roles.find((r) => r.id === id)?.label ?? id) : "aucun accès");
  return (
    <div className="sso-sim-out" role="status">
      <strong>{sim.login_allowed ? "Connexion autorisée" : sim.login_denied_reason}</strong>
      {sim.matched.length > 0 ? (
        <ul>
          {sim.matched.map((m) => (
            <li key={`${m.rule}-${m.group}`}>
              « {m.group} » correspond à la règle « {m.pattern} »
            </li>
          ))}
        </ul>
      ) : (
        <span className="hint">Aucun groupe ne correspond à une règle.</span>
      )}
      <ul>
        {sim.workspaces.map((w) => (
          <li key={w.workspace}>
            Espace <strong>{w.name}</strong> : {roleName(w.role)}
            {w.user_groups.length ? ` · groupe(s) : ${w.user_groups.join(", ")}` : ""}
          </li>
        ))}
        {sim.manages_platform_admin && <li>Administrateur de la plate-forme : {sim.platform_admin ? "oui" : "non"}</li>}
      </ul>
    </div>
  );
}
