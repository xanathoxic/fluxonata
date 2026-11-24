import { describe, expect, it } from "vitest";
import { applyDrag, autoCoords, autoPorts, autoRoute, cleanRoute, clampCoords, coordsOf, dragValue, facePoint, GRID, JUNCTION, junctionCoords, junctionFaces, trunkPoint, viaRoute, labelPoint, MAX_COORDS, orthogonalRoute, PORT_GAP, roundedPath, routePoints, simplifyRoute, STUB, type Face, type Pt, type Rect } from "./edgeRoute";

const RL = { sf: "r", tf: "l" } as const;
const S = { x: 100, y: 100 };
const T = { x: 300, y: 40 };
const box = (x: number, y: number): Rect => ({ x, y, w: 45, h: 45 });
const orthogonal = (pts: Pt[]) => pts.slice(1).every((p, i) => p.x === (pts[i] as Pt).x || p.y === (pts[i] as Pt).y);
const bends = (pts: Pt[]) => pts.length - 2;
const segment = (route: ReturnType<typeof orthogonalRoute>, index: number) => {
  const g = route.segments.find((x) => x.index === index);
  if (!g) throw new Error(`segment ${index} absent`);
  return g;
};
const route = (a: Rect, b: Rect) => {
  const ports = autoPorts(a, b);
  return orthogonalRoute(facePoint(a, ports.sf, "out"), facePoint(b, ports.tf, "in"), ports, undefined, [a, b]);
};

describe("tracé automatique entre deux faces", () => {
  it("droite → gauche : un segment vertical au milieu, toujours orthogonal", () => {
    const r = orthogonalRoute(S, T, RL);
    expect(r.coords).toEqual([200]);
    expect(r.points).toEqual([S, { x: 200, y: 100 }, { x: 200, y: 40 }, T]);
    expect(orthogonal(r.points)).toBe(true);
  });

  it("tous les segments se déplacent : les horizontaux (haut/bas) et le vertical (gauche/droite)", () => {
    const r = orthogonalRoute(S, T, RL);
    expect(r.segments.map((g) => [g.index, g.orientation, g.terminal])).toEqual([[0, "h", "start"], [1, "v", null], [2, "h", "end"]]);
  });

  it("une ligne droite entre deux faces alignées reste droite, avec un segment de milieu à longueur nulle qu'on pourra tirer", () => {
    const r = orthogonalRoute({ x: 0, y: 50 }, { x: 200, y: 50 }, RL);
    expect(r.coords).toHaveLength(1);
    expect(new Set(r.points.map((p) => p.y))).toEqual(new Set([50]));
  });

  it("bas → haut : même tracé, tourné d'un quart de tour (V-H-V)", () => {
    const r = orthogonalRoute({ x: 100, y: 100 }, { x: 40, y: 300 }, { sf: "b", tf: "t" });
    expect(r.coords).toEqual([200]);
    expect(r.points).toEqual([{ x: 100, y: 100 }, { x: 100, y: 200 }, { x: 40, y: 200 }, { x: 40, y: 300 }]);
    expect(r.segments.map((g) => g.orientation)).toEqual(["v", "h", "v"]);
  });

  it("droite → haut : un seul coude, deux segments", () => {
    const r = orthogonalRoute({ x: 100, y: 100 }, { x: 300, y: 200 }, { sf: "r", tf: "t" });
    expect(r.points).toEqual([{ x: 100, y: 100 }, { x: 300, y: 100 }, { x: 300, y: 200 }]);
    expect(r.coords).toEqual([]);
  });

  it("vers l'arrière, la ligne fait un détour : elle sort d'abord de la face puis revient, sans jamais faire demi-tour", () => {
    const r = orthogonalRoute({ x: 300, y: 100 }, { x: 100, y: 20 }, RL);
    expect(orthogonal(r.points)).toBe(true);
    expect(r.points[1]).toEqual({ x: 300 + STUB, y: 100 });
    expect(r.coords).toHaveLength(3);
  });

  it("deux faces du même côté font un C autour des étapes", () => {
    const r = orthogonalRoute({ x: 100, y: 100 }, { x: 180, y: 220 }, { sf: "r", tf: "r" });
    expect(orthogonal(r.points)).toBe(true);
    expect(Math.max(...r.points.map((p) => p.x))).toBeGreaterThanOrEqual(180 + STUB);
  });

  it("un segment attaché à une étape trop court pour être pris n'est pas proposé", () => {
    const r = orthogonalRoute({ x: 300, y: 100 }, { x: 100, y: 20 }, RL);
    expect(r.segments.every((g) => !g.terminal)).toBe(true);
  });

  it("aucun tracé ne tient (faces contradictoires, icônes qui se chevauchent) : la ligne est droite, en dernier recours", () => {
    const a = box(0, 0);
    const b = box(10, 5);
    const r = orthogonalRoute(facePoint(a, "r", "out"), facePoint(b, "l", "in"), RL, undefined, [a, b]);
    expect(r.points.length).toBeGreaterThanOrEqual(2);
  });
});

describe("points de connexion d'une face : entrée et sortie écartées", () => {
  const r = box(100, 100); // le centre de l'icône est à (122,5 ; 122,5)
  it("l'entrée et la sortie d'une même face sont de part et d'autre du centre, à la distance voulue", () => {
    for (const f of ["t", "r", "b", "l"] as Face[]) {
      const i = facePoint(r, f, "in");
      const o = facePoint(r, f, "out");
      expect(Math.hypot(i.x - o.x, i.y - o.y)).toBeCloseTo(2 * PORT_GAP);
    }
  });

  it("répartition : face gauche, entrée en haut et sortie en bas ; face droite, l'inverse ; faces haut et bas, entrée à gauche et sortie à droite", () => {
    expect(facePoint(r, "l", "in").y).toBeLessThan(122.5);
    expect(facePoint(r, "l", "out").y).toBeGreaterThan(122.5);
    expect(facePoint(r, "r", "in").y).toBeGreaterThan(122.5); // face droite : entrée en bas…
    expect(facePoint(r, "r", "out").y).toBeLessThan(122.5); // …et sortie en haut
    for (const f of ["t", "b"] as Face[]) {
      expect(facePoint(r, f, "in").x).toBeLessThan(122.5);
      expect(facePoint(r, f, "out").x).toBeGreaterThan(122.5);
    }
  });

  it("les points sont sur la face : bords de l'icône", () => {
    expect(facePoint(r, "l", "in").x).toBe(100);
    expect(facePoint(r, "r", "out").x).toBe(145);
    expect(facePoint(r, "t", "out").y).toBe(100);
    expect(facePoint(r, "b", "in").y).toBe(145);
  });

  it("une chaîne droite reste droite : la sortie à droite et l'entrée à gauche de l'étape suivante sont à la même hauteur", () => {
    const a = box(0, 0);
    const b = box(300, 0);
    expect(facePoint(a, "r", "out").y).toBe(facePoint(b, "l", "in").y);
    const line = orthogonalRoute(facePoint(a, "r", "out"), facePoint(b, "l", "in"), RL, undefined, [a, b]);
    expect(orthogonal(line.points)).toBe(true);
    expect(new Set(line.points.map((p) => p.y)).size).toBe(1);
  });

  it("vers une étape placée en dessous : la sortie (à droite) et l'entrée (à gauche) sont écartées de deux fois l'écart", () => {
    expect(facePoint(box(0, 0), "b", "out").x - facePoint(box(0, 300), "t", "in").x).toBeCloseTo(2 * PORT_GAP);
  });

  it("le décalage ne change pas le choix des faces", () => {
    expect(autoPorts(box(0, 0), box(300, 0), 0)).toEqual(autoPorts(box(0, 0), box(300, 0)));
  });
});

describe("points de connexion presque alignés", () => {
  const tall = (pts: Pt[]) => Math.max(...pts.map((p) => p.y)) - Math.min(...pts.map((p) => p.y));
  it("à une fraction de pixel près, la ligne reste droite : jamais d'arche au-dessus ou au-dessous des étapes", () => {
    const s = { x: 100, y: 135.9 };
    for (const dy of [0, 0.1, 0.19, 0.3, 0.49, 0.6, 1, 1.4]) {
      const t = { x: 600, y: s.y + dy };
      const r = orthogonalRoute(s, t, RL);
      expect(tall(r.points), `dy=${dy}`).toBeLessThanOrEqual(dy + 0.01);
      expect(autoRoute(s, t, RL)).not.toBeNull();
      expect(tall(autoRoute(s, t, RL) as Pt[]), `autoRoute dy=${dy}`).toBeLessThanOrEqual(dy + 0.01);
    }
  });

  it("de même pour deux faces haut/bas décalées d'une fraction de pixel", () => {
    const s = { x: 135.9, y: 100 };
    for (const dx of [0.1, 0.19, 0.4, 1]) {
      const r = orthogonalRoute(s, { x: s.x + dx, y: 500 }, { sf: "b", tf: "t" });
      expect(Math.max(...r.points.map((p) => p.x)) - Math.min(...r.points.map((p) => p.x)), `dx=${dx}`).toBeLessThanOrEqual(dx + 0.01);
    }
  });

  it("la ligne finit au vrai point de connexion, pas au point aligné", () => {
    const t = { x: 600, y: 136.09 };
    const pts = autoRoute({ x: 100, y: 135.9 }, t, RL) as Pt[];
    expect(pts[pts.length - 1]).toEqual(t);
  });

  it("un vrai décalage reste un décrochement, et deux étapes en sens inverse gardent leur détour", () => {
    const r = orthogonalRoute({ x: 100, y: 100 }, { x: 600, y: 140 }, RL);
    expect(tall(r.points)).toBe(40); // un Z, pas une arche
    const back = orthogonalRoute({ x: 600, y: 100.2 }, { x: 100, y: 100 }, RL);
    expect(tall(back.points)).toBeGreaterThanOrEqual(24); // vers l'arrière : le détour (au moins un dégagement de 24) est conservé
  });
});

describe("choix automatique des faces (suit les étapes quand on les déplace)", () => {
  const cases: [string, Rect, Rect, Face, Face][] = [
    ["cible à droite", box(0, 0), box(300, 0), "r", "l"],
    ["cible à droite et plus bas", box(0, 0), box(300, 200), "r", "l"],
    ["cible à droite et plus haut", box(0, 200), box(300, 0), "r", "l"],
    ["cible juste en dessous", box(100, 0), box(100, 200), "b", "t"],
    ["cible juste au-dessus", box(100, 200), box(100, 0), "t", "b"],
    ["cible à gauche", box(300, 0), box(0, 0), "l", "r"],
  ];
  it.each(cases)("%s", (_n, a, b, sf, tf) => {
    expect(autoPorts(a, b)).toEqual({ sf, tf });
  });

  it("cible en bas à gauche : la ligne ne contourne pas l'étape, elle sort à gauche et entre par le haut (un seul coude)", () => {
    const r = route(box(208, 50), box(128, 300));
    expect(r.ports).toEqual({ sf: "l", tf: "t" });
    expect(bends(r.points)).toBe(1);
    expect(orthogonal(r.points)).toBe(true);
  });

  it("le choix change quand on déplace la cible", () => {
    const a = box(0, 0);
    expect(autoPorts(a, box(300, 0))).toEqual({ sf: "r", tf: "l" });
    expect(autoPorts(a, box(0, 300))).toEqual({ sf: "b", tf: "t" });
  });

  it("la ligne automatique ne traverse jamais l'icône de départ ni celle d'arrivée", () => {
    for (const [a, b] of [[box(0, 0), box(300, 200)], [box(300, 0), box(0, 200)], [box(100, 0), box(100, 200)], [box(208, 50), box(128, 300)]] as [Rect, Rect][]) {
      const r = route(a, b);
      for (const [from, to] of r.points.slice(1).map((p, i) => [r.points[i] as Pt, p] as [Pt, Pt])) {
        for (const rect of [a, b]) {
          const inside = Math.max(from.x, to.x) > rect.x + 3 && Math.min(from.x, to.x) < rect.x + rect.w - 3 && Math.max(from.y, to.y) > rect.y + 3 && Math.min(from.y, to.y) < rect.y + rect.h - 3;
          expect(inside).toBe(false);
        }
      }
    }
  });

  it("autoRoute renvoie null quand rien ne tient, jamais une ligne en diagonale", () => {
    const a = box(0, 0);
    const route2 = autoRoute(facePoint(a, "r", "out"), facePoint(box(300, 200), "l", "in"), RL, []);
    expect(route2 && orthogonal(route2)).toBe(true);
  });
});

describe("réglage d'un tracé", () => {
  it("le premier segment vertical reste à droite de la source, le dernier à gauche de la cible", () => {
    const at = (x: number) => orthogonalRoute({ x: 100, y: 0 }, { x: 300, y: 80 }, RL, [x]).points[1]?.x;
    expect(at(150)).toBe(150);
    expect(at(0)).toBe(100 + STUB);
    expect(at(999)).toBe(300 - STUB);
  });

  it("déplace le segment vertical", () => {
    const r = orthogonalRoute(S, T, RL);
    expect(applyDrag(S, T, RL, undefined, segment(r, 1), 240)).toEqual([240]);
  });

  it("tirer le segment horizontal d'arrivée ajoute deux coudes : le vertical est gardé, l'arrivée se fait par un court segment", () => {
    const r = orthogonalRoute(S, T, RL);
    const coords = applyDrag(S, T, RL, undefined, segment(r, 2), -20);
    expect(coords).toEqual([200, -20, T.x - STUB]);
    expect(routePoints(S, T, RL, coords)).toEqual([S, { x: 200, y: 100 }, { x: 200, y: -20 }, { x: 276, y: -20 }, { x: 276, y: 40 }, T]);
  });

  it("tirer le segment horizontal de départ ajoute deux coudes du côté de la source", () => {
    const r = orthogonalRoute(S, T, RL);
    const coords = applyDrag(S, T, RL, undefined, segment(r, 0), 160);
    expect(coords).toEqual([S.x + STUB, 160, 200]);
    expect(orthogonal(routePoints(S, T, RL, coords))).toBe(true);
  });

  it("fonctionne de la même façon pour des faces haut/bas : tirer le segment vertical d'arrivée ajoute des coudes", () => {
    const s = { x: 100, y: 100 };
    const t = { x: 40, y: 300 };
    const ports = { sf: "b", tf: "t" } as const;
    const r = orthogonalRoute(s, t, ports);
    const first = applyDrag(s, t, ports, undefined, segment(r, 0), 160); // le départ (vertical) se déplace vers la droite
    expect(first).toEqual([s.y + STUB, 160, 200]);
    expect(orthogonal(routePoints(s, t, ports, first))).toBe(true);
    const mid = applyDrag(s, t, ports, undefined, segment(r, 1), 240); // le segment horizontal du milieu descend
    expect(mid).toEqual([240]);
  });

  it("à l'instant où l'on prend un segment attaché, la ligne ne bouge pas", () => {
    const r = orthogonalRoute(S, T, RL);
    for (const index of [0, 2]) {
      const grabbed = applyDrag(S, T, RL, undefined, segment(r, index), index === 0 ? S.y : T.y);
      expect(simplifyRoute(S, T, RL, grabbed)).toBeUndefined();
    }
  });

  it("déplace les trois segments du milieu d'un détour", () => {
    const A = { x: 300, y: 100 };
    const B = { x: 100, y: 20 };
    const back = orthogonalRoute(A, B, RL);
    expect(applyDrag(A, B, RL, undefined, segment(back, 2), 220)[1]).toBe(220);
    expect(applyDrag(A, B, RL, undefined, segment(back, 1), 400)[0]).toBe(400);
    expect(applyDrag(A, B, RL, undefined, segment(back, 3), 0).at(-1)).toBe(0); // plus à gauche : permis
    expect(applyDrag(A, B, RL, undefined, segment(back, 3), 500).at(-1)).toBe(B.x - STUB); // dans l'étape cible : ramené à l'arrivée
  });

  it("ne dépasse pas le nombre maximal de coudes", () => {
    const full = Array.from({ length: MAX_COORDS }, (_, i) => (i % 2 === 0 ? 150 + i * 4 : 60 + i));
    const t = { x: 900, y: 500 };
    const r = orthogonalRoute(S, t, RL, full);
    const first = r.segments.find((g) => g.terminal === "start");
    if (first) expect(applyDrag(S, t, RL, full, first, 0)).toEqual(clampCoords(S, t, RL, full));
  });

  it("un tracé enregistré qui ne tient plus redevient automatique", () => {
    expect(clampCoords(S, { x: 110, y: 0 }, RL, [105])).toEqual(autoCoords(S, { x: 110, y: 0 }, RL));
    expect(clampCoords(S, T, RL, [1, 2])).toEqual(autoCoords(S, T, RL)); // forme invalide : nombre pair pour deux faces parallèles
    expect(orthogonalRoute(S, T, RL, [Number.NaN]).coords).toEqual(autoCoords(S, T, RL));
    expect(clampCoords(S, T, { sf: "r", tf: "t" }, [5])).toEqual(autoCoords(S, T, { sf: "r", tf: "t" })); // un coude n'a pas de coordonnée intérieure
  });

  it("simplifie : segments de longueur nulle supprimés, segments alignés fusionnés, rien si c'est le tracé automatique", () => {
    expect(simplifyRoute(S, T, RL, [240])).toEqual([240]);
    expect(simplifyRoute(S, T, RL, [200, -20, 276])).toEqual([200, -20, 276]);
    expect(simplifyRoute(S, T, RL, [200, 100, 276])).toEqual([276]);
    expect(simplifyRoute(S, T, RL, [200])).toBeUndefined();
  });

  it("la valeur d'un glissement suit l'axe du segment, arrondie à 4 pixels", () => {
    const r = orthogonalRoute(S, T, RL);
    expect(dragValue(segment(r, 1), { x: 131, y: 999 })).toBe(132);
    expect(dragValue(segment(r, 2), { x: 999, y: 61 })).toBe(60);
  });

  it("avec l'aimant, la valeur d'un glissement est calée sur la grille de 16 pixels", () => {
    const r = orthogonalRoute(S, T, RL);
    expect(GRID).toBe(16);
    expect(dragValue(segment(r, 1), { x: 131, y: 0 }, GRID)).toBe(128);
    expect(dragValue(segment(r, 1), { x: 137, y: 0 }, GRID)).toBe(144);
    expect(dragValue(segment(r, 2), { x: 0, y: 61 }, GRID)).toBe(64);
    expect(dragValue(segment(r, 1), { x: 131, y: 0 })).toBe(132); // sans aimant : 4 pixels
  });

  it("les coordonnées d'un tracé se relisent depuis ses points", () => {
    expect(coordsOf([S, { x: 200, y: 100 }, { x: 200, y: 40 }, T])).toEqual([200]);
    expect(coordsOf([S, T])).toEqual([]);
  });

  it("n'enregistre que des entiers valides", () => {
    expect(cleanRoute([10.4, 7.6, 3])).toEqual([10, 8, 3]);
    expect(cleanRoute([])).toEqual([]);
    expect(cleanRoute([Number.NaN])).toBeUndefined();
    expect(cleanRoute("x")).toBeUndefined();
    expect(cleanRoute(Array.from({ length: MAX_COORDS + 1 }, () => 1))).toBeUndefined();
  });
});

describe("tronc commun des liens qui partagent un point de connexion", () => {
  const src = box(0, 100);
  const out = (b: Rect) => ({ s: facePoint(src, "r", "out"), t: facePoint(b, "l", "in"), boxes: [src, b] });
  it("des liens qui partent du même point se séparent au même endroit, à JUNCTION de la face, quelle que soit la cible", () => {
    const xs = [box(300, 0), box(300, 100), box(340, 220)].map((b) => {
      const { s, t, boxes } = out(b);
      return junctionCoords(s, t, RL, boxes, true, false)?.[0];
    });
    expect(new Set(xs).size).toBe(1);
    expect(xs[0]).toBe(Math.round(facePoint(src, "r", "out").x + JUNCTION));
  });

  it("des liens qui arrivent au même point se rejoignent à JUNCTION avant la face d'arrivée", () => {
    const dst = box(400, 100);
    const t = facePoint(dst, "l", "in");
    const xs = [box(0, 0), box(0, 100), box(40, 220)].map((b) => junctionCoords(facePoint(b, "r", "out"), t, RL, [b, dst], false, true)?.[0]);
    expect(new Set(xs).size).toBe(1);
    expect(xs[0]).toBe(Math.round(t.x - JUNCTION));
  });

  it("un lien seul, ou dont les deux extrémités sont partagées, garde son tracé automatique", () => {
    const { s, t, boxes } = out(box(300, 0));
    expect(junctionCoords(s, t, RL, boxes, false, false)).toBeUndefined();
    expect(junctionCoords(s, t, RL, boxes, true, true)).toBeUndefined();
  });

  it("ne s'applique pas à un coude, à un détour, ni à des faces d'orientations différentes", () => {
    const a = box(300, 100);
    expect(junctionCoords(facePoint(a, "r", "out"), facePoint(box(0, 100), "l", "in"), RL, [a, box(0, 100)], true, false)).toBeUndefined(); // vers l'arrière : détour
    const L = { sf: "r", tf: "t" } as const;
    expect(junctionCoords(facePoint(src, "r", "out"), facePoint(box(300, 250), "t", "in"), L, [src, box(300, 250)], true, false)).toBeUndefined(); // un coude
  });

  it("fonctionne aussi pour les faces bas → haut (tronc horizontal)", () => {
    const a = box(100, 0);
    const bs = [box(0, 300), box(100, 300), box(200, 320)];
    const ys = bs.map((b) => junctionCoords(facePoint(a, "b", "out"), facePoint(b, "t", "in"), { sf: "b", tf: "t" }, [a, b], true, false)?.[0]);
    expect(new Set(ys).size).toBe(1);
    expect(ys[0]).toBe(Math.round(facePoint(a, "b", "out").y + JUNCTION));
  });

  it("le tracé avec ce tronc reste orthogonal et valide", () => {
    const { s, t, boxes } = out(box(300, 0));
    const hint = junctionCoords(s, t, RL, boxes, true, false);
    const r = orthogonalRoute(s, t, RL, hint, boxes);
    expect(orthogonal(r.points)).toBe(true);
    expect(r.coords).toEqual(hint);
  });
});

describe("lien qui passe par un point de jonction", () => {
  const R = 8;
  const src = box(0, 100);
  const s = facePoint(src, "r", "out");
  const target = (x: number, y: number) => ({ t: facePoint(box(x, y), "l", "in"), boxes: [src, box(x, y)] });

  it("va de la source à l'entrée verte (à gauche) du point, puis de l'une de ses sorties rouges à la cible, toujours en segments droits", () => {
    const j = { x: 120, y: 122 };
    const { t, boxes } = target(300, 0);
    const pts = viaRoute(s, "r", t, "l", [j], R, boxes);
    expect(pts[0]).toEqual(s);
    expect(pts[pts.length - 1]).toEqual(t);
    expect(pts.some((p) => p.x === j.x - R && p.y === j.y)).toBe(true); // arrive à l'entrée, à gauche du rond
    expect(pts.some((p) => junctionFaces().outs.some((f) => (f === "r" ? p.x === j.x + R && p.y === j.y : f === "t" ? p.x === j.x && p.y === j.y - R : p.x === j.x && p.y === j.y + R)))).toBe(true); // repart par une sortie
  });

  it("des liens qui partent du même point de connexion partagent la même branche jusqu'au point de jonction, puis se séparent", () => {
    const j = { x: 120, y: 122 };
    const legs = [target(300, 0), target(300, 100), target(340, 220)].map(({ t, boxes }) => viaRoute(s, "r", t, "l", [j], R, boxes));
    const entry = (pts: Pt[]) => pts.slice(0, pts.findIndex((p) => p.x === j.x - R && p.y === j.y) + 1);
    expect(new Set(legs.map((l) => JSON.stringify(entry(l)))).size).toBe(1); // la même branche commune, point pour point
    expect(new Set(legs.map((l) => JSON.stringify(l.at(-2)))).size).toBeGreaterThan(1); // puis des arrivées différentes
  });

  it("la sortie choisie suit la cible : une cible au-dessus part par le haut, une cible en dessous ne part jamais par le haut", () => {
    const j = { x: 120, y: 122 };
    const exitOf = (ty: number): Pt => {
      const dst = box(120, ty);
      const pts = viaRoute(s, "r", facePoint(dst, "t", "in"), "t", [j], R, [src, dst]);
      return pts.find((p) => (p.x === j.x && Math.abs(p.y - j.y) === R) || (p.x === j.x + R && p.y === j.y)) as Pt;
    };
    expect(exitOf(-200).y).toBe(j.y - R); // au-dessus : sortie du haut
    expect(exitOf(400).y).toBeGreaterThanOrEqual(j.y); // en dessous : sortie de droite ou du bas
    expect(exitOf(400)).not.toEqual({ x: j.x, y: j.y - R });
  });

  it("deux points de jonction en chaîne : le premier mène à l'entrée du second", () => {
    const a = { x: 120, y: 122 };
    const b = { x: 260, y: 60 };
    const { t, boxes } = target(400, 0);
    const pts = viaRoute(s, "r", t, "l", [a, b], R, boxes);
    expect(pts.some((p) => p.x === b.x - R && p.y === b.y)).toBe(true);
    expect(pts.findIndex((p) => p.x === a.x - R && p.y === a.y)).toBeLessThan(pts.findIndex((p) => p.x === b.x - R && p.y === b.y));
  });

  it("inversé, un point de jonction a trois entrées vertes (gauche, haut, bas) et une sortie rouge à droite", () => {
    expect(junctionFaces(false)).toEqual({ ins: ["l"], outs: ["r", "t", "b"] });
    expect(junctionFaces(undefined)).toEqual({ ins: ["l"], outs: ["r", "t", "b"] });
    expect(junctionFaces(true)).toEqual({ ins: ["l", "t", "b"], outs: ["r"] });
  });

  it("un point inversé rassemble : des liens venus de trois côtés y entrent par la face la plus proche et repartent tous par la sortie de droite", () => {
    const j = { x: 300, y: 200, flip: true };
    const dst = box(500, 190);
    const t = facePoint(dst, "l", "in");
    const entryOf = (pts: Pt[]) => pts.find((p) => (p.x === j.x - R && p.y === j.y) || (p.x === j.x && Math.abs(p.y - j.y) === R)) as Pt;
    const left = box(0, 190);
    const above = box(270, -150);
    const below = box(270, 450);
    const e1 = entryOf(viaRoute(facePoint(left, "r", "out"), "r", t, "l", [j], R, [left, dst]));
    const e2 = entryOf(viaRoute(facePoint(above, "b", "out"), "b", t, "l", [j], R, [above, dst]));
    const e3 = entryOf(viaRoute(facePoint(below, "t", "out"), "t", t, "l", [j], R, [below, dst]));
    expect(e1).toEqual({ x: j.x - R, y: j.y }); // de la gauche : par l'entrée de gauche
    expect(e2).toEqual({ x: j.x, y: j.y - R }); // d'en haut : par l'entrée du haut
    expect(e3).toEqual({ x: j.x, y: j.y + R }); // d'en bas : par l'entrée du bas
    for (const src of [[left, "r"], [above, "b"], [below, "t"]] as [Rect, Face][]) {
      const pts = viaRoute(facePoint(src[0], src[1], "out"), src[1], t, "l", [j], R, [src[0], dst]);
      expect(pts.some((p) => p.x === j.x + R && p.y === j.y)).toBe(true); // repart toujours par la sortie de droite
    }
  });

  it("un point de jonction proche de l'étape ne provoque pas de détour en créneau : à la même hauteur le tronçon reste droit", () => {
    const dst = box(500, 100);
    const t = facePoint(dst, "l", "in");
    for (const dx of [30, 40, 50, 56, 58, 64, 80, 120]) {
      const j = { x: src.x + 45 + dx, y: s.y }; // à la hauteur du point de connexion de l'étape
      const pts = viaRoute(s, "r", t, "l", [j], R, [src, dst]);
      const entry = pts.findIndex((p) => p.x === j.x - R && p.y === j.y);
      expect(entry, `dx=${dx}`).toBeGreaterThan(0);
      const toRing = pts.slice(0, entry + 1);
      expect(new Set(toRing.map((p) => Math.round(p.y))).size, `dx=${dx} : ${JSON.stringify(toRing)}`).toBe(1); // aucun écart de hauteur : une ligne droite
    }
  });

  it("à quelques pixels de hauteur près, le tronçon fait au plus un petit décrochement (un seul changement de hauteur), jamais un créneau", () => {
    const dst = box(500, 100);
    const t = facePoint(dst, "l", "in");
    for (const dy of [-12, -6, -3, 3, 6, 12]) {
      for (const dx of [40, 58, 90]) {
        const j = { x: src.x + 45 + dx, y: s.y + dy };
        const pts = viaRoute(s, "r", t, "l", [j], R, [src, dst]);
        const toRing = pts.slice(0, pts.findIndex((p) => p.x === j.x - R && p.y === j.y) + 1);
        const heights = toRing.map((p) => Math.round(p.y));
        const changes = heights.slice(1).filter((y, i) => y !== heights[i]).length;
        expect(changes, `dy=${dy} dx=${dx} : ${JSON.stringify(toRing)}`).toBeLessThanOrEqual(1);
        expect(Math.max(...heights) - Math.min(...heights)).toBeLessThanOrEqual(Math.abs(dy) + 1); // ne sort pas de l'écart entre l'étape et le point
      }
    }
  });

  it("le point par défaut d'une jonction automatique est sur le prolongement de la face, à JUNCTION", () => {
    expect(trunkPoint({ x: 50, y: 70 }, "r")).toEqual({ x: 50 + JUNCTION, y: 70 });
    expect(trunkPoint({ x: 50, y: 70 }, "l")).toEqual({ x: 50 - JUNCTION, y: 70 });
    expect(trunkPoint({ x: 50, y: 70 }, "b")).toEqual({ x: 50, y: 70 + JUNCTION });
    expect(trunkPoint({ x: 50, y: 70 }, "t")).toEqual({ x: 50, y: 70 - JUNCTION });
  });
});

describe("dessin de la ligne", () => {
  it("arrondit les angles sans sortir de la ligne", () => {
    expect(roundedPath([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }])).toMatch(/^M 0 0 L 90 0 Q 100 0 100 10 L 100 100$/);
  });

  it("un point marqué « franc » garde un angle droit, les autres restent arrondis", () => {
    const chain = [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }];
    expect(roundedPath(chain, 10, [{ x: 100, y: 0 }])).toBe("M 0 0 L 100 0 Q 100 0 100 0 L 100 100");
    expect(roundedPath(chain, 10, [{ x: 5, y: 5 }])).toBe(roundedPath(chain));
  });

  it("réduit le rayon sur un segment court et ignore les points confondus", () => {
    expect(roundedPath([{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 4, y: 0 }, { x: 4, y: 100 }])).toContain("Q 4 0 4 2");
    expect(roundedPath([])).toBe("");
  });

  it("pose le libellé au milieu du segment le plus long", () => {
    expect(labelPoint([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 100 }, { x: 30, y: 100 }])).toEqual({ x: 10, y: 50 });
  });
});
