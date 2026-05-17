import type { Metadata, Viewport } from "next";
import { DM_Serif_Display, Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

// Editorial display serif — used for brand mark, section openers, chapter numerals.
// DM Serif Display is a refined, high-contrast serif close to the Tiempos/GT Sectra editorial intent.
const displayFont = DM_Serif_Display({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
  variable: "--font-display",
});

// Body / UI sans — geometric, warm, excellent tabular figures.
const sansFont = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: "Portfolio Intelligence",
  description: "Real-time portfolio tracking with tax-optimized recommendations",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  themeColor: "#0A0B0F",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`dark ${displayFont.variable} ${sansFont.variable}`}
    >
      <body className="min-h-screen font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
