// Content-shaped loading placeholders (replaces bare "Loading…" text).

export function DetailSkeleton() {
  return (
    <div className="detail skeleton" aria-busy="true" aria-label="Loading workflow">
      <div className="detail-head">
        <div style={{ flex: 1 }}>
          <div className="skel skel-line" style={{ width: "38%", height: 22 }} />
          <div className="skel skel-line" style={{ width: "22%", height: 14, marginTop: 10 }} />
        </div>
        <div className="skel skel-line" style={{ width: 96, height: 34 }} />
      </div>
      <div className="tabbar">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="skel skel-line" style={{ width: 72, height: 16, margin: "8px 6px" }} />
        ))}
      </div>
      <div className="detail-body">
        <div className="graph">
          <div className="skel skel-fill" />
        </div>
        <aside className="inspector">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} style={{ marginBottom: 16 }}>
              <div className="skel skel-line" style={{ width: "40%", height: 12 }} />
              <div className="skel skel-line" style={{ width: "100%", height: 40, marginTop: 6 }} />
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}

export function ListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <ul className="workflow-list" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <li key={i} className="wf-item">
          <div className="skel skel-line" style={{ width: "70%", height: 14 }} />
          <div className="skel skel-line" style={{ width: "45%", height: 12, marginTop: 8 }} />
        </li>
      ))}
    </ul>
  );
}
