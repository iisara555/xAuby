import { UnifiedConsole } from "../../ui/unified-console";

const views = new Set(["home", "trade", "activity", "settings", "admin"]);

export default async function ConsolePage({
  params,
}: {
  params: Promise<{ view?: string[] }>;
}) {
  const { view } = await params;
  const candidate = view?.[0] ?? "home";
  const initialView = views.has(candidate) ? candidate : "home";
  return <UnifiedConsole initialView={initialView} />;
}
