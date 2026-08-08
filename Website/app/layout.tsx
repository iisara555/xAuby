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
  title: "xAuby | Live Gold & Bitcoin Trading Research Platform",
  description:
    "An owner-operated gold and bitcoin trading platform built around reproducible certificates, live execution controls, and evidence-gated growth.",
  openGraph: {
    title: "xAuby | Real capital. Reproducible evidence.",
    description:
      "Live gold and bitcoin trading, certificate-bound research, and an evidence-gated roadmap.",
    images: [
      {
        url: "/og.png",
        width: 1200,
        height: 630,
        alt: "xAuby gold and bitcoin trading research platform",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "xAuby | Real capital. Reproducible evidence.",
    description:
      "Live gold and bitcoin trading, certificate-bound research, and an evidence-gated roadmap.",
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={spaceGrotesk.variable}>
      <body>{children}</body>
    </html>
  );
}
