"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";

export default function UpgradeModal({
  open,
  onClose,
  blocking = false,
}: {
  open: boolean;
  onClose: () => void;
  /** When true, the modal cannot be dismissed (no backdrop close, no "Not now")
   *  until the account is created. Used for the hard guest gate. */
  blocking?: boolean;
}) {
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
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-ink/60 px-4"
      onClick={blocking ? undefined : onClose}
    >
      <div
        className="brutal w-full max-w-sm bg-paper p-6 space-y-5 mb-4 sm:mb-0 shadow-brutal-lg"
        onClick={(e) => e.stopPropagation()}
      >
        {done ? (
          <div className="text-center space-y-3">
            <p className="text-ink text-lg font-black">You&apos;re all set</p>
            <p className="text-ink/70 text-sm font-medium">Your progress is saved to your new account.</p>
            <button
              onClick={onClose}
              className="brutal-btn w-full bg-accent-lime text-ink py-3"
            >
              {blocking ? "Keep watching" : "Done"}
            </button>
          </div>
        ) : (
          <>
            <div className="space-y-1">
              <p className="text-ink text-lg font-black">
                {blocking ? "Sign up to keep watching" : "Save your progress"}
              </p>
              <p className="text-ink/70 text-sm font-medium">
                {blocking
                  ? "You've hit the free preview limit. Create a free account to keep watching — your progress carries over."
                  : "Create a free account to keep your learning history across devices. Your current progress carries over."}
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
                className="brutal w-full bg-white px-4 py-3 text-ink placeholder-ink/40 font-medium focus:outline-none focus:shadow-brutal"
              />
              <input
                type="password"
                placeholder="Create a password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
                className="brutal w-full bg-white px-4 py-3 text-ink placeholder-ink/40 font-medium focus:outline-none focus:shadow-brutal"
              />
              <button
                type="submit"
                disabled={submitting || !email.trim() || !password}
                className="brutal-btn w-full bg-accent-yellow text-ink py-3 disabled:opacity-40"
              >
                {submitting ? "..." : "Create account"}
              </button>
              {error && <div className="brutal bg-accent-pink text-white text-sm font-bold px-3 py-2 text-center">{error}</div>}
            </form>
            {blocking ? (
              <a
                href="/login"
                className="block w-full text-center text-ink/50 hover:text-ink text-sm font-bold transition"
              >
                Already have an account? Log in
              </a>
            ) : (
              <button onClick={onClose} className="w-full text-ink/50 hover:text-ink text-sm font-bold transition">
                Not now
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
