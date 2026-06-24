"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { setUserInterests } from "@/lib/api";
import { Button } from "@/components/pop/Button";

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
    <div className="min-h-screen bg-canvas text-on-surface flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-md space-y-8">
        <div className="text-center space-y-2">
          <h1 className="font-display text-4xl font-extrabold">Personalize your feed</h1>
          <p className="text-on-surface-muted text-sm">We&apos;ll match content to your level and interests</p>
        </div>

        {/* Grade level */}
        <div className="space-y-3">
          <p className="text-sm font-bold uppercase tracking-wide text-on-surface-muted">What&apos;s your level?</p>
          <div className="grid grid-cols-3 gap-2">
            {GRADE_LEVELS.map(({ label, value }) => (
              <button
                key={value}
                onClick={() => setGrade(value)}
                className={`rounded-control border px-3 py-3 text-sm font-semibold transition duration-base ${
                  grade === value
                    ? "bg-primary text-on-primary border-primary shadow-elev-1"
                    : "bg-surface text-on-surface border-outline hover:brightness-95"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Interests */}
        <div className="space-y-3">
          <p className="text-sm font-bold uppercase tracking-wide text-on-surface-muted">
            What are you into? <span className="text-on-surface-muted/70">(pick 3+)</span>
          </p>
          <div className="grid grid-cols-3 gap-3">
            {INTEREST_TAGS.map((label) => {
              const active = selected.has(label);
              return (
                <button
                  key={label}
                  onClick={() => toggle(label)}
                  className={`rounded-control border px-3 py-4 text-sm font-semibold transition duration-base ${
                    active
                      ? "bg-primary text-on-primary border-primary shadow-elev-1"
                      : "bg-surface text-on-surface border-outline hover:brightness-95"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        {!grade && selected.size >= 3 && (
          <p className="text-center text-on-surface-muted text-sm font-medium -mt-4">Select your level to continue</p>
        )}
        {grade && selected.size > 0 && selected.size < 3 && (
          <p className="text-center text-on-surface-muted text-sm font-medium -mt-4">Pick {3 - selected.size} more to continue</p>
        )}

        <Button size="lg" onClick={handleContinue} disabled={!canContinue || saving} className="w-full">
          {saving ? "Saving..." : "Start learning"}
        </Button>

        <button
          onClick={() => router.replace("/")}
          disabled={saving}
          className="w-full text-on-surface-muted hover:text-on-surface text-sm font-semibold py-1 transition"
        >
          Skip for now
        </button>
      </div>
    </div>
  );
}
