"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { setUserInterests } from "@/lib/api";

const INTEREST_TAGS = [
  { label: "Science", emoji: "🔬" },
  { label: "History", emoji: "📜" },
  { label: "Math", emoji: "📐" },
  { label: "Technology", emoji: "💻" },
  { label: "Space", emoji: "🚀" },
  { label: "Biology", emoji: "🧬" },
  { label: "Philosophy", emoji: "🧠" },
  { label: "Economics", emoji: "📈" },
  { label: "Engineering", emoji: "⚙️" },
  { label: "Art", emoji: "🎨" },
  { label: "Psychology", emoji: "💭" },
  { label: "Language", emoji: "🗣️" },
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
    <div className="min-h-screen bg-black text-white flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-md space-y-8">
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold">Personalize your feed</h1>
          <p className="text-zinc-400 text-sm">We&apos;ll match content to your level and interests</p>
        </div>

        {/* Grade level */}
        <div className="space-y-3">
          <p className="text-sm font-medium text-zinc-300">What&apos;s your level?</p>
          <div className="grid grid-cols-3 gap-2">
            {GRADE_LEVELS.map(({ label, value }) => (
              <button
                key={value}
                onClick={() => setGrade(value)}
                className={`rounded-2xl px-3 py-3 text-sm font-medium transition active:scale-95 ${
                  grade === value
                    ? "bg-white text-black"
                    : "bg-zinc-900 text-zinc-300 border border-zinc-800 hover:border-zinc-600"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Interests */}
        <div className="space-y-3">
          <p className="text-sm font-medium text-zinc-300">What are you into? <span className="text-zinc-500">(pick 3+)</span></p>
          <div className="grid grid-cols-3 gap-3">
            {INTEREST_TAGS.map(({ label, emoji }) => {
              const active = selected.has(label);
              return (
                <button
                  key={label}
                  onClick={() => toggle(label)}
                  className={`flex flex-col items-center gap-1.5 rounded-2xl px-3 py-4 text-sm font-medium transition active:scale-95 ${
                    active
                      ? "bg-white text-black"
                      : "bg-zinc-900 text-zinc-300 border border-zinc-800 hover:border-zinc-600"
                  }`}
                >
                  <span className="text-2xl">{emoji}</span>
                  <span>{label}</span>
                </button>
              );
            })}
          </div>
        </div>

        {!grade && selected.size >= 3 && (
          <p className="text-center text-zinc-500 text-sm -mt-4">Select your level to continue</p>
        )}
        {grade && selected.size > 0 && selected.size < 3 && (
          <p className="text-center text-zinc-500 text-sm -mt-4">Pick {3 - selected.size} more to continue</p>
        )}

        <button
          onClick={handleContinue}
          disabled={!canContinue || saving}
          className="w-full bg-white text-black font-semibold py-4 rounded-xl text-base disabled:opacity-40 hover:bg-zinc-100 transition"
        >
          {saving ? "Saving…" : "Start learning"}
        </button>

        <button
          onClick={() => router.replace("/")}
          disabled={saving}
          className="w-full text-zinc-500 hover:text-white text-sm py-1 transition"
        >
          Skip for now
        </button>
      </div>
    </div>
  );
}
