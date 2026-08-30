import { useState } from "react";

export interface DateRange {
  start: string; // ISO datetime
  end: string;
}

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString();
}

const PRESETS: { label: string; days: number }[] = [
  { label: "Today", days: 1 },
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
];

/** A row of preset buttons (palette.md's filter-control spec) — plain buttons rather than
 * the full check-marked preset-list component, since this admin tool has one filter, not a
 * shared filter bar across many charts. */
export function DateRangePicker({ onChange }: { onChange: (range: DateRange) => void }) {
  const [activeDays, setActiveDays] = useState(7);

  function selectPreset(days: number) {
    setActiveDays(days);
    onChange({ start: daysAgo(days), end: new Date().toISOString() });
  }

  return (
    <div style={{ display: "flex", gap: 8 }}>
      {PRESETS.map((p) => (
        <button
          key={p.days}
          onClick={() => selectPreset(p.days)}
          style={{
            padding: "6px 12px",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: activeDays === p.days ? "var(--series-1)" : "var(--surface-1)",
            color: activeDays === p.days ? "#fff" : "var(--text-primary)",
          }}
        >
          {p.label}
        </button>
      ))}
    </div>
  );
}

export function defaultRange(): DateRange {
  return { start: daysAgo(7), end: new Date().toISOString() };
}
