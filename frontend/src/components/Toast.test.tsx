import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ToastProvider, useToast } from "./Toast";

function Demo({ undo }: { undo: () => void }) {
  const t = useToast();
  return <button type="button" onClick={() => t.show("Workflow supprimé.", { undo })}>go</button>;
}

describe("Toast", () => {
  it("announces in a status region, offers Annuler for a reversible action and closes after use", async () => {
    const undo = vi.fn();
    render(<ToastProvider><Demo undo={undo} /></ToastProvider>);
    await userEvent.click(screen.getByRole("button", { name: "go" }));
    expect(screen.getByRole("status")).toHaveTextContent("Workflow supprimé.");
    await userEvent.click(screen.getByRole("button", { name: "Annuler" }));
    expect(undo).toHaveBeenCalledOnce();
    expect(screen.queryByText("Workflow supprimé.")).not.toBeInTheDocument();
  });

  it("goes away by itself", () => {
    vi.useFakeTimers();
    render(<ToastProvider><Demo undo={() => undefined} /></ToastProvider>);
    act(() => { screen.getByRole("button", { name: "go" }).click(); });
    expect(screen.getByText("Workflow supprimé.")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(9000); });
    expect(screen.queryByText("Workflow supprimé.")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
