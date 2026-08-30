import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { useSelectedTenant } from "../lib/auth";

const OUTCOME_TONE: Record<string, string> = {
  success: "var(--good)",
  unavailable: "var(--critical)",
  out_of_stock: "var(--warning)",
  declined: "var(--text-secondary)",
};

export function SessionDetail() {
  const { sessionId = "" } = useParams();
  const [tenantId] = useSelectedTenant();

  const { data, isLoading } = useQuery({
    queryKey: ["session-events", tenantId, sessionId],
    queryFn: () => api.getSessionEvents(tenantId!, sessionId),
    enabled: !!tenantId,
  });

  if (!tenantId) return <p>No tenant selected.</p>;

  return (
    <div>
      <Link to="/sessions">&larr; Back to sessions</Link>
      <h1>{sessionId}</h1>

      {isLoading && <p>Loading…</p>}

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {data?.map((event, i) => (
          <div
            key={`${event.turn_id}-${event.seq}-${i}`}
            style={{
              background: "var(--surface-1)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: 12,
              display: "flex",
              justifyContent: "space-between",
              gap: 12,
            }}
          >
            <div>
              <div style={{ fontWeight: 600 }}>{event.intent}</div>
              <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                {event.action} · {new Date(event.occurred_at).toLocaleTimeString()}
                {event.turn_elapsed_ms != null && ` · ${event.turn_elapsed_ms}ms`}
              </div>
              {Object.keys(event.details).length > 0 && (
                <pre style={{ fontSize: 12, margin: "4px 0 0", color: "var(--text-muted)" }}>
                  {JSON.stringify(event.details)}
                </pre>
              )}
            </div>
            <div
              style={{
                color: OUTCOME_TONE[event.outcome] ?? "var(--text-primary)",
                fontWeight: 600,
              }}
            >
              {event.outcome}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
