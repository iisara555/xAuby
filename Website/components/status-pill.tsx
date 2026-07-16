export function StatusPill({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: "good" | "warn" | "bad" | "neutral";
}) {
  return <span className={`status-pill status-${tone}`}><i />{label}</span>;
}
