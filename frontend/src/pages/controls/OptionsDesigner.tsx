import { useMemo, useState } from "react";
import { FormIcon, IconButton, IconLabel } from "../../forms/FormIcon";
import { MAX_ITEMS, type ControlItem } from "../../forms/controls";
import { isBlock } from "../../forms/types";

/** The options as a tree: a group is a parent, its options are its children (stored flat, `group` on each option). */
interface Leaf { uid: number; item: ControlItem }
type Top = { uid: number; kind: "opt"; item: ControlItem } | { uid: number; kind: "group"; name: string; children: Leaf[] };
let counter = 0;
const uid = () => ++counter;

export function toTree(items: ControlItem[]): Top[] {
  const out: Top[] = [];
  for (const o of items) {
    const { group, ...item } = o;
    const last = out[out.length - 1];
    if (!group) out.push({ uid: uid(), kind: "opt", item });
    else if (last?.kind === "group" && last.name === group) last.children.push({ uid: uid(), item });
    else out.push({ uid: uid(), kind: "group", name: group, children: [{ uid: uid(), item }] });
  }
  return out;
}
export const flatten = (tree: Top[]): ControlItem[] => tree.flatMap((t) => (t.kind === "opt" ? [t.item] : t.children.map((c) => (t.name.trim() ? { ...c.item, group: t.name } : c.item))));

const locate = (tree: Top[], id: number): { i: number; j: number | null } | null => {
  for (let i = 0; i < tree.length; i++) {
    const t = tree[i] as Top;
    if (t.uid === id) return { i, j: null };
    if (t.kind === "group") { const j = t.children.findIndex((c) => c.uid === id); if (j >= 0) return { i, j }; }
  }
  return null;
};
const slug = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
const swap = <T,>(a: T[], i: number, d: -1 | 1): T[] => { const j = i + d; if (j < 0 || j >= a.length) return a; const c = [...a]; [c[i], c[j]] = [c[j] as T, c[i] as T]; return c; };
const detailCount = (it: ControlItem) => (it.parts ?? []).filter((p) => !isBlock(p.type)).length;

interface Props { items: ControlItem[]; edit: boolean; onChange: (items: ControlItem[]) => void }

/** The options of a list or a choice, designed in place: groups and options as a tree on the left, the selected node's properties on the right
 * (every option repeats the control's model: only the values differ). Changes apply to the control as they are made. */
export function OptionsEditor({ items, edit, onChange }: Props) {
  const [tree, setTree] = useState<Top[]>(() => toTree(items));
  const [sel, setSel] = useState<number | null>(() => (tree[0] ? (tree[0] as Top).uid : null));
  const [bulk, setBulk] = useState("");
  const update = (t: Top[]) => { setTree(t); onChange(flatten(t)); };
  const all = flatten(tree);
  const loc = sel === null ? null : locate(tree, sel);
  const top = loc ? (tree[loc.i] as Top) : null;
  const node: { item: ControlItem; leafOf: number | null } | null = loc && top ? (loc.j !== null && top.kind === "group" ? { item: (top.children[loc.j] as Leaf).item, leafOf: loc.i } : top.kind === "opt" ? { item: top.item, leafOf: null } : null) : null;
  const groupSel = top && top.kind === "group" && loc?.j === null ? top : null;
  const groups = tree.filter((t): t is Extract<Top, { kind: "group" }> => t.kind === "group");

  const freeValue = (taken: Set<string>, base = "choix") => { let n = taken.size + 1; let v = base === "choix" ? `choix-${n}` : base; while (taken.has(v)) v = base === "choix" ? `choix-${++n}` : `${base}-${++n}`; return v; };
  const values = () => new Set(all.map((o) => o.value));
  const patchItem = (id: number, p: Partial<ControlItem>) => update(tree.map((t) => (t.uid === id && t.kind === "opt" ? { ...t, item: { ...t.item, ...p } } : t.kind === "group" ? { ...t, children: t.children.map((c) => (c.uid === id ? { ...c, item: { ...c.item, ...p } } : c)) } : t)));
  const edit1 = (id: number, p: Partial<ControlItem>) => { patchItem(id, p); };

  const addOption = () => {
    const taken = values();
    const label = `Option ${all.length + 1}`;
    const leaf: Leaf = { uid: uid(), item: { value: freeValue(taken), label } };
    const next = [...tree];
    if (groupSel) { next[loc!.i] = { ...groupSel, children: [...groupSel.children, leaf] }; }
    else if (loc && top && loc.j !== null && top.kind === "group") { const c = [...top.children]; c.splice(loc.j + 1, 0, leaf); next[loc.i] = { ...top, children: c }; }
    else next.splice(loc ? loc.i + 1 : next.length, 0, { uid: leaf.uid, kind: "opt", item: leaf.item });
    update(next); setSel(leaf.uid);
  };
  const addGroup = () => {
    const g: Top = { uid: uid(), kind: "group", name: `Groupe ${groups.length + 1}`, children: [] };
    const next = [...tree]; next.splice(loc ? loc.i + 1 : next.length, 0, g);
    update(next); setSel(g.uid);
  };
  const move = (d: -1 | 1) => {
    if (!loc || !top) return;
    if (loc.j !== null && top.kind === "group") { const next = [...tree]; next[loc.i] = { ...top, children: swap(top.children, loc.j, d) }; update(next); }
    else update(swap(tree, loc.i, d));
  };
  const indent = () => { // a top-level option becomes the last child of the group above
    if (!loc || !top || top.kind !== "opt" || loc.i === 0) return;
    const above = tree[loc.i - 1] as Top;
    if (above.kind !== "group") return;
    const next = [...tree]; next[loc.i - 1] = { ...above, children: [...above.children, { uid: top.uid, item: top.item }] }; next.splice(loc.i, 1); update(next);
  };
  const outdent = () => { // a child leaves its group, just after it
    if (!loc || !top || top.kind !== "group" || loc.j === null) return;
    const leaf = top.children[loc.j] as Leaf;
    const next = [...tree]; next[loc.i] = { ...top, children: top.children.filter((_, k) => k !== loc.j) }; next.splice(loc.i + 1, 0, { uid: leaf.uid, kind: "opt", item: leaf.item }); update(next);
  };
  const moveToGroup = (target: string) => {
    if (!loc || !top || !node) return;
    const id = sel as number;
    const item = node.item;
    let next = tree.map((t) => (t.kind === "group" ? { ...t, children: t.children.filter((c) => c.uid !== id) } : t)).filter((t) => t.uid !== id);
    if (target === "") { const at = loc.j !== null ? loc.i + 1 : loc.i; next.splice(Math.min(at, next.length), 0, { uid: id, kind: "opt", item }); }
    else next = next.map((t) => (t.uid === Number(target) && t.kind === "group" ? { ...t, children: [...t.children, { uid: id, item }] } : t));
    update(next);
  };
  const remove = () => {
    if (!loc || !top) return;
    if (loc.j !== null && top.kind === "group") { const next = [...tree]; next[loc.i] = { ...top, children: top.children.filter((_, k) => k !== loc.j) }; update(next); setSel(top.uid); }
    else { const next = tree.filter((_, k) => k !== loc.i); update(next); setSel(next[Math.min(loc.i, next.length - 1)]?.uid ?? null); }
  };
  const dissolve = () => {
    if (!loc || !groupSel) return;
    const next = [...tree]; next.splice(loc.i, 1, ...groupSel.children.map((c): Top => ({ uid: c.uid, kind: "opt", item: c.item })));
    update(next); setSel(groupSel.children[0]?.uid ?? next[loc.i]?.uid ?? null);
  };
  const paste = () => { // « # Groupe » starts a group, every other line is an option (« libellé » or « valeur | libellé »)
    const taken = values();
    const next = [...tree];
    let current: Extract<Top, { kind: "group" }> | null = null;
    for (const raw of bulk.split("\n").map((l) => l.trim()).filter(Boolean)) {
      if (raw.startsWith("#")) { current = { uid: uid(), kind: "group", name: raw.replace(/^#+\s*/, "") || "Groupe", children: [] }; next.push(current); continue; }
      const [a, b] = raw.includes("|") ? raw.split("|").map((x) => x.trim()) : [slug(raw), raw];
      const value = freeValue(taken, slug(a as string) || "choix");
      taken.add(value);
      const item: ControlItem = { value, label: (b as string) || (a as string) };
      if (current) current.children.push({ uid: uid(), item }); else next.push({ uid: uid(), kind: "opt", item });
    }
    update(next); setBulk("");
  };

  const problems = useMemo(() => {
    const seen = new Set<string>(); const out: string[] = [];
    for (const o of all) { if (!o.value.trim()) out.push(`« ${o.label || "(sans nom)"} » n'a pas de valeur.`); else if (seen.has(o.value)) out.push(`La valeur « ${o.value} » est utilisée deux fois.`); seen.add(o.value); }
    return out;
  }, [all]);
  const canIndent = Boolean(loc && top && top.kind === "opt" && loc.i > 0 && tree[loc.i - 1]?.kind === "group");
  const label = (o: ControlItem) => o.label || o.value || "(sans nom)";
  const row = (id: number, text: string, icon: "boxes" | "layers", extra: string, child: boolean) => (
    <li key={id} className={child ? "od-child" : undefined}>
      <button type="button" className={`od-row${sel === id ? " on" : ""}`} aria-pressed={sel === id} onClick={() => { setSel(id); }}><FormIcon name={icon} size={16} /><span className="od-t">{text}</span>{extra && <span className="od-x">{extra}</span>}</button>
    </li>
  );
  const itemTag = (o: ControlItem) => [o.disabled ? "grisée" : "", detailCount(o) ? `${detailCount(o)} champ(s) propre(s)` : ""].filter(Boolean).join(" · ");
  return (
    <>
      <fieldset disabled={!edit} className="od-fs">
        <div className="od">
          <nav className="od-tree" aria-label="Structure des options">
            <div className="od-bar">
              <button type="button" onClick={addOption} disabled={all.length >= MAX_ITEMS}><IconLabel icon="plus">Ajouter une option</IconLabel></button>
              <button type="button" onClick={addGroup}><IconLabel icon="layers">Ajouter un groupe</IconLabel></button>
            </div>
            <div className="fb-toolbar" role="toolbar" aria-label="Actions sur la sélection">
              <IconButton icon="up" label="Monter" disabled={!loc} onClick={() => move(-1)} />
              <IconButton icon="down" label="Descendre" disabled={!loc} onClick={() => move(1)} />
              <IconButton icon="right" label="Placer dans le groupe au-dessus" disabled={!canIndent} onClick={indent} />
              <IconButton icon="back" label="Sortir du groupe" disabled={!(loc && loc.j !== null)} onClick={outdent} />
              <IconButton icon="trash" danger label="Supprimer la sélection" disabled={!loc || Boolean(groupSel && groupSel.children.length)} onClick={remove} />
            </div>
            {tree.length === 0 && <p className="hint">Aucune option : ajoutez-en une, ou collez une liste ci-dessous.</p>}
            <ul className="od-list">
              {tree.map((t) => t.kind === "opt" ? row(t.uid, label(t.item), "boxes", itemTag(t.item), false) : (
                <li key={t.uid}>
                  <ul className="od-grp">
                    {row(t.uid, t.name || "(groupe sans nom)", "layers", `${t.children.length} option(s)`, false)}
                    {t.children.map((c) => row(c.uid, label(c.item), "boxes", itemTag(c.item), true))}
                  </ul>
                </li>
              ))}
            </ul>
            <details className="od-bulk">
              <summary>Ajouter plusieurs options d'un coup</summary>
              <label htmlFor="od-bulk">Une option par ligne (« libellé » ou « valeur | libellé »). Une ligne « # Nom » ouvre un groupe.</label>
              <textarea id="od-bulk" rows={6} value={bulk} onChange={(e) => setBulk(e.target.value)} placeholder={"Saab\nOpel\n# US Brands\nFord\nChrysler"} />
              <button type="button" onClick={paste} disabled={!bulk.trim()}><IconLabel icon="plus">Ajouter la liste</IconLabel></button>
            </details>
          </nav>
          <section className="od-edit" aria-label="Propriétés de la sélection">
            {groupSel ? (
              <>
                <h3><FormIcon name="layers" size={16} /> Groupe</h3>
                <div className="field"><label htmlFor="od-gname">Nom du groupe</label><input id="od-gname" value={groupSel.name} maxLength={120} onChange={(e) => update(tree.map((t) => (t.uid === groupSel.uid && t.kind === "group" ? { ...t, name: e.target.value } : t)))} /></div>
                <p className="hint">Le nom s'affiche comme titre au-dessus de ses options ({groupSel.children.length}). Utilisez « Ajouter une option » pour en créer dedans, ou « Placer dans le groupe au-dessus ».</p>
                <button type="button" onClick={dissolve}><IconLabel icon="back">Dissocier le groupe (garder les options)</IconLabel></button>
                {groupSel.children.length > 0 && <p className="hint">Un groupe ne se supprime que vide : dissociez-le d'abord.</p>}
              </>
            ) : node && sel !== null ? (
              <>
                <div className="field"><label htmlFor="od-label">Libellé de l'option</label><input id="od-label" value={node.item.label ?? ""} onChange={(e) => edit1(sel, { label: e.target.value })} /></div>
                <div className="field"><label htmlFor="od-value">Valeur remise au workflow</label><input id="od-value" className="mono" value={node.item.value} onChange={(e) => edit1(sel, { value: e.target.value })} /></div>
                <div className="field field-inline"><input id="od-off" type="checkbox" checked={Boolean(node.item.disabled)} onChange={(e) => edit1(sel, { disabled: e.target.checked ? true : undefined })} /><label htmlFor="od-off">Désactivée (visible, grisée, non sélectionnable)</label></div>
                <div className="field"><label htmlFor="od-grp">Groupe</label>
                  <select id="od-grp" value={node.leafOf !== null ? String((tree[node.leafOf] as Top).uid) : ""} onChange={(e) => moveToGroup(e.target.value)}>
                    <option value="">Aucun groupe</option>
                    {groups.map((g) => <option key={g.uid} value={g.uid}>{g.name || "(groupe sans nom)"}</option>)}
                  </select></div>
                {detailCount(node.item) > 0 && (
                  <p className="hint" role="note">Cette option a {detailCount(node.item)} champ(s) qui lui sont propres (ancien format). Toutes les options devraient avoir les mêmes champs : placez-les dans le modèle.{" "}
                    <button type="button" onClick={() => { const { parts: _p, ...rest } = node.item; update(setItem(tree, sel, rest)); }}><IconLabel icon="trash">Retirer ces champs</IconLabel></button></p>
                )}
              </>
            ) : <p className="hint">Sélectionnez une option ou un groupe à gauche pour le régler.</p>}
            {problems.length > 0 && <ul className="fb-problems" role="alert" aria-label="Problèmes des options">{problems.slice(0, 4).map((p, i) => <li key={i}>{p}</li>)}</ul>}
          </section>
        </div>
      </fieldset>
    </>
  );
}

function setItem(tree: Top[], id: number, item: ControlItem): Top[] {
  return tree.map((t) => (t.uid === id && t.kind === "opt" ? { ...t, item } : t.kind === "group" ? { ...t, children: t.children.map((c) => (c.uid === id ? { ...c, item } : c)) } : t));
}
