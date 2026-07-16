import type { Metadata } from "next";
import { Space_Grotesk } from "next/font/google";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-space-grotesk",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://xauby.vercel.app"),
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
    <html lang="en" className={spaceGrotesk.variable}>
      <body>{children}</body>
    </html>
  );
}
