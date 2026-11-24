/** Tracé d'un lien : une ligne orthogonale (segments horizontaux et verticaux, angles arrondis) dont la personne glisse les segments. */
export interface Pt { x: number; y: number }

const dist = (a: Pt, b: Pt) => Math.hypot(b.x - a.x, b.y - a.y);

/** Points d'un tracé sans doublons consécutifs (deux points confondus ne font pas un segment). */
function distinct(pts: Pt[]): Pt[] {
  return pts.filter((p, i) => i === 0 || dist(pts[i - 1] as Pt, p) >= 0.5);
}

/** `M … L … Q …` : segments droits, angles arrondis (rayon réduit si un segment est court). Les points de `sharp` gardent un angle franc : c'est le cas
 *  d'un point de jonction, où les branches doivent partir droit, en croix. */
export function roundedPath(chain: Pt[], radius = 10, sharp: Pt[] = []): string {
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
    const r = sharp.some((p) => p.x === cur.x && p.y === cur.y) ? 0 : Math.min(radius, d1 / 2, d2 / 2);
    const a = { x: cur.x + ((prev.x - cur.x) / d1) * r, y: cur.y + ((prev.y - cur.y) / d1) * r };
    const b = { x: cur.x + ((next.x - cur.x) / d2) * r, y: cur.y + ((next.y - cur.y) / d2) * r };
    d += ` L ${a.x} ${a.y} Q ${cur.x} ${cur.y} ${b.x} ${b.y}`;
  }
  return d;
}

/** Les quatre faces d'une étape : haut, droite, bas, gauche. Chacune porte un point de connexion, utilisable en entrée comme en sortie. */
export type Face = "t" | "r" | "b" | "l";
export interface Ports { sf: Face; tf: Face }
export interface Rect { x: number; y: number; w: number; h: number }
export const FACES: Face[] = ["r", "l", "t", "b"];
/** Sens dans lequel on s'éloigne d'une face. */
const OUT: Record<Face, Pt> = { r: { x: 1, y: 0 }, l: { x: -1, y: 0 }, b: { x: 0, y: 1 }, t: { x: 0, y: -1 } };
const isH = (f: Face) => f === "l" || f === "r";

/** Un tracé orthogonal se décrit par ses coordonnées intérieures, en alternance, à partir du premier segment perpendiculaire à la face de départ
 *  (abscisse d'un segment vertical, ordonnée d'un segment horizontal…). Le premier et le dernier segment tiennent à une étape : leur position est
 *  celle de la face. Ex. `[x]` : départ à droite, un segment vertical, arrivée à gauche ; `[]` : un coude (départ à droite, arrivée par le haut). */
export type Coords = number[];
export interface Segment {
  /** Rang du segment sur la ligne complète (0 : celui qui part de la source) ; il alterne horizontal / vertical. */
  index: number;
  orientation: "v" | "h";
  from: Pt;
  to: Pt;
  mid: Pt;
  /** « start » / « end » : les segments attachés à une étape ; leur position est celle de la face, les tirer ajoute des coudes. */
  terminal: "start" | "end" | null;
}
export interface Orthogonal { points: Pt[]; segments: Segment[]; coords: Coords; ports: Ports }

/** Longueur minimale du départ (hors de l'étape source) et de l'arrivée (avant l'étape cible), avant le premier coude. */
export const STUB = 24;
/** Un segment attaché à une étape n'est proposé au glisser que s'il est assez long pour qu'on puisse le prendre. */
export const MIN_GRAB = 40;
/** Au plus ce nombre de coordonnées : au-delà le tracé n'est plus lisible et le graphe enregistré reste petit. */
export const MAX_COORDS = 9;
const SNAP = 4;
/** Écart (en pixels du schéma) en deçà duquel deux points de connexion sont considérés comme alignés. */
const ALIGN_TOLERANCE = 1.5;
/** Coût d'un coude dans le choix du tracé automatique (en pixels de longueur équivalente), et pénalités de choix des faces. */
const BEND_COST = 30;
const THROUGH_BOX = 100000;
const FACE_COST: Record<Face, number> = { r: 0, l: 0, t: 12, b: 50 }; // le libellé de l'étape est sous l'icône : on évite d'y passer
const NATURAL = 40; // sortir à droite pour entrer à gauche : le sens de lecture

const length = (a: Pt, b: Pt) => Math.hypot(b.x - a.x, b.y - a.y);
const at = (f: Face, p: Pt, d: number): Pt => ({ x: p.x + OUT[f].x * d, y: p.y + OUT[f].y * d });

/** Écart (en pixels du schéma) entre le point d'entrée et le point de sortie d'une même face : un lien qui arrive et un lien qui part par la même face ne
 *  se confondent pas. Répartition : face gauche, entrée en haut et sortie en bas ; face droite, l'inverse (entrée en bas, sortie en haut), de sorte que la
 *  sortie à droite et l'entrée à gauche de l'étape suivante sont à la même hauteur (une chaîne droite reste droite) ; faces haut et bas, entrée à gauche
 *  et sortie à droite. */
export const PORT_GAP = 5.6;

/** Le décalage d'un point de connexion le long de sa face : positif vers le bas ou la droite. */
export function portOffset(f: Face, role: "in" | "out", gap = PORT_GAP): number {
  const d = role === "out" ? gap : -gap;
  return f === "r" ? -d : d;
}

/** Le point de connexion d'une face : le centre de la face, décalé de `portOffset` le long de la face. */
export function facePoint(r: Rect, f: Face, role: "in" | "out" = "out", gap = PORT_GAP): Pt {
  const d = portOffset(f, role, gap);
  return f === "r" ? { x: r.x + r.w, y: r.y + r.h / 2 + d } : f === "l" ? { x: r.x, y: r.y + r.h / 2 + d } : f === "t" ? { x: r.x + r.w / 2 + d, y: r.y } : { x: r.x + r.w / 2 + d, y: r.y + r.h };
}

/** Points sans doublons, sans point intermédiaire d'un segment droit. `null` si la ligne fait demi-tour sur elle-même. */
function tidy(raw: Pt[]): Pt[] | null {
  const pts = raw.filter((p, i) => i === 0 || length(raw[i - 1] as Pt, p) >= 0.5);
  const out: Pt[] = [];
  for (const p of pts) {
    const a = out[out.length - 2];
    const b = out[out.length - 1];
    if (a && b) {
      const cross = (b.x - a.x) * (p.y - b.y) - (b.y - a.y) * (p.x - b.x);
      const dot = (b.x - a.x) * (p.x - b.x) + (b.y - a.y) * (p.y - b.y);
      if (cross === 0 && dot < 0) return null;
      if (cross === 0) out.pop();
    }
    out.push(p);
  }
  return out;
}

const crossesBox = (a: Pt, b: Pt, r: Rect): boolean => {
  const m = 3; // une ligne qui longe l'icône ou y entre par sa face n'en traverse pas l'intérieur
  const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x), y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y);
  return x1 > r.x + m && x0 < r.x + r.w - m && y1 > r.y + m && y0 < r.y + r.h - m;
};

function cost(pts: Pt[], boxes: Rect[]): number {
  let total = (pts.length - 2) * BEND_COST;
  for (let i = 1; i < pts.length; i++) {
    total += length(pts[i - 1] as Pt, pts[i] as Pt);
    for (const r of boxes) if (crossesBox(pts[i - 1] as Pt, pts[i] as Pt, r)) total += THROUGH_BOX;
  }
  return total;
}

/** Tous les segments sont horizontaux ou verticaux. */
const axisAligned = (pts: Pt[]) => pts.slice(1).every((p, i) => p.x === (pts[i] as Pt).x || p.y === (pts[i] as Pt).y);

/** La ligne doit sortir de la face de départ et entrer par la face d'arrivée, chaque fois sur au moins `STUB`. */
function leavesAndEnters(pts: Pt[], ports: Ports, minStart = STUB, minEnd = STUB): boolean {
  const first = pts[0] as Pt;
  const second = pts[1] as Pt;
  const penult = pts[pts.length - 2] as Pt;
  const last = pts[pts.length - 1] as Pt;
  const o = OUT[ports.sf];
  const e = OUT[ports.tf];
  return (second.x - first.x) * o.x + (second.y - first.y) * o.y >= minStart - 0.5 && (penult.x - last.x) * e.x + (penult.y - last.y) * e.y >= minEnd - 0.5;
}

/** Le tracé automatique entre deux faces : on essaie un coude, des Z et des U avec un décalage au milieu ou au-delà des deux extrémités, et on
 *  garde le plus court (les coudes et les traversées d'icônes coûtent). `null` si aucun tracé ne tient. */
export function autoRoute(s: Pt, t0: Pt, ports: Ports, boxes: Rect[] = [], stubs = { s: STUB, t: STUB }): Pt[] | null {
  // Deux points de connexion « alignés » à une fraction de pixel près (étapes alignées, icônes de tailles différentes) sont traités comme alignés : sinon le décrochement
  // minuscule est absorbé, le tracé droit devient une légère diagonale, refusée, et il ne reste que des tracés en arche.
  const aligned = (isH(ports.sf) && isH(ports.tf) && Math.abs(s.y - t0.y) < ALIGN_TOLERANCE) || (!isH(ports.sf) && !isH(ports.tf) && Math.abs(s.x - t0.x) < ALIGN_TOLERANCE);
  const t: Pt = aligned ? (isH(ports.sf) ? { x: t0.x, y: s.y } : { x: s.x, y: t0.y }) : t0;
  const s1 = at(ports.sf, s, stubs.s);
  const t1 = at(ports.tf, t, stubs.t);
  const xs = [(s1.x + t1.x) / 2, Math.max(s1.x, t1.x) + STUB, Math.min(s1.x, t1.x) - STUB].map(Math.round);
  const ys = [(s1.y + t1.y) / 2, Math.max(s1.y, t1.y) + STUB, Math.min(s1.y, t1.y) - STUB].map(Math.round);
  const zx = (x: number): Pt[] => [{ x, y: s1.y }, { x, y: t1.y }];
  const zy = (y: number): Pt[] => [{ x: s1.x, y }, { x: t1.x, y }];
  // l'ordre compte : à coût égal on garde le premier, donc le Z centré avant le coude collé à une extrémité
  const mids: Pt[][] = [[], zx(xs[0] as number), zy(ys[0] as number), [{ x: t1.x, y: s1.y }], [{ x: s1.x, y: t1.y }], zx(xs[1] as number), zx(xs[2] as number), zy(ys[1] as number), zy(ys[2] as number)];
  let best: Pt[] | null = null;
  let bestCost = Number.POSITIVE_INFINITY;
  for (const mid of mids) {
    const pts = tidy([s, s1, ...mid, t1, t]);
    if (!pts || pts.length < 2 || !axisAligned(pts) || !leavesAndEnters(pts, ports, stubs.s, stubs.t)) continue;
    const c = cost(pts, boxes);
    if (c < bestCost) { best = pts; bestCost = c; }
  }
  if (best && aligned) best = [...best.slice(0, -1), t0]; // le tracé finit au vrai point de connexion (l'écart est d'une fraction de pixel)
  return best;
}

/** Les faces choisies automatiquement entre deux icônes : la paire qui donne la ligne la plus courte et la plus lisible. Elle suit les étapes quand on
 *  les déplace. */
export function autoPorts(source: Rect, target: Rect, gap = PORT_GAP): Ports {
  let best: Ports = { sf: "r", tf: "l" };
  let bestCost = Number.POSITIVE_INFINITY;
  for (const sf of FACES) {
    for (const tf of FACES) {
      const route = autoRoute(facePoint(source, sf, "out", gap), facePoint(target, tf, "in", gap), { sf, tf }, [source, target]);
      if (!route) continue;
      const c = cost(route, [source, target]) + FACE_COST[sf] + FACE_COST[tf] - (sf === "r" && tf === "l" ? NATURAL : 0);
      if (c < bestCost) { best = { sf, tf }; bestCost = c; }
    }
  }
  return best;
}

/** Les coordonnées intérieures d'un tracé (voir `Coords`) : la position de chaque segment qui n'est ni le premier ni le dernier. */
export function coordsOf(pts: Pt[]): Coords {
  const out: Coords = [];
  for (let j = 1; j < pts.length - 2; j++) {
    const a = pts[j] as Pt;
    const b = pts[j + 1] as Pt;
    out.push(a.x === b.x ? a.x : a.y);
  }
  return out;
}

const shapeOk = (c: unknown, ports: Ports): c is Coords =>
  Array.isArray(c) && c.length <= MAX_COORDS && c.every((v) => typeof v === "number" && Number.isFinite(v)) && (c.length % 2 === 0) === (isH(ports.sf) !== isH(ports.tf));

/** Distance, depuis la face, du point de jonction où des liens qui partent du même point se séparent (ou arrivent au même point se rejoignent). */
export const JUNCTION = STUB + 4;

/** Le tronc commun de plusieurs liens qui partent du même point de connexion (ou y arrivent) : leur segment vertical (ou horizontal, pour les faces haut
 *  et bas) est posé au même endroit, à `JUNCTION` de la face, au lieu de l'être à mi-chemin de chaque cible. Les liens se séparent alors tous au même
 *  point (qu'on marque d'un point de jonction) et ne s'emmêlent plus. Rien (`undefined`) si le tracé automatique n'est pas un simple « Z », ou si les deux
 *  extrémités sont partagées (le milieu reste alors le plus lisible). */
export function junctionCoords(s: Pt, t: Pt, ports: Ports, boxes: Rect[], sharedOut: boolean, sharedIn: boolean): Coords | undefined {
  if (sharedOut === sharedIn || isH(ports.sf) !== isH(ports.tf)) return undefined;
  const auto = autoCoords(s, t, ports, boxes);
  if (auto.length !== 1) return undefined;
  const face = sharedOut ? ports.sf : ports.tf;
  const p = sharedOut ? s : t;
  const axis = isH(face) ? "x" : "y";
  return [Math.round(p[axis] + OUT[face][axis] * JUNCTION)];
}

/** Le centre d'un point de jonction automatique : sur le prolongement de la face, à `JUNCTION` du point de connexion. */
export function trunkPoint(p: Pt, face: Face): Pt {
  return at(face, p, JUNCTION);
}

/** Coût d'un tracé (longueur, coudes, traversées d'icônes) : de quoi comparer deux tracés possibles. */
export function routeCost(pts: Pt[], boxes: Rect[] = []): number {
  return cost(pts, boxes);
}

/** Le centre d'un point de jonction porté par un lien (voir `viaRoute`). `flip` : le point est inversé (voir `junctionFaces`). */
export interface Via { x: number; y: number; flip?: boolean }
/** Les faces d'un point de jonction : ses entrées (vertes) et ses sorties (rouges). Par défaut une entrée à gauche et trois sorties (droite, haut, bas) : plusieurs liens
 *  en repartent par la même face. Inversé (double-clic) : trois entrées (gauche, haut, bas) et une sortie à droite, pour rassembler plusieurs liens en un seul. */
export const junctionFaces = (flip?: boolean): { ins: Face[]; outs: Face[] } => (flip ? { ins: ["l", "t", "b"], outs: ["r"] } : { ins: ["l"], outs: ["r", "t", "b"] });

/** Dégagement minimal d'un tronçon de lien du côté d'un point de jonction, et du côté d'une étape, quand le tronçon touche un point de jonction. */
const JUNCTION_STUB = 6;
const VIA_NODE_STUB = 14;

/** Le tracé d'un lien qui passe par un ou plusieurs points de jonction (de rayon `r`) : de la source à l'entrée (à gauche) du premier, puis de la face de
 *  sortie qui donne le meilleur tracé (droite, haut ou bas) à l'entrée du suivant, enfin à la cible. Plusieurs liens peuvent donc quitter un point de jonction
 *  par la même face, et l'arrivée d'un lien suivant le même chemin est confondue avec celle des autres : ils partagent la branche commune. */
export function viaRoute(s: Pt, sf: Face, t: Pt, tf: Face, via: Via[], r: number, boxes: Rect[] = []): Pt[] {
  const rings = via.map((j): Rect => ({ x: j.x - r, y: j.y - r, w: 2 * r, h: 2 * r }));
  const all = [...boxes, ...rings];
  const pts: Pt[] = [s];
  // chaque tronçon a un point de jonction à l'un de ses bouts (ou aux deux) : de ce côté un court dégagement suffit, sinon une jonction proche de l'étape ferait un détour en créneau
  const leg = (a: Pt, af: Face, aJunction: boolean, b: Pt, bf: Face, bJunction: boolean): Pt[] =>
    autoRoute(a, b, { sf: af, tf: bf }, all, { s: aJunction ? JUNCTION_STUB : VIA_NODE_STUB, t: bJunction ? JUNCTION_STUB : VIA_NODE_STUB }) ?? [a, b];
  /** La meilleure face parmi `faces` d'un point de jonction pour relier `other` (le tracé le moins coûteux). */
  const best = (j: Via, faces: Face[], other: (p: Pt, f: Face) => Pt[]): { face: Face; point: Pt } => {
    let pick: { face: Face; point: Pt; c: number } | undefined;
    for (const face of faces) {
      const point = at(face, j, r);
      const c = cost(other(point, face), all);
      if (!pick || c < pick.c) pick = { face, point, c };
    }
    return pick as { face: Face; point: Pt };
  };
  let from = s;
  let fromFace = sf;
  via.forEach((j, i) => {
    const faces = junctionFaces(j.flip);
    const entry = best(j, faces.ins, (p, f) => leg(from, fromFace, i > 0, p, f, true));
    pts.push(...leg(from, fromFace, i > 0, entry.point, entry.face, true).slice(1));
    const following = via[i + 1];
    const next = following ? best(following, junctionFaces(following.flip).ins, () => [from, from]) : null; // le prochain point : sa première entrée
    const target = next ? { p: next.point, f: next.face } : { p: t, f: tf };
    const exit = best(j, faces.outs, (p, f) => leg(p, f, true, target.p, target.f, !!next));
    pts.push(exit.point); // à travers l'anneau, caché par son fond
    from = exit.point;
    fromFace = exit.face;
  });
  pts.push(...leg(from, fromFace, via.length > 0, t, tf, false).slice(1));
  return pts;
}

/** Les coordonnées du tracé automatique. */
export function autoCoords(s: Pt, t: Pt, ports: Ports, boxes: Rect[] = []): Coords {
  const pts = autoRoute(s, t, ports, boxes);
  if (!pts) return [];
  // une ligne droite entre deux faces parallèles est un « Z » dont le segment du milieu est de longueur nulle : on garde sa place pour pouvoir le tirer
  if (pts.length === 2) return [Math.round(isH(ports.sf) ? (s.x + t.x) / 2 : (s.y + t.y) / 2)];
  return coordsOf(pts);
}

/** Les points de passage d'un tracé (source, coudes, cible) : toujours des segments horizontaux et verticaux, en alternance. */
export function routePoints(s: Pt, t: Pt, ports: Ports, coords: Coords): Pt[] {
  const pts: Pt[] = [s];
  let x = s.x;
  let y = s.y;
  let vertical = !isH(ports.sf); // orientation du segment qui vient d'être tracé
  for (const v of coords) {
    vertical = !vertical;
    if (vertical) x = v; else y = v;
    pts.push({ x, y });
  }
  vertical = !vertical;
  if (vertical) x = t.x; else y = t.y;
  pts.push({ x, y }, t);
  return pts;
}

/** Remet un tracé dans ses bornes (le premier segment s'éloigne de la source d'au moins `STUB`, le dernier s'arrête `STUB` avant la cible). Un tracé qui
 *  ne tient plus (étapes déplacées, faces changées) redevient automatique. */
export function clampCoords(s: Pt, t: Pt, ports: Ports, coords: Coords, boxes: Rect[] = []): Coords {
  if (!shapeOk(coords, ports)) return autoCoords(s, t, ports, boxes);
  const out = [...coords];
  const bound = (i: number, face: Face, p: Pt) => { // s'éloigner de la face d'au moins STUB, du côté où elle regarde
    const axis = isH(face) ? "x" : "y";
    const edge = p[axis] + OUT[face][axis] * STUB;
    const v = out[i] as number;
    out[i] = OUT[face][axis] > 0 ? Math.max(v, edge) : Math.min(v, edge);
  };
  if (out.length > 0) {
    bound(0, ports.sf, s);
    bound(out.length - 1, ports.tf, t);
  }
  const pts = tidy(routePoints(s, t, ports, out));
  return pts && leavesAndEnters(pts, ports) ? out : autoCoords(s, t, ports, boxes);
}

/** Le tracé orthogonal d'un lien et ses segments déplaçables. Un segment de longueur nulle n'a rien à déplacer ; un segment attaché à une étape
 *  n'est proposé que s'il est assez long (`MIN_GRAB`). */
export function orthogonalRoute(s: Pt, t: Pt, ports: Ports, saved?: Coords, boxes: Rect[] = []): Orthogonal {
  const coords = saved && shapeOk(saved, ports) ? clampCoords(s, t, ports, saved, boxes) : autoCoords(s, t, ports, boxes);
  const drawn = tidy(routePoints(s, t, ports, coords));
  if (!drawn || !leavesAndEnters(drawn, ports)) return { points: [s, t], segments: [], coords: [], ports }; // aucun tracé ne tient (étapes qui se chevauchent) : droit, en dernier recours
  const points = routePoints(s, t, ports, coords);
  const last = points.length - 2;
  const segments: Segment[] = [];
  for (let i = 0; i <= last; i++) {
    const from = points[i] as Pt;
    const to = points[i + 1] as Pt;
    const terminal = i === 0 ? "start" : i === last ? "end" : null;
    const len = length(from, to);
    if (len < 0.5 || (terminal && len < MIN_GRAB)) continue;
    segments.push({ index: i, orientation: (i % 2 === 0) === isH(ports.sf) ? "h" : "v", from, to, mid: { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 }, terminal });
  }
  return { points, segments, coords, ports };
}

/** Le pas de la grille du schéma (l'écart des points du fond) : celui sur lequel l'aimant cale les étapes et les segments. */
export const GRID = 16;

/** La valeur d'un segment (abscisse ou ordonnée) quand on le glisse jusqu'au point `p` du schéma, arrondie à 4 pixels (ou au pas de la grille quand
 *  l'aimant est actif). */
export function dragValue(segment: Segment, p: Pt, grid = SNAP): number {
  return Math.round((segment.orientation === "v" ? p.x : p.y) / grid) * grid;
}

/** Les coordonnées après avoir mis le segment à `value`. Un segment attaché à une étape garde sa position côté icône : on ajoute deux coudes (un
 *  départ ou une arrivée de `STUB`, puis le segment déplacé), comme le font les éditeurs de diagrammes. */
export function applyDrag(s: Pt, t: Pt, ports: Ports, saved: Coords | undefined, segment: Segment, value: number, boxes: Rect[] = []): Coords {
  const base = orthogonalRoute(s, t, ports, saved, boxes).coords;
  let coords = base;
  let k = segment.index - 1;
  if (segment.terminal === "start") {
    const stub = isH(ports.sf) ? at(ports.sf, s, STUB).x : at(ports.sf, s, STUB).y;
    coords = [stub, s[isH(ports.sf) ? "y" : "x"], ...base];
    k = 1;
  } else if (segment.terminal === "end") {
    const stub = isH(ports.tf) ? at(ports.tf, t, STUB).x : at(ports.tf, t, STUB).y;
    coords = [...base, t[isH(ports.tf) ? "y" : "x"], stub];
    k = base.length;
  }
  if (coords.length > MAX_COORDS) return base;
  const next = [...coords];
  next[k] = value;
  return clampCoords(s, t, ports, next, boxes);
}

/** Le tracé tel qu'on l'enregistre : les segments de longueur nulle disparaissent, les segments alignés fusionnent ; rien (automatique) si le
 *  résultat est celui du tracé automatique. */
export function simplifyRoute(s: Pt, t: Pt, ports: Ports, coords: Coords, boxes: Rect[] = []): Coords | undefined {
  const pts = tidy(routePoints(s, t, ports, clampCoords(s, t, ports, coords, boxes)));
  if (!pts) return undefined;
  const inner = coordsOf(pts);
  if (!shapeOk(inner, ports)) return undefined;
  const auto = autoCoords(s, t, ports, boxes);
  return inner.length === auto.length && inner.every((v, i) => v === auto[i]) ? undefined : inner;
}

/** Les coordonnées telles qu'on les enregistre : des entiers valides, rien si le tracé est invalide. */
export function cleanRoute(coords: unknown): Coords | undefined {
  if (!Array.isArray(coords) || coords.length > MAX_COORDS || !coords.every((v) => typeof v === "number" && Number.isFinite(v))) return undefined;
  return coords.map((v) => Math.round(v));
}

/** Où poser le libellé : au milieu du segment le plus long. */
export function labelPoint(chain: Pt[]): Pt {
  const mids = chain.slice(1).map((p, i) => { const a = chain[i] as Pt; return { x: (a.x + p.x) / 2, y: (a.y + p.y) / 2 }; });
  let best = 0;
  let bestLen = -1;
  for (let i = 1; i < chain.length; i++) {
    const len = dist(chain[i - 1] as Pt, chain[i] as Pt);
    if (len > bestLen) { bestLen = len; best = i - 1; }
  }
  return mids[best] ?? chain[0] ?? { x: 0, y: 0 };
}
