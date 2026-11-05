import { useEffect, useMemo, useRef, useState } from "react";
import { api, type ApiError } from "../../api";
import { useApp } from "../../context";
import { DefinitionForm, type LookupFn } from "../DefinitionForm";
import { FormIcon, type FormIconName } from "../FormIcon";
import { orderedFields } from "../layout";
import { Pop } from "../StyleEditor";
import { isBlock, type FormDefinition } from "../types";
import { Inspector } from "./Inspector";
import { parse, ruleKey, serialize, setDecl, setMany, type Rule } from "./model";
import { CUSTOM, PARTS, SCREENS, STATES, defaultTarget, describe, ruleOf, selectorsFor, targetFromElement, targetOf, type Scope, type Target } from "./targets";
import { THEMES, applyTheme } from "./themes";

const icon = (n: string) => n as FormIconName;
const GROUPS: [string, string][] = [["form", "Formulaire"], ["field", "Champs"], ["content", "Contenu"], ["action", "Boutons"]];

/** Menu rows with an icon and a name (the list of things to style is long: names stay readable). */
function MenuRow({ on, iconName, children, onClick }: { on: boolean; iconName: string; children: React.ReactNode; onClick: () => void }) {
  return <button type="button" role="menuitemradio" aria-checked={on} className={`cs-mrow${on ? " on" : ""}`} onClick={onClick}><FormIcon name={icon(iconName)} size={16} /><span>{children}</span></button>;
}

interface Props { def: FormDefinition; css: string | undefined; onCss: (css: string | undefined) => void; edit: boolean; lookup?: LookupFn; onCode: () => void }

/** The no-code CSS designer. A rule is a target (what), a state and a screen; the controls write declarations into it and the
 * CSS appears as it is written. Clicking the preview picks the target. The text is checked by the server, which writes the
 * canonical version that is stored. */
export function CssDesigner({ def, css, onCss, edit, lookup, onCode }: Props) {
  const { workspace } = useApp();
  const [rules, setRules] = useState<Rule[]>(() => parse(css) ?? []);
  const readable = parse(css) !== null;
  const [target, setTarget] = useState<Target>(defaultTarget);
  const [pick, setPick] = useState(true);
  const [force, setForce] = useState(true);
  const [panel, setPanel] = useState("type");
  const [problem, setProblem] = useState("");
  // the check could not be made (session over, network): the CSS is not refused, it is just not saved yet
  const [offline, setOffline] = useState("");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const committed = useRef(css ?? "");
  const seq = useRef(0);
  const zone = useRef<HTMLDivElement>(null);

  useEffect(() => { if ((css ?? "") !== committed.current) { committed.current = css ?? ""; setRules(parse(css) ?? []); } }, [css]);

  const apply = (next: Rule[]) => {
    setRules(next);
    const text = serialize(next);
    const mine = ++seq.current;
    window.setTimeout(() => {
      if (mine !== seq.current) return;
      api<{ css: { css: string; problems: { line: number; message: string }[] } }>(`/workspaces/${workspace.slug}/form-library/check/`, { json: { css: text } })
        .then((r) => {
          if (mine !== seq.current) return;
          const first = r.css.problems[0];
          setOffline("");
          if (first) { setProblem(`Ligne ${first.line} : ${first.message}`); return; }
          setProblem("");
          committed.current = r.css.css;
          onCss(r.css.css || undefined);
        })
        .catch((e: ApiError) => {
          if (mine !== seq.current) return;
          setOffline(e?.status === 401 || e?.status === 403 ? "Votre session a expiré : reconnectez-vous. Vos réglages restent dans cette page tant que vous ne la quittez pas." : "Le serveur n'a pas pu vérifier le CSS pour le moment : vos réglages sont conservés ici.");
        });
    }, 350);
  };

  const at = ruleOf(target);
  const selKey = ruleKey(at);
  const rule = rules.find((r) => ruleKey(r) === selKey);
  const usable = at.selectors.length > 0;
  const set = (p: string, v: string) => usable && apply(setDecl(rules, at, p, v));
  const many = (c: Record<string, string>) => usable && apply(setMany(rules, at, c));

  const text = useMemo(() => serialize(rules), [rules]);
  // preview: the selected rule is shown always on (a hover rule would otherwise need a hovering mouse), and the target is outlined
  const previewCss = useMemo(() => {
    const normal = selectorsFor({ ...target, state: "normal" });
    const staged = target.state !== "normal" || target.screen !== "all";
    let list = rules;
    if (force && staged && rule) list = [...rules.filter((r) => ruleKey(r) !== selKey), { ...rule, selectors: normal, media: null }];
    const base = serialize(list);
    const mark = pick && normal.length > 0 ? `${normal.join(",\n")} {\n  outline: 2px dashed #d97706;\n  outline-offset: 2px;\n}` : "";
    return [base, mark].filter(Boolean).join("\n\n");
  }, [rules, target, force, rule, selKey, pick]);

  const onPick = (e: React.MouseEvent) => {
    if (!pick) return;
    const root = zone.current?.querySelector(".fb-form");
    if (!root) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.type !== "click") return;
    const hit = targetFromElement(e.target as Element, root);
    if (!hit) return;
    setTarget((t) => {
      const part = PARTS.find((p) => p.id === hit.part);
      let scope: Scope = t.scope;
      if (e.shiftKey && hit.key) scope = { kind: "key", key: hit.key };
      else if (e.altKey && hit.type) scope = { kind: "type", type: hit.type };
      else if (t.scope.kind === "key" && hit.key) scope = { kind: "key", key: hit.key };
      else if (t.scope.kind === "type" && hit.type) scope = { kind: "type", type: hit.type };
      if (part && !part.scoped) scope = { kind: "all" };
      return { ...t, part: hit.part, scope, custom: undefined };
    });
  };

  const fields = orderedFields(def).filter((f) => !isBlock(f.type));
  const types = [...new Set(fields.map((f) => f.type))];
  const part = PARTS.find((p) => p.id === target.part);
  const state = STATES.find((s) => s.id === target.state) ?? (STATES[0] as (typeof STATES)[number]);
  const screen = SCREENS.find((s) => s.id === target.screen) ?? (SCREENS[0] as (typeof SCREENS)[number]);
  const scopeLabel = target.scope.kind === "key" ? `Champ : ${fields.find((f) => f.key === (target.scope as { key: string }).key)?.label ?? target.scope.key}` : target.scope.kind === "type" ? `Type : ${target.scope.type}` : "Tous les champs";
  // the selected rule is highlighted in the generated text
  const gen = useMemo(() => {
    if (!rule) return { before: text, mark: "", after: "" };
    const plain = serialize([{ ...rule, media: null }]);
    const want = rule.media ? plain.split("\n").map((l) => `  ${l}`).join("\n") : plain;
    const i = text.indexOf(want);
    return i < 0 ? { before: text, mark: "", after: "" } : { before: text.slice(0, i), mark: want, after: text.slice(i + want.length) };
  }, [text, rule]);

  if (!readable) {
    return (
      <div className="cs-designer cs-unreadable">
        <p role="status">Ce CSS a été écrit à la main sous une forme que le concepteur visuel ne sait pas relire. Il reste modifiable en mode code.</p>
        <button type="button" onClick={onCode}><FormIcon name="code" size={16} /> Ouvrir en mode code</button>
      </div>
    );
  }
  return (
    <div className="cs-designer">
      <div className="cs-bar" role="toolbar" aria-label="Cible du style">
        <button type="button" className={`fb-sbtn cs-wide${pick ? " on" : ""}`} aria-pressed={pick} aria-label="Sélectionner dans l'aperçu" title="Cliquer dans l'aperçu pour choisir l'élément (Maj + clic : ce champ · Alt + clic : ce type de champ)" onClick={() => setPick(!pick)}><FormIcon name="pointer" /></button>
        <span className="fb-sep" aria-hidden="true" />
        <Pop label={`Élément à styler : ${target.part === CUSTOM ? "sélecteur libre" : part?.label}`} icon={<span className="cs-chipbtn"><FormIcon name={icon(part?.icon ?? "code")} /><span>{target.part === CUSTOM ? "Sélecteur libre" : part?.label}</span></span>}>
          {(close) => (
            <>
            <div role="menu" aria-label="Élément à styler" className="cs-menu">
              {GROUPS.map(([g, name]) => (
                <div key={g}><div className="cs-mh" role="presentation">{name}</div>{PARTS.filter((p) => p.group === g).map((p) => <MenuRow key={p.id} on={target.part === p.id} iconName={p.icon} onClick={() => { setTarget({ ...target, part: p.id, scope: p.scoped ? target.scope : { kind: "all" }, custom: undefined }); close(); }}>{p.label}</MenuRow>)}</div>
              ))}
              <div className="cs-mh" role="presentation">Avancé</div>
              <MenuRow on={target.part === CUSTOM} iconName="code" onClick={() => setTarget({ ...target, part: CUSTOM, custom: target.custom ?? "" })}>Sélecteur libre…</MenuRow>
            </div>
              {target.part === CUSTOM && <input className="cs-text" aria-label="Sélecteur libre" placeholder=".fb-field > label, [data-key=&quot;nom&quot;] input" value={target.custom ?? ""} onChange={(e) => setTarget({ ...target, custom: e.target.value })} onKeyDown={(e) => { if (e.key === "Enter") close(); }} />}
            </>
          )}
        </Pop>
        {part?.scoped && (
          <Pop label={`Portée : ${scopeLabel}`} icon={<span className="cs-chipbtn"><FormIcon name="link" /><span>{scopeLabel}</span></span>}>
            {(close) => (
              <div role="menu" aria-label="Portée" className="cs-menu">
                <MenuRow on={target.scope.kind === "all"} iconName="width3" onClick={() => { setTarget({ ...target, scope: { kind: "all" } }); close(); }}>Tous les champs</MenuRow>
                {types.length > 0 && <div className="cs-mh" role="presentation">Un type de champ</div>}
                {types.map((t) => <MenuRow key={t} on={target.scope.kind === "type" && target.scope.type === t} iconName={t} onClick={() => { setTarget({ ...target, scope: { kind: "type", type: t } }); close(); }}>{t}</MenuRow>)}
                {fields.length > 0 && <div className="cs-mh" role="presentation">Un champ précis</div>}
                {fields.map((f) => <MenuRow key={f.key} on={target.scope.kind === "key" && target.scope.key === f.key} iconName={f.type} onClick={() => { setTarget({ ...target, scope: { kind: "key", key: f.key } }); close(); }}>{f.label || f.key}</MenuRow>)}
              </div>
            )}
          </Pop>
        )}
        <Pop label={`État : ${state.label}`} icon={<span className="cs-chipbtn"><FormIcon name={icon(state.icon)} /><span>{state.label}</span></span>}>
          {(close) => <div role="menu" aria-label="État" className="cs-menu">{STATES.map((s) => <MenuRow key={s.id} on={target.state === s.id} iconName={s.icon} onClick={() => { setTarget({ ...target, state: s.id }); close(); }}>{s.label}</MenuRow>)}</div>}
        </Pop>
        <Pop label={`Écran : ${screen.label}`} icon={<span className="cs-chipbtn"><FormIcon name={icon(screen.icon)} /><span>{screen.label.replace(/ \(.*\)/, "")}</span></span>}>
          {(close) => <div role="menu" aria-label="Écran" className="cs-menu">{SCREENS.map((s) => <MenuRow key={s.id} on={target.screen === s.id} iconName={s.icon} onClick={() => { setTarget({ ...target, screen: s.id }); close(); }}>{s.label}</MenuRow>)}</div>}
        </Pop>
        <button type="button" className={`fb-sbtn${force ? " on" : ""}`} aria-pressed={force} aria-label="Montrer cet état et cet écran dans l'aperçu" title="Montrer cet état (survol, focus…) et cet écran dans l'aperçu, même sans les provoquer" onClick={() => setForce(!force)}><FormIcon name={force ? "eye" : "eyeOff"} /></button>
        <span className="fb-spacer" />
        <Pop label={`Règles du formulaire : ${rules.length}`} icon={<span className="cs-chipbtn"><FormIcon name="list2" /><span>{rules.length}</span></span>}>
          {(close) => (
            <div role="menu" aria-label="Règles" className="cs-menu">
              {rules.length === 0 && <MenuRow on={false} iconName="info" onClick={close}>Aucune règle : choisissez un élément et réglez-le.</MenuRow>}
              {rules.map((r) => (
                <div key={ruleKey(r)} className="cs-rulerow">
                  <MenuRow on={ruleKey(r) === selKey} iconName="design" onClick={() => { setTarget(targetOf(r)); close(); }}>{describe(r)} <small>({r.decls.length})</small></MenuRow>
                  {edit && <button type="button" role="menuitem" className="cs-x" aria-label={`Supprimer la règle : ${describe(r)}`} title="Supprimer la règle" onClick={() => apply(rules.filter((x) => ruleKey(x) !== ruleKey(r)))}><FormIcon name="trash" size={14} /></button>}
                </div>
              ))}
            </div>
          )}
        </Pop>
        {edit && (
          <Pop label="Thèmes prêts à l'emploi" icon={<FormIcon name="palette" />}>
            {(close) => (
              <div role="group" aria-label="Thèmes" className="cs-themes">
                {THEMES.map((t) => (
                  <div key={t.id} className="cs-theme">
                    <span className="cs-swatchpair" aria-hidden="true"><i style={{ background: t.swatch[0] }} /><i style={{ background: t.swatch[1] }} /></span>
                    <span className="cs-tname"><strong>{t.label}</strong><small>{t.description}</small></span>
                    <button type="button" aria-label={`Thème ${t.label} : remplacer le CSS`} title="Remplacer toutes les règles par ce thème" onClick={() => { apply(applyTheme(rules, t, "replace")); close(); }}>Remplacer</button>
                    <button type="button" aria-label={`Thème ${t.label} : ajouter aux règles`} title="Ajouter ce thème aux règles existantes" onClick={() => { apply(applyTheme(rules, t, "merge")); close(); }}>Ajouter</button>
                  </div>
                ))}
              </div>
            )}
          </Pop>
        )}
        <span className={`fb-pill ${problem ? "bad" : offline ? "wait" : "ok"}`} role="status"><FormIcon name={problem ? "warn" : offline ? "info" : "check"} size={14} />{problem ? "CSS refusé" : offline ? "Non enregistré" : text ? "CSS appliqué" : "Aucun CSS"}</span>
      </div>
      {problem && <p role="alert" className="field-error cs-problem">{problem}</p>}
      {offline && <p role="alert" className="cs-problem hint">{offline} <button type="button" onClick={() => apply(rules)}>Réessayer</button></p>}
      <div className="cs-main">
        <section className={`cs-preview${pick ? " cs-pick-on" : ""}`} aria-label="Aperçu du formulaire" ref={zone} onClickCapture={onPick} onMouseDownCapture={onPick}>
          <DefinitionForm definition={{ ...def, css: previewCss || undefined }} values={values} onChange={setValues} lookup={lookup}><button type="button" className="primary">Envoyer</button></DefinitionForm>
        </section>
        <section className="cs-side" aria-label="Réglages de la règle">
          <p className="cs-now" title="La règle en cours de modification"><FormIcon name="design" size={14} /> {describe(at) || "Choisissez un élément"}</p>
          {usable ? <Inspector rule={rule} set={set} setMany={many} active={panel} onActive={setPanel} disabled={!edit} /> : <p className="hint">Saisissez un sélecteur dans le menu « Élément à styler ».</p>}
        </section>
      </div>
      <details className="cs-gen" open>
        <summary><FormIcon name="braces" size={16} /> CSS généré en direct <small>{text ? text.split("\n").length : 0} ligne(s)</small></summary>
        <div className="cs-genbar">
          <button type="button" onClick={() => { void navigator.clipboard?.writeText(text); }} disabled={!text}><FormIcon name="copy" size={14} /> Copier</button>
          <button type="button" onClick={onCode}><FormIcon name="code" size={14} /> Modifier à la main</button>
        </div>
        <pre className="cs-code" aria-label="CSS généré" tabIndex={0}>{gen.before}{gen.mark && <mark>{gen.mark}</mark>}{gen.after}{text ? "" : "/* Le CSS apparaît ici dès que vous réglez un élément. */"}</pre>
      </details>
    </div>
  );
}

