export function TurboDefs() {
  return (
    <svg className="turbo-defs" aria-hidden>
      <defs>
        <linearGradient id="vast-edge-gradient" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#73c8fd" />
          <stop offset="100%" stopColor="#3ddc97" />
        </linearGradient>
        <marker
          id="vast-edge-circle"
          viewBox="-5 -5 10 10"
          refX="0"
          refY="0"
          markerUnits="strokeWidth"
          markerWidth="10"
          markerHeight="10"
          orient="auto"
        >
          <circle cx="0" cy="0" r="2.4" fill="#3ddc97" stroke="#0a1424" strokeWidth="1" />
        </marker>
      </defs>
    </svg>
  );
}
