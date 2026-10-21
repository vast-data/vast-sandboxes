import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { agentShellUrl } from "../../api";
import { TerminalIcon } from "../icons";

type Phase = "connecting" | "open" | "closed";

interface Props {
  workflowId: string;
  slug: string;
  agentName: string;
  onClose: () => void;
}

export function AgentShell({ workflowId, slug, agentName, onClose }: Props) {
  const [output, setOutput] = useState("");
  const [line, setLine] = useState("");
  const [phase, setPhase] = useState<Phase>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const outRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    const ws = new WebSocket(agentShellUrl(workflowId, slug));
    wsRef.current = ws;
    ws.onopen = () => setPhase("open");
    ws.onmessage = (e) => setOutput((prev) => (prev + String(e.data)).slice(-100_000));
    ws.onerror = () =>
      setOutput((prev) => prev + "\n[connection error — the sandbox may not be reachable]\n");
    ws.onclose = () => setPhase("closed");
    return () => {
      ws.onclose = null;
      ws.close();
    };
  }, [workflowId, slug]);

  useEffect(() => {
    const el = outRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [output]);

  function send(data: string) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      send(line + "\n");
      setLine("");
    } else if (e.key === "c" && e.ctrlKey) {
      e.preventDefault();
      send("\x03"); // Ctrl-C to the PTY
    }
  }

  return (
    <div className="agent-shell">
      <div className="shell-head">
        <span className="shell-title">
          <TerminalIcon size={13} /> shell · {agentName}
        </span>
        <span className={`shell-phase ${phase}`}>
          {phase === "connecting" ? "connecting…" : phase === "open" ? "live" : "disconnected"}
        </span>
        <button className="link-btn" onClick={onClose}>
          Close
        </button>
      </div>
      <pre className="shell-out" ref={outRef}>
        {output || "Opening a session on the agent sandbox…"}
      </pre>
      <div className="shell-input">
        <span className="shell-prompt">$</span>
        <input
          value={line}
          disabled={phase !== "open"}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          placeholder={phase === "open" ? "type a command, Enter to run (Ctrl-C to interrupt)" : ""}
          onChange={(e) => setLine(e.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>
    </div>
  );
}
