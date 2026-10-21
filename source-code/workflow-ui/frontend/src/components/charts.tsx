import { useState } from "react";
import { ExpandableText } from "./ExpandableText";

export interface Metric {
  label: string;
  value: number;
  unit?: string;
}

export type ChartType = "bar" | "line" | "donut" | "radar";

const BAR_COLORS = ["#1FD9FE", "#7C6CF6", "#39D98A", "#F5A623", "#FF7A8A", "#4CC9F0", "#B084F6"];

function color(i: number): string {
  return BAR_COLORS[i % BAR_COLORS.length];
}

function formatValue(v: number): string {
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function clean(data: Metric[]): Metric[] {
  return data.filter((d) => Number.isFinite(d.value));
}

export function BarChart({ data }: { data: Metric[] }) {
  const items = clean(data);
  if (items.length === 0) return null;
  const max = Math.max(...items.map((d) => Math.abs(d.value)), 1);
  return (
    <div className="chart">
      {items.map((d, i) => {
        const pct = Math.max(2, (Math.abs(d.value) / max) * 100);
        return (
          <div key={`${d.label}-${i}`} className="chart-row">
            <ExpandableText text={d.label} className="chart-label" />
            <span className="chart-track">
              <span
                className="chart-fill"
                style={{ width: `${pct}%`, ["--fill" as string]: color(i) } as React.CSSProperties}
              />
            </span>
            <span className="chart-value">
              {formatValue(d.value)}
              {d.unit ? ` ${d.unit}` : ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function LineChart({ data }: { data: Metric[] }) {
  const items = clean(data);
  if (items.length === 0) return null;
  const W = 320;
  const H = 150;
  const pad = 8;
  const max = Math.max(...items.map((d) => d.value), 1);
  const min = Math.min(...items.map((d) => d.value), 0);
  const span = max - min || 1;
  const step = items.length > 1 ? (W - pad * 2) / (items.length - 1) : 0;
  const pts = items.map((d, i) => {
    const x = pad + i * step;
    const y = H - pad - ((d.value - min) / span) * (H - pad * 2);
    return { x, y, d };
  });
  const path = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = `${path} L${pts[pts.length - 1].x.toFixed(1)},${H - pad} L${pts[0].x.toFixed(1)},${H - pad} Z`;
  const gid = `line-fill-${W}-${items.length}`;
  return (
    <div className="svg-chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1FD9FE" stopOpacity={0.34} />
            <stop offset="55%" stopColor="#39D98A" stopOpacity={0.12} />
            <stop offset="100%" stopColor="#39D98A" stopOpacity={0} />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gid})`} stroke="none" />
        <path
          d={path}
          fill="none"
          stroke="#1FD9FE"
          strokeWidth={2}
          style={{ filter: "drop-shadow(0 0 4px rgba(31,217,254,0.5))" }}
        />
        {pts.map((p, i) => (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r={2.6} fill="#1FD9FE">
              <title>{`${p.d.label}: ${formatValue(p.d.value)}${p.d.unit ? ` ${p.d.unit}` : ""}`}</title>
            </circle>
          </g>
        ))}
      </svg>
      <div className="axis-labels">
        {items.map((d, i) => (
          <ExpandableText key={i} text={d.label} className="axis-label" />
        ))}
      </div>
    </div>
  );
}

export function DonutChart({ data }: { data: Metric[] }) {
  const items = clean(data).filter((d) => d.value > 0);
  if (items.length === 0) return null;
  const total = items.reduce((s, d) => s + d.value, 0) || 1;
  const R = 60;
  const stroke = 26;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="svg-chart donut">
      <svg viewBox="0 0 160 160" role="img">
        <g transform="translate(80,80) rotate(-90)">
          {items.map((d, i) => {
            const frac = d.value / total;
            const dash = frac * C;
            const seg = (
              <circle
                key={i}
                r={R}
                fill="none"
                stroke={color(i)}
                strokeWidth={stroke}
                strokeDasharray={`${dash} ${C - dash}`}
                strokeDashoffset={-offset}
              >
                <title>{`${d.label}: ${formatValue(d.value)} (${Math.round(frac * 100)}%)`}</title>
              </circle>
            );
            offset += dash;
            return seg;
          })}
        </g>
        <text x="80" y="84" textAnchor="middle" className="donut-total">
          {formatValue(total)}
        </text>
      </svg>
      <ul className="legend">
        {items.map((d, i) => (
          <li key={i}>
            <span className="legend-dot" style={{ background: color(i) }} />
            <ExpandableText text={d.label} className="legend-label" />
            <span className="legend-value">
              {formatValue(d.value)}
              {d.unit ? ` ${d.unit}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RadarChart({ data }: { data: Metric[] }) {
  const items = clean(data);
  if (items.length < 3) return <BarChart data={items} />; // radar needs >= 3 axes
  const size = 200;
  const c = size / 2;
  const R = c - 30;
  const max = Math.max(...items.map((d) => Math.abs(d.value)), 1);
  const angle = (i: number) => (Math.PI * 2 * i) / items.length - Math.PI / 2;
  const point = (v: number, i: number) => {
    const r = (Math.abs(v) / max) * R;
    return [c + r * Math.cos(angle(i)), c + r * Math.sin(angle(i))];
  };
  const rings = [0.25, 0.5, 0.75, 1];
  const poly = items.map((d, i) => point(d.value, i).map((n) => n.toFixed(1)).join(",")).join(" ");
  return (
    <div className="svg-chart">
      <svg viewBox={`0 0 ${size} ${size}`} role="img">
        {rings.map((f, ri) => (
          <polygon
            key={ri}
            points={items
              .map((_, i) => {
                const x = c + R * f * Math.cos(angle(i));
                const y = c + R * f * Math.sin(angle(i));
                return `${x.toFixed(1)},${y.toFixed(1)}`;
              })
              .join(" ")}
            fill="none"
            stroke="rgba(255,255,255,0.08)"
          />
        ))}
        {items.map((_, i) => {
          const [x, y] = point(max, i);
          return <line key={i} x1={c} y1={c} x2={x} y2={y} stroke="rgba(255,255,255,0.08)" />;
        })}
        <polygon points={poly} fill="rgba(124,108,246,0.28)" stroke="#7C6CF6" strokeWidth={2} />
        {items.map((d, i) => {
          const [x, y] = point(d.value, i);
          return (
            <circle key={i} cx={x} cy={y} r={2.6} fill="#7C6CF6">
              <title>{`${d.label}: ${formatValue(d.value)}${d.unit ? ` ${d.unit}` : ""}`}</title>
            </circle>
          );
        })}
      </svg>
      <ul className="legend compact">
        {items.map((d, i) => (
          <li key={i}>
            <ExpandableText text={d.label} className="legend-label" />
            <span className="legend-value">{formatValue(d.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const RENDERERS: Record<ChartType, (p: { data: Metric[] }) => React.ReactElement | null> = {
  bar: BarChart,
  line: LineChart,
  donut: DonutChart,
  radar: RadarChart,
};

const LABELS: Record<ChartType, string> = {
  bar: "Bar",
  line: "Line",
  donut: "Donut",
  radar: "Radar",
};

export function ChartSwitcher({
  data,
  types = ["bar", "line", "donut", "radar"],
  defaultType = "bar",
}: {
  data: Metric[];
  types?: ChartType[];
  defaultType?: ChartType;
}) {
  const [type, setType] = useState<ChartType>(defaultType);
  const items = clean(data);
  if (items.length === 0) return null;
  const Renderer = RENDERERS[type];
  return (
    <div className="chart-switcher">
      <div className="chart-toolbar">
        {types.map((t) => (
          <button
            key={t}
            className={t === type ? "active" : ""}
            onClick={() => setType(t)}
            type="button"
          >
            {LABELS[t]}
          </button>
        ))}
      </div>
      <Renderer data={items} />
    </div>
  );
}

export function ConfidenceMeter({ value }: { value: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  const color = pct >= 70 ? "#39D98A" : pct >= 40 ? "#F5A623" : "#FF7A8A";
  return (
    <div className="meter" title={`Confidence ${pct}%`}>
      <span className="meter-track">
        <span
          className="meter-fill"
          style={{ width: `${pct}%`, ["--fill" as string]: color } as React.CSSProperties}
        />
      </span>
      <span className="meter-value" style={{ color }}>
        {pct}%
      </span>
    </div>
  );
}
