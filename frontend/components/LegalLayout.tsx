import Link from "next/link";

/**
 * Shared shell for the static legal pages (Terms, Privacy, Content Policy).
 *
 * It only provides chrome: the brand header, a back link, and a footer of
 * cross-links, so the individual pages supply just their section content. The
 * page content is NOT legal advice.
 */
export default function LegalLayout({
  title,
  lastUpdated,
  children,
}: {
  title: string;
  lastUpdated: string;
  children: React.ReactNode;
}) {
  return (
    <main className="min-h-screen bg-canvas text-on-surface px-4 py-10">
      <div className="w-full max-w-2xl mx-auto space-y-6">
        {/* Brand + back to app */}
        <div className="flex items-center justify-between">
          <Link href="/" className="font-display text-3xl font-extrabold tracking-tight leading-none">
            Curio<span className="text-primary">.</span>
          </Link>
          <Link
            href="/"
            className="rounded-pill bg-surface-alt text-on-surface border border-outline text-sm font-semibold px-4 py-2 shadow-elev-1 transition hover:brightness-95"
          >
            Back to app
          </Link>
        </div>

        {/* Title block */}
        <div className="space-y-1">
          <h1 className="font-display text-4xl font-extrabold leading-tight">{title}</h1>
          <p className="text-on-surface-muted text-xs font-bold uppercase tracking-wide">
            Last updated: {lastUpdated}
          </p>
        </div>

        {/* Page content */}
        <div className="bg-surface rounded-card border border-outline shadow-elev-1 p-6 space-y-6 text-on-surface-muted text-sm leading-relaxed">
          {children}
        </div>

        {/* Cross-links */}
        <div className="flex flex-wrap gap-3 pt-2">
          <Link href="/terms" className="rounded-pill bg-surface-alt text-on-surface border border-outline text-xs font-semibold px-3.5 py-2 transition hover:brightness-95">
            Terms of Service
          </Link>
          <Link href="/privacy" className="rounded-pill bg-surface-alt text-on-surface border border-outline text-xs font-semibold px-3.5 py-2 transition hover:brightness-95">
            Privacy Policy
          </Link>
          <Link href="/content-policy" className="rounded-pill bg-surface-alt text-on-surface border border-outline text-xs font-semibold px-3.5 py-2 transition hover:brightness-95">
            Content Policy
          </Link>
        </div>
      </div>
    </main>
  );
}

/** A titled section block, used by each legal page for consistent headings. */
export function LegalSection({
  heading,
  children,
}: {
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h2 className="font-display text-lg font-extrabold text-on-surface">{heading}</h2>
      <div className="space-y-2">{children}</div>
    </section>
  );
}
