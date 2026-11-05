import { useState } from "react";
import { HelpTip } from "../components/HelpTip";
import { Overlay } from "../components/Overlay";
import type { ControlPrefill } from "./controls";
import { FormIcon } from "./FormIcon";
import { ModeTabs } from "./ModeTabs";
import { ServiceReading, ServiceTry } from "./ServiceFields";
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
