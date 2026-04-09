"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function Home() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user) {
      router.push("/dashboard");
    }
  }, [user, loading, router]);

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
            href="/login"
            className="px-8 py-3 bg-accent text-background font-semibold rounded-lg hover:bg-accent-hover transition-colors"
          >
            Sign In
          </Link>
        </div>
        <p className="text-sm text-text-muted">
          v2.0 — FastAPI + Next.js + Supabase
        </p>
      </div>
    </main>
  );
}
