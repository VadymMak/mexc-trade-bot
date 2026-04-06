import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "MEXC Trade Bot",
  description: "HFT Trading Dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <nav style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 24px",
          height: "52px",
          borderBottom: "1px solid var(--color-border)",
          background: "var(--color-bg-card)",
          position: "sticky",
          top: 0,
          zIndex: 100,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            <span style={{ color: "var(--color-accent)", fontWeight: 700, marginRight: "20px", letterSpacing: "0.05em" }}>
              ⚡ MEXC BOT
            </span>
            {[
              { href: "/", label: "Trading" },
              { href: "/scanner", label: "Scanner" },
              { href: "/history", label: "History" },
              { href: "/settings", label: "Settings" },
            ].map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                style={{
                  padding: "6px 14px",
                  borderRadius: "6px",
                  color: "var(--color-text-muted)",
                  fontSize: "13px",
                  transition: "color 0.15s, background 0.15s",
                }}
              >
                {label}
              </Link>
            ))}
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
            {/* Bot status — will be made dynamic in future */}
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                background: "var(--color-green)",
                display: "inline-block",
              }} />
              <span style={{ color: "var(--color-text-muted)", fontSize: "12px" }}>BOT ON</span>
            </div>

            {/* PnL placeholder */}
            <span style={{
              color: "var(--color-text-muted)",
              fontSize: "12px",
              padding: "4px 10px",
              borderRadius: "6px",
              background: "var(--color-bg-hover)",
              border: "1px solid var(--color-border)",
            }}>
              PnL: —
            </span>
          </div>
        </nav>

        <main style={{ minHeight: "calc(100vh - 52px)" }}>
          {children}
        </main>
      </body>
    </html>
  );
}
