import { useEffect, useRef, useState } from "react";
import { resetPlatform } from "../api";
import type { ResetResult } from "../types";
import { GearIcon, TrashIcon } from "./icons";

const CONFIRM_PHRASE = "RESET";

export function SettingsMenu({ onReset }: { onReset: () => void }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ResetResult | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function openReset() {
    setOpen(false);
    setPhrase("");
    setError(null);
    setResult(null);
    setConfirming(true);
  }

  async function runReset() {
    setBusy(true);
    setError(null);
    try {
      const res = await resetPlatform();
      setResult(res);
      onReset();
    } catch {
      setError("Reset failed. Check backend permissions and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings" ref={ref}>
      <button
        className="icon-btn"
        title="Settings"
        aria-label="Settings"
        onClick={() => setOpen((v) => !v)}
      >
        <GearIcon size={17} />
      </button>
      {open && (
        <div className="menu">
          <div className="menu-label">Platform settings</div>
          <button className="menu-item danger" onClick={openReset}>
            <TrashIcon size={14} /> Reset platform to zero…
          </button>
        </div>
      )}

      {confirming && (
        <div className="overlay" onClick={() => !busy && setConfirming(false)}>
          <div className="card modal" onClick={(e) => e.stopPropagation()}>
            {result ? (
              <>
                <h3>Platform reset complete</h3>
                <p className="muted">
                  Cleared {result.tables_cleared.length} data stores and deleted{" "}
                  {result.pvcs_deleted.length} PVC(s) across {result.departments.length}{" "}
                  department(s). Deployments, users and departments were kept.
                </p>
                <div className="modal-actions">
                  <button className="btn btn-primary" onClick={() => setConfirming(false)}>
                    Done
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3>Reset platform to zero</h3>
                <p className="muted">
                  This permanently deletes <strong>all workflows, runs, telemetry</strong>{" "}
                  and <strong>every workflow PVC</strong> in all departments. The deployment,
                  users and departments are kept. This cannot be undone.
                </p>
                <p className="muted">
                  Type <code>{CONFIRM_PHRASE}</code> to confirm:
                </p>
                <input
                  className="reset-input"
                  value={phrase}
                  autoFocus
                  placeholder={CONFIRM_PHRASE}
                  onChange={(e) => setPhrase(e.target.value)}
                />
                {error && <div className="error">{error}</div>}
                <div className="modal-actions">
                  <button className="btn" onClick={() => setConfirming(false)} disabled={busy}>
                    Cancel
                  </button>
                  <button
                    className="btn btn-danger"
                    onClick={runReset}
                    disabled={busy || phrase !== CONFIRM_PHRASE}
                  >
                    {busy ? "Resetting…" : "Reset everything"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
