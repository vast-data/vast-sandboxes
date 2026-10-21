import { useEffect, useRef } from "react";

// Generic polling primitive. Runs `fn` every `intervalMs` while `enabled`
export function usePolling(
  fn: () => void | Promise<void>,
  intervalMs: number,
  enabled = true,
): void {
  const saved = useRef(fn);
  saved.current = fn;

  useEffect(() => {
    if (!enabled || intervalMs <= 0) return;
    let active = true;
    const id = setInterval(() => {
      if (active) void saved.current();
    }, intervalMs);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [intervalMs, enabled]);
}
