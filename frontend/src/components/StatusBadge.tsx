const ICONS: Record<string, string> = {
  queued: "⏳", running: "⟳", compensating: "↶", waiting: "⏸", retry_wait: "⏳", uncertain: "❓", succeeded: "✔", failed: "✖", skipped: "↷", cancelled: "⊘",
};

/** Status is conveyed by icon + text, never by colour alone. */
export function StatusBadge({ status, label }: { status: string; label: string }) {
  return <span className={`status status-${status}`}><span aria-hidden="true">{ICONS[status]} </span>{label}</span>;
}
