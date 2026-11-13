import type { CSSProperties, ReactNode } from "react";

/** Zone de lecture défilante : texte, code, JSON ou tableau large affichés en lecture seule, dans un cadre qui peut défiler.
 *
 *  Comportement commun, voulu : (1) le cadre est atteignable au clavier (`tabIndex=0`), sinon une personne qui n'utilise pas la
 *  souris ne peut pas faire défiler son contenu (règle axe `scrollable-region-focusable`, WCAG 2.1.1) ; (2) il porte un NOM
 *  obligatoire et un rôle de région, pour que le lecteur d'écran sache de quoi il s'agit (un `aria-label` n'est pas pris en compte
 *  sur un `<pre>` ou un `<div>` sans rôle). `role="log"` convient à un journal qui s'allonge. `as="section"` pour un conteneur
 *  qui est déjà une section (rôle de région implicite). Le nom est un paramètre requis : pas de zone focalisable sans nom. */
export function ReadRegion({
  label,
  as = "pre",
  role = "region",
  id,
  className,
  style,
  children,
}: {
  label: string;
  as?: "pre" | "div" | "section";
  role?: "region" | "log";
  id?: string;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  const Tag = as;
  return (
    // Le linter n'analyse pas une balise dynamique : la conformité (nom, rôle, focus clavier) est établie par ReadRegion.test.tsx, pas par l'absence de diagnostic.
    <Tag id={id} className={className} style={style} role={as === "section" ? undefined : role} aria-label={label} tabIndex={0}>
      {children}
    </Tag>
  );
}
