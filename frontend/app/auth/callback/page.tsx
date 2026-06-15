"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { getUserProfile } from "@/lib/api";

export default function AuthCallback() {
  const router = useRouter();

  useEffect(() => {
    supabase.auth.getSession().then(async ({ data }) => {
      const userId = data.session?.user?.id;
      const token = data.session?.access_token;
      if (!userId || !token) {
        router.replace("/");
        return;
      }
      const profile = await getUserProfile(userId, token);
      router.replace(profile.onboarding_complete ? "/" : "/onboarding");
    });
  }, [router]);

  return (
    <div className="fixed inset-0 bg-paper flex items-center justify-center">
      <div className="w-10 h-10 border-[3px] border-ink border-t-accent-pink rounded-full animate-spin" />
    </div>
  );
}
