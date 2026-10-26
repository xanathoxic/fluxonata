import type { NodeType } from "./types";

const PATHS: Record<string, React.ReactElement> = {
  script: <><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4M9.5 12h6M9.5 15h6M9.5 18h4" /></>,
  condition: <path d="M12 3l9 9-9 9-9-9z" />,
  wait: <path d="M7 3h10v4l-5 5 5 5v4H7v-4l5-5-5-5z" />,
  approval: <><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4.5 3.5-7 8-7s8 2.5 8 7" /></>,
  action: <><circle cx="12" cy="12" r="3.5" /><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1" /></>,
  subworkflow: <><rect x="3" y="3" width="18" height="18" rx="2" /><rect x="6.5" y="6.5" width="4.5" height="4.5" /><rect x="13" y="13" width="4.5" height="4.5" /><path d="M11 8.75h2.25V13" /></>,
  foreach: <path d="M4 12a8 8 0 0 1 14-5M18 3v4h-4M20 12a8 8 0 0 1-14 5M6 21v-4h4" />,
  join: <path d="M5 4v6a4 4 0 0 0 4 4h6a4 4 0 0 0 4-4V4M12 14v7" />,
  ai: <path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z" />,
  throw: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.2v.8" /></>,
  note: <><path d="M5 4h14v12l-4 4H5z" /><path d="M15 20v-4h4M8 9h8M8 13h5" /></>,
  end: <><path d="M12 22s7-6.5 7-12a7 7 0 0 0-14 0c0 5.5 7 12 7 12z" /><circle cx="12" cy="10" r="2.6" /></>,
};

export type IconKind = NodeType | "note" | "end" | "start";

/** Decorative line icon (the accessible name always comes from the visible label next to it). */
export function NodeIcon({ kind, size = 28 }: { kind: IconKind; size?: number }) {
  if (kind === "start") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <circle cx="12" cy="12" r="10" className="icon-start" />
        <path d="M6.5 12h9M12 7.5l4.5 4.5-4.5 4.5" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" className={`icon icon-${kind}`}>
      {PATHS[kind]}
    </svg>
  );
}
