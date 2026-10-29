import { ExprPreview } from "./ExprPreview";
import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";

type Agg = { name: string; fn: string; expr?: string };
const OPS: [string, string, string][] = [
  ["filter", "Filtrer", "Garde les éléments qui vérifient une condition"],
  ["map", "Transformer chaque élément", "Construit un nouvel objet pour chaque élément"],
  ["sort", "Trier", "Ordonne la liste selon une valeur"],
  ["group", "Regrouper et calculer", "Regroupe par clé, avec comptage, somme, moyenne…"],
  ["unique", "Dédoublonner", "Supprime les doublons"],
  ["merge", "Fusionner deux listes", "Jointure sur une clé, ou mise bout à bout"],
  ["limit", "Garder les N premiers", "Limite la taille de la liste"],
  ["split", "Scinder un texte en liste", "Découpe un texte selon un séparateur"],
  ["chunk", "Découper une liste en lots", "Regroupe les éléments par paquets de N"],
  ["set", "Définir des champs", "Calcule quelques valeurs (sans liste)"],
];
const FNS = ["count", "sum", "avg", "min", "max", "first", "list"];

function Rows({ label, rows, onChange, hint, valueLabel }: { label: string; rows: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; hint?: string; valueLabel: string }) {
  const entries = Object.entries(rows);
  const rename = (old: string, name: string) => onChange(Object.fromEntries(entries.map(([k, v]) => [k === old ? name : k, v])));
  return (
    <fieldset className="bare"><legend>{label}</legend>
      {entries.map(([k, v], i) => (
        <div key={i} className="field field-inline">
          <input aria-label={`Nom du champ ${i + 1}`} value={k} placeholder="nom" onChange={(e) => rename(k, e.target.value)} />
          <input aria-label={`${valueLabel} du champ ${i + 1}`} value={String(v ?? "")} placeholder="{{ item.valeur }}" onChange={(e) => onChange({ ...rows, [k]: e.target.value })} />
          <button type="button" aria-label={`Retirer le champ ${i + 1}`} onClick={() => onChange(Object.fromEntries(entries.filter(([x]) => x !== k)))}>✕</button>
        </div>))}
      <button type="button" onClick={() => onChange({ ...rows, [`champ_${entries.length + 1}`]: "" })}>Ajouter un champ</button>
      {hint && <HelpTip>{hint}</HelpTip>}
    </fieldset>
  );
}

function Text({ id, label, value, onChange, placeholder, hint, items }: { id: string; label: string; value: unknown; onChange: (v: string) => void; placeholder?: string; hint?: string; items?: string }) {
  return <div className="field"><label htmlFor={id}>{label}</label><input id={id} list="expr-paths" value={String(value ?? "")} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />{String(value ?? "") && <ExprPreview expr={String(value)} items={items} />}{hint && <HelpTip>{hint}</HelpTip>}</div>;
}

export function TransformConfig({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const op = node.op ?? "filter";
  const set = (patch: Partial<GraphNode>) => onUpdate(patch);
  const aggs = (node.aggregates ?? []) as Agg[];
  const per = "Dans les expressions, « item » désigne l'élément courant (ex. item.montant > 100).";
  return (
    <>
      <div className="field">
<HelpTip>{OPS.find(([v]) => v === op)?.[2]}. Résultat : <code>steps.{node.id}.output.{op === "set" ? "<champ>" : "items"}</code>{op === "set" ? "" : " et .count"}.</HelpTip><label htmlFor="tr-op">Opération</label>
        <select id="tr-op" value={op} onChange={(e) => set({ op: e.target.value as GraphNode["op"] })}>{OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
      {op !== "set" && op !== "split" && <Text id="tr-items" label="Liste à traiter" value={node.items} onChange={(v) => set({ items: v })} placeholder="steps.appel.output.body" hint="Une expression qui donne une liste." />}
      {op === "split" && <>
        <Text id="tr-text" label="Texte à scinder" value={node.text} onChange={(v) => set({ text: v })} placeholder="inputs.adresses" hint="Une expression qui donne un texte." />
        <Text id="tr-sep" label="Séparateur" value={node.separator ?? ","} onChange={(v) => set({ separator: v })} placeholder="," hint="Pour un retour à la ligne, saisissez \n." /></>}
      {op === "chunk" && <div className="field"><label htmlFor="tr-size">Taille d'un lot</label><input id="tr-size" type="number" min={1} value={node.size ?? 10} onChange={(e) => set({ size: Number(e.target.value) })} /></div>}
      {op === "filter" && <Text items={node.items} id="tr-where" label="Condition" value={node.where} onChange={(v) => set({ where: v })} placeholder="item.montant > 100" hint={per} />}
      {(op === "sort" || op === "unique") && <Text items={node.items} id="tr-by" label={op === "sort" ? "Trier selon" : "Clé d'unicité (vide = élément entier)"} value={node.by} onChange={(v) => set({ by: v })} placeholder="item.nom" hint={per} />}
      {op === "sort" && <div className="field"><label htmlFor="tr-order">Ordre</label><select id="tr-order" value={node.order ?? "asc"} onChange={(e) => set({ order: e.target.value as "asc" | "desc" })}><option value="asc">Croissant</option><option value="desc">Décroissant</option></select></div>}
      {op === "limit" && <>
        <div className="field"><label htmlFor="tr-count">Nombre d'éléments à garder</label><input id="tr-count" type="number" min={0} value={node.count ?? 10} onChange={(e) => set({ count: Number(e.target.value) })} /></div>
        <div className="field"><label htmlFor="tr-skip">Éléments à ignorer au début</label><input id="tr-skip" type="number" min={0} value={node.skip ?? 0} onChange={(e) => set({ skip: Number(e.target.value) })} /></div></>}
      {(op === "map" || op === "set") && <Rows label={op === "map" ? "Champs de chaque élément" : "Champs calculés"} rows={(node.fields ?? {}) as Record<string, unknown>} onChange={(v) => set({ fields: v })} valueLabel="Valeur" hint={op === "map" ? per : "Valeurs fixes ou {{ expressions }}."} />}
      {op === "group" && <>
        <Text items={node.items} id="tr-gby" label="Regrouper selon" value={node.by} onChange={(v) => set({ by: v })} placeholder="item.service" hint={per} />
        <fieldset className="bare"><legend>Calculs par groupe (le nombre d'éléments est toujours fourni : count)</legend>
          {aggs.map((a, i) => (
            <div key={i} className="field field-inline">
              <input aria-label={`Nom du calcul ${i + 1}`} value={a.name} placeholder="total" onChange={(e) => set({ aggregates: aggs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} />
              <select aria-label={`Fonction du calcul ${i + 1}`} value={a.fn} onChange={(e) => set({ aggregates: aggs.map((x, j) => (j === i ? { ...x, fn: e.target.value } : x)) })}>{FNS.map((f) => <option key={f}>{f}</option>)}</select>
              <input aria-label={`Valeur du calcul ${i + 1}`} value={a.expr ?? ""} placeholder="item.montant" onChange={(e) => set({ aggregates: aggs.map((x, j) => (j === i ? { ...x, expr: e.target.value } : x)) })} />
              <button type="button" aria-label={`Retirer le calcul ${i + 1}`} onClick={() => set({ aggregates: aggs.filter((_, j) => j !== i) })}>✕</button>
            </div>))}
          <button type="button" onClick={() => set({ aggregates: [...aggs, { name: `calcul_${aggs.length + 1}`, fn: "sum", expr: "item." }] })}>Ajouter un calcul</button>
        </fieldset></>}
      {op === "merge" && <>
        <Text id="tr-right" label="Seconde liste" value={node.right} onChange={(v) => set({ right: v })} placeholder="inputs.proprietaires" />
        <div className="field"><label htmlFor="tr-mode">Mode</label><select id="tr-mode" value={node.merge_mode ?? "inner"} onChange={(e) => set({ merge_mode: e.target.value as "inner" | "left" | "append" })}>
          <option value="inner">Jointure (garder les correspondances)</option><option value="left">Jointure à gauche (garder toute la première liste)</option><option value="append">Mettre bout à bout</option></select></div>
        {(node.merge_mode ?? "inner") !== "append" && <>
          <Text items={node.items} id="tr-lby" label="Clé de la première liste" value={node.left_by} onChange={(v) => set({ left_by: v })} placeholder="item.id" hint={per} />
          <Text items={node.right} id="tr-rby" label="Clé de la seconde liste" value={node.right_by} onChange={(v) => set({ right_by: v })} placeholder="item.id" /></>}</>}
    </>
  );
}
