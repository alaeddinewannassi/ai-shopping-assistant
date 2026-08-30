import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { useSelectedTenant } from "../lib/auth";
import { FunnelChart } from "../components/FunnelChart";
import { DateRangePicker, defaultRange, type DateRange } from "../components/DateRangePicker";

const STAGE_LABELS: Record<string, string> = {
  sessions: "Sessions",
  discovery: "Discovery",
  proposal: "Proposal",
  confirmed: "Confirmed",
  cart_mutated: "Cart mutated",
  checkout_proposed: "Checkout proposed",
  ordered: "Ordered",
};

export function Funnel() {
  const [tenantId] = useSelectedTenant();
  const [range, setRange] = useState<DateRange>(defaultRange());

  const { data, isLoading, error } = useQuery({
    queryKey: ["funnel", tenantId, range.start, range.end],
    queryFn: () => api.getFunnel(tenantId!, range.start, range.end),
    enabled: !!tenantId,
  });

  if (!tenantId) return <p>No tenant selected.</p>;

  const stages = data
    ? (Object.keys(STAGE_LABELS) as (keyof typeof STAGE_LABELS)[]).map((key) => ({
        label: STAGE_LABELS[key],
        value: data[key as keyof typeof data] as number,
      }))
    : [];

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
        <h1 style={{ margin: 0 }}>Funnel</h1>
        <DateRangePicker onChange={setRange} />
      </div>

      {isLoading && <p>Loading…</p>}
      {error && <p style={{ color: "var(--critical)" }}>Failed to load funnel.</p>}

      {data && (
        <div
          style={{
            background: "var(--surface-1)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: 20,
            maxWidth: 480,
          }}
        >
          <FunnelChart stages={stages} />
        </div>
      )}
    </div>
  );
}
