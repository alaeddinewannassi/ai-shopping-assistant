import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useSelectedTenant } from "../lib/auth";

const OUTCOMES = ["", "browsing", "cart", "ordered", "abandoned"];

export function Sessions() {
  const [tenantId] = useSelectedTenant();
  const [outcome, setOutcome] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["sessions", tenantId, outcome],
    queryFn: () => api.listSessions(tenantId!, { outcome: outcome || undefined }),
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
        <h1 style={{ margin: 0 }}>Sessions</h1>
        <select
          value={outcome}
          onChange={(e) => setOutcome(e.target.value)}
          style={{ padding: 6, borderRadius: 6 }}
        >
          {OUTCOMES.map((o) => (
            <option key={o} value={o}>
              {o || "All outcomes"}
            </option>
          ))}
        </select>
      </div>

      {isLoading && <p>Loading…</p>}

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--text-secondary)", fontSize: 13 }}>
            <th style={{ padding: "8px 4px" }}>Session</th>
            <th style={{ padding: "8px 4px" }}>Last seen</th>
            <th style={{ padding: "8px 4px" }}>Turns</th>
            <th style={{ padding: "8px 4px" }}>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((s) => (
            <tr key={s.session_id} style={{ borderTop: "1px solid var(--gridline)" }}>
              <td style={{ padding: "8px 4px" }}>
                <Link to={`/sessions/${encodeURIComponent(s.session_id)}`}>{s.session_id}</Link>
              </td>
              <td style={{ padding: "8px 4px" }}>{new Date(s.last_seen_at).toLocaleString()}</td>
              <td style={{ padding: "8px 4px" }}>{s.turn_count}</td>
              <td style={{ padding: "8px 4px" }}>{s.outcome}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
