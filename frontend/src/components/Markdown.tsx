import type { ReactNode } from "react";

/** Markdown rendered as React elements: nothing is ever written as HTML, so text written by a workflow author (or by whoever fills a
 *  value into it) cannot inject markup. Supported: headings, paragraphs, bold, italic, `code`, fenced code, lists, quotes, rules and
 *  links (http, https and mailto only). Anything else shows as plain text. Titles start at <h3>: the dialog's own title is the <h2>. */

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: { id: string; text: string }[] }
  | { kind: "quote"; text: string }
  | { kind: "code"; text: string }
  | { kind: "rule" };

const HEADING = /^(#{1,4})\s+(.*?)\s*#*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBER = /^\s*\d+[.)]\s+(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;

export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
    paragraph = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    if (/^\s*```/.test(line)) {
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i] ?? ""); i++) code.push(lines[i] ?? "");
      blocks.push({ kind: "code", text: code.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    const bullet = BULLET.exec(line);
    const number = NUMBER.exec(line);
    if (!line.trim()) flush();
    else if (RULE.test(line)) {
      flush();
      blocks.push({ kind: "rule" });
    } else if (heading) {
      flush();
      blocks.push({ kind: "heading", level: (heading[1] ?? "#").length, text: heading[2] ?? "" });
    } else if (bullet || number) {
      flush();
      const ordered = !bullet;
      const last = blocks[blocks.length - 1];
      const item = (bullet ?? number)?.[1] ?? "";
      if (last?.kind === "list" && last.ordered === ordered) last.items.push({ id: `i${blocks.length}-${last.items.length}`, text: item });
      else blocks.push({ kind: "list", ordered, items: [{ id: `i${blocks.length}-0`, text: item }] });
    } else if (/^\s*>\s?/.test(line)) {
      flush();
      const text = line.replace(/^\s*>\s?/, "");
      const last = blocks[blocks.length - 1];
      if (last?.kind === "quote") last.text += ` ${text}`;
      else blocks.push({ kind: "quote", text });
    } else paragraph.push(line.trim());
  }
  flush();
  return blocks;
}

const INLINE = /`([^`]+)`|\*\*(.+?)\*\*|__(.+?)__|\*([^*\s][^*]*)\*|_([^_\s][^_]*)_|\[([^\]]+)\]\(([^)\s]+)\)/g;
const SAFE_LINK = /^(https?:\/\/|mailto:)/i;

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const key = `${keyBase}-${n++}`;
    if (m[1] !== undefined) out.push(<code key={key}>{m[1]}</code>);
    else if (m[2] !== undefined || m[3] !== undefined) out.push(<strong key={key}>{inline(m[2] ?? m[3] ?? "", key)}</strong>);
    else if (m[4] !== undefined || m[5] !== undefined) out.push(<em key={key}>{inline(m[4] ?? m[5] ?? "", key)}</em>);
    else if (SAFE_LINK.test(m[7] ?? ""))
      out.push(
        <a key={key} href={m[7]} target="_blank" rel="noopener noreferrer">
          {inline(m[6] ?? "", key)}
        </a>,
      );
    else out.push(m[6] ?? ""); // a link to anything else (javascript:, data:…) is only its text
    last = at + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ source, className }: { source: string; className?: string }) {
  const blocks = parseBlocks(source);
  return (
    <div className={`md${className ? ` ${className}` : ""}`}>
      {blocks.map((b, i) => {
        const key = `b${i}`;
        switch (b.kind) {
          case "heading": {
            const Tag = `h${Math.min(b.level + 2, 6)}` as "h3" | "h4" | "h5" | "h6";
            return <Tag key={key}>{inline(b.text, key)}</Tag>;
          }
          case "list": {
            const Tag = b.ordered ? "ol" : "ul";
            return (
              <Tag key={key}>
                {b.items.map((it) => (
                  <li key={it.id}>{inline(it.text, it.id)}</li>
                ))}
              </Tag>
            );
          }
          case "quote":
            return <blockquote key={key}>{inline(b.text, key)}</blockquote>;
          case "code":
            return (
              <pre key={key}>
                <code>{b.text}</code>
              </pre>
            );
          case "rule":
            return <hr key={key} />;
          default:
            return <p key={key}>{inline(b.text, key)}</p>;
        }
      })}
    </div>
  );
}
