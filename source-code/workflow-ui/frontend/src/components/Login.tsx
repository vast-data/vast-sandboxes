import { type FormEvent, useState } from "react";
import { login, setToken } from "../api";
import type { User } from "../types";
import { BrandMark } from "./Brand";
import { LockIcon, UserIcon } from "./icons";

interface Props {
  onAuthenticated: (user: User) => void;
}

export function Login({ onAuthenticated }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result = await login(username, password);
      setToken(result.access_token);
      onAuthenticated(result.user);
    } catch {
      setError("Invalid username or password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="login-bg" aria-hidden>
        <div className="stars stars-far" />
        <div className="stars stars-mid" />
        <div className="stars stars-near" />
        <div className="nebula nebula-1" />
        <div className="nebula nebula-2" />
      </div>

      <div className="login-stage">
        <div className="login-glow" aria-hidden />
        <form className="login-card" onSubmit={submit}>
          <div className="login-hero">
            <BrandMark size={40} />
            <h1>Secure agents on VAST</h1>
            <p>Sandboxed per department</p>
          </div>

          <div className="field">
            <label>Username</label>
            <div className="input-wrap">
              <UserIcon size={16} />
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="your VAST user"
                autoFocus
              />
            </div>
          </div>

          <div className="field">
            <label>Password</label>
            <div className="input-wrap">
              <LockIcon size={16} />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>
          </div>

          {error && <div className="error">{error}</div>}

          <button type="submit" className="login-submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>

          <div className="powered">
            Powered by <span className="accent">NemoClaw</span> +{" "}
            <span className="accent">OpenShell</span> on
            <span className="accent">VAST</span>
          </div>
        </form>
      </div>
    </div>
  );
}
