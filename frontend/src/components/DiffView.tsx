/** Différences entre deux versions (format unifié) : numéros de ligne, ajouts en vert, suppressions en rouge. */
export function DiffView({ diff, from, to }: { diff: string; from: string; to: string }) {
  let a = 0, b = 0;
  const rows = diff.split("\n").filter((l, i, all) => !(l === "" && i === all.length - 1)).map((line, i) => {
    if (line.startsWith("---") || line.startsWith("+++")) return { i, kind: "file", a: "", b: "", line };
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(line);
    if (hunk) { a = Number(hunk[1]); b = Number(hunk[2]); return { i, kind: "hunk", a: "", b: "", line }; }
    if (line.startsWith("+")) return { i, kind: "add", a: "", b: String(b++), line };
    if (line.startsWith("-")) return { i, kind: "del", a: String(a++), b: "", line };
    return { i, kind: "ctx", a: String(a++), b: String(b++), line };
  });
  return (
    <div className="diff-view" role="region" aria-label={`Différences entre la version ${from} et la version ${to}`} tabIndex={0}>
      <table>
        <caption className="sr-only">Différences de code entre {from} et {to}</caption>
        <tbody>
          {rows.map((r) => (
            <tr key={r.i} className={`df-${r.kind}`}>
              <td className="df-n" aria-hidden="true">{r.a}</td><td className="df-n" aria-hidden="true">{r.b}</td>
              <td className="df-t"><span className="sr-only">{r.kind === "add" ? "Ajout : " : r.kind === "del" ? "Suppression : " : ""}</span>{r.line || " "}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
