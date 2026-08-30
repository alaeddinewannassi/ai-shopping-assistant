interface FunnelStage {
  label: string;
  value: number;
}

/** Single-series ordered-category bar chart (dataviz skill): one color (categorical slot
 * 1) for every bar, bar LENGTH is the only magnitude encoding — an ordinal color ramp would
 * double-encode the same value as both length and hue (anti-patterns.md), so every bar gets
 * the same fill. Direct labels (stage, count, % of the top stage) since there are only 7
 * bars — no separate hover tooltip needed at this scale. */
export function FunnelChart({ stages }: { stages: FunnelStage[] }) {
  const max = Math.max(1, ...stages.map((s) => s.value));

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: 10 }}
      role="img"
      aria-label="Conversion funnel"
    >
      {stages.map((stage) => {
        const pct = (stage.value / max) * 100;
        const pctOfTop = max > 0 ? Math.round((stage.value / max) * 100) : 0;
        return (
          <div key={stage.label}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: 13,
                color: "var(--text-secondary)",
                marginBottom: 4,
              }}
            >
              <span>{stage.label}</span>
              <span>
                {stage.value.toLocaleString()} ({pctOfTop}%)
              </span>
            </div>
            <div
              style={{
                background: "var(--gridline)",
                borderRadius: 4,
                height: 22,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  minWidth: stage.value > 0 ? 4 : 0,
                  height: "100%",
                  background: "var(--series-1)",
                  borderRadius: 4,
                  transition: "width 200ms ease",
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
