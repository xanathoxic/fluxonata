import { render, screen } from "@testing-library/react";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): never {
  throw new Error("forme inattendue");
}

describe("ErrorBoundary", () => {
  it("replaces a crashing piece with a message and leaves the rest of the page", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<><p>reste de la page</p><ErrorBoundary label="Commandes SSH"><Boom /></ErrorBoundary></>);
    expect(screen.getByText("reste de la page")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Impossible d'afficher « Commandes SSH »");
    quiet.mockRestore();
  });

  it("renders its children when nothing is wrong", () => {
    render(<ErrorBoundary label="x"><p>ok</p></ErrorBoundary>);
    expect(screen.getByText("ok")).toBeInTheDocument();
  });
});
