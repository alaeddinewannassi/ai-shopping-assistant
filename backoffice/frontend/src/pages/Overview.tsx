import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { useSelectedTenant } from "../lib/auth";
import { StatTile } from "../components/StatTile";
import { DateRangePicker, defaultRange, type DateRange } from "../components/DateRangePicker";
import { TimeseriesChart } from "../components/TimeseriesChart";

type TrendMetric = "session_count" | "turn_count" | "llm_tokens";

const TREND_METRICS: { key: TrendMetric; label: string }[] = [
  { key: "session_count", label: "Sessions" },
  { key: "turn_count", label: "Turns" },
  { key: "llm_tokens", label: "LLM tokens" },
];

function _timeAgo(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
}

function _headroomTone(remaining: number, limit: number): "critical" | "warning" | "default" {
  if (limit <= 0) return "default";
  const ratio = remaining / limit;
  if (ratio < 0.1) return "critical";
  if (ratio < 0.3) return "warning";
  return "default";
}

export function Overview() {
  const [tenantId] = useSelectedTenant();
  const [range, setRange] = useState<DateRange>(defaultRange());
  const [trendMetric, setTrendMetric] = useState<TrendMetric>("session_count");

  const { data, isLoading, error } = useQuery({
    queryKey: ["overview", tenantId, range.start, range.end],
    queryFn: () => api.getOverview(tenantId!, range.start, range.end),
    enabled: !!tenantId,
  });

  const { data: trend } = useQuery({
    queryKey: ["overview-timeseries", tenantId, range.start, range.end],
    queryFn: () => api.getTimeseries(tenantId!, range.start, range.end),
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
            label="Checkout rate"
            value={`${(data.checkout_rate * 100).toFixed(1)}%`}
            tone={data.checkout_rate > 0 ? "good" : "default"}
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

      {data && data.llm_snapshot_at != null && (
        <div
          style={{
            marginTop: 24,
            padding: 16,
            border: "1px solid var(--border)",
            borderRadius: 8,
            background: "var(--surface-1)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 style={{ margin: 0, fontSize: 16 }}>LLM capacity</h2>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Groq free-tier headroom for this model, as of {_timeAgo(data.llm_snapshot_at)}
            </span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 12 }}>
            <StatTile
              label="Requests remaining"
              value={`${data.llm_requests_remaining!.toLocaleString()} / ${data.llm_requests_limit!.toLocaleString()}`}
              tone={_headroomTone(data.llm_requests_remaining!, data.llm_requests_limit!)}
            />
            <StatTile
              label="Tokens remaining"
              value={`${data.llm_tokens_remaining!.toLocaleString()} / ${data.llm_tokens_limit!.toLocaleString()}`}
              tone={_headroomTone(data.llm_tokens_remaining!, data.llm_tokens_limit!)}
            />
          </div>
        </div>
      )}

      {trend && (
        <div
          style={{
            marginTop: 24,
            padding: 16,
            border: "1px solid var(--border)",
            borderRadius: 8,
            background: "var(--surface-1)",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 16,
            }}
          >
            <h2 style={{ margin: 0, fontSize: 16 }}>Trend</h2>
            <div style={{ display: "flex", gap: 8 }}>
              {TREND_METRICS.map((m) => (
                <button
                  key={m.key}
                  onClick={() => setTrendMetric(m.key)}
                  style={{
                    padding: "6px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--border)",
                    background: trendMetric === m.key ? "var(--series-1)" : "var(--surface-1)",
                    color: trendMetric === m.key ? "#fff" : "var(--text-primary)",
                  }}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>
          <TimeseriesChart
            points={trend.map((p) => ({ date: p.date, value: p[trendMetric] }))}
            label={`${TREND_METRICS.find((m) => m.key === trendMetric)!.label} per day`}
          />
        </div>
      )}
    </div>
  );
}
