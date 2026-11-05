import { useMemo, useState } from "react";
import { Overlay } from "../../components/Overlay";
import { FormIcon, IconLabel } from "../../forms/FormIcon";
import type { FormCard, FormFull } from "../../forms/library";
import "./inputform.css";

interface Props {
  choices: FormCard[];
  current: { form: string; version: string | null };
  versions: FormFull["versions"];
  canCreate: boolean;
  creating: boolean;
  onPickForm: (slug: string) => void;
  onApply: (form: string, version: string | null) => void;
  onCreate: () => void;
  onClose: () => void;
}

/** Choosing the library form (and the version) of the workflow, in a dialog: a searchable list of cards, the version below, applied on « Utiliser ». */
export function FormChooser({ choices, current, versions, canCreate, creating, onPickForm, onApply, onCreate, onClose }: Props) {
  const [form, setForm] = useState(current.form);
  const [version, setVersion] = useState<string>(current.version ?? "");
  const [q, setQ] = useState("");
  const shown = useMemo(() => choices.filter((f) => !q || `${f.name} ${f.description} ${f.tags.join(" ")}`.toLowerCase().includes(q.toLowerCase())).sort((a, b) => Number(b.slug === current.form) - Number(a.slug === current.form)), [choices, q, current.form]);
  const chosen = choices.find((f) => f.slug === form);
  const pick = (slug: string) => { setForm(slug); setVersion(slug === current.form ? current.version ?? "" : ""); onPickForm(slug); };
  return (
    <Overlay variant="modal" wide title="Formulaire d'entrée" description="Choisissez le formulaire de la bibliothèque qui sert de page de lancement à ce workflow." onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="button" className="primary" disabled={!form} onClick={() => onApply(form, version || null)}>Utiliser ce formulaire</button></>}>
      <div className="fc">
        <div className="field"><label htmlFor="fc-q">Rechercher</label><input id="fc-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Nom, description ou étiquette" /></div>
        <div className="fc-list" role="radiogroup" aria-label="Formulaires de la bibliothèque">
          {shown.map((f) => (
            <label key={f.slug} className={`fc-card${form === f.slug ? " on" : ""}`}>
              <input type="radio" name="fc-form" value={f.slug} checked={form === f.slug} onChange={() => pick(f.slug)} />
              <span className="fc-ic"><FormIcon name="design" size={20} /></span>
              <span className="fc-tx"><strong>{f.name}</strong><span className="hint">{f.description || `${f.fields} champ(s)`}</span></span>
              <span className="fc-v">v{f.latest_version?.version}</span>
            </label>
          ))}
          {shown.length === 0 && <p className="hint">Aucun formulaire publié ne correspond.</p>}
        </div>
        {form && chosen && (
          <div className="field">
            <label htmlFor="fc-ver">Version</label>
            <select id="fc-ver" value={version} onChange={(e) => setVersion(e.target.value)}>
              <option value="">Dernière publiée à la publication du workflow ({chosen.latest_version?.version})</option>
              {versions.map((v) => <option key={v.version} value={v.version}>{v.version}</option>)}
            </select>
          </div>
        )}
        {canCreate && (
          <p className="fc-new"><button type="button" onClick={onCreate} disabled={creating}><IconLabel icon="wand">Créer un formulaire à partir des entrées</IconLabel></button> <span className="hint">Un champ par entrée du workflow, publié puis utilisé tout de suite.</span></p>
        )}
      </div>
    </Overlay>
  );
}
