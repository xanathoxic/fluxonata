import { BRANDS } from "./brandIconData";

/** Perceived luminance of a hex colour (0 dark … 1 light), used to keep a logo readable on a white tile. */
const luminance = (hex: string) => {
  const n = parseInt(hex, 16);
  return (0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
};
const HUES = [186, 221, 262, 316, 28, 152, 340, 45];

/** True when we ship the logo of this integration or connection type. */
export const hasBrand = (slug?: string | null) => Boolean(slug && BRANDS[slug]);

/** The logo of the target application (decorative: the name is always written next to it). A monogram stands in when
 *  we have no logo for the service. */
export function BrandIcon({ slug, name, size = 24 }: { slug?: string | null; name: string; size?: number }) {
  const b = slug ? BRANDS[slug] : undefined;
  const box = { width: size, height: size, borderRadius: Math.round(size * 0.28) };
  if (b) {
    const dark = luminance(b.hex) > 0.8; // very light brand colours would vanish on white
    return (
      <span className="brand-ico" aria-hidden="true" style={box}>
        <svg viewBox="0 0 24 24" width={Math.round(size * 0.66)} height={Math.round(size * 0.66)} focusable="false"><path d={b.path} fill={dark ? "#1e293b" : `#${b.hex}`} /></svg>
      </span>
    );
  }
  const hue = HUES[[...name].reduce((n, c) => n + c.charCodeAt(0), 0) % HUES.length] as number;
  const letters = name.replace(/\(.*?\)/g, "").split(/[\s_-]+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
  return <span className="brand-ico brand-mono" aria-hidden="true" data-letters={letters} style={{ ...box, background: `hsl(${hue} 55% 30%)`, fontSize: Math.round(size * 0.4) }} />;
}
