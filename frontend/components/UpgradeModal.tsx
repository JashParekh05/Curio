"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";

export default function UpgradeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { upgradeAccount } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  if (!open) return null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || !password) return;
    setSubmitting(true);
    setError("");
    const { error: err } = await upgradeAccount(trimmed, password);
    setSubmitting(false);
    if (err) {
      setError(
        /already|registered|exists/i.test(err)
          ? "That email already has an account — sign in from the login page instead."
          : err,
      );
      return;
    }
    setDone(true);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60 backdrop-blur-sm px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm bg-zinc-900 border border-zinc-800 rounded-2xl p-6 space-y-5 mb-4 sm:mb-0"
        onClick={(e) => e.stopPropagation()}
      >
        {done ? (
          <div className="text-center space-y-3">
            <p className="text-white text-lg font-semibold">You&apos;re all set 🎉</p>
            <p className="text-zinc-400 text-sm">Your progress is saved to your new account.</p>
            <button
              onClick={onClose}
              className="w-full bg-white text-black font-semibold py-3 rounded-xl hover:bg-zinc-100 transition"
            >
              Done
            </button>
          </div>
        ) : (
          <>
            <div className="space-y-1">
              <p className="text-white text-lg font-semibold">Save your progress</p>
              <p className="text-zinc-400 text-sm">
                Create a free account to keep your learning history across devices. Your current
                progress carries over.
              </p>
            </div>
            <form onSubmit={handleSubmit} className="space-y-3">
              <input
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
                autoFocus
                className="w-full bg-zinc-950 border border-zinc-700 rounded-xl px-4 py-3 text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-400"
              />
              <input
                type="password"
                placeholder="Create a password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
                className="w-full bg-zinc-950 border border-zinc-700 rounded-xl px-4 py-3 text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-400"
              />
              <button
                type="submit"
                disabled={submitting || !email.trim() || !password}
                className="w-full bg-white text-black font-semibold py-3 rounded-xl disabled:opacity-40 hover:bg-zinc-100 transition"
              >
                {submitting ? "…" : "Create account"}
              </button>
              {error && <p className="text-red-400 text-sm text-center">{error}</p>}
            </form>
            <button onClick={onClose} className="w-full text-zinc-500 hover:text-white text-sm transition">
              Not now
            </button>
          </>
        )}
      </div>
    </div>
  );
}
