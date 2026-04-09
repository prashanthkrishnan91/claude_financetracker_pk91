"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import Link from "next/link";

export default function LoginPage() {
  const { signIn, signUp, loading } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [submitting, setSubmitting] = useState(false);
  const [signupSuccess, setSignupSuccess] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      if (mode === "login") {
        const result = await signIn(email, password);
        if (result.error) setError(result.error);
      } else {
        const result = await signUp(email, password);
        if (result.error) {
          setError(result.error);
        } else {
          setSignupSuccess(true);
        }
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (signupSuccess) {
    return (
      <main className="flex min-h-screen items-center justify-center p-6">
        <div className="w-full max-w-sm space-y-6 text-center">
          <h1 className="text-3xl font-display text-text-primary">
            Check your email
          </h1>
          <p className="text-text-secondary">
            We sent a confirmation link to <strong>{email}</strong>. Click it to
            activate your account.
          </p>
          <button
            onClick={() => {
              setMode("login");
              setSignupSuccess(false);
            }}
            className="text-accent hover:underline text-sm"
          >
            Back to login
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm space-y-8">
        <div className="text-center">
          <h1 className="text-3xl font-display text-text-primary">
            Portfolio Intelligence
          </h1>
          <p className="text-sm text-text-secondary mt-2">
            {mode === "login" ? "Sign in to your account" : "Create your account"}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="text-xs text-text-muted block mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full px-3 py-2.5 bg-surface border border-border rounded-lg text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label className="text-xs text-text-muted block mb-1">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              className="w-full px-3 py-2.5 bg-surface border border-border rounded-lg text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
              placeholder="Min 8 characters"
            />
          </div>

          {error && (
            <p className="text-sm text-danger bg-danger/10 px-3 py-2 rounded-lg">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting || loading}
            className="w-full py-2.5 bg-accent text-background font-semibold rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50"
          >
            {submitting
              ? "..."
              : mode === "login"
              ? "Sign In"
              : "Create Account"}
          </button>
        </form>

        <div className="text-center">
          <button
            onClick={() => {
              setMode(mode === "login" ? "signup" : "login");
              setError("");
            }}
            className="text-sm text-text-secondary hover:text-accent transition-colors"
          >
            {mode === "login"
              ? "Need an account? Sign up"
              : "Already have an account? Sign in"}
          </button>
        </div>

        <div className="text-center">
          <Link href="/" className="text-xs text-text-muted hover:text-text-secondary">
            v2.0 -- Portfolio Intelligence
          </Link>
        </div>
      </div>
    </main>
  );
}
