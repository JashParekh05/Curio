import Link from "next/link";

/**
 * Small, visually-quiet footer linking to the static legal pages. Drop it at
 * the bottom of entry screens (home, login) so Terms / Privacy / Content Policy
 * are always reachable without competing with primary actions.
 */
export default function LegalFooter() {
  return (
    <footer className="w-full max-w-xl mx-auto pt-8 pb-2">
      <div className="flex items-center justify-center gap-4 text-on-surface-muted text-xs font-semibold">
        <Link href="/terms" className="hover:text-on-surface transition">
          Terms
        </Link>
        <span aria-hidden>·</span>
        <Link href="/privacy" className="hover:text-on-surface transition">
          Privacy
        </Link>
        <span aria-hidden>·</span>
        <Link href="/content-policy" className="hover:text-on-surface transition">
          Content Policy
        </Link>
      </div>
    </footer>
  );
}
