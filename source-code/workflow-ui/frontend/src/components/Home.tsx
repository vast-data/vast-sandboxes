import { useRef } from "react";
import heroMascot from "../assets/hero-vast-astronaut.png";
import type { User } from "../types";
import { PlusIcon } from "./icons";
import { Pillars } from "./Pillars";

interface Props {
  onStart: () => void;
  ctaLabel?: string;
  user?: User | null;
}

export function Home({ onStart, ctaLabel = "Start your first workflow", user }: Props) {
  const pillarsRef = useRef<HTMLDivElement | null>(null);

  const isAdmin = !!user?.is_admin;
  const deptKey = user?.department || "";
  const deptName = user?.department_display || user?.department || "";
  const role = user?.persona?.trim() || "";

  const workspaceLabel = isAdmin
    ? "Observer · all departments"
    : deptName
      ? `${deptName} workspace`
      : "";

  const tagline = isAdmin
    ? "One control room for every department."
    : deptName
      ? `Your ${deptName} agent workspace.`
      : "Sandboxed agents for any job.";

  const sub = isAdmin
    ? "You're observing every department's agent runs across the platform. Open Workflows to see all activity, or the Admin dashboard for sandbox health and usage by department."
    : "A research-lead fans out OpenClaw sub-agents into OpenShell sandboxes on VAST block — NemoClaw policy, zero-copy clone, snapshots, VastDB grounding, fully observable.";

  const ctaText = isAdmin ? "Browse all workflows" : ctaLabel;

  return (
    <div className="empty-hero" data-department={deptKey || undefined}>
      <div className="hero-grid">
        <div className="hero-copy">
          {workspaceLabel && (
            <div
              className={`workspace-badge ${isAdmin ? "is-admin" : `dept-${deptKey}`}`}
            >
              <span className="ws-dot" aria-hidden="true" />
              {workspaceLabel}
            </div>
          )}
          <span className="hero-eyebrow">
            {user ? `Welcome, ${user.username}` : "Agentic platform"} · NemoClaw + OpenShell
            on VAST
          </span>
          <h1 className="hero-title">Secure agents on VAST</h1>
          <p className="hero-tagline">{tagline}</p>
          <p className="hero-sub">{sub}</p>

          {role && !isAdmin && (
            <div className="role-card">
              <span className="role-card-label">Your role</span>
              <p>{role}</p>
            </div>
          )}

          <div className="hero-cta">
            <button className="btn btn-primary btn-lg" onClick={onStart}>
              <PlusIcon size={18} /> {ctaText}
            </button>
            <button
              className="btn btn-ghost btn-lg"
              onClick={() => pillarsRef.current?.scrollIntoView({ behavior: "smooth" })}
            >
              Explore the platform →
            </button>
          </div>
          <div className="hero-trust">
            <span>NVIDIA OpenShell</span>
            <span>NemoClaw blueprint</span>
            <span>VastDB + Block CSI</span>
          </div>
        </div>

        <div className="hero-visual">
          <div className="hero-aura" aria-hidden="true" />
          <div className="hero-stars" aria-hidden="true" />
          <div className="hero-mascot-frame">
            <span className="hero-orbit hero-orbit-1" aria-hidden="true">
              <i />
            </span>
            <span className="hero-orbit hero-orbit-2" aria-hidden="true">
              <i />
            </span>
            <img
              className="hero-mascot"
              src={heroMascot}
              alt="Secure agents on VAST"
            />
          </div>
        </div>
      </div>

      <section className="pillars-section" ref={pillarsRef}>
        <div className="section-intro">
          <span className="hero-eyebrow">Why it holds up in production</span>
          <h2 className="section-title">Built on the VAST Data Platform</h2>
          <p className="section-sub">
            NemoClaw recipes, OpenShell-governed sandboxes, VAST isolation. Every run is
            observable end to end and grounded in your own data.
          </p>
        </div>
        <Pillars />
      </section>
    </div>
  );
}
