import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useState, type CSSProperties } from "react";
import { api, type ApiError } from "../../api";
import {
  contrast,
  DEFAULT_ACCENT,
  isHex,
  MIN_CONTRAST,
  NAV_PRESETS,
  navTheme,
  nearestAccessible,
  PRESETS,
  report,
  soft,
  themeVars,
} from "../../branding/palette";
import { setBranding, toPublic, type AssetRef, type Branding } from "../../branding/store";
import { Badge, Confirm, Section } from "../../components/AdminUi";
import { HelpTip } from "../../components/HelpTip";
import { Tabs } from "../../components/Tabs";
import { errorText } from "../../lib";

type Kind = "logo" | "logo_dark" | "icon";
interface AdminBranding extends Branding {
  defaults: { name: string; accent: string };
  limits: { image_bytes: number; side: number; formats: string[] };
  stored: { product_name: string; accent: string; topbar_bg: string; sidebar_bg: string };
  updated_by: string;
}
interface Draft {
  product_name: string;
  tagline: string;
  login_message: string;
  footer_text: string;
  footer_url: string;
  accent: string;
  topbar_bg: string;
  sidebar_bg: string;
  login_style: Branding["login_style"];
  logo_has_name: boolean;
}

const STYLES: [Branding["login_style"], string, string][] = [
  ["plain", "Sobre", "Fond neutre de l'application."],
  ["tint", "Fond teinté", "Fond doux à la couleur d'accent."],
  ["gradient", "Dégradé", "Dégradé doux de la couleur d'accent."],
];
const KINDS: { kind: Kind; title: string; help: string }[] = [
  {
    kind: "logo",
    title: "Logo",
    help: "Affiché en haut à gauche et sur la page de connexion. Un logo horizontal sur fond transparent donne le meilleur rendu.",
  },
  {
    kind: "logo_dark",
    title: "Logo pour fond sombre",
    help: "Facultatif. Affiché dans la barre du haut quand son fond est sombre (un logo foncé y serait illisible) ; à défaut, le logo ci-dessus est utilisé.",
  },
  { kind: "icon", title: "Icône", help: "Icône de l'onglet du navigateur. Une image à peu près carrée ; à défaut, le logo est utilisé." },
];
const ko = (n: number) => `${Math.max(1, Math.round(n / 1024))} Ko`;
const fromServer = (d: AdminBranding): Draft => ({
  product_name: d.stored.product_name,
  tagline: d.tagline,
  login_message: d.login_message,
  footer_text: d.footer.text,
  footer_url: d.footer.url,
  accent: d.stored.accent,
  topbar_bg: d.stored.topbar_bg,
  sidebar_bg: d.stored.sidebar_bg,
  login_style: d.login_style,
  logo_has_name: d.logo_has_name,
});
const navBg = (v: string): string => (isHex(v) ? v : "#ffffff");

function Preview({ draft, name, data }: { draft: Draft; name: string; data: AdminBranding }) {
  const [view, setView] = useState("app");
  const accent = isHex(draft.accent) ? draft.accent : DEFAULT_ACCENT;
  const vars = themeVars(accent, true) as CSSProperties;
  const tb = navTheme(navBg(draft.topbar_bg));
  const sb = navTheme(navBg(draft.sidebar_bg));
  const logo = tb.dark && data.logo_dark ? data.logo_dark : data.logo;
  const mark = (fg?: string) =>
    logo ? (
      <>
        <img src={logo.url} alt="" style={{ height: 26, width: "auto", maxWidth: 160 }} />
        {!draft.logo_has_name && <strong style={{ color: fg }}>{name}</strong>}
      </>
    ) : (
      <>
        <span className="logo-mono" aria-hidden="true" style={{ width: 26, height: 26, fontSize: 14 }}>
          {[...name][0]?.toUpperCase()}
        </span>
        <strong style={{ color: fg }}>{name}</strong>
      </>
    );
  return (
    <div className="brand-sticky">
      <Tabs
        label="Aperçu"
        active={view}
        onChange={setView}
        tabs={[
          {
            id: "app",
            label: "Aperçu : application",
            content: (
              <div className="brand-preview" style={vars} role="img" aria-label="Aperçu de l'application avec les réglages en cours">
                <header style={{ background: tb.bg, color: tb.fg, borderBottomColor: tb.border }}>
                  {mark(tb.fg)}
                  <span style={{ marginLeft: "auto", padding: "3px 10px", borderRadius: 8, background: tb.hover, fontWeight: 600 }}>Espace</span>
                  <span style={{ color: tb.muted }}>admin</span>
                </header>
                <div className="bp-body">
                  <aside style={{ background: sb.bg, color: sb.fg, borderRightColor: sb.border }}>
                    <small style={{ color: sb.muted, textTransform: "uppercase", fontSize: 10, padding: "0 10px" }}>Utiliser</small>
                    <span style={{ background: sb.active, fontWeight: 700 }}>Accueil</span>
                    <span>Workflows</span>
                    <span>Connexions</span>
                  </aside>
                  <div className="bp-main">
                    <strong>Exemple de page</strong>
                    <span className="bp-link">Un lien</span>
                    <span className="bp-btn">Un bouton</span>
                  </div>
                </div>
              </div>
            ),
          },
          {
            id: "login",
            label: "Aperçu : connexion",
            content: (
              <div
                className={`brand-preview login-${draft.login_style}`}
                style={vars}
                role="img"
                aria-label="Aperçu de la page de connexion avec les réglages en cours"
              >
                <div
                  className="bp-login"
                  style={{
                    background:
                      draft.login_style === "plain"
                        ? "var(--bg)"
                        : draft.login_style === "tint"
                          ? "var(--accent-soft)"
                          : "linear-gradient(135deg, var(--accent-soft), color-mix(in srgb, var(--accent) 22%, white))",
                  }}
                >
                  <div className="bp-card">
                    {data.logo ? <img src={data.logo.url} alt="" style={{ height: 26, width: "auto", maxWidth: 160 }} /> : mark()}
                    {draft.tagline && <span style={{ color: "var(--muted)" }}>{draft.tagline}</span>}
                    {draft.login_message && (
                      <span style={{ background: "var(--accent-soft)", padding: "6px 8px", borderRadius: 8 }}>{draft.login_message}</span>
                    )}
                    <span className="bp-btn" style={{ background: "var(--accent)", color: "var(--accent-fg)", padding: "6px 14px", borderRadius: 8 }}>
                      Se connecter
                    </span>
                  </div>
                  {draft.footer_text && <small style={{ marginTop: 8, color: "var(--muted)" }}>{draft.footer_text}</small>}
                </div>
              </div>
            ),
          },
        ]}
      />
      <p className="hint">L'aperçu suit vos réglages avant l'enregistrement. Les images, elles, sont enregistrées dès que vous les choisissez.</p>
    </div>
  );
}

function AssetSlot({
  kind,
  title,
  help,
  asset,
  data,
  onDone,
}: {
  kind: Kind;
  title: string;
  help: string;
  asset: AssetRef | null;
  data: AdminBranding;
  onDone: (d: AdminBranding) => void;
}) {
  const id = useId();
  const [err, setErr] = useState<string | null>(null);
  const send = useMutation<AdminBranding, ApiError, File>({
    mutationFn: (file) => {
      const form = new FormData();
      form.append("file", file);
      return api<AdminBranding>(`/platform/branding/assets/${kind}/`, { method: "PUT", form });
    },
    onSuccess: (d) => {
      setErr(null);
      onDone(d);
    },
    onError: (e) => setErr(errorText(e)),
  });
  const remove = useMutation<AdminBranding, ApiError>({
    mutationFn: () => api<AdminBranding>(`/platform/branding/assets/${kind}/`, { method: "DELETE" }),
    onSuccess: (d) => {
      setErr(null);
      onDone(d);
    },
    onError: (e) => setErr(errorText(e)),
  });
  const pick = (f: File | undefined) => {
    if (!f) return;
    if (f.size > data.limits.image_bytes) {
      setErr(`Le fichier dépasse ${ko(data.limits.image_bytes)}.`);
      return;
    }
    send.mutate(f);
  };
  return (
    <div className="asset-slot">
      <div className={`asset-thumb${kind === "logo_dark" ? " asset-thumb-dark" : ""}`}>
        {asset ? <img src={asset.url} alt={`${title} actuel`} /> : <span className="hint">Par défaut</span>}
      </div>
      <div>
        <strong>{title}</strong>{" "}
        {asset && (
          <Badge tone="info">
            {asset.content_type.replace("image/", "").replace("svg+xml", "svg").toUpperCase()}
            {asset.bytes ? ` · ${ko(asset.bytes)}` : ""}
          </Badge>
        )}
        <p className="hint">{help}</p>
        <div className="actions">
          <label htmlFor={id} className="button">
            {asset ? "Remplacer…" : "Choisir une image…"}
          </label>
          <input
            id={id}
            className="sr-only"
            type="file"
            accept="image/png,image/jpeg,image/webp,image/svg+xml"
            disabled={send.isPending}
            onChange={(e) => {
              pick(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          {asset && (
            <button type="button" onClick={() => remove.mutate()} disabled={remove.isPending} aria-label={`Retirer ${title.toLowerCase()}`}>
              Retirer
            </button>
          )}
        </div>
        {send.isPending && (
          <p role="status" className="hint">
            Envoi en cours…
          </p>
        )}
        {err && (
          <p role="alert" className="field-error">
            Erreur : {err}
          </p>
        )}
      </div>
    </div>
  );
}

/** Fond d'une barre : palettes, sélecteur, valeur `#rrggbb`, et ce qui s'en déduit (texte, icônes, survol, élément actif) avec ses contrastes. */
function BarColor({
  id,
  title,
  hint,
  value,
  accent,
  onChange,
}: {
  id: string;
  title: string;
  hint: string;
  value: string;
  accent: string;
  onChange: (v: string) => void;
}) {
  const bad = value !== "" && !isHex(value);
  const t = navTheme(navBg(value));
  const rows: [string, number][] = [
    ["Texte et icônes", contrast(t.fg, t.bg)],
    ["Texte atténué (titres de groupes, identifiant)", contrast(t.muted, t.bg)],
    ["Texte d'un élément survolé", contrast(t.fg, t.hover)],
    ["Texte de l'élément actif", contrast(t.fg, t.active)],
  ];
  const presets = [...NAV_PRESETS, { name: "Couleur d'accent", bg: accent }, { name: "Accent très clair", bg: soft(accent) }];
  return (
    <Section title={title} hint={hint}>
      <ul className="swatches" aria-label={`Fonds proposés : ${title.toLowerCase()}`}>
        {presets.map((p) => (
          <li key={p.name}>
            <button type="button" className="swatch" aria-pressed={value.toLowerCase() === p.bg.toLowerCase()} onClick={() => onChange(p.bg)}>
              <i style={{ background: p.bg }} />
              {p.name}
            </button>
          </li>
        ))}
      </ul>
      <div className="field">
        <label htmlFor={id}>Couleur de fond</label>
        <div className="color-row">
          <input
            aria-label={`Sélecteur de couleur : ${title.toLowerCase()}`}
            type="color"
            value={isHex(value) ? value : "#ffffff"}
            onChange={(e) => onChange(e.target.value)}
          />
          <input
            id={id}
            type="text"
            value={value}
            placeholder="Blanc d'origine"
            maxLength={7}
            spellCheck={false}
            aria-invalid={bad}
            onChange={(e) => onChange(e.target.value.trim())}
          />
          {value !== "" && (
            <button type="button" onClick={() => onChange("")}>
              Couleur d'origine
            </button>
          )}
        </div>
      </div>
      {bad ? (
        <p role="status" className="field-error">
          La couleur doit s'écrire #rrggbb (par exemple #0f172a).
        </p>
      ) : (
        <div role="status">
          <p className="hint">
            <Badge tone="ok">Lisible</Badge> Le texte ({t.dark ? "blanc" : "foncé"}), les icônes, le survol et l'élément actif se règlent tout seuls d'après ce
            fond.
          </p>
          <ul className="contrast-list">
            {rows.map(([label, v]) => (
              <li key={label}>
                <span>{label}</span>
                <span className={v >= MIN_CONTRAST ? "ok" : "ko"}>
                  {v.toFixed(2)}:1 {v >= MIN_CONTRAST ? "✓" : "✗"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}

/** Image de marque de la plate-forme (ADR 0033) : nom, logos, couleurs (accent, barres), page de connexion. Réservé aux administrateurs de la plate-forme ; le serveur revérifie tout. */
export function BrandingTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["platform", "branding"], queryFn: () => api<AdminBranding>("/platform/branding/") });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [tab, setTab] = useState("identity");
  const [confirmReset, setConfirmReset] = useState(false);
  const [saved, setSaved] = useState(false);
  const data = q.data;
  useEffect(() => {
    if (data && !draft) setDraft(fromServer(data));
  }, [data, draft]);
  const done = (d: AdminBranding, keepDraft = true) => {
    qc.setQueryData(["platform", "branding"], d);
    setBranding(toPublic(d));
    void qc.invalidateQueries({ queryKey: ["platform", "audit"] });
    if (!keepDraft) setDraft(fromServer(d));
  };
  const save = useMutation<AdminBranding, ApiError, Draft>({
    mutationFn: (d) => api<AdminBranding>("/platform/branding/", { method: "PATCH", json: d }),
    onSuccess: (d) => {
      done(d, false);
      setSaved(true);
    },
  });
  const reset = useMutation<AdminBranding, ApiError>({
    mutationFn: () => api<AdminBranding>("/platform/branding/reset/", { method: "POST" }),
    onSuccess: (d) => {
      done(d, false);
      setConfirmReset(false);
      setSaved(true);
    },
  });
  const changed = useMemo(() => !!(data && draft) && JSON.stringify(fromServer(data)) !== JSON.stringify(draft), [data, draft]);
  if (q.error)
    return (
      <p role="alert" className="field-error">
        Erreur : {errorText(q.error)}
      </p>
    );
  if (!data || !draft) return <p role="status">Chargement…</p>;
  const set = (patch: Partial<Draft>) => {
    setSaved(false);
    save.reset();
    setDraft({ ...draft, ...patch });
  };
  const effective = isHex(draft.accent) ? draft.accent : DEFAULT_ACCENT;
  const rep = report(effective);
  const badHex = draft.accent !== "" && !isHex(draft.accent);
  const colorBlocked = badHex || !rep.ok;
  const barsBad = (draft.topbar_bg !== "" && !isHex(draft.topbar_bg)) || (draft.sidebar_bg !== "" && !isHex(draft.sidebar_bg));
  const blocked = colorBlocked || barsBad;
  const name = draft.product_name.trim() || data.defaults.name;
  const rows: [string, number][] = [
    ["Texte de la couleur sur fond blanc", rep.onWhite],
    ["… sur le fond des pages", rep.onPage],
    ["… sur son fond doux (liens, onglet actif)", rep.onSoft],
    ["Texte blanc sur la couleur (boutons)", rep.buttonText],
  ];

  const identity = (
    <Section title="Identité" hint="Le nom remplace « Fluxonata » dans l'en-tête, l'onglet du navigateur et la page de connexion.">
      <div className="field">
        <label htmlFor="br-name">Nom du produit</label>
        <input
          id="br-name"
          value={draft.product_name}
          maxLength={60}
          placeholder={data.defaults.name}
          onChange={(e) => set({ product_name: e.target.value })}
        />
      </div>
      <div className="field">
        <label htmlFor="br-tag">Slogan (page de connexion)</label>
        <input id="br-tag" value={draft.tagline} maxLength={140} onChange={(e) => set({ tagline: e.target.value })} />
      </div>
    </Section>
  );

  const logos = (
    <Section
      title="Logos"
      hint={`${data.limits.formats.join(", ")} · ${ko(data.limits.image_bytes)} au plus · ${data.limits.side} × ${data.limits.side} pixels au plus. Un SVG est relu et nettoyé : aucun script n'y est conservé.`}
    >
      {KINDS.map((k) => (
        <AssetSlot key={k.kind} {...k} asset={data[k.kind]} data={data} onDone={(d) => done(d)} />
      ))}
      <div className="field field-inline">
        <input id="br-hasname" type="checkbox" checked={draft.logo_has_name} onChange={(e) => set({ logo_has_name: e.target.checked })} />
        <label htmlFor="br-hasname">Mon logo contient déjà mon nom (ne pas l'écrire à côté)</label>
      </div>
    </Section>
  );

  const colors = (
    <Section
      title="Couleur d'accent"
      hint="Boutons, liens, onglets actifs et contour de focus. Les autres couleurs (succès, erreur, avertissement) gardent leur sens et ne changent pas."
    >
      <ul className="swatches" aria-label="Palettes proposées">
        {PRESETS.map((p) => (
          <li key={p.accent}>
            <button
              type="button"
              className="swatch"
              aria-pressed={effective.toLowerCase() === p.accent}
              onClick={() => set({ accent: p.accent === DEFAULT_ACCENT ? "" : p.accent })}
            >
              <i style={{ background: p.accent }} />
              {p.name}
            </button>
          </li>
        ))}
      </ul>
      <div className="field">
        <label htmlFor="br-accent-hex">Couleur personnalisée</label>
        <div className="color-row">
          <input
            aria-label="Sélecteur de couleur"
            type="color"
            value={isHex(effective) ? effective : DEFAULT_ACCENT}
            onChange={(e) => set({ accent: e.target.value })}
          />
          <input
            id="br-accent-hex"
            type="text"
            value={draft.accent}
            placeholder={DEFAULT_ACCENT}
            maxLength={7}
            spellCheck={false}
            aria-invalid={colorBlocked}
            aria-describedby="br-contrast"
            onChange={(e) => set({ accent: e.target.value.trim() })}
          />
          {draft.accent !== "" && (
            <button type="button" onClick={() => set({ accent: "" })}>
              Couleur d'origine
            </button>
          )}
          {!badHex && !rep.ok && (
            <button type="button" onClick={() => set({ accent: nearestAccessible(effective) })}>
              Ajuster automatiquement
            </button>
          )}
        </div>
      </div>
      <div id="br-contrast" role="status">
        {badHex ? (
          <p className="field-error">La couleur doit s'écrire #rrggbb (par exemple #0e7490).</p>
        ) : rep.ok ? (
          <p className="hint">
            <Badge tone="ok">Lisible</Badge> Tous les contrastes atteignent {MIN_CONTRAST}:1.
          </p>
        ) : (
          <p className="field-error">
            Cette couleur est trop claire : {rep.worst.toFixed(2)}:1, il faut au moins {MIN_CONTRAST}:1. Le bouton « Ajuster automatiquement » assombrit la
            teinte jusqu'au seuil.
          </p>
        )}
        {!badHex && (
          <ul className="contrast-list">
            {rows.map(([label, v]) => (
              <li key={label}>
                <span>{label}</span>
                <span className={v >= MIN_CONTRAST ? "ok" : "ko"}>
                  {v.toFixed(2)}:1 {v >= MIN_CONTRAST ? "✓" : "✗"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );

  const menus = (
    <>
      <BarColor
        id="br-topbar"
        title="Barre du haut"
        hint="Le fond de la barre qui porte le logo, l'espace, l'environnement, la recherche et le compte."
        value={draft.topbar_bg}
        accent={effective}
        onChange={(v) => set({ topbar_bg: v })}
      />
      <BarColor
        id="br-sidebar"
        title="Menu de gauche"
        hint="Le fond du menu de navigation (entrées, groupes, sous-menus)."
        value={draft.sidebar_bg}
        accent={effective}
        onChange={(v) => set({ sidebar_bg: v })}
      />
      <div className="actions">
        <button type="button" disabled={draft.topbar_bg === draft.sidebar_bg} onClick={() => set({ sidebar_bg: draft.topbar_bg })}>
          Appliquer la couleur de la barre du haut au menu de gauche
        </button>
      </div>
      <p className="hint">Sur un fond sombre, ajoutez un « logo pour fond sombre » dans l'onglet Logos : un logo foncé y serait illisible.</p>
    </>
  );

  const login = (
    <Section title="Page de connexion" hint="Ce que voit une personne avant de se connecter.">
      <fieldset className="field">
        <legend>Fond</legend>
        {STYLES.map(([v, label, help]) => (
          <div key={v} className="field-inline">
            <input id={`br-style-${v}`} type="radio" name="br-style" checked={draft.login_style === v} onChange={() => set({ login_style: v })} />
            <label htmlFor={`br-style-${v}`}>
              {label} <span className="hint">— {help}</span>
            </label>
          </div>
        ))}
      </fieldset>
      <div className="field">
        <label htmlFor="br-msg">Message d'accueil</label>
        <textarea id="br-msg" rows={2} maxLength={300} value={draft.login_message} onChange={(e) => set({ login_message: e.target.value })} />
        <p className="hint">Par exemple : « Accès réservé au personnel. En cas de difficulté, contactez le support. » Texte simple, sans lien.</p>
      </div>
      <div className="field">
        <label htmlFor="br-foot">Texte du pied de page</label>
        <input id="br-foot" maxLength={200} value={draft.footer_text} onChange={(e) => set({ footer_text: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor="br-url">Lien du pied de page (facultatif)</label>
        <input
          id="br-url"
          maxLength={300}
          value={draft.footer_url}
          placeholder="https://support.exemple.fr ou mailto:support@exemple.fr"
          onChange={(e) => set({ footer_url: e.target.value.trim() })}
        />
      </div>
    </Section>
  );

  return (
    <>
      <div className="toolbar-row">
        <span style={{ flex: 1 }}>
          <HelpTip caption="À savoir">
            L'image de marque s'applique à toute la plate-forme, y compris à la page de connexion et aux formulaires publics. Les couleurs sont contrôlées pour
            rester lisibles (contraste WCAG 2.2 AA) : une couleur d'accent trop claire est refusée, et le texte des barres se règle tout seul d'après leur fond.
            Chaque changement est consigné dans le journal.
          </HelpTip>
        </span>
        <button type="button" onClick={() => setConfirmReset(true)}>
          Rétablir l'image d'origine
        </button>
      </div>
      <div className="brand-grid">
        <div>
          <Tabs
            label="Réglages de l'image de marque"
            active={tab}
            onChange={setTab}
            tabs={[
              { id: "identity", label: "Identité", content: identity },
              { id: "logos", label: "Logos", content: logos },
              { id: "colors", label: "Couleurs", badge: colorBlocked ? "!" : undefined, badgeTone: "alert", content: colors },
              { id: "menus", label: "Barres", badge: barsBad ? "!" : undefined, badgeTone: "alert", content: menus },
              { id: "login", label: "Connexion", content: login },
            ]}
          />
          <div className="actions brand-save">
            <button type="button" className="primary" disabled={!changed || blocked || save.isPending} onClick={() => save.mutate(draft)}>
              Enregistrer
            </button>
            {changed && (
              <button
                type="button"
                onClick={() => {
                  setDraft(fromServer(data));
                  save.reset();
                }}
              >
                Annuler les modifications
              </button>
            )}
            {changed && blocked && <span className="hint">Corrigez la couleur signalée (!) pour enregistrer.</span>}
            {saved && !changed && (
              <span role="status" className="hint">
                Enregistré : l'image de marque est appliquée.
              </span>
            )}
            {data.updated_by && !changed && !saved && <span className="hint">Dernière modification par {data.updated_by}.</span>}
          </div>
          {save.error && (
            <p role="alert" className="field-error">
              Erreur : {errorText(save.error)}
            </p>
          )}
        </div>
        <Preview draft={draft} name={name} data={data} />
      </div>
      {confirmReset && (
        <Confirm
          title="Rétablir l'image d'origine ?"
          confirmLabel="Rétablir"
          busy={reset.isPending}
          onCancel={() => setConfirmReset(false)}
          onConfirm={() => reset.mutate()}
        >
          <p>
            Le nom, les logos, les couleurs (accent et barres), le slogan, le message et le pied de page reviennent aux valeurs d'origine de Fluxonata. Les
            images téléversées sont supprimées.
          </p>
          {reset.error && (
            <p role="alert" className="field-error">
              Erreur : {errorText(reset.error)}
            </p>
          )}
        </Confirm>
      )}
    </>
  );
}
