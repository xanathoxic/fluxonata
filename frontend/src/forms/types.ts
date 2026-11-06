export type DataFieldType = "text" | "textarea" | "number" | "email" | "date" | "select" | "radio" | "combobox" | "checkbox" | "checkbox_list" | "datagrid" | "custom";
/** Content blocks: text and separators of the page. No value, not bound to a workflow input. */
export type BlockType = "heading" | "paragraph" | "divider";
export type FieldType = DataFieldType | BlockType;
export const BLOCK_TYPES: BlockType[] = ["heading", "paragraph", "divider"];
export const isBlock = (t: FieldType): t is BlockType => (BLOCK_TYPES as string[]).includes(t);

/** Placement on the 12-column grid of a section: a row, a first column and a width in columns. */
/** `height` is the number of rows the field covers (1 when absent). */
export interface Layout { row: number; col: number; span: number; height?: number }
export interface FormStyle {
  accent?: string;
  density?: "compact" | "normal" | "comfortable";
  labels?: "top" | "left";
  radius?: "none" | "small" | "medium" | "large";
  width?: "narrow" | "medium" | "wide" | "full";
  sections?: "card" | "plain" | "line";
  button?: "left" | "center" | "right" | "full";
}

export interface FieldOption { value: string; label?: string }
export interface DataSource { connection: string; path: string; items_path?: string; label_path?: string; value_path?: string; group_path?: string; disabled_path?: string; connection_from?: string; connections?: Record<string, string>; items_as?: "list" | "entries"; where?: string; label_expr?: string; sort?: "label" | "label_desc"; params?: string[] }
export interface GridColumn { key: string; label?: string; type?: "text" | "number" }
export interface FormField {
  key: string;
  label: string;
  type: FieldType;
  required?: boolean;
  help?: string;
  placeholder?: string;
  min?: number;
  max?: number;
  visible_if?: string;
  options?: FieldOption[];
  /** « Cases multiples » only: the usual list of boxes, or a dual list (available / chosen, the chosen ones in order) */
  display?: "list" | "dual";
  /** the titles of the two columns of a dual list (default: « Disponibles » / « Sélectionnés ») */
  available_label?: string;
  chosen_label?: string;
  source?: DataSource;
  columns?: GridColumn[];
  layout?: Layout;
  /** conditions and what they change (see rules.ts and triggers/formrules.py) */
  rules?: import("./rules").Rule[];
  /** Formula of a calculated field (see calc.ts): its value is always the one computed, never typed. */
  compute?: string;
  /** headings only: 2, 3 or 4 */
  level?: 2 | 3 | 4;
  /** custom controls only: the control (and version) this field comes from, with a copy of its description */
  control?: import("./controls").ControlRef;
  /** custom controls only: the whole value pre-filled from a web service (this form's own reading; the control carries none) */
  prefill?: import("./controls").ControlPrefill;
}
export interface FormSection { id?: string; title: string; description?: string; fields: FormField[] }
export interface FormDefinition {
  version?: number;
  sections: FormSection[];
  style?: FormStyle;
  /** the author's own CSS, in the canonical form the server wrote (see triggers/formcss.py) */
  css?: string;
  /** a workflow's `input_form` only: which field (key) feeds each input of the workflow ({input: field key}; "" = none) */
  map?: Record<string, string>;
}

export const FIELD_TYPES: { type: FieldType; label: string; hint: string; group?: "content" }[] = [
  { type: "text", label: "Texte court", hint: "Une ligne" },
  { type: "textarea", label: "Texte long", hint: "Plusieurs lignes" },
  { type: "number", label: "Nombre", hint: "Avec bornes facultatives" },
  { type: "email", label: "Adresse e-mail", hint: "Format vérifié" },
  { type: "date", label: "Date", hint: "AAAA-MM-JJ" },
  { type: "select", label: "Liste déroulante", hint: "Un choix" },
  { type: "radio", label: "Boutons radio", hint: "Un choix, tous visibles" },
  { type: "combobox", label: "Liste avec recherche", hint: "Un choix, on tape pour filtrer" },
  { type: "checkbox", label: "Case à cocher", hint: "Oui / non" },
  { type: "checkbox_list", label: "Cases multiples", hint: "Plusieurs choix" },
  { type: "datagrid", label: "Tableau", hint: "Lignes à colonnes fixes" },
  { type: "heading", label: "Titre", hint: "Intitulé d'une partie", group: "content" },
  { type: "paragraph", label: "Texte", hint: "Explication ou consigne", group: "content" },
  { type: "divider", label: "Séparateur", hint: "Trait horizontal", group: "content" },
];
export const CHOICE_TYPES: FieldType[] = ["select", "radio", "combobox", "checkbox_list"];
/** Types a control's sub-field may have (no dynamic list or table; a published control may be nested, from the palette). */
export const PART_TYPES: FieldType[] = ["text", "textarea", "number", "email", "date", "select", "radio", "checkbox", "checkbox_list", "heading", "paragraph", "divider"];
/** JSON type each field produces: it must match the workflow input it is bound to. */
export const PRODUCES: Record<DataFieldType, string> = { text: "string", textarea: "string", email: "string", date: "string", select: "string", radio: "string", combobox: "string", number: "number", checkbox: "boolean", checkbox_list: "array", datagrid: "array", custom: "object" };

/** Connection kinds that can feed a dynamic list (those of the HTTP step). */
export const HTTP_KINDS = ["http_bearer", "api_key", "basic", "oauth2_client", "oauth2_code", "aws_sigv4"];

export const emptyDefinition = (): FormDefinition => ({ version: 1, sections: [{ id: "s1", title: "Informations", fields: [] }] });
/** Reference to a library form, as a workflow stores it: no version = « the latest published one » at publication, which pins it. */
export interface FormRefInfo { form: string; version: string | null }
/** A workflow's `input_form`: a bare reference while drafting, a copy of the pinned definition (+ `ref`) once published. */
export type InputForm = (FormDefinition & { ref?: FormRefInfo }) | { ref: FormRefInfo; sections?: undefined; version?: number; map?: Record<string, string> };

export const allFields = (d: FormDefinition): FormField[] => d.sections.flatMap((s) => s.fields);
