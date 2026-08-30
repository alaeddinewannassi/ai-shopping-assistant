interface StatTileProps {
  label: string;
  value: string;
  tone?: "default" | "good" | "warning" | "critical";
}

const TONE_VAR: Record<NonNullable<StatTileProps["tone"]>, string> = {
  default: "var(--text-primary)",
  good: "var(--good)",
  warning: "var(--warning)",
  critical: "var(--critical)",
};

/** A bare stat tile — no plot, so no hover layer needed (dataviz skill's interaction.md:
 * "The only form that skips it is a bare stat tile with no plot"). */
export function StatTile({ label, value, tone = "default" }: StatTileProps) {
  return (
    <div
      style={{
        background: "var(--surface-1)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "16px 20px",
        minWidth: 160,
      }}
    >
      <div style={{ color: "var(--text-secondary)", fontSize: 13, marginBottom: 6 }}>{label}</div>
      <div style={{ color: TONE_VAR[tone], fontSize: 28, fontWeight: 600 }}>{value}</div>
    </div>
  );
}
