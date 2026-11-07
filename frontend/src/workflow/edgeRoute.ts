/** Tracé d'un lien : une ligne brisée qui passe par les points que la personne a posés (coordonnées du schéma). */
export interface Pt { x: number; y: number }

/** Au plus ce nombre de points par lien : au-delà le tracé n'est plus lisible, et le graphe enregistré reste petit. */
export const MAX_POINTS = 12;

const dist = (a: Pt, b: Pt) => Math.hypot(b.x - a.x, b.y - a.y);

/** Points d'un tracé sans doublons consécutifs (deux points confondus ne font pas un segment). */
function distinct(pts: Pt[]): Pt[] {
  return pts.filter((p, i) => i === 0 || dist(pts[i - 1] as Pt, p) >= 0.5);
}

/** `M … L … Q …` : segments droits, angles arrondis (rayon réduit si un segment est court). */
export function roundedPath(chain: Pt[], radius = 10): string {
  const pts = distinct(chain);
  const first = pts[0];
  if (!first) return "";
  let d = `M ${first.x} ${first.y}`;
  for (let i = 1; i < pts.length; i++) {
    const prev = pts[i - 1] as Pt;
    const cur = pts[i] as Pt;
    const next = pts[i + 1];
    if (!next) { d += ` L ${cur.x} ${cur.y}`; break; }
    const d1 = dist(prev, cur);
    const d2 = dist(cur, next);
    const r = Math.min(radius, d1 / 2, d2 / 2);
    const a = { x: cur.x + ((prev.x - cur.x) / d1) * r, y: cur.y + ((prev.y - cur.y) / d1) * r };
    const b = { x: cur.x + ((next.x - cur.x) / d2) * r, y: cur.y + ((next.y - cur.y) / d2) * r };
    d += ` L ${a.x} ${a.y} Q ${cur.x} ${cur.y} ${b.x} ${b.y}`;
  }
  return d;
}

/** Milieu de chaque segment : là où s'accroche la poignée « ajouter un point ». */
export function segmentMidpoints(chain: Pt[]): Pt[] {
  return chain.slice(1).map((p, i) => { const a = chain[i] as Pt; return { x: (a.x + p.x) / 2, y: (a.y + p.y) / 2 }; });
}

/** Où poser le libellé : au milieu du segment le plus long. */
export function labelPoint(chain: Pt[]): Pt {
  const mids = segmentMidpoints(chain);
  let best = 0;
  let bestLen = -1;
  for (let i = 1; i < chain.length; i++) {
    const len = dist(chain[i - 1] as Pt, chain[i] as Pt);
    if (len > bestLen) { bestLen = len; best = i - 1; }
  }
  return mids[best] ?? chain[0] ?? { x: 0, y: 0 };
}

/** Points tels qu'on les enregistre : entiers, valides, sans doublons consécutifs, `MAX_POINTS` au plus. */
export function cleanPoints(points: Pt[]): Pt[] {
  const ok = points.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)).map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) }));
  return ok.filter((p, i) => i === 0 || p.x !== (ok[i - 1] as Pt).x || p.y !== (ok[i - 1] as Pt).y).slice(0, MAX_POINTS);
}
