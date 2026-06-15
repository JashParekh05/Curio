"use client";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="fixed inset-0 bg-paper flex flex-col items-center justify-center gap-5 text-ink px-6">
      <p className="text-3xl font-black">Something went wrong</p>
      <button
        onClick={reset}
        className="brutal-btn bg-accent-yellow text-ink text-sm px-6 py-3"
      >
        Try again
      </button>
    </div>
  );
}
