import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { useSelectedTenant } from "../lib/auth";
import { StatTile } from "../components/StatTile";
import { DateRangePicker, defaultRange, type DateRange } from "../components/DateRangePicker";

export function Overview() {
  const [tenantId] = useSelectedTenant();
  const [range, setRange] = useState<DateRange>(defaultRange());

  const { data, isLoading, error } = useQuery({
    queryKey: ["overview", tenantId, range.start, range.end],
    queryFn: () => api.getOverview(tenantId!, range.start, range.end),
    enabled: !!tenantId,
  });

  if (!tenantId) return <p>No tenant selected.</p>;

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 20,
        }}
      >
        <h1 style={{ margin: 0 }}>Overview</h1>
        <DateRangePicker onChange={setRange} />
      </div>

      {isLoading && <p>Loading…</p>}
      {error && <p style={{ color: "var(--critical)" }}>Failed to load overview.</p>}

      {data && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
          <StatTile label="Sessions" value={data.session_count.toLocaleString()} />
          <StatTile label="Turns" value={data.turn_count.toLocaleString()} />
          <StatTile
            label="Conversion rate"
            value={`${(data.conversion_rate * 100).toFixed(1)}%`}
            tone={data.conversion_rate > 0 ? "good" : "default"}
          />
          <StatTile
            label="Avg turn latency"
            value={
              data.avg_turn_latency_ms != null ? `${Math.round(data.avg_turn_latency_ms)} ms` : "—"
            }
          />
          <StatTile
            label="p95 turn latency"
            value={
              data.p95_turn_latency_ms != null ? `${Math.round(data.p95_turn_latency_ms)} ms` : "—"
            }
            tone={
              data.p95_turn_latency_ms != null && data.p95_turn_latency_ms > 2000
                ? "warning"
                : "default"
            }
          />
          <StatTile
            label="Error rate"
            value={`${(data.error_rate * 100).toFixed(1)}%`}
            tone={data.error_rate > 0.05 ? "critical" : "default"}
          />
        </div>
      )}
    </div>
  );
}
