"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { useAuth } from "@/lib/auth-context";

export default function LoginPage() {
  const router = useRouter();
  const { isAuthenticated, isGuest, loading, upgradeAccount } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSignUp, setIsSignUp] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Redirect only real accounts — a guest always has a (anonymous) user, so
  // gating on `isAuthenticated` lets guests reach this page to sign up / sign in.
  // Onboarding routing for new accounts is handled by the home page.
  useEffect(() => {
    if (!loading && isAuthenticated) router.replace("/");
  }, [isAuthenticated, loading, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) return;
    setSubmitting(true);
    setError("");

    if (isSignUp) {
      if (isGuest) {
        // Upgrade the anonymous guest in place so their progress is preserved.
        const { error: err } = await upgradeAccount(trimmedEmail, password);
        setSubmitting(false);
        if (err) {
          setError(/already|registered|exists/i.test(err)
            ? "That email already has an account. Switch to Sign in below."
            : err);
          return;
        }
        // Auth-state flip redirects via the effect above.
      } else {
        const { data, error: err } = await supabase.auth.signUp({ email: trimmedEmail, password });
        setSubmitting(false);
        if (err) { setError(err.message); return; }
        if (!data.session) {
          setError("Account created — check your email to confirm, then sign in.");
          return;
        }
      }
    } else {
      const { error: err } = await supabase.auth.signInWithPassword({ email: trimmedEmail, password });
      setSubmitting(false);
      if (err) { setError(err.message); return; }
    }
  }

  if (loading) return null;

  return (
    <main className="min-h-screen bg-black text-white flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-8">
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-bold tracking-tight">Curio</h1>
          <p className="text-zinc-400">{isSignUp ? "Create an account" : "Sign in to track your progress"}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={submitting}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-3 text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-400"
            autoFocus
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={submitting}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-3 text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-400"
          />
          <button
            type="submit"
            disabled={submitting || !email.trim() || !password}
            className="w-full bg-white text-black font-semibold py-3 rounded-xl disabled:opacity-40 hover:bg-zinc-100 transition"
          >
            {submitting ? "…" : isSignUp ? "Create account" : "Sign in"}
          </button>
          {error && <p className="text-red-400 text-sm text-center">{error}</p>}
        </form>

        <button
          onClick={() => { setIsSignUp(!isSignUp); setError(""); }}
          className="w-full text-zinc-500 hover:text-white text-sm transition text-center"
        >
          {isSignUp ? "Already have an account? Sign in" : "No account? Sign up"}
        </button>
      </div>
    </main>
  );
}
