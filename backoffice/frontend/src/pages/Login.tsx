import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { ApiError } from "../lib/api";

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/overview");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
      }}
    >
      <form
        onSubmit={onSubmit}
        style={{
          background: "var(--surface-1)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: 32,
          width: 320,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <h1 style={{ fontSize: 18, margin: 0 }}>Backoffice login</h1>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ padding: 8, borderRadius: 6, border: "1px solid var(--border)" }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
          Password
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ padding: 8, borderRadius: 6, border: "1px solid var(--border)" }}
          />
        </label>
        {error && <div style={{ color: "var(--critical)", fontSize: 13 }}>{error}</div>}
        <button type="submit" disabled={submitting} style={{ padding: 10, borderRadius: 6 }}>
          {submitting ? "Logging in…" : "Log in"}
        </button>
      </form>
    </div>
  );
}
