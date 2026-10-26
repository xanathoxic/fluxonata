import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap, type CompletionContext } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { javascript } from "@codemirror/lang-javascript";
import { python } from "@codemirror/lang-python";
import { bracketMatching, HighlightStyle, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { Compartment, EditorState } from "@codemirror/state";
import { drawSelection, EditorView, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers } from "@codemirror/view";
import { useEffect, useRef } from "react";

export interface Completion { label: string; detail?: string }
interface Props {
  value: string;
  onChange: (value: string) => void;
  language: "python" | "javascript";
  completions?: Completion[];
  ariaLabel: string;
  readOnly?: boolean;
  minHeight?: string;
}

// Token colours come from CSS variables so that the light theme keeps WCAG AA contrast (see styles.css).
const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.operatorKeyword, t.definitionKeyword, t.moduleKeyword], color: "var(--syn-keyword)" },
  { tag: [t.string, t.special(t.string)], color: "var(--syn-string)" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "var(--syn-number)" },
  { tag: [t.comment, t.lineComment, t.blockComment], color: "var(--syn-comment)", fontStyle: "italic" },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.className, t.typeName], color: "var(--syn-func)" },
  { tag: [t.propertyName, t.attributeName], color: "var(--syn-prop)" },
]);

const theme = EditorView.theme({
  "&": { fontSize: "14px", border: "1px solid var(--border)", borderRadius: "4px", backgroundColor: "var(--bg)", color: "var(--fg)" },
  "&.cm-focused": { outline: "3px solid var(--accent)", outlineOffset: "1px" },
  ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", lineHeight: "1.5" },
  ".cm-gutters": { backgroundColor: "var(--card)", color: "var(--muted)", borderRight: "1px solid var(--border)" },
  ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "color-mix(in srgb, var(--accent) 10%, transparent)" },
  ".cm-content": { caretColor: "var(--fg)" },
});

/**
 * Accessible code editor (CodeMirror 6). Tab indents like in any code editor, so Escape is the documented way out:
 * it moves focus to the element right after the editor (keyboard users are never trapped).
 */
export function CodeEditor({ value, onChange, language, completions = [], ariaLabel, readOnly = false, minHeight = "260px" }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const exit = useRef<HTMLSpanElement>(null);
  const view = useRef<EditorView | null>(null);
  const lang = useRef(new Compartment());
  const ro = useRef(new Compartment());
  const onChangeRef = useRef(onChange);
  const completionsRef = useRef(completions);
  onChangeRef.current = onChange;
  completionsRef.current = completions;

  useEffect(() => {
    if (!host.current) return;
    const source = (ctx: CompletionContext) => {
      const word = ctx.matchBefore(/[\w.]*/);
      if (!word || (word.from === word.to && !ctx.explicit)) return null;
      return { from: word.from, options: completionsRef.current.map((c) => ({ label: c.label, detail: c.detail, type: "variable" })), validFor: /^[\w.]*$/ };
    };
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(), highlightActiveLineGutter(), history(), drawSelection(), indentOnInput(), bracketMatching(), closeBrackets(), highlightActiveLine(),
        syntaxHighlighting(highlight),
        autocompletion({ override: [source] }),
        keymap.of([
          { key: "Escape", run: () => { exit.current?.focus(); return true; } },
          ...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, ...completionKeymap, indentWithTab,
        ]),
        lang.current.of(language === "python" ? python() : javascript()),
        ro.current.of(EditorState.readOnly.of(readOnly)),
        EditorView.contentAttributes.of({ "aria-label": ariaLabel, "aria-multiline": "true", tabindex: "0" }),
        EditorView.updateListener.of((u) => { if (u.docChanged) onChangeRef.current(u.state.doc.toString()); }),
        theme,
      ],
    });
    view.current = new EditorView({ state, parent: host.current });
    return () => { view.current?.destroy(); view.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the editor is created once; props are synced below
  }, []);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);
  useEffect(() => {
    view.current?.dispatch({ effects: lang.current.reconfigure(language === "python" ? python() : javascript()) });
  }, [language]);
  useEffect(() => {
    view.current?.dispatch({ effects: ro.current.reconfigure(EditorState.readOnly.of(readOnly)) });
  }, [readOnly]);

  return (
    <div className="code-editor" style={{ ["--editor-min" as string]: minHeight }}>
      <div ref={host} />
      <span ref={exit} tabIndex={-1} className="editor-exit">Fin de l'éditeur de code</span>
      <p className="hint">Touche Tab : indente. Échap : quitte l'éditeur pour continuer la navigation au clavier. Ctrl+Espace : suggestions de variables.</p>
    </div>
  );
}
