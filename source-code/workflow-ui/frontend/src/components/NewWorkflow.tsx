import { type FormEvent, useMemo, useState } from "react";
import { createWorkflow } from "../api";
import type { ExamplePrompt } from "../types";

interface Props {
  onCreated: (workflowId: string) => void;
  department: string;
  examplePrompts?: ExamplePrompt[];
}

const DEFAULT_EXAMPLES: ExamplePrompt[] = [
  {
    title: "GE competitive pressure",
    prompt:
      "For General Electric's earnings call, what did management and analysts say about industrial orders, aviation recovery, and competitive pressure?",
  },
  {
    title: "Healthcare demand signals",
    prompt:
      "Across Healthcare-sector earnings (Illumina, Dentsply Sirona, Affimed), what demand and pipeline themes appear?",
  },
  {
    title: "Similar earnings narratives",
    prompt:
      "Find earnings calls with similar guidance and margin themes to American Express.",
  },
];

export function NewWorkflow({
  onCreated,
  department,
  examplePrompts = [],
}: Props) {
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const examples = useMemo(
    () => (examplePrompts.length > 0 ? examplePrompts : DEFAULT_EXAMPLES),
    [examplePrompts],
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !prompt.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const detail = await createWorkflow(title.trim(), prompt.trim());
      setTitle("");
      setPrompt("");
      onCreated(detail.workflow.workflow_id);
    } catch {
      setError("Could not start the research. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  function useExample(ex: ExamplePrompt) {
    setTitle(ex.title);
    setPrompt(ex.prompt);
  }

  return (
    <div className="composer">
      <div className="card">
        <h2>Start a new research</h2>
        <p className="muted">
          Give one prompt to your department&apos;s NemoClaw agent. A research-lead plans
          in the platform, then fans out OpenClaw sub-agents into OpenShell sandboxes on
          VAST for <strong>{department}</strong> — every decision logged.
        </p>

        <div className="chips" style={{ marginTop: 14 }}>
          {examples.map((ex) => (
            <button type="button" key={ex.title} className="chip" onClick={() => useExample(ex)}>
              {ex.title}
            </button>
          ))}
        </div>

        <form onSubmit={submit}>
          <div className="field">
            <label>Title</label>
            <input
              value={title}
              placeholder="e.g. competitor mentions in this call"
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="field">
            <label>Prompt</label>
            <textarea
              value={prompt}
              rows={5}
              placeholder="Analyze earnings calls: competitive pressure, guidance, similar companies."
              onChange={(e) => setPrompt(e.target.value)}
            />
          </div>
          {error && <div className="error">{error}</div>}
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Starting…" : "Start research"}
          </button>
        </form>
      </div>
    </div>
  );
}
