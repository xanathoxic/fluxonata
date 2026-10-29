import { EditorView } from "@codemirror/view";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef, useState } from "react";
import { CodeEditor, type CodeEditorHandle } from "./CodeEditor";

function Harness({ language = "python" as const }) {
  const [v, setV] = useState("x = 1");
  return (
    <>
      <CodeEditor value={v} onChange={setV} language={language} ariaLabel="Code du test" completions={[{ label: "System.log" }]} />
      <button type="button">Après l'éditeur</button>
      <output aria-label="contenu">{v}</output>
    </>
  );
}

describe("CodeEditor", () => {
  it("exposes an accessible multiline textbox and reports edits (jsdom has no layout: edits go through the view API)", () => {
    render(<Harness />);
    const box = screen.getByRole("textbox", { name: "Code du test" });
    expect(box).toHaveAttribute("aria-multiline", "true");
    const view = EditorView.findFromDOM(box.closest(".cm-editor") as HTMLElement) as EditorView;
    expect(view.state.doc.toString()).toBe("x = 1");
    act(() => view.dispatch({ changes: { from: view.state.doc.length, insert: "\ny = 2" } }));
    expect(screen.getByLabelText("contenu")).toHaveTextContent("x = 1 y = 2");
  });

  it("does not trap keyboard users: Escape leaves the editor, Tab then reaches the next control", async () => {
    render(<Harness />);
    const box = screen.getByRole("textbox", { name: "Code du test" });
    box.focus();
    await userEvent.keyboard("{Escape}");
    expect(box).not.toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: /À savoir/ })).toHaveFocus(); // l'aide sur les touches vient juste après l'éditeur
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Après l'éditeur" })).toHaveFocus();
  });

  it("insère du texte au curseur par la poignée et publie la position du curseur", () => {
    const ref = createRef<CodeEditorHandle>();
    const seen: { line: number; col: number; lines: number }[] = [];
    function Insert() {
      const [v, setV] = useState("a\nb");
      return <CodeEditor ref={ref} value={v} onChange={setV} language="python" ariaLabel="Code du test" onCursor={(c) => seen.push(c)} />;
    }
    render(<Insert />);
    const view = EditorView.findFromDOM(screen.getByRole("textbox", { name: "Code du test" }).closest(".cm-editor") as HTMLElement) as EditorView;
    act(() => view.dispatch({ selection: { anchor: 2 } }));
    act(() => ref.current?.insert("vars"));
    expect(view.state.doc.toString()).toBe("a\nvarsb");
    expect(seen.at(-1)).toMatchObject({ line: 2, col: 5, lines: 2 });
  });
});
