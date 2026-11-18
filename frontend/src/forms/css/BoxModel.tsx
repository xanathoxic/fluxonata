import { useState } from "react";
import { FormIcon } from "../FormIcon";
import { UNITS, formatBox, joinLength, parseBox, splitLength, type Box } from "./values";

const SIDES = ["Haut", "Droite", "Bas", "Gauche"] as const;
const ORDER = ["top", "right", "bottom", "left"] as const;

interface BoxProps {
  /** « Marge extérieure », « Marge intérieure » : sert aux noms accessibles */
  label: string;
  value: string;
  onChange: (v: string) => void;
  /** une marge extérieure peut être négative et « auto » (centrer) ; un padding, jamais */
  outer?: boolean;
  children: React.ReactNode;
}

/** Une des deux boîtes : quatre côtés (liés ou non), une unité, et au milieu ce qu'elle entoure. */
function Box4({ label, value, onChange, outer, children }: BoxProps) {
  const sides = parseBox(value);
  const unit = splitLength(sides.find((s) => splitLength(s)) ?? "")?.unit || "px";
  const [linked, setLinked] = useState(sides.every((s) => s === sides[0]));
  const centered = sides[1] === "auto" && sides[3] === "auto";
  const num = (s: string) => splitLength(s)?.n ?? "";
  const write = (b: Box) => onChange(formatBox(b));
  const edit = (i: number, v: string) => write(linked ? [v, v, v, v] : (sides.map((s, k) => (k === i ? v : s)) as Box));
  const setUnit = (u: string) => write(sides.map((s) => (splitLength(s) ? joinLength(num(s), u) : s)) as Box);
  const center = () => write(centered ? [sides[0], "", sides[2], ""] : [sides[0], "auto", sides[2], "auto"]);
  return (
    <div className={`cs-box ${outer ? "cs-box-outer" : "cs-box-inner"}`}>
      <div className="cs-boxbar">
        <span className="cs-boxname">{outer ? "Extérieur" : "Intérieur"}</span>
        <button
          type="button"
          className={`cs-opt${linked ? " on" : ""}`}
          aria-pressed={linked}
          aria-label={`${label} : lier les quatre côtés`}
          title={linked ? "Quatre côtés liés" : "Côtés indépendants"}
          onClick={() => setLinked(!linked)}
        >
          <FormIcon name={linked ? "lock" : "unlock"} size={14} />
        </button>
        {outer && (
          <button
            type="button"
            className={`cs-opt${centered ? " on" : ""}`}
            aria-pressed={centered}
            aria-label={`${label} : centrer horizontalement (auto à gauche et à droite)`}
            title="Centrer horizontalement (auto à gauche et à droite)"
            onClick={center}
          >
            <FormIcon name="alignC" size={14} />
          </button>
        )}
        <select aria-label={`${label} : unité`} value={unit} onChange={(e) => setUnit(e.target.value)}>
          {UNITS.slice(0, 4).map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
      </div>
      <div className="cs-boxgrid">
        {ORDER.map((pos, i) => (
          <input
            key={pos}
            className={`cs-bside cs-b-${pos}`}
            type="number"
            min={outer ? -200 : 0}
            max={200}
            step={1}
            aria-label={`${label} : ${SIDES[i]?.toLowerCase()}`}
            title={`${SIDES[i]}${linked ? " (les quatre côtés sont liés)" : ""}`}
            placeholder={sides[i] === "auto" ? "auto" : "0"}
            value={num(sides[i] ?? "")}
            onChange={(e) => edit(i, e.target.value === "" ? "" : joinLength(e.target.value, unit))}
          />
        ))}
        <div className="cs-bcenter">{children}</div>
      </div>
    </div>
  );
}

interface Props {
  margin: string;
  padding: string;
  onMargin: (v: string) => void;
  onPadding: (v: string) => void;
}

/** Marge (autour) et padding (dedans) d'un élément, comme deux boîtes imbriquées : on lit d'un coup d'œil ce qui entoure quoi.
 * Les quatre côtés se lient ou se règlent un par un, la marge peut être négative ou centrée (auto). */
export function BoxModel({ margin, padding, onMargin, onPadding }: Props) {
  return (
    <div className="cs-boxmodel">
      <Box4 outer label="Marge extérieure" value={margin} onChange={onMargin}>
        <Box4 label="Marge intérieure" value={padding} onChange={onPadding}>
          <span className="cs-elt">Élément</span>
        </Box4>
      </Box4>
    </div>
  );
}
