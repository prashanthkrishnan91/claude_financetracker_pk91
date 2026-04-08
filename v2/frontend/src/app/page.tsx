import Link from "next/link";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-6">
      <div className="max-w-2xl text-center space-y-8">
        <h1 className="text-5xl font-display text-text-primary">
          Portfolio Intelligence
        </h1>
        <p className="text-xl text-text-secondary">
          Real-time portfolio tracking, tax-optimized recommendations, and DRIP
          analytics — built for serious investors.
        </p>
        <div className="flex gap-4 justify-center">
          <Link
            href="/dashboard"
            className="px-8 py-3 bg-accent text-background font-semibold rounded-lg hover:bg-accent-hover transition-colors"
          >
            Open Dashboard
          </Link>
          <Link
            href="/settings"
            className="px-8 py-3 border border-border text-text-primary rounded-lg hover:bg-surface-elevated transition-colors"
          >
            Settings
          </Link>
        </div>
        <p className="text-sm text-text-muted">
          v2.0 — FastAPI + Next.js + Supabase
        </p>
      </div>
    </main>
  );
}
