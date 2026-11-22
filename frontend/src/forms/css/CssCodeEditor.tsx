import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
  snippetCompletion,
  type Completion,
  type CompletionContext,
  type CompletionResult,
} from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab, redo, undo } from "@codemirror/commands";
import { css } from "@codemirror/lang-css";
import { bracketMatching, foldGutter, foldKeymap, HighlightStyle, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { lintGutter, setDiagnostics, type Diagnostic } from "@codemirror/lint";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { EditorState, type Range } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  placeholder,
  ViewPlugin,
  type ViewUpdate,
  WidgetType,
} from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import { forwardRef, useEffect, useEffectEvent, useImperativeHandle, useRef } from "react";
import { ALL_PROPERTIES, CLASSES, VARIABLES, VOCAB } from "./cssVocabulary";

export interface CssField {
  key: string;
  type: string;
  label: string;
}
export interface CssProblem {
  line: number;
  message: string;
}
export interface CssEditorHandle {
  /** Insère à la position du curseur. */
  insert: (text: string) => void;
  /** Ajoute une règle vide à la fin et place le curseur dedans. */
  addRule: (selector: string) => void;
  undo: () => void;
  redo: () => void;
  focus: () => void;
  hasFocus: () => boolean;
}
interface Props {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  ariaLabel: string;
  fields: CssField[];
  problems: CssProblem[];
  onCursor?: (pos: { line: number; col: number }) => void;
}

const COLOR_RE = /#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)|hsla?\([^)]*\)/g;
const COLOR_PROP = /color|background|border|outline|shadow|fill|stroke/;
const COLORS: [string, string][] = [
  ["var(--accent)", "accent"],
  ["var(--fg)", "texte"],
  ["var(--muted)", "discret"],
  ["var(--border)", "bordure"],
  ["transparent", "transparent"],
  ["currentColor", "couleur du texte"],
  ["#1d4ed8", "bleu"],
  ["#15803d", "vert"],
  ["#b91c1c", "rouge"],
  ["#f8fafc", "gris très clair"],
  ["#0f172a", "bleu nuit"],
];

/** Gabarit d'extrait : « §{ » tient la place de « ${ » (champ à remplir). */
const slot = (template: string, info: Completion) => snippetCompletion(template.replaceAll("§{", "$" + "{"), info);

class Swatch extends WidgetType {
  constructor(
    readonly text: string,
    readonly editable: boolean,
  ) {
    super();
  }
  eq(o: Swatch) {
    return o.text === this.text && o.editable === this.editable;
  }
  toDOM(view: EditorView) {
    const wrap = document.createElement("span");
    wrap.className = "cm-swatch";
    wrap.style.background = this.text;
    if (!this.editable || view.state.readOnly) return wrap;
    const input = document.createElement("input");
    input.type = "color";
    input.tabIndex = -1;
    input.setAttribute("aria-label", `Choisir une autre couleur que ${this.text}`);
    if (/^#[0-9a-f]{6}$/i.test(this.text)) input.value = this.text.toLowerCase();
    input.addEventListener("change", () => {
      const from = view.posAtDOM(wrap);
      if (view.state.doc.sliceString(from, from + this.text.length) === this.text)
        view.dispatch({ changes: { from, to: from + this.text.length, insert: input.value } });
    });
    wrap.append(input);
    return wrap;
  }
  ignoreEvent() {
    return false;
  }
}
/** Une pastille de couleur devant chaque couleur écrite ; un clic ouvre le sélecteur de couleur (sauf couleurs avec transparence). */
const swatches = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = this.build(view);
    }
    update(u: ViewUpdate) {
      if (u.docChanged || u.viewportChanged) this.decorations = this.build(u.view);
    }
    build(view: EditorView) {
      const out: Range<Decoration>[] = [];
      for (const { from, to } of view.visibleRanges) {
        const text = view.state.doc.sliceString(from, to);
        for (const m of text.matchAll(COLOR_RE)) {
          const editable = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(m[0]);
          out.push(Decoration.widget({ widget: new Swatch(m[0], editable), side: -1 }).range(from + (m.index ?? 0)));
        }
      }
      return Decoration.set(out, true);
    }
  },
  { decorations: (v) => v.decorations },
);

/** Où est le curseur : dans les accolades d'une règle (déclarations) ou en dehors (sélecteurs, @media). */
function contextAt(doc: string, pos: number) {
  const stack: ("rule" | "media")[] = [];
  let start = 0;
  let inComment = false;
  for (let i = 0; i < pos; i++) {
    const c = doc[i];
    if (inComment) {
      if (c === "*" && doc[i + 1] === "/") {
        inComment = false;
        i++;
      }
      continue;
    }
    if (c === "/" && doc[i + 1] === "*") {
      inComment = true;
      i++;
      continue;
    }
    if (c === "{") {
      stack.push(/^\s*@media/.test(doc.slice(start, i)) ? "media" : "rule");
      start = i + 1;
    } else if (c === "}") {
      stack.pop();
      start = i + 1;
    } else if (c === ";") start = i + 1;
  }
  return { inComment, inRule: stack[stack.length - 1] === "rule", segment: doc.slice(start, pos) };
}

function completeFor(fields: () => CssField[]) {
  return (ctx: CompletionContext): CompletionResult | null => {
    const doc = ctx.state.doc.toString();
    const { inComment, inRule, segment } = contextAt(doc, ctx.pos);
    if (inComment) return null;
    // var(--… : les variables du thème
    const v = ctx.matchBefore(/var\(\s*--[\w-]*/);
    if (v) {
      const at = v.text.lastIndexOf("--");
      return { from: v.from + at, options: VARIABLES.map(([label, detail]) => ({ label, detail, type: "variable" })), validFor: /^--[\w-]*$/ };
    }
    if (inRule) {
      const colon = segment.indexOf(":");
      if (colon < 0) {
        const w = ctx.matchBefore(/[\w-]*/);
        if (!w || (w.from === w.to && !ctx.explicit)) return null;
        return {
          from: w.from,
          options: ALL_PROPERTIES.map((p) => snippetCompletion(`${p.name}: \${};`, { label: p.name, detail: p.group, type: "property" })),
          validFor: /^[\w-]*$/,
        };
      }
      const prop = segment.slice(0, colon).trim().toLowerCase();
      const w = ctx.matchBefore(/[#\w-]*/);
      if (!w || (w.from === w.to && !ctx.explicit)) return null;
      const options: Completion[] = (VOCAB.enums[prop] ?? []).map((label) => ({ label, type: "keyword" }));
      if (COLOR_PROP.test(prop)) options.push(...COLORS.map(([label, detail]) => ({ label, detail, type: "constant" })));
      options.push(
        ...["var", "calc", "min", "max", "clamp"]
          .filter((f) => VOCAB.functions.includes(f))
          .map((f) => snippetCompletion(`${f}(\${})`, { label: `${f}()`, type: "function" })),
      );
      if (COLOR_PROP.test(prop))
        options.push(
          ...["rgb", "rgba", "hsl", "linear-gradient", "color-mix"].map((f) => snippetCompletion(`${f}(\${})`, { label: `${f}()`, type: "function" })),
        );
      options.push(...["0", "1px", "2px", "4px", "8px", "12px", "16px", "1rem", "100%", "auto"].map((label) => ({ label, type: "text", boost: -1 })));
      return { from: w.from, options, validFor: /^[#\w-]*$/ };
    }
    // hors accolades : sélecteurs, pseudo-classes, @media
    const pseudo = ctx.matchBefore(/:[\w-]*/);
    if (pseudo) return { from: pseudo.from + 1, options: VOCAB.pseudoClasses.map((label) => ({ label, type: "keyword" })), validFor: /^[\w-]*$/ };
    const attr = ctx.matchBefore(/\[[\w-]*/);
    if (attr) {
      const opts: Completion[] = VOCAB.attributes.map((a) => ({ label: `[${a}]`, type: "property" }));
      for (const f of fields()) opts.push({ label: `[data-key="${f.key}"]`, detail: f.label || f.key, type: "variable", boost: 2 });
      for (const ty of [...new Set(fields().map((f) => f.type))]) opts.push({ label: `[data-type="${ty}"]`, detail: "type de champ", type: "variable" });
      return { from: attr.from, options: opts, validFor: /^\[[\w-]*$/ };
    }
    const at = ctx.matchBefore(/@[\w-]*/);
    if (at)
      return {
        from: at.from,
        options: [
          slot("@media (max-width: §{600}px) {\n  §{}\n}", { label: "@media (max-width…)", detail: "petits écrans", type: "keyword" }),
          slot("@media (min-width: §{900}px) {\n  §{}\n}", { label: "@media (min-width…)", detail: "grands écrans", type: "keyword" }),
          slot("@media (prefers-color-scheme: dark) {\n  §{}\n}", {
            label: "@media (prefers-color-scheme: dark)",
            detail: "mode sombre",
            type: "keyword",
          }),
        ],
      };
    const w = ctx.matchBefore(/[.&\w*-]*/);
    if (!w || (w.from === w.to && !ctx.explicit)) return null;
    const options: Completion[] = [
      { label: "&", detail: "la racine du formulaire", type: "keyword" },
      ...CLASSES.map(([label, detail]) => ({ label, detail, type: "class" })),
      ...VOCAB.elements.map((label) => ({ label, type: "type" })),
      ...fields().map((f) => ({ label: `[data-key="${f.key}"]`, detail: f.label || f.key, type: "variable", boost: 2 })),
    ];
    return { from: w.from, options, validFor: /^[.&\w*-]*$/ };
  };
}

// Couleurs des jetons : variables CSS du thème clair (contraste AA, voir styles.css).
const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.operatorKeyword], color: "var(--syn-keyword)" },
  { tag: [t.string, t.special(t.string)], color: "var(--syn-string)" },
  { tag: [t.number, t.unit, t.atom, t.color, t.bool], color: "var(--syn-number)" },
  { tag: [t.comment, t.lineComment, t.blockComment], color: "var(--syn-comment)", fontStyle: "italic" },
  { tag: [t.className, t.tagName, t.typeName, t.function(t.variableName)], color: "var(--syn-func)" },
  { tag: [t.propertyName, t.attributeName, t.variableName], color: "var(--syn-prop)" },
]);
const theme = EditorView.theme(
  {
    "&": { height: "100%", fontSize: "13px", border: "1px solid var(--border)", borderRadius: "8px", backgroundColor: "#f8fafc", color: "#0f172a" },
    "&.cm-focused": { outline: "3px solid var(--accent)", outlineOffset: "1px" },
    ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", lineHeight: "1.5", overflow: "auto" },
    ".cm-gutters": { backgroundColor: "#eef2f7", color: "#475569", borderRight: "1px solid var(--border)" },
    ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "rgba(29, 78, 216, .08)" },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "#0f172a", borderLeftWidth: "2px" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { background: "rgba(59, 130, 246, .28) !important" },
    ".cm-panels": { backgroundColor: "#eef2f7", color: "#0f172a", borderBottom: "1px solid var(--border)" },
  },
  { dark: false },
);

/** Éditeur de CSS (CodeMirror 6) : coloration, numéros de ligne, repli, recherche (Ctrl+F), suggestions de ce que le serveur accepte
 * (propriétés, valeurs, sélecteurs du formulaire, variables), pastilles de couleur avec sélecteur, et erreurs du serveur à la ligne.
 * Tab indente ; Échap rend le clavier à la page. */
export const CssCodeEditor = forwardRef<CssEditorHandle, Props>(function CssCodeEditor(
  { value, onChange, readOnly = false, ariaLabel, fields, problems, onCursor },
  handle,
) {
  const host = useRef<HTMLDivElement>(null);
  const exit = useRef<HTMLSpanElement>(null);
  const view = useRef<EditorView | null>(null);
  const fieldsRef = useRef(fields);
  const onChangeRef = useRef(onChange);
  const cursorRef = useRef(onCursor);
  fieldsRef.current = fields;
  onChangeRef.current = onChange;
  cursorRef.current = onCursor;

  useImperativeHandle(handle, () => ({
    insert: (text) => {
      const v = view.current;
      if (!v || v.state.readOnly) return;
      const { from, to } = v.state.selection.main;
      v.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length }, scrollIntoView: true });
      v.focus();
    },
    addRule: (selector) => {
      const v = view.current;
      if (!v || v.state.readOnly) return;
      const end = v.state.doc.length;
      const lead = end === 0 ? "" : v.state.doc.toString().endsWith("\n\n") ? "" : v.state.doc.toString().endsWith("\n") ? "\n" : "\n\n";
      const ins = `${lead}${selector} {\n  \n}\n`;
      v.dispatch({ changes: { from: end, insert: ins }, selection: { anchor: end + lead.length + selector.length + 5 }, scrollIntoView: true });
      v.focus();
    },
    undo: () => {
      if (view.current) {
        undo(view.current);
        view.current.focus();
      }
    },
    redo: () => {
      if (view.current) {
        redo(view.current);
        view.current.focus();
      }
    },
    focus: () => view.current?.focus(),
    hasFocus: () => !!view.current?.hasFocus,
  }));

  const createView = useEffectEvent((parent: HTMLElement) => {
    const state = EditorState.create({
      doc: value,
      extensions: [
        placeholder("/* Le CSS apparaît ici dès que vous réglez un élément. */"),
        lineNumbers(),
        foldGutter(),
        lintGutter(),
        highlightActiveLineGutter(),
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        highlightActiveLine(),
        highlightSelectionMatches(),
        search({ top: true }),
        css(),
        syntaxHighlighting(highlight),
        autocompletion({ override: [completeFor(() => fieldsRef.current)], icons: true }),
        swatches,
        keymap.of([
          {
            key: "Escape",
            run: () => {
              exit.current?.focus();
              return true;
            },
          },
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...foldKeymap,
          ...completionKeymap,
          indentWithTab,
        ]),
        EditorState.readOnly.of(readOnly),
        EditorView.contentAttributes.of({ "aria-label": ariaLabel, "aria-multiline": "true", tabindex: "0", spellcheck: "false" }),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) onChangeRef.current(u.state.doc.toString());
          if (u.docChanged || u.selectionSet) {
            const head = u.state.selection.main.head;
            const ln = u.state.doc.lineAt(head);
            cursorRef.current?.({ line: ln.number, col: head - ln.from + 1 });
          }
        }),
        theme,
      ],
    });
    view.current = new EditorView({ state, parent });
  });
  useEffect(() => {
    if (!host.current) return;
    createView(host.current);
    return () => {
      view.current?.destroy();
      view.current = null;
    };
  }, []);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  // les erreurs du serveur sont soulignées à leur ligne, avec le message au survol
  useEffect(() => {
    const v = view.current;
    if (!v) return;
    const doc = v.state.doc;
    const list: Diagnostic[] = problems.map((p) => {
      const ln = doc.line(Math.min(Math.max(1, p.line), doc.lines));
      return { from: ln.from, to: Math.max(ln.to, ln.from), severity: "error", message: p.message };
    });
    v.dispatch(setDiagnostics(v.state, list));
  }, [problems]);

  return (
    <div className="cs-cm">
      <div ref={host} className="cs-cm-host" />
      <span ref={exit} tabIndex={-1} className="editor-exit">
        Fin de l'éditeur de CSS
      </span>
    </div>
  );
});
