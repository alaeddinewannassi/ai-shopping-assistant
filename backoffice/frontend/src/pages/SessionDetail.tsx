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
        {data?.map((event, i) => {
          // turn_completed's details carry the actual shopper message and assistant reply
          // (see chatbot/backend's log_turn_completed) — the single most useful thing for
          // reading what actually happened, so it gets its own conversation-style rendering
          // instead of a raw JSON dump. Every other event still shows its own details
          // (product_id/variant_id/quantity/code/...) as JSON — compact structured data,
          // not prose, so JSON stays the right format for it.
          const message = typeof event.details.message === "string" ? event.details.message : null;
          const reply = typeof event.details.reply === "string" ? event.details.reply : null;
          const otherDetails = Object.fromEntries(
            Object.entries(event.details).filter(([key]) => key !== "message" && key !== "reply"),
          );

          return (
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
                {(message || reply) && (
                  <div style={{ fontSize: 13, margin: "6px 0 0", maxWidth: 520 }}>
                    {message && (
                      <div>
                        <span style={{ color: "var(--text-muted)" }}>Shopper: </span>
                        {message}
                      </div>
                    )}
                    {reply && (
                      <div>
                        <span style={{ color: "var(--text-muted)" }}>Assistant: </span>
                        {reply}
                      </div>
                    )}
                  </div>
                )}
                {Object.keys(otherDetails).length > 0 && (
                  <pre style={{ fontSize: 12, margin: "4px 0 0", color: "var(--text-muted)" }}>
                    {JSON.stringify(otherDetails)}
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
          );
        })}
      </div>
    </div>
  );
}
