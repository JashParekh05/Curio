"use client";

import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

// TikTok-style persistent bottom tab bar (Friendly Pop). Mounted once at the
// app root. Shows only inside the app (a signed-in guest or real account) and
// never on the pre-app / immersive-auth / parked routes below.
const HIDDEN_PREFIXES = ["/welcome", "/onboarding", "/login", "/auth", "/play"];

const TABS = [
  {
    href: "/",
    label: "Home",
    // house
    path: "M3 9.5 12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1V9.5Z",
  },
  {
    href: "/discover",
    label: "Discover",
    // compass
    path: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM15 9l-1.8 5.2L8 16l1.8-5.2L15 9Z",
  },
  {
    href: "/learn",
    label: "Learn",
    // graduation cap
    path: "M12 4 2 9l10 5 10-5-10-5ZM18 11.5V16c0 1.7-2.7 3-6 3s-6-1.3-6-3v-4.5",
  },
];

export default function BottomNav() {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useAuth();

  if (!user) return null;
  if (!pathname || HIDDEN_PREFIXES.some((p) => pathname.startsWith(p))) return null;

  // "/" matches exactly; the others match by prefix (so /learn?mode=basic and
  // /feed deep links still light up the right tab).
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <nav className="fixed bottom-0 inset-x-0 z-40 bg-surface/95 backdrop-blur border-t border-outline shadow-elev-3 pb-[env(safe-area-inset-bottom)]">
      <div className="mx-auto max-w-md flex items-stretch justify-around">
        {TABS.map((t) => {
          const active = isActive(t.href);
          const tone = active ? "text-primary" : "text-on-surface-muted";
          return (
            <button
              key={t.href}
              onClick={() => router.push(t.href)}
              aria-label={t.label}
              aria-current={active ? "page" : undefined}
              className="relative flex-1 flex flex-col items-center justify-center gap-0.5 py-2.5 transition active:translate-y-px motion-reduce:active:translate-y-0"
            >
              {active && <span className="absolute top-0 h-0.5 w-8 rounded-pill bg-primary" />}
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                className={`w-6 h-6 transition-colors ${tone}`}
              >
                <path d={t.path} />
              </svg>
              <span className={`text-[11px] font-semibold transition-colors ${tone}`}>{t.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
