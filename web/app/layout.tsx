import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/nav";

export const metadata: Metadata = {
  title: "Ancora",
  description: "A fault-tolerant runtime for durable AI workflows.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Dark-first: the `dark` class is applied at the root.
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen antialiased">
        <div className="flex min-h-screen">
          <Nav />
          <div className="flex min-w-0 flex-1 flex-col">
            <header className="flex h-14 items-center border-b px-6">
              <h1 className="text-sm font-medium text-muted-foreground">
                Durable AI Workflow Runtime
              </h1>
            </header>
            <main className="min-w-0 flex-1 p-6">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
