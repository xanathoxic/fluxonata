import { useState } from "react";
import { CssDesigner } from "./css/CssDesigner";
import { CssEditor } from "./CssEditor";
import { DefinitionForm, type LookupFn } from "./DefinitionForm";
import { ModeTabs } from "./ModeTabs";
import type { FormDefinition } from "./types";

/** The CSS view: the no-code designer (default) or the code editor, on the very same text. */
export function CssTab({ def, onCss, edit, lookup }: { def: FormDefinition; onCss: (css: string | undefined) => void; edit: boolean; lookup?: LookupFn }) {
  const [mode, setMode] = useState("visual");
  const [values, setValues] = useState<Record<string, unknown>>({});
  return (
    <div className="cs-tabwrap">
      <div className="cs-modes"><ModeTabs label="Mode du CSS" active={mode} onChange={setMode} tabs={[{ id: "visual", label: "Visuel", icon: "design" }, { id: "code", label: "Code", icon: "code" }]} /></div>
      {mode === "visual" ? (
        <CssDesigner def={def} css={def.css} onCss={onCss} edit={edit} lookup={lookup} onCode={() => setMode("code")} />
      ) : (
        <div className="fb-style-layout cs-code-layout">
          <section className="fb-panel fb-scroll" aria-label="CSS personnalisé"><h2>CSS personnalisé</h2><CssEditor css={def.css} onChange={onCss} edit={edit} /></section>
          <section className="fb-panel fb-scroll" aria-label="Aperçu du CSS"><h2>Aperçu</h2><DefinitionForm definition={def} values={values} onChange={setValues} lookup={lookup}><button type="button" className="primary">Envoyer</button></DefinitionForm></section>
        </div>
      )}
    </div>
  );
}
