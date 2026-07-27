import Link from "next/link";

export default function CarryPage() {
  return (
    <main style={{ padding: "2rem", maxWidth: 720 }}>
      <h1 style={{ marginBottom: "0.5rem" }}>Carry research</h1>
      <p style={{ color: "#888", marginBottom: "1.5rem" }}>
        Funding / basis snapshots collected across MEXC &amp; Gate (perp vs spot).
        Download the full dataset as CSV and analyze offline.
      </p>
      <a
        href="/api/proxy/api/carry/export-dataset"
        download
        style={{
          display: "inline-block",
          padding: "0.6rem 1rem",
          borderRadius: 6,
          background: "#1f6feb",
          color: "#fff",
          textDecoration: "none",
          fontWeight: 600,
        }}
      >
        ⬇ Export carry data (CSV)
      </a>
      <div style={{ marginTop: "1.5rem" }}>
        <Link href="/" style={{ color: "#888" }}>
          ← Back
        </Link>
      </div>
    </main>
  );
}
