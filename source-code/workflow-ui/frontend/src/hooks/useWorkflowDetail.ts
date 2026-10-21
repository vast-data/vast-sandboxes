import { useCallback, useEffect, useState } from "react";
import { getWorkflowDetail } from "../api";
import { isActive } from "../theme";
import type { WorkflowDetail } from "../types";
import { usePolling } from "./usePolling";

const ACTIVE_INTERVAL_MS = 2500; // near real-time while agents are running
const IDLE_INTERVAL_MS = 20000; // slow keep-alive once the run is terminal

interface Result {
  detail: WorkflowDetail | null;
  setDetail: (d: WorkflowDetail) => void;
  error: string | null;
  reload: () => Promise<void>;
}

export function useWorkflowDetail(workflowId: string): Result {
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getWorkflowDetail(workflowId);
      setDetail(data);
      setError(null);
    } catch {
      setError("Failed to load workflow");
    }
  }, [workflowId]);

  useEffect(() => {
    setDetail(null);
    setError(null);
    void load();
  }, [load]);

  const qaPending = detail?.qa_messages?.some((m) => m.status === "running") ?? false;
  const active = isActive(detail?.workflow.status) || qaPending;
  usePolling(load, active ? ACTIVE_INTERVAL_MS : IDLE_INTERVAL_MS, true);

  return { detail, setDetail, error, reload: load };
}
