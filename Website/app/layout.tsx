import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://x-auby.vercel.app"),
  title: "xAuby | Gold Trading Research Platform",
  description:
    "A research-first automated gold trading platform with documented validation before capital deployment.",
  openGraph: {
    title: "xAuby | Research first. Capital second.",
    description: "Gold Trading Research Platform",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "xAuby Gold Trading Research Platform" }]
  },
  twitter: {
    card: "summary_large_image",
    title: "xAuby | Research first. Capital second.",
    description: "Gold Trading Research Platform",
    images: ["/og.png"]
  }
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
