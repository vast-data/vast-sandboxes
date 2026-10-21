import { useCallback, useEffect, useRef, useState } from "react";
import { cleanSummary, isSubAgent } from "../attrs";
import { isActive } from "../theme";
import { useToast } from "../toast";
import type { WorkflowDetail } from "../types";

interface Result {
  summaryUnseen: boolean;
  markSummarySeen: () => void;
}

export function useWorkflowNotifications(
  detail: WorkflowDetail | null,
  tab: string,
  /** When false, skip summary toasts and the unseen badge. */
  reportNotificationsEnabled = true,
  /** Tab id that counts as "seen" for the merged report (summary vs synthesis). */
  reportTab = "summary",
): Result {
  const toast = useToast();
  const [summaryUnseen, setSummaryUnseen] = useState(false);

  const tabRef = useRef(tab);
  tabRef.current = tab;

  // Per-workflow baselines so we only toast on genuine transitions between polls.
  const wfRef = useRef<string | null>(null);
  const doneRef = useRef(0);
  const synthRef = useRef(false);
  const summaryRef = useRef(false);

  const markSummarySeen = useCallback(() => setSummaryUnseen(false), []);

  useEffect(() => {
    if (!detail) return;
    const wf = detail.workflow;
    const nodes = detail.graph?.nodes ?? [];
    const subs = nodes.filter(isSubAgent);
    const total = subs.length;
    const done = subs.filter((n) => !isActive(n.status)).length;
    const summaryNode = nodes.find((n) => n.attributes.summary);
    const summaryReady = !!(summaryNode && cleanSummary(summaryNode.attributes.summary).trim());
    // Synthesis is underway once every sandbox is done but the summary hasn't landed yet.
    const synthesizing = isActive(wf.status) && total > 0 && done === total && !summaryReady;

    if (wfRef.current !== wf.workflow_id) {
      wfRef.current = wf.workflow_id;
      doneRef.current = done;
      synthRef.current = synthesizing;
      summaryRef.current = summaryReady;
      setSummaryUnseen(false);
      return;
    }

    if (total > 0 && done > doneRef.current) {
      toast.push(`Agent sandbox ${done} of ${total} finished`, "info");
    }
    doneRef.current = done;

    if (reportNotificationsEnabled) {
      if (synthesizing && !synthRef.current) {
        toast.push(`Synthesis for "${wf.title}" is being generated…`, "info");
      }
      if (summaryReady && !summaryRef.current) {
        toast.push(`Synthesis for "${wf.title}" is ready`, "success");
        if (tabRef.current !== reportTab) setSummaryUnseen(true);
      }
    }
    synthRef.current = synthesizing;
    summaryRef.current = summaryReady;
  }, [detail, reportNotificationsEnabled, reportTab, toast]);

  return { summaryUnseen, markSummarySeen };
}
