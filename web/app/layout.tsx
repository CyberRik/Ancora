import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Nav } from "@/components/nav";
import { TopBar } from "@/components/top-bar";

const sans = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Ancora",
    template: "%s · Ancora",
  },
  description: "A fault-tolerant runtime for durable AI workflows.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Dark-first: the `dark` class is applied at the root.
  return (
    <html lang="en" className={`dark ${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen bg-background font-sans antialiased">
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:border focus:bg-card focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        <div className="flex min-h-screen">
          <Nav />
          <div className="flex min-w-0 flex-1 flex-col">
            <TopBar />
            <main id="content" className="min-w-0 flex-1 px-6 py-7 lg:px-8">
              {/* Capped so a four-column table stays readable on a 27" monitor
                  instead of stretching across 2500px. */}
              <div className="mx-auto w-full max-w-[1440px]">{children}</div>
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
