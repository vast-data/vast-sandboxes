import { useLayoutEffect, useRef, useState } from "react";

interface Props {
  text: string;
  className?: string;
  lines?: number;
  moreLabel?: string;
}

/** Reveals a control only when the rendered text is actually clipped. */
export function ExpandableText({
  text,
  className = "",
  lines = 1,
  moreLabel = "Show more",
}: Props) {
  const contentRef = useRef<HTMLSpanElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [clipped, setClipped] = useState(false);

  useLayoutEffect(() => {
    const content = contentRef.current;
    if (!content || expanded) return;

    const measure = () => {
      setClipped(
        content.scrollWidth > content.clientWidth + 1 ||
          content.scrollHeight > content.clientHeight + 1,
      );
    };
    measure();

    const observer = new ResizeObserver(measure);
    observer.observe(content);
    return () => observer.disconnect();
  }, [expanded, lines, text]);

  return (
    <span
      className={`expandable-text ${expanded ? "is-expanded" : ""}`}
      style={{ ["--collapsed-lines" as string]: lines } as React.CSSProperties}
    >
      <span ref={contentRef} className={`expandable-text-content ${className}`} title={text}>
        {text}
      </span>
      {(clipped || expanded) && (
        <button
          type="button"
          className="expandable-text-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Show less" : moreLabel}
        </button>
      )}
    </span>
  );
}
