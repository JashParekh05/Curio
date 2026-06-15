"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
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
  signOut: () => Promise<void>;
  /** Convert the current anonymous guest into a permanent account in place. */
  upgradeAccount: (email: string, password: string) => Promise<{ error: string | null }>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  session: null,
  loading: true,
  isGuest: false,
  isAuthenticated: false,
  signOut: async () => {},
  upgradeAccount: async () => ({ error: null }),
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  // Guards against minting two anonymous users under React 18 StrictMode's
  // dev double-mount.
  const anonInFlightRef = useRef(false);

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
      if (!anonInFlightRef.current) {
        anonInFlightRef.current = true;
        const { data: anon, error } = await supabase.auth.signInAnonymously();
        if (error) {
          console.warn("[auth] anonymous sign-in failed (enable Anonymous sign-ins in Supabase):", error.message);
        } else if (!cancelled && anon.session) {
          setSession(anon.session);
        }
        anonInFlightRef.current = false;
      }
      if (!cancelled) setLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, s) => {
      if (!cancelled) setSession(s);
    });

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, []);

  const user = session?.user ?? null;
  const isGuest = !!user?.is_anonymous;
  const isAuthenticated = !!user && !user.is_anonymous;

  async function signOut() {
    await supabase.auth.signOut();
    resetGuestProgress();
    // Never leave the app session-less: signing out just resets to a fresh
    // guest identity so guards and feeds keep working.
    const { data, error } = await supabase.auth.signInAnonymously();
    if (!error) setSession(data.session);
  }

  async function upgradeAccount(email: string, password: string): Promise<{ error: string | null }> {
    // updateUser keeps the same user_id, so the guest's watch history and taste
    // vectors carry over with no migration; is_anonymous flips to false.
    const { error } = await supabase.auth.updateUser({ email, password });
    if (error) return { error: error.message };
    const { data } = await supabase.auth.getSession();
    if (data.session) setSession(data.session);
    resetGuestProgress();
    return { error: null };
  }

  return (
    <AuthContext.Provider value={{ user, session, loading, isGuest, isAuthenticated, signOut, upgradeAccount }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
