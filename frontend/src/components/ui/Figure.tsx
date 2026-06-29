import type { ReactNode } from "react";

/** One large stat in a `.figrow` — label over a mono value, optional sub-line. */
export function Figure({
  label,
  value,
  valueClass = "",
  valueStyle,
  sub,
}: {
  label: ReactNode;
  value: ReactNode;
  valueClass?: string;
  valueStyle?: React.CSSProperties;
  sub?: ReactNode;
}) {
  return (
    <div className="fig">
      <span className="lab">{label}</span>
      <span className={`val ${valueClass}`} style={valueStyle}>
        {value}
      </span>
      {sub !== undefined && <span className="sub">{sub}</span>}
    </div>
  );
}
