export interface DailyValue {
  date: string; // ISO calendar date, YYYY-MM-DD
  value: number;
}

function formatShortDate(iso: string): string {
  const parts = iso.split("-");
  return `${parts[1]}/${parts[2]}`;
}

/** Single-series daily bar chart (same dataviz-skill rule FunnelChart follows: one series,
 * one color — bar HEIGHT is the only magnitude encoding, never doubled up with hue). Added
 * to answer the single biggest gap found reviewing the live dashboard: Overview only ever
 * showed one flat number for the whole selected date range, with no way to tell whether
 * activity was growing, shrinking, or when within the period something changed. Direct
 * value labels above each bar (no separate legend/tooltip needed at 30 bars or fewer); the
 * bar row scrolls horizontally on its own rather than shrinking bars to illegibility on a
 * 30-day range. */
export function TimeseriesChart({ points, label }: { points: DailyValue[]; label: string }) {
  if (points.length === 0) {
    return <p style={{ color: "var(--text-muted)" }}>No data for this range.</p>;
  }

  const max = Math.max(1, ...points.map((p) => p.value));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }} role="img" aria-label={label}>
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 4,
          height: 140,
          overflowX: "auto",
          paddingBottom: 4,
        }}
      >
        {points.map((p) => {
          const pct = (p.value / max) * 100;
          return (
            <div
              key={p.date}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "flex-end",
                flex: "1 0 22px",
                minWidth: 22,
                height: "100%",
              }}
            >
              <span style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>
                {p.value > 0 ? p.value.toLocaleString() : ""}
              </span>
              <div
                style={{
                  width: "100%",
                  height: `${pct}%`,
                  minHeight: p.value > 0 ? 2 : 0,
                  background: "var(--series-1)",
                  borderRadius: "3px 3px 0 0",
                }}
              />
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 4, overflowX: "auto" }}>
        {points.map((p) => (
          <span
            key={p.date}
            style={{
              flex: "1 0 22px",
              minWidth: 22,
              fontSize: 10,
              color: "var(--text-muted)",
              textAlign: "center",
            }}
          >
            {formatShortDate(p.date)}
          </span>
        ))}
      </div>
    </div>
  );
}
