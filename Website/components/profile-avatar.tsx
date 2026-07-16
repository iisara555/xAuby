import Image from "next/image";

export function ProfileAvatar({
  name,
  email,
  src,
  size = 36,
}: {
  name?: string;
  email: string;
  src?: string | null;
  size?: number;
}) {
  const label = (name || email.split("@")[0] || "U").trim();
  if (src) {
    return <Image className="profile-avatar-image" src={src} width={size} height={size} alt={`${label} profile`} unoptimized />;
  }
  return <span className="profile-avatar-fallback" style={{ width: size, height: size }} aria-label={`${label} profile`}>{label.slice(0, 2).toUpperCase()}</span>;
}
