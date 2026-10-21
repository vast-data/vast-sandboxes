import vastLogo from "../assets/vast_logo.svg";

interface Props {
  size?: number;
  onClick?: () => void;
}

export function BrandMark({ size = 22 }: { size?: number }) {
  return (
    <img
      className="brand-mark"
      src={vastLogo}
      alt="VAST"
      style={{ height: size, width: "auto" }}
    />
  );
}

export function Brand({ size = 22, onClick }: Props) {
  // VAST wordmark only. This is a capability on the platform, not a second product name.
  return (
    <div className="brand" onClick={onClick}>
      <BrandMark size={size} />
    </div>
  );
}
