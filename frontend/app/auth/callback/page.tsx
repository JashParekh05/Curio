"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

export default function AuthCallback() {
  const router = useRouter();

  useEffect(() => {
    // Supabase exchanges the token from the URL hash automatically on init.
    // Wait for session to settle, then redirect home.
    supabase.auth.getSession().then(() => {
      router.replace("/");
    });
  }, [router]);

  return (
    <div className="fixed inset-0 bg-black flex items-center justify-center">
      <div className="w-10 h-10 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
    </div>
  );
}
