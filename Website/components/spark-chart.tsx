export function SparkChart({ positive = true }: { positive?: boolean }) {
  const points = positive
    ? "0,74 38,65 72,69 112,45 150,53 190,31 228,40 270,18 310,28 350,12 400,20"
    : "0,20 38,31 72,25 112,48 150,40 190,62 228,54 270,76 310,65 350,83 400,70";
  return (
    <svg className="spark-chart" viewBox="0 0 400 96" role="img" aria-label="Recent performance trend">
      <defs>
        <linearGradient id={`fill-${positive}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ff431a" stopOpacity=".28" />
          <stop offset="1" stopColor="#ff431a" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`M ${points.replaceAll(" ", " L ")} L 400 96 L 0 96 Z`} fill={`url(#fill-${positive})`} />
      <polyline points={points} fill="none" stroke="#ff431a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
