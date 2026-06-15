"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { setUserInterests } from "@/lib/api";

const INTEREST_TAGS = [
  "Science",
  "History",
  "Math",
  "Technology",
  "Space",
  "Biology",
  "Philosophy",
  "Economics",
  "Engineering",
  "Art",
  "Psychology",
  "Language",
];

// Rotating accent fills for selected interest tiles.
const TAG_COLORS = ["bg-accent-yellow", "bg-accent-cyan", "bg-accent-lime", "bg-accent-pink", "bg-accent-orange", "bg-accent-purple"];

const GRADE_LEVELS = [
  { label: "Preschool", value: "preschool" },
  { label: "Elementary", value: "elementary" },
  { label: "Middle School", value: "middle_school" },
  { label: "High School", value: "high_school" },
  { label: "College", value: "college" },
  { label: "Professional", value: "professional" },
];

export default function OnboardingPage() {
  const router = useRouter();
  const { user, session } = useAuth();
  const [grade, setGrade] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  function toggle(label: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(label) ? next.delete(label) : next.add(label);
      return next;
    });
  }

  async function handleContinue() {
    if (!user || !session || !grade || selected.size < 3) return;
    setSaving(true);
    try {
      await setUserInterests(user.id, Array.from(selected), session.access_token, grade);
    } catch {
      // best-effort — still proceed to home
    }
    router.replace("/");
  }

  const canContinue = grade !== null && selected.size >= 3;

  return (
    <div className="min-h-screen bg-paper text-ink flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-md space-y-8">
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-black">Personalize your feed</h1>
          <p className="text-ink/70 text-sm font-medium">We&apos;ll match content to your level and interests</p>
        </div>

        {/* Grade level */}
        <div className="space-y-3">
          <p className="text-sm font-black uppercase tracking-wide text-ink/60">What&apos;s your level?</p>
          <div className="grid grid-cols-3 gap-2">
            {GRADE_LEVELS.map(({ label, value }) => (
              <button
                key={value}
                onClick={() => setGrade(value)}
                className={`brutal-btn px-3 py-3 text-sm ${
                  grade === value ? "bg-accent-purple text-white" : "bg-white text-ink"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Interests */}
        <div className="space-y-3">
          <p className="text-sm font-black uppercase tracking-wide text-ink/60">What are you into? <span className="text-ink/40">(pick 3+)</span></p>
          <div className="grid grid-cols-3 gap-3">
            {INTEREST_TAGS.map((label, i) => {
              const active = selected.has(label);
              return (
                <button
                  key={label}
                  onClick={() => toggle(label)}
                  className={`brutal-btn px-3 py-4 text-sm ${
                    active ? `${TAG_COLORS[i % TAG_COLORS.length]} text-ink` : "bg-white text-ink"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        {!grade && selected.size >= 3 && (
          <p className="text-center text-ink/60 text-sm font-bold -mt-4">Select your level to continue</p>
        )}
        {grade && selected.size > 0 && selected.size < 3 && (
          <p className="text-center text-ink/60 text-sm font-bold -mt-4">Pick {3 - selected.size} more to continue</p>
        )}

        <button
          onClick={handleContinue}
          disabled={!canContinue || saving}
          className="brutal-btn w-full bg-accent-yellow text-ink py-4 text-base disabled:opacity-40"
        >
          {saving ? "Saving..." : "Start learning"}
        </button>

        <button
          onClick={() => router.replace("/")}
          disabled={saving}
          className="w-full text-ink/50 hover:text-ink text-sm font-bold py-1 transition"
        >
          Skip for now
        </button>
      </div>
    </div>
  );
}
