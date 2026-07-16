import { InviteForm } from "@/components/invite-form";

export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <InviteForm token={token} />;
}
