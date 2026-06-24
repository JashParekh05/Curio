"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { Session, User } from "@supabase/supabase-js";
import { supabase } from "./supabase";
import { resetGuestProgress } from "./guest-progress";

interface AuthContextValue {
  user: User | null;
  session: Session | null;
  loading: boolean;
  /** True for an anonymous (no-login) guest session. */
  isGuest: boolean;
  /** True only for a real, non-anonymous account. */
  isAuthenticated: boolean;
  /** True when no session could be established at all (anonymous sign-in
   *  failed after retries). The UI should show a recovery affordance instead of
   *  a blank shell. */
  anonFailed: boolean;
  signOut: () => Promise<void>;
  /** Convert the current anonymous guest into a permanent account in place. */
  upgradeAccount: (email: string, password: string) => Promise<{ error: string | null }>;
  /** Sign in to an existing account (switches identity; guest progress not merged). */
  signIn: (email: string, password: string) => Promise<{ error: string | null }>;
  /** Retry establishing a guest session after a failure. */
  retryGuest: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  session: null,
  loading: true,
  isGuest: false,
  isAuthenticated: false,
  anonFailed: false,
  signOut: async () => {},
  upgradeAccount: async () => ({ error: null }),
  signIn: async () => ({ error: null }),
  retryGuest: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [anonFailed, setAnonFailed] = useState(false);
  // Guards against minting two anonymous users under React 18 StrictMode's
  // dev double-mount, and against overlapping retries.
  const anonInFlightRef = useRef(false);

  // Establish an anonymous guest session, retrying a few times before giving
  // up. Returns true on success. On terminal failure it flips `anonFailed` so
  // the UI can surface a retry instead of silently rendering a session-less
  // (not-guest / not-authed) shell.
  const ensureGuest = useCallback(async (): Promise<boolean> => {
    if (anonInFlightRef.current) return false;
    anonInFlightRef.current = true;
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        const { data, error } = await supabase.auth.signInAnonymously();
        if (!error && data.session) {
          setSession(data.session);
          setAnonFailed(false);
          return true;
        }
        if (attempt < 2) await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
      }
      console.warn("[auth] anonymous sign-in failed (enable Anonymous sign-ins in Supabase)");
      setAnonFailed(true);
      return false;
    } finally {
      anonInFlightRef.current = false;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    supabase.auth.getSession().then(async ({ data }) => {
      if (cancelled) return;
      if (data.session) {
        setSession(data.session);
        setLoading(false);
        return;
      }
      // No session — sign in as an anonymous guest so the app is usable with no
      // login. Requires "Anonymous sign-ins" enabled in the Supabase dashboard.
      if (!cancelled) await ensureGuest();
      if (!cancelled) setLoading(false);
    });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, s) => {
      if (!cancelled) setSession(s);
    });

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, [ensureGuest]);

  const user = session?.user ?? null;
  const isGuest = !!user?.is_anonymous;
  const isAuthenticated = !!user && !user.is_anonymous;

  async function signOut() {
    await supabase.auth.signOut();
    resetGuestProgress();
    // Never leave the app session-less: reset to a fresh guest identity (with
    // retry). If it fails, ensureGuest flips anonFailed so the UI can recover.
    setSession(null);
    await ensureGuest();
  }

  async function upgradeAccount(email: string, password: string): Promise<{ error: string | null }> {
    // updateUser keeps the same user_id, so the guest's watch history and taste
    // vectors carry over with no migration; is_anonymous flips to false. We do
    // NOT reset guest progress here — that's only for prepping the NEXT guest on
    // sign-out; wiping it on a successful upgrade would zero the counter for the
    // session the user just earned.
    const { error } = await supabase.auth.updateUser({ email, password });
    if (error) return { error: error.message };
    const { data } = await supabase.auth.getSession();
    if (data.session) setSession(data.session);
    return { error: null };
  }

  async function signIn(email: string, password: string): Promise<{ error: string | null }> {
    // Sign in to an EXISTING account. Unlike upgradeAccount this switches the
    // session to that account's user_id (the guest's progress is not merged).
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) return { error: error.message };
    const { data } = await supabase.auth.getSession();
    if (data.session) setSession(data.session);
    resetGuestProgress();
    return { error: null };
  }

  const retryGuest = useCallback(async () => {
    setLoading(true);
    await ensureGuest();
    setLoading(false);
  }, [ensureGuest]);

  return (
    <AuthContext.Provider
      value={{ user, session, loading, isGuest, isAuthenticated, anonFailed, signOut, upgradeAccount, signIn, retryGuest }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
