import { useEffect, useMemo, useRef, useState } from "react";
import { api, type ApiError } from "../../api";
import { useApp } from "../../context";
import { DefinitionForm, type LookupFn } from "../DefinitionForm";
import { FormIcon, type FormIconName } from "../FormIcon";
import { orderedFields } from "../layout";
import { Pop } from "../StyleEditor";
import { isBlock, type FormDefinition } from "../types";
import { Inspector } from "./Inspector";
import { PickPanel } from "./PickPanel";
import { ancestorsOf, candidatesFor, describeElement, kindName, snapTarget, type Candidate } from "./pick";
import { parse, ruleKey, serialize, setDecl, setMany, type Rule } from "./model";
import {
  CUSTOM,
  PARTS,
  SCREENS,
  STATES,
  defaultTarget,
  describe as describeTarget,
  ruleOf,
  selectorsFor,
  targetFromElement,
  targetOf,
  type Scope,
  type Target,
} from "./targets";
import { THEMES, applyTheme } from "./themes";
import { CssLive } from "./CssLive";

const icon = (n: string) => n as FormIconName;
const GROUPS: [string, string][] = [
  ["form", "Formulaire"],
  ["field", "Champs"],
  ["control", "Contrôles personnalisés"],
  ["content", "Contenu"],
  ["action", "Boutons"],
];

/** Menu rows with an icon and a name (the list of things to style is long: names stay readable). */
function MenuRow({ on, iconName, children, onClick }: { on: boolean; iconName: string; children: React.ReactNode; onClick: () => void }) {
  return (
    <button type="button" role="menuitemradio" aria-checked={on} className={`cs-mrow${on ? " on" : ""}`} onClick={onClick}>
      <FormIcon name={icon(iconName)} size={16} />
      <span>{children}</span>
    </button>
  );
}

interface Props {
  def: FormDefinition;
  css: string | undefined;
  onCss: (css: string | undefined) => void;
  edit: boolean;
  lookup?: LookupFn;
}

/** The no-code CSS designer. A rule is a target (what), a state and a screen; the controls write declarations into it and the
 * CSS appears as it is written. Clicking the preview picks the target. The text is checked by the server, which writes the
 * canonical version that is stored. */
export function CssDesigner({ def, css, onCss, edit, lookup }: Props) {
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
  // « cible libre » : l'élément survolé (entouré dans l'aperçu), puis ce qu'on peut viser à partir du dernier clic
  const [hover, setHover] = useState("");
  const [cands, setCands] = useState<Candidate[]>([]);
  // l'élément cliqué et ses parents (du plus proche au plus lointain), et le niveau visé dans cette liste
  const [chain, setChain] = useState<Element[]>([]);
  const [lvl, setLvl] = useState(0);
  const lit = useRef<HTMLElement | null>(null);
  const unlight = () => {
    if (lit.current) {
      lit.current.style.outline = "";
      lit.current.style.outlineOffset = "";
      lit.current = null;
    }
  };
  useEffect(
    () => () => {
      const el = lit.current;
      if (el) {
        el.style.outline = "";
        el.style.outlineOffset = "";
      }
    },
    [],
  );

  useEffect(() => {
    if ((css ?? "") !== committed.current) {
      committed.current = css ?? "";
      setRules(parse(css) ?? []);
    }
  }, [css]);

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
          if (first) {
            setProblem(`Ligne ${first.line} : ${first.message}`);
            return;
          }
          setProblem("");
          committed.current = r.css.css;
          onCss(r.css.css || undefined);
        })
        .catch((e: ApiError) => {
          if (mine !== seq.current) return;
          setOffline(
            e?.status === 401 || e?.status === 403
              ? "Votre session a expiré : reconnectez-vous. Vos réglages restent dans cette page tant que vous ne la quittez pas."
              : "Le serveur n'a pas pu vérifier le CSS pour le moment : vos réglages sont conservés ici.",
          );
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

  const free = target.part === CUSTOM;
  const onHover = (e: React.MouseEvent) => {
    if (!pick || !free) {
      unlight();
      return;
    }
    const root = zone.current?.querySelector(".fb-form");
    const el = root ? snapTarget(e.target as Element, root) : null;
    if (el === lit.current) return;
    unlight();
    if (!el || !root) {
      setHover("");
      return;
    }
    (el as HTMLElement).style.outline = "2px solid #2563eb";
    (el as HTMLElement).style.outlineOffset = "1px";
    lit.current = el as HTMLElement;
    setHover(describeElement(el, root));
  };
  /** vise cet élément (le niveau `i` de la liste) : propose ce qu'on peut en viser et écrit le premier choix */
  const aim = (el: Element, i: number) => {
    const root = zone.current?.querySelector(".fb-form");
    if (!root || !el.isConnected) {
      setChain([]);
      setCands([]);
      return;
    }
    const list = candidatesFor(el, root);
    setLvl(i);
    setCands(list);
    if (list[0]) setTarget((t) => ({ ...t, custom: (list[0] as Candidate).selector }));
  };
  const onLevel = (outerFirst: number) => {
    const i = chain.length - 1 - outerFirst;
    const el = chain[i];
    if (el) aim(el, i);
  };
  const onPick = (e: React.MouseEvent) => {
    if (!pick) return;
    const root = zone.current?.querySelector(".fb-form");
    if (!root) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.type !== "click") return;
    if (free) {
      const el = snapTarget(e.target as Element, root);
      if (!el) return;
      setChain(ancestorsOf(el, root));
      aim(el, 0);
      return;
    }
    const hit = targetFromElement(e.target as Element, root);
    if (!hit) return;
    setTarget((t) => {
      const part = PARTS.find((p) => p.id === hit.part);
      let scope: Scope = t.scope;
      if (e.shiftKey && hit.key) scope = hit.control ? { kind: "in", key: hit.control, sub: hit.key } : { kind: "key", key: hit.key };
      else if (e.altKey && hit.type) scope = { kind: "type", type: hit.type };
      else if ((e.ctrlKey || e.metaKey) && (hit.control ?? (hit.type === "custom" ? hit.key : undefined)))
        scope = { kind: "in", key: (hit.control ?? hit.key) as string };
      else if (t.scope.kind === "in" && hit.control === t.scope.key) scope = { kind: "in", key: t.scope.key, sub: t.scope.sub ? hit.key : undefined };
      else if (t.scope.kind === "in" && hit.type === "custom" && hit.key === t.scope.key) scope = t.scope;
      else if (t.scope.kind === "key" && hit.key) scope = { kind: "key", key: hit.key };
      else if (t.scope.kind === "type" && hit.type) scope = { kind: "type", type: hit.type };
      if (part && !part.scoped) scope = { kind: "all" };
      return { ...t, part: hit.part, scope, custom: undefined };
    });
  };

  const fields = orderedFields(def).filter((f) => !isBlock(f.type));
  const types = [...new Set(fields.map((f) => f.type))];
  const customs = fields.filter((f) => f.type === "custom");
  /** the sub-fields a custom control is made of (its own fields, or those of its options) */
  const subsOf = (key: string) => {
    const spec = fields.find((f) => f.key === key)?.control?.spec;
    const parts = [...(spec?.parts ?? []), ...(spec?.items ?? []).flatMap((o) => o.parts ?? [])];
    return parts.filter((p, i) => !isBlock(p.type) && parts.findIndex((q) => q.key === p.key) === i);
  };
  const part = PARTS.find((p) => p.id === target.part);
  const state = STATES.find((s) => s.id === target.state) ?? (STATES[0] as (typeof STATES)[number]);
  const screen = SCREENS.find((s) => s.id === target.screen) ?? (SCREENS[0] as (typeof SCREENS)[number]);
  const labelOf = (key: string) => fields.find((f) => f.key === key)?.label || key;
  const scopeLabel =
    target.scope.kind === "in"
      ? `Dans « ${labelOf(target.scope.key)} »${target.scope.sub ? ` › ${subsOf(target.scope.key).find((s) => s.key === (target.scope as { sub: string }).sub)?.label || target.scope.sub}` : ""}`
      : target.scope.kind === "key"
        ? `Champ : ${fields.find((f) => f.key === (target.scope as { key: string }).key)?.label ?? target.scope.key}`
        : target.scope.kind === "type"
          ? `Type : ${target.scope.type}`
          : "Tous les champs";
  if (!readable) {
    return (
      <div className="cs-designer cs-unreadable">
        <p role="status">Ce CSS a été écrit à la main sous une forme que le concepteur visuel ne sait pas relire. Il reste modifiable dans la zone du bas.</p>
        <CssLive text={css ?? ""} edit={edit} onCss={onCss} fields={fields.map((f) => ({ key: f.key, type: f.type, label: f.label }))} />
      </div>
    );
  }
  return (
    <div className="cs-designer">
      <div className="cs-bar" role="toolbar" aria-label="Cible du style">
        <button
          type="button"
          className={`fb-sbtn cs-wide${pick ? " on" : ""}`}
          aria-pressed={pick}
          aria-label="Sélectionner dans l'aperçu"
          title="Cliquer dans l'aperçu pour choisir l'élément (Maj + clic : ce champ seul · Alt + clic : ce type de champ · Ctrl + clic : l'intérieur du contrôle personnalisé)"
          onClick={() => setPick(!pick)}
        >
          <FormIcon name="pointer" />
        </button>
        <span className="fb-sep" aria-hidden="true" />
        <Pop
          label={`Élément à styler : ${target.part === CUSTOM ? "cible libre" : part?.label}`}
          icon={
            <span className="cs-chipbtn">
              <FormIcon name={icon(part?.icon ?? "code")} />
              <span>{target.part === CUSTOM ? "Cible libre" : part?.label}</span>
            </span>
          }
        >
          {(close) => (
            <>
              <div role="menu" aria-label="Élément à styler" className="cs-menu">
                {GROUPS.map(([g, name]) => (
                  <div key={g}>
                    <div className="cs-mh" role="presentation">
                      {name}
                    </div>
                    {PARTS.filter((p) => p.group === g).map((p) => (
                      <MenuRow
                        key={p.id}
                        on={target.part === p.id}
                        iconName={p.icon}
                        onClick={() => {
                          setTarget({ ...target, part: p.id, scope: p.scoped ? target.scope : { kind: "all" }, custom: undefined });
                          close();
                        }}
                      >
                        {p.label}
                      </MenuRow>
                    ))}
                  </div>
                ))}
                <div className="cs-mh" role="presentation">
                  Avancé
                </div>
                <MenuRow on={target.part === CUSTOM} iconName="code" onClick={() => setTarget({ ...target, part: CUSTOM, custom: target.custom ?? "" })}>
                  Cible libre (cliquer dans l'aperçu)…
                </MenuRow>
              </div>
            </>
          )}
        </Pop>
        {part?.scoped && (
          <Pop
            label={`Portée : ${scopeLabel}`}
            icon={
              <span className="cs-chipbtn">
                <FormIcon name="link" />
                <span>{scopeLabel}</span>
              </span>
            }
          >
            {(close) => (
              <div role="menu" aria-label="Portée" className="cs-menu">
                <MenuRow
                  on={target.scope.kind === "all"}
                  iconName="width3"
                  onClick={() => {
                    setTarget({ ...target, scope: { kind: "all" } });
                    close();
                  }}
                >
                  Tous les champs
                </MenuRow>
                {types.length > 0 && (
                  <div className="cs-mh" role="presentation">
                    Un type de champ
                  </div>
                )}
                {types.map((t) => (
                  <MenuRow
                    key={t}
                    on={target.scope.kind === "type" && target.scope.type === t}
                    iconName={t}
                    onClick={() => {
                      setTarget({ ...target, scope: { kind: "type", type: t } });
                      close();
                    }}
                  >
                    {t}
                  </MenuRow>
                ))}
                {fields.length > 0 && (
                  <div className="cs-mh" role="presentation">
                    Un champ précis
                  </div>
                )}
                {fields.map((f) => (
                  <MenuRow
                    key={f.key}
                    on={target.scope.kind === "key" && target.scope.key === f.key}
                    iconName={f.type}
                    onClick={() => {
                      setTarget({ ...target, scope: { kind: "key", key: f.key } });
                      close();
                    }}
                  >
                    {f.label || f.key}
                  </MenuRow>
                ))}
                {customs.length > 0 && (
                  <div className="cs-mh" role="presentation">
                    À l'intérieur d'un contrôle personnalisé
                  </div>
                )}
                {customs.map((f) => (
                  <div key={`in-${f.key}`}>
                    <MenuRow
                      on={target.scope.kind === "in" && target.scope.key === f.key && !target.scope.sub}
                      iconName="custom"
                      onClick={() => {
                        setTarget({ ...target, scope: { kind: "in", key: f.key } });
                        close();
                      }}
                    >
                      Tout l'intérieur de « {f.label || f.key} »
                    </MenuRow>
                    {subsOf(f.key).map((s) => (
                      <div key={s.key} className="cs-sub">
                        <MenuRow
                          on={target.scope.kind === "in" && target.scope.key === f.key && target.scope.sub === s.key}
                          iconName={s.type}
                          onClick={() => {
                            setTarget({ ...target, scope: { kind: "in", key: f.key, sub: s.key } });
                            close();
                          }}
                        >
                          ↳ {s.label || s.key}
                        </MenuRow>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </Pop>
        )}
        <Pop
          label={`État : ${state.label}`}
          icon={
            <span className="cs-chipbtn">
              <FormIcon name={icon(state.icon)} />
              <span>{state.label}</span>
            </span>
          }
        >
          {(close) => (
            <div role="menu" aria-label="État" className="cs-menu">
              {STATES.map((s) => (
                <MenuRow
                  key={s.id}
                  on={target.state === s.id}
                  iconName={s.icon}
                  onClick={() => {
                    setTarget({ ...target, state: s.id });
                    close();
                  }}
                >
                  {s.label}
                </MenuRow>
              ))}
            </div>
          )}
        </Pop>
        <Pop
          label={`Écran : ${screen.label}`}
          icon={
            <span className="cs-chipbtn">
              <FormIcon name={icon(screen.icon)} />
              <span>{screen.label.replace(/ \(.*\)/, "")}</span>
            </span>
          }
        >
          {(close) => (
            <div role="menu" aria-label="Écran" className="cs-menu">
              {SCREENS.map((s) => (
                <MenuRow
                  key={s.id}
                  on={target.screen === s.id}
                  iconName={s.icon}
                  onClick={() => {
                    setTarget({ ...target, screen: s.id });
                    close();
                  }}
                >
                  {s.label}
                </MenuRow>
              ))}
            </div>
          )}
        </Pop>
        <button
          type="button"
          className={`fb-sbtn${force ? " on" : ""}`}
          aria-pressed={force}
          aria-label="Montrer cet état et cet écran dans l'aperçu"
          title="Montrer cet état (survol, focus…) et cet écran dans l'aperçu, même sans les provoquer"
          onClick={() => setForce(!force)}
        >
          <FormIcon name={force ? "eye" : "eyeOff"} />
        </button>
        <span className="fb-spacer" />
        <Pop
          label={`Règles du formulaire : ${rules.length}`}
          icon={
            <span className="cs-chipbtn">
              <FormIcon name="list2" />
              <span>{rules.length}</span>
            </span>
          }
        >
          {(close) => (
            <div role="menu" aria-label="Règles" className="cs-menu">
              {rules.length === 0 && (
                <MenuRow on={false} iconName="info" onClick={close}>
                  Aucune règle : choisissez un élément et réglez-le.
                </MenuRow>
              )}
              {rules.map((r) => (
                <div key={ruleKey(r)} className="cs-rulerow">
                  <MenuRow
                    on={ruleKey(r) === selKey}
                    iconName="design"
                    onClick={() => {
                      setTarget(targetOf(r));
                      close();
                    }}
                  >
                    {describeTarget(r)} <small>({r.decls.length})</small>
                  </MenuRow>
                  {edit && (
                    <button
                      type="button"
                      role="menuitem"
                      className="cs-x"
                      aria-label={`Supprimer la règle : ${describeTarget(r)}`}
                      title="Supprimer la règle"
                      onClick={() => apply(rules.filter((x) => ruleKey(x) !== ruleKey(r)))}
                    >
                      <FormIcon name="trash" size={14} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </Pop>
        {edit && (
          <Pop label="Thèmes prêts à l'emploi" icon={<FormIcon name="palette" />}>
            {(close) => (
              // biome-ignore lint/a11y/useSemanticElements: rôle explicite conservé ; l'élément natif changerait la mise en forme
              <div role="group" aria-label="Thèmes" className="cs-themes">
                {THEMES.map((t) => (
                  <div key={t.id} className="cs-theme">
                    <span className="cs-swatchpair" aria-hidden="true">
                      <i style={{ background: t.swatch[0] }} />
                      <i style={{ background: t.swatch[1] }} />
                    </span>
                    <span className="cs-tname">
                      <strong>{t.label}</strong>
                      <small>{t.description}</small>
                    </span>
                    <button
                      type="button"
                      aria-label={`Thème ${t.label} : remplacer le CSS`}
                      title="Remplacer toutes les règles par ce thème"
                      onClick={() => {
                        apply(applyTheme(rules, t, "replace"));
                        close();
                      }}
                    >
                      Remplacer
                    </button>
                    <button
                      type="button"
                      aria-label={`Thème ${t.label} : ajouter aux règles`}
                      title="Ajouter ce thème aux règles existantes"
                      onClick={() => {
                        apply(applyTheme(rules, t, "merge"));
                        close();
                      }}
                    >
                      Ajouter
                    </button>
                  </div>
                ))}
              </div>
            )}
          </Pop>
        )}
        <span className={`fb-pill ${problem ? "bad" : offline ? "wait" : "ok"}`} role="status">
          <FormIcon name={problem ? "warn" : offline ? "info" : "check"} size={14} />
          {problem ? "CSS refusé" : offline ? "Non enregistré" : text ? "CSS appliqué" : "Aucun CSS"}
        </span>
      </div>
      {problem && (
        <p role="alert" className="field-error cs-problem">
          {problem}
        </p>
      )}
      {offline && (
        <p role="alert" className="cs-problem hint">
          {offline}{" "}
          <button type="button" onClick={() => apply(rules)}>
            Réessayer
          </button>
        </p>
      )}
      <div className="cs-main">
        <section
          className={`cs-preview${pick ? " cs-pick-on" : ""}`}
          aria-label="Aperçu du formulaire"
          ref={zone}
          onClickCapture={onPick}
          onMouseDownCapture={onPick}
          onMouseMove={onHover}
          onMouseLeave={() => {
            unlight();
            setHover("");
          }}
        >
          <DefinitionForm definition={{ ...def, css: previewCss || undefined, submit: undefined }} values={values} onChange={setValues} lookup={lookup}>
            <button type="button" className="primary">
              Envoyer
            </button>
          </DefinitionForm>
        </section>
        <section className="cs-side" aria-label="Réglages de la règle">
          <p className="cs-now" title="La règle en cours de modification">
            <FormIcon name="design" size={14} /> {describeTarget(at) || "Choisissez un élément"}
          </p>
          {free && (
            <PickPanel
              hover={hover}
              candidates={cands}
              levels={[...chain]
                .reverse()
                .map((n) => ({
                  label: kindName(n),
                  title: zone.current?.querySelector(".fb-form") ? describeElement(n, zone.current.querySelector(".fb-form") as Element) : kindName(n),
                }))}
              level={chain.length - 1 - lvl}
              onLevel={onLevel}
              selector={target.custom ?? ""}
              onChoose={(s) => setTarget((t) => ({ ...t, custom: s }))}
              onSelector={(s) => {
                setCands([]);
                setChain([]);
                setTarget((t) => ({ ...t, custom: s }));
              }}
              disabled={!edit}
            />
          )}
          {usable ? (
            <Inspector rule={rule} set={set} setMany={many} active={panel} onActive={setPanel} disabled={!edit} />
          ) : (
            <p className="hint">Cliquez sur un élément de l'aperçu pour le cibler.</p>
          )}
        </section>
      </div>
      <CssLive text={text} edit={edit} onCss={onCss} fields={fields.map((f) => ({ key: f.key, type: f.type, label: f.label }))} />
    </div>
  );
}
