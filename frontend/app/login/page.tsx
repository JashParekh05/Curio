"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { useAuth } from "@/lib/auth-context";

export default function LoginPage() {
  const router = useRouter();
  const { user, loading } = useAuth();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!loading && user) router.replace("/");
  }, [user, loading, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = email.trim();
    if (!trimmed) return;
    setSubmitting(true);
    setError("");
    const { error: err } = await supabase.auth.signInWithOtp({
      email: trimmed,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
    });
    setSubmitting(false);
    if (err) {
      setError(err.message);
    } else {
      setSent(true);
    }
  }

  if (loading) return null;

  return (
    <main className="min-h-screen bg-black text-white flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-sm space-y-8">
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-bold tracking-tight">LearnReel</h1>
          <p className="text-zinc-400">Sign in to track your progress</p>
        </div>

        {sent ? (
          <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6 text-center space-y-3">
            <p className="text-white font-semibold">Check your inbox</p>
            <p className="text-zinc-400 text-sm">
              We sent a magic link to <span className="text-white">{email}</span>.
              Click it to sign in — no password needed.
            </p>
            <button
              onClick={() => setSent(false)}
              className="text-zinc-500 hover:text-white text-sm transition"
            >
              Use a different email
            </button>
          </div>
        ) : (
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
            <button
              type="submit"
              disabled={submitting || !email.trim()}
              className="w-full bg-white text-black font-semibold py-3 rounded-xl disabled:opacity-40 hover:bg-zinc-100 transition"
            >
              {submitting ? "Sending…" : "Send magic link"}
            </button>
            {error && <p className="text-red-400 text-sm text-center">{error}</p>}
          </form>
        )}
      </div>
    </main>
  );
}
