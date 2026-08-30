import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { useSelectedTenant } from "../lib/auth";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      style={{
        background: "var(--surface-1)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: 20,
        marginBottom: 20,
      }}
    >
      <h2 style={{ fontSize: 15, marginTop: 0 }}>{title}</h2>
      {children}
    </section>
  );
}

function AdapterConfigSection({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["adapter-config", tenantId],
    queryFn: () => api.getAdapterConfig(tenantId).catch(() => null),
  });
  const [platform, setPlatform] = useState("prestashop");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      api.upsertAdapterConfig(tenantId, { platform, base_url: baseUrl, api_key: apiKey }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["adapter-config", tenantId] }),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    mutation.mutate();
  }

  return (
    <Section title="Store connection">
      {data && (
        <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
          Currently: {data.platform} at {data.base_url} (key {data.api_key ?? "not set"})
        </p>
      )}
      <form
        onSubmit={onSubmit}
        style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}
      >
        <label style={{ display: "flex", flexDirection: "column", fontSize: 13 }}>
          Platform
          <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="prestashop">prestashop</option>
            <option value="mock">mock</option>
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 13 }}>
          Base URL
          <input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://store.example/api"
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 13 }}>
          API key
          <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" />
        </label>
        <button type="submit" disabled={mutation.isPending}>
          Save
        </button>
      </form>
    </Section>
  );
}

function LlmConfigSection({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["llm-config", tenantId],
    queryFn: () => api.getLlmConfig(tenantId).catch(() => null),
  });
  const [provider, setProvider] = useState("rule-based-stub");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      api.upsertLlmConfig(tenantId, {
        provider,
        model: model || undefined,
        api_key: apiKey || undefined,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm-config", tenantId] }),
  });

  return (
    <Section title="LLM provider">
      {data && (
        <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>
          Currently: {data.provider} {data.model && `(${data.model})`} — key{" "}
          {data.api_key ?? "not set"}
        </p>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 13 }}>
          Provider
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="rule-based-stub">rule-based-stub</option>
            <option value="free-tier-hosted">free-tier-hosted</option>
            <option value="hosted-paid">hosted-paid</option>
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 13 }}>
          Model (optional)
          <input value={model} onChange={(e) => setModel(e.target.value)} />
        </label>
        <label style={{ display: "flex", flexDirection: "column", fontSize: 13 }}>
          API key (optional)
          <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" />
        </label>
        <button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
          Save
        </button>
      </div>
    </Section>
  );
}

function WidgetKeysSection({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["widget-keys", tenantId],
    queryFn: () => api.listWidgetKeys(tenantId),
  });
  const [origin, setOrigin] = useState("");

  const issue = useMutation({
    mutationFn: () => api.issueWidgetKey(tenantId, origin ? [origin] : []),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["widget-keys", tenantId] }),
  });
  const revoke = useMutation({
    mutationFn: (keyId: string) => api.revokeWidgetKey(tenantId, keyId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["widget-keys", tenantId] }),
  });

  return (
    <Section title="Widget keys">
      {data?.map((key) => (
        <div
          key={key.id}
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "6px 0",
            fontSize: 13,
          }}
        >
          <code>{key.public_key}</code>
          <span>{key.is_active ? "active" : "revoked"}</span>
          {key.is_active && (
            <button onClick={() => revoke.mutate(key.id)} disabled={revoke.isPending}>
              Revoke
            </button>
          )}
        </div>
      ))}
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <input
          placeholder="Allowed origin (optional)"
          value={origin}
          onChange={(e) => setOrigin(e.target.value)}
          style={{ flex: 1 }}
        />
        <button onClick={() => issue.mutate()} disabled={issue.isPending}>
          Issue new key
        </button>
      </div>
      {data?.[0] && (
        <pre style={{ fontSize: 12, marginTop: 12, color: "var(--text-muted)" }}>
          {`<assistant-chat-widget api-base="..." tenant-key="${data[0].public_key}"></assistant-chat-widget>`}
        </pre>
      )}
    </Section>
  );
}

function PromoRulesSection({ tenantId }: { tenantId: string }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["promo-rules", tenantId],
    queryFn: () => api.listPromoRules(tenantId),
  });
  const [ruleId, setRuleId] = useState("");
  const [condition, setCondition] = useState("");
  const [targetCode, setTargetCode] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      api.upsertPromoRule(tenantId, ruleId, {
        condition,
        target_code: targetCode,
        priority: 0,
        stackable_with: [],
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["promo-rules", tenantId] }),
  });

  return (
    <Section title="Promo rules">
      <table style={{ width: "100%", fontSize: 13, marginBottom: 12 }}>
        <tbody>
          {data?.map((rule) => (
            <tr key={rule.rule_id}>
              <td>{rule.rule_id}</td>
              <td>{rule.condition}</td>
              <td>{rule.target_code}</td>
              <td>{rule.is_active ? "active" : "inactive"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input placeholder="rule id" value={ruleId} onChange={(e) => setRuleId(e.target.value)} />
        <input
          placeholder="condition"
          value={condition}
          onChange={(e) => setCondition(e.target.value)}
        />
        <input
          placeholder="target code"
          value={targetCode}
          onChange={(e) => setTargetCode(e.target.value)}
        />
        <button onClick={() => mutation.mutate()} disabled={mutation.isPending || !ruleId}>
          Save rule
        </button>
      </div>
    </Section>
  );
}

export function Settings() {
  const [tenantId] = useSelectedTenant();
  if (!tenantId) return <p>No tenant selected.</p>;

  return (
    <div>
      <h1>Settings</h1>
      <AdapterConfigSection tenantId={tenantId} />
      <LlmConfigSection tenantId={tenantId} />
      <WidgetKeysSection tenantId={tenantId} />
      <PromoRulesSection tenantId={tenantId} />
    </div>
  );
}
