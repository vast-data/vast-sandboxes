import { useEffect, useRef, useState } from "react";
import { endpointLabel, isHostedNvidia, shortModelName } from "../attrs";
import type { PlatformModelInfo } from "../types";

interface Leg {
  key: string;
  label: string;
  runs: string;
  model: string;
  url: string;
}

function legsOf(info: PlatformModelInfo): Leg[] {
  return [
    {
      key: "generation",
      label: "Generation",
      runs: "research-lead planning + final synthesis",
      model: info.model,
      url: info.base_url,
    },
    {
      key: "openclaw",
      label: "OpenClaw · OpenShell",
      runs: "every sub-agent turn in an OpenShell sandbox (tools, shell, memory)",
      model: info.openclaw_primary || info.model,
      url: info.openclaw_base_url || info.base_url,
    },
    {
      key: "embeddings",
      label: "Embeddings",
      runs: `RAG query vectors${info.embed_dim ? ` · ${info.embed_dim} dims` : ""}`,
      model: info.embed_model,
      url: info.embed_base_url || info.base_url,
    },
  ];
}

export function ModelBadge({
  info,
  title,
  compact,
}: {
  info: PlatformModelInfo | null;
  title?: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!info) return null;
  const label = shortModelName(info.model || "unknown");
  const legs = legsOf(info);
  const tip = title || `${info.provider} — click for models and endpoints`;

  return (
    <div className="model-badge-wrap" ref={ref}>
      <button
        type="button"
        className={`model-badge ${info.enabled ? "on" : "off"} ${compact ? "compact" : ""}`}
        title={tip}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="model-badge-dot" aria-hidden="true" />
        <span className="model-badge-label">{compact ? label : `Model: ${label}`}</span>
        {!compact && !info.enabled && <span className="model-badge-sub">offline</span>}
      </button>

      {/* The header badge sits at the right edge, the compact one (workflow detail) at
          the left — anchor each so the panel opens inward, not off-screen. */}
      {open && (
        <div
          className={`model-pop ${compact ? "anchor-left" : ""}`}
          role="dialog"
          aria-label="Active models and endpoints"
        >
          <div className="menu-label">
            {info.provider}
            {!info.enabled && " · offline"}
          </div>
          {legs.map((leg) => {
            const hosted = isHostedNvidia(leg.url);
            return (
              <div className="model-leg" key={leg.key}>
                <div className="model-leg-head">
                  <span className="model-leg-name">{leg.label}</span>
                  <span className={`model-leg-tag ${hosted ? "hosted" : "self"}`}>
                    {hosted ? "NGC" : "self-hosted"}
                  </span>
                </div>
                <div className="model-leg-model">{shortModelName(leg.model)}</div>
                <div className="model-leg-url" title={leg.url}>
                  {endpointLabel(leg.url)}
                </div>
                <div className="model-leg-runs">{leg.runs}</div>
              </div>
            );
          })}
          {info.split_endpoints && (
            <div className="model-pop-note">
              Embeddings run on their own endpoint. The corpus is indexed at{" "}
              {info.embed_dim} dims, so the embedding model cannot change without
              re-ingesting it — only generation moves.
            </div>
          )}
          {!info.enabled && (
            <div className="model-pop-note">
              Model calls are disabled — runs fall back to offline synthesis text.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
