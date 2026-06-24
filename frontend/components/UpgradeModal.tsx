"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/pop/Button";
import { Input } from "@/components/pop/Input";

export default function UpgradeModal({
  open,
  onClose,
  blocking = false,
}: {
  open: boolean;
  onClose: () => void;
  /** When true, the modal cannot be dismissed (no backdrop close, no "Not now")
   *  until the learner creates an account or signs in. Used for the guest gate. */
  blocking?: boolean;
}) {
  const { upgradeAccount, signIn } = useAuth();
  const [mode, setMode] = useState<"signup" | "signin">("signup");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  if (!open) return null;

  function switchMode(next: "signup" | "signin") {
    setMode(next);
    setError("");
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || !password) return;
    setSubmitting(true);
    setError("");
    const { error: err } =
      mode === "signup"
        ? await upgradeAccount(trimmed, password)
        : await signIn(trimmed, password);
    setSubmitting(false);
    if (err) {
      if (mode === "signup" && /already|registered|exists/i.test(err)) {
        // Existing account — flip to sign-in right here (no trip to /login).
        setMode("signin");
        setError("That email already has an account. Sign in below.");
      } else if (mode === "signin") {
        setError("Couldn't sign in. Check your email and password.");
      } else {
        setError(err);
      }
      return;
    }
    if (mode === "signup") {
      setDone(true);
    } else {
      // Signed into an existing account — the auth-state change resolves the gate.
      onClose();
    }
  }

  const title =
    mode === "signin"
      ? "Welcome back"
      : blocking
        ? "Sign up to keep watching"
        : "Save your progress";
  const subtitle =
    mode === "signin"
      ? "Sign in to your account."
      : blocking
        ? "You've hit the free preview limit. Create a free account to keep watching. Your progress carries over."
        : "Create a free account to keep your history across devices. Your progress carries over.";

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 px-4"
      onClick={blocking ? undefined : onClose}
    >
      <div
        className="w-full max-w-sm bg-surface text-on-surface rounded-xl2 p-6 space-y-5 mb-4 sm:mb-0 shadow-elev-3"
        onClick={(e) => e.stopPropagation()}
      >
        {done ? (
          <div className="text-center space-y-3">
            <p className="font-display text-xl font-extrabold">You&apos;re all set</p>
            <p className="text-on-surface-muted text-sm">Your progress is saved to your new account.</p>
            <Button onClick={onClose} className="w-full">
              {blocking ? "Keep watching" : "Done"}
            </Button>
          </div>
        ) : (
          <>
            <div className="space-y-1">
              <p className="font-display text-xl font-extrabold">{title}</p>
              <p className="text-on-surface-muted text-sm">{subtitle}</p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-3">
              <Input
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={submitting}
                autoFocus
              />
              <Input
                type="password"
                placeholder={mode === "signup" ? "Create a password" : "Password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={submitting}
              />
              <Button type="submit" disabled={submitting || !email.trim() || !password} className="w-full">
                {submitting ? "…" : mode === "signup" ? "Create account" : "Sign in"}
              </Button>
              {error && <p className="text-danger text-sm font-medium text-center">{error}</p>}
            </form>

            <button
              onClick={() => switchMode(mode === "signup" ? "signin" : "signup")}
              className="block w-full text-center text-on-surface-muted hover:text-on-surface text-sm font-medium transition"
            >
              {mode === "signup" ? "Already have an account? Sign in" : "Need an account? Create one"}
            </button>

            {!blocking && (
              <button
                onClick={onClose}
                className="block w-full text-center text-on-surface-muted hover:text-on-surface text-xs font-medium transition"
              >
                Not now
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
