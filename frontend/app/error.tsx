"use client";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="fixed inset-0 bg-canvas flex flex-col items-center justify-center gap-5 text-on-surface px-6">
      <p className="font-display text-3xl font-extrabold">Something went wrong</p>
      <button
        onClick={reset}
        className="rounded-pill bg-primary text-on-primary text-sm font-semibold px-6 py-3 shadow-elev-1 transition hover:brightness-[1.03]"
      >
        Try again
      </button>
    </div>
  );
}
