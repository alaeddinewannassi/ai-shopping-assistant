import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../lib/api";

export function AdminTenants() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["all-tenants"], queryFn: api.listTenants });
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => api.createTenant(slug, name),
    onSuccess: () => {
      setSlug("");
      setName("");
      setError(null);
      qc.invalidateQueries({ queryKey: ["all-tenants"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Failed to create tenant"),
  });

  return (
    <div>
      <h1>All tenants (superadmin)</h1>

      {isLoading && <p>Loading…</p>}
      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 20 }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--text-secondary)", fontSize: 13 }}>
            <th style={{ padding: "8px 4px" }}>Slug</th>
            <th style={{ padding: "8px 4px" }}>Name</th>
            <th style={{ padding: "8px 4px" }}>Status</th>
            <th style={{ padding: "8px 4px" }}>Plan</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((t) => (
            <tr key={t.id} style={{ borderTop: "1px solid var(--gridline)" }}>
              <td style={{ padding: "8px 4px" }}>{t.slug}</td>
              <td style={{ padding: "8px 4px" }}>{t.name}</td>
              <td style={{ padding: "8px 4px" }}>{t.status}</td>
              <td style={{ padding: "8px 4px" }}>{t.plan}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ fontSize: 15 }}>Create tenant</h2>
      <div style={{ display: "flex", gap: 8 }}>
        <input placeholder="slug" value={slug} onChange={(e) => setSlug(e.target.value)} />
        <input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
        <button onClick={() => create.mutate()} disabled={create.isPending || !slug || !name}>
          Create
        </button>
      </div>
      {error && <p style={{ color: "var(--critical)" }}>{error}</p>}
    </div>
  );
}
