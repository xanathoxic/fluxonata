import { CssDesigner } from "./css/CssDesigner";
import type { LookupFn } from "./DefinitionForm";
import type { FormDefinition } from "./types";

/** The CSS view: the no-code designer, with the generated CSS editable by hand in the panel at the bottom. */
export function CssTab({ def, onCss, edit, lookup }: { def: FormDefinition; onCss: (css: string | undefined) => void; edit: boolean; lookup?: LookupFn }) {
  return (
    <div className="cs-tabwrap">
      <CssDesigner def={def} css={def.css} onCss={onCss} edit={edit} lookup={lookup} />
    </div>
  );
}
