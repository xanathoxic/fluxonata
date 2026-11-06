import { useState, type ReactNode } from "react";
import { HelpTip } from "../components/HelpTip";
import { Overlay } from "../components/Overlay";
import type { ControlPrefill, ControlSpec } from "./controls";
import { FormIcon } from "./FormIcon";
import { ModeTabs } from "./ModeTabs";
import { CallCard, ServiceReading, ServiceTry, ShapingCard, SpotsCard } from "./ServiceFields";
import type { DataSource, FormField } from "./types";

interface Props { field: FormField; connections: string[]; onApply: (next: FormField) => void; onClose: () => void }
const valid = (c: { connection: string; path: string } | undefined): boolean => !c || (c.connection.trim() !== "" && c.path.startsWith("/"));

/** « Données depuis un service », in a window centred on the screen like the conditions: what the FIELD reads from a web service (the
 * control is reused by many forms, which each read their own): its options (if the control takes them) and/or its pre-filled value.
 * Each reading is tried on a sample reply, without any call. Nothing changes until « Appliquer ». */
export function ServiceOverlay({ field, connections, onApply, onClose }: Props) {
  const spec = field.control?.spec;
  const canOptions = Boolean(spec?.dynamic);
  const [tab, setTab] = useState<"options" | "prefill">(canOptions ? "options" : "prefill");
  const [source, setSource] = useState<DataSource | undefined>(field.source);
  const [prefill, setPrefill] = useState<ControlPrefill | undefined>(field.prefill);
  if (!spec) return null;
  const initial = { connection: connections[0] ?? "", path: "/" };
  const ok = valid(source) && valid(prefill);
  const apply = () => {
    const { source: _s, prefill: _p, ...rest } = field;
    onApply({ ...rest, ...(source ? { source } : {}), ...(prefill ? { prefill } : {}) });
    onClose();
  };
  const tabs = [
    ...(canOptions ? [{ id: "options", label: `Options${source ? " ✓" : ""}`, icon: "list2" as const }] : []),
    { id: "prefill", label: `Valeur préremplie${prefill ? " ✓" : ""}`, icon: "wand" as const },
  ];
  return (
    <Overlay variant="modal" wide title={`Service — « ${field.label || field.key} »`} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="button" className="primary" onClick={apply} disabled={!ok}>Appliquer</button></>}>
      <div className="sv-top">
        <div className="cc-tabs"><ModeTabs label="Données du champ" active={tab} onChange={(t) => setTab(t as "options" | "prefill")} tabs={tabs} /></div>
        <HelpTip label="Aide : données depuis un service">Propre à ce formulaire : le contrôle est réutilisable, chaque formulaire lit son propre service, par une connexion de l'espace (aucun secret côté navigateur). Rien n'est modifié avant « Appliquer ».</HelpTip>
        {!ok && <span role="alert" className="field-error sv-bad"><FormIcon name="warn" size={14} /> Connexion et chemin (« / … ») requis</span>}
      </div>
      {tab === "options" && canOptions ? (
        <div className="sv-pane">
          <div className="sv-switch field field-inline"><input id="fp-src-on" type="checkbox" checked={Boolean(source)} onChange={(e) => setSource(e.target.checked ? { ...initial } : undefined)} /><label htmlFor="fp-src-on">Options lues depuis un service</label>
            <HelpTip label="Aide : options">En plus des options fixes du contrôle. Les valeurs reçues d'un service ne sont contrôlées que dans leur forme : le workflow doit les traiter comme non vérifiées.</HelpTip></div>
          {source && (
            <div className="sv-cols">
              <ServiceReading idPrefix="fp-options" kind="options" value={source} connections={connections} onChange={(v) => setSource(v as DataSource)} />
              <ServiceTry kind="options" spec={spec} config={source} />
            </div>
          )}
        </div>
      ) : (
        <div className="sv-pane">
          <div className="sv-switch field field-inline"><input id="fp-pre-on" type="checkbox" checked={Boolean(prefill)} onChange={(e) => setPrefill(e.target.checked ? { ...initial } : undefined)} /><label htmlFor="fp-pre-on">Valeur préremplie par un service</label>
            <HelpTip label="Aide : valeur préremplie">La personne peut modifier la valeur. Une réponse qui ne respecte pas le contrôle est refusée en entier.</HelpTip></div>
          {prefill && (
            <div className="sv-cols">
              <ServiceReading idPrefix="fp-prefill" kind="prefill" value={prefill} connections={connections} onChange={(v) => setPrefill(v as ControlPrefill)} />
              <ServiceTry kind="prefill" spec={spec} config={prefill} />
            </div>
          )}
        </div>
      )}
    </Overlay>
  );
}

/** What the preview of a reading needs from a control; a plain choice field has none, so it is tried as an empty, dynamic choice. */
const CHOICE_SPEC: ControlSpec = { version: 1, mode: "choice", parts: [], dynamic: true };

type ChoiceTab = "call" | "read" | "shape" | "try";

/** « Liste dynamique » of a plain choice field (list, radio buttons, search box, boxes), in the same centred window as the services of a
 * custom control: one tab per step: the call, where to read in the reply, how to shape it, and a try on a sample reply (no call is made).
 * The panes stay mounted so a pasted sample survives a change of tab. Nothing changes until « Appliquer ». */
export function ChoiceSourceOverlay({ field, connections, onApply, onClose }: Props) {
  const [tab, setTab] = useState<ChoiceTab>("call");
  const [source, setSource] = useState<DataSource>(field.source ?? { connection: connections[0] ?? "", path: "/" });
  const ok = valid(source);
  const shaped = Boolean(source.items_as === "entries" || source.where || source.label_expr || source.sort);
  const apply = () => { onApply({ ...field, source }); onClose(); };
  const tabs = [
    { id: "call", label: `Appel${ok ? "" : " ⚠"}`, icon: "link" as const },
    { id: "read", label: "Réponse", icon: "search" as const },
    { id: "shape", label: `Mise en forme${shaped ? " ✓" : ""}`, icon: "sort" as const },
    { id: "try", label: "Essai", icon: "play" as const },
  ];
  // `hidden` seul ne suffit pas : `.sv-pane { display: grid }` l'emporte sur la feuille de style du navigateur, tous les panneaux resteraient affichés
  const pane = (id: ChoiceTab, children: ReactNode) => <div className="sv-pane" role="tabpanel" hidden={tab !== id} style={tab === id ? undefined : { display: "none" }}>{children}</div>;
  return (
    <Overlay variant="modal" wide title={`Liste dynamique — « ${field.label || field.key} »`} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="button" className="primary" onClick={apply} disabled={!ok}>Appliquer</button></>}>
      <div className="sv-top">
        <div className="cc-tabs"><ModeTabs label="Étapes de la liste dynamique" active={tab} onChange={(t) => setTab(t as ChoiceTab)} tabs={tabs} /></div>
        <HelpTip label="Aide : liste dynamique">Les choix sont lus dans un service au moment où la personne remplit le formulaire. Propre à ce formulaire : le serveur appelle le service par une connexion de l'espace (aucun secret côté navigateur). Les valeurs reçues ne sont contrôlées que dans leur forme : le workflow doit les traiter comme non vérifiées. Rien n'est modifié avant « Appliquer ».</HelpTip>
        {!ok && <span role="alert" className="field-error sv-bad"><FormIcon name="warn" size={14} /> Connexion et chemin (« / … ») requis</span>}
      </div>
      {pane("call", <CallCard idPrefix="fp-choice" value={source} connections={connections} onChange={(v) => setSource(v as DataSource)} />)}
      {pane("read", <SpotsCard idPrefix="fp-choice" kind="options" value={source} onChange={(v) => setSource(v as DataSource)} />)}
      {pane("shape", <ShapingCard idPrefix="fp-choice" value={source} onChange={setSource} />)}
      {pane("try", <ServiceTry kind="options" spec={CHOICE_SPEC} config={source} />)}
    </Overlay>
  );
}
