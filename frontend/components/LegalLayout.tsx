import Link from "next/link";

/**
 * Shared shell for the static legal pages (Terms, Privacy, Content Policy).
 *
 * It only provides chrome — the brand header, a back link, the
 * "needs legal review" placeholder banner, and a footer of cross-links — so the
 * individual pages supply just their section content. The placeholder banner is
 * intentional and MUST stay until a lawyer has reviewed and replaced the
 * template copy; the page content is NOT legal advice.
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
    <main className="min-h-screen bg-paper text-ink px-4 py-10">
      <div className="w-full max-w-2xl mx-auto space-y-6">
        {/* Brand + back to app */}
        <div className="flex items-center justify-between">
          <Link href="/" className="text-3xl font-black tracking-tight leading-none">
            Curio<span className="text-accent-pink">.</span>
          </Link>
          <Link
            href="/"
            className="brutal-btn bg-white text-ink text-sm px-3 py-2"
          >
            Back to app
          </Link>
        </div>

        {/* Title block */}
        <div className="space-y-1">
          <h1 className="text-4xl font-black leading-tight">{title}</h1>
          <p className="text-ink/60 text-xs font-bold uppercase tracking-wide">
            Last updated: {lastUpdated}
          </p>
        </div>

        {/* Page content */}
        <div className="brutal-card p-6 space-y-6 text-ink/80 text-sm leading-relaxed font-medium">
          {children}
        </div>

        {/* Cross-links */}
        <div className="flex flex-wrap gap-3 pt-2">
          <Link href="/terms" className="brutal-btn bg-accent-cyan text-ink text-xs px-3 py-2">
            Terms of Service
          </Link>
          <Link href="/privacy" className="brutal-btn bg-accent-lime text-ink text-xs px-3 py-2">
            Privacy Policy
          </Link>
          <Link href="/content-policy" className="brutal-btn bg-accent-pink text-white text-xs px-3 py-2">
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
      <h2 className="text-lg font-black text-ink">{heading}</h2>
      <div className="space-y-2">{children}</div>
    </section>
  );
}
