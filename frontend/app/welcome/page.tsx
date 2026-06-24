"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { markIntroSeen } from "@/lib/intro";
import { Button } from "@/components/pop/Button";

type Platform = "ios" | "android" | "desktop";

function detectPlatform(): Platform {
  if (typeof navigator === "undefined") return "desktop";
  const ua = navigator.userAgent || "";
  if (/iphone|ipad|ipod/i.test(ua)) return "ios";
  if (/android/i.test(ua)) return "android";
  return "desktop";
}

interface Slide {
  tag: string;
  tagColor: string;
  title: string;
  body: string;
  // Optional numbered steps rendered as cards.
  steps?: string[];
}

const SLIDES: Slide[] = [
  {
    tag: "WHAT IS THIS",
    tagColor: "bg-primary text-on-primary",
    title: "Learning, as a feed.",
    body: "Curio turns any topic into a feed of short videos — like a scrolling app, but every clip teaches you something.",
  },
  {
    tag: "HOW IT WORKS",
    tagColor: "bg-secondary text-on-secondary",
    title: "Tell us what to learn.",
    body: "Type a topic and we build you a path. Swipe through it. The more you watch, the better it gets at picking what's next.",
    steps: ["Search a topic", "Swipe the video path", "We adapt to your taste"],
  },
  {
    tag: "LEVEL UP",
    tagColor: "bg-success text-white",
    title: "Test yourself, build streaks.",
    body: "Hit Plan in any feed to see your notes, take quick quizzes, rack up a streak, and master topics. It's how learning sticks.",
  },
];

export default function WelcomePage() {
  const router = useRouter();
  const [index, setIndex] = useState(0);
  const [platform, setPlatform] = useState<Platform>("desktop");

  useEffect(() => {
    setPlatform(detectPlatform());
  }, []);

  // The install slide is appended last so platform copy can be tailored.
  const totalSlides = SLIDES.length + 1;
  const isInstallSlide = index === SLIDES.length;
  const isLast = index === totalSlides - 1;

  function finish() {
    markIntroSeen();
    router.replace("/");
  }

  function next() {
    if (isLast) finish();
    else setIndex((i) => i + 1);
  }

  const installSteps =
    platform === "ios"
      ? ["Tap the Share button in your browser bar", "Scroll down and tap \"Add to Home Screen\"", "Tap Add — Curio lands on your home screen"]
      : platform === "android"
      ? ["Tap the menu (three dots) in your browser", "Tap \"Add to Home screen\" or \"Install app\"", "Confirm — Curio lands on your home screen"]
      : ["Open Curio on your phone's browser", "iPhone: Share, then Add to Home Screen", "Android: Menu, then Add to Home screen"];

  return (
    <main className="min-h-screen bg-canvas text-on-surface flex flex-col px-4 py-8">
      <div className="w-full max-w-md mx-auto flex-1 flex flex-col">
        {/* Top bar: brand + skip */}
        <div className="flex items-center justify-between">
          <h1 className="font-display text-2xl font-extrabold tracking-tight">
            Curio<span className="text-primary">.</span>
          </h1>
          <button
            onClick={finish}
            className="text-on-surface-muted hover:text-on-surface text-sm font-semibold transition"
          >
            Skip
          </button>
        </div>

        {/* Progress bars */}
        <div className="flex gap-2 mt-6">
          {Array.from({ length: totalSlides }).map((_, i) => (
            <div
              key={i}
              className={`h-2 flex-1 rounded-pill transition-colors ${
                i <= index ? "bg-primary" : "bg-surface-alt"
              }`}
            />
          ))}
        </div>

        {/* Slide body */}
        <div className="flex-1 flex flex-col justify-center py-8">
          {!isInstallSlide ? (
            <div className="space-y-5">
              <span className={`inline-block rounded-pill ${SLIDES[index].tagColor} text-xs font-bold px-3 py-1`}>
                {SLIDES[index].tag}
              </span>
              <h2 className="font-display text-4xl font-extrabold leading-tight">{SLIDES[index].title}</h2>
              <p className="text-on-surface-muted text-base leading-relaxed">{SLIDES[index].body}</p>
              {SLIDES[index].steps && (
                <div className="space-y-2 pt-2">
                  {SLIDES[index].steps!.map((step, si) => (
                    <div key={step} className="bg-surface rounded-card border border-outline shadow-elev-1 flex items-center gap-3 px-3 py-2.5">
                      <span className="bg-primary text-on-primary rounded-pill w-7 h-7 flex items-center justify-center text-xs font-bold shrink-0">
                        {si + 1}
                      </span>
                      <span className="font-semibold text-sm">{step}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-5">
              <span className="inline-block rounded-pill bg-warning text-on-accent text-xs font-bold px-3 py-1">
                ADD TO HOME SCREEN
              </span>
              <h2 className="font-display text-4xl font-extrabold leading-tight">Use it like a real app.</h2>
              <p className="text-on-surface-muted text-base leading-relaxed">
                Curio runs full-screen, no app store needed. Add it to your home screen so it's one tap away.
              </p>
              <div className="space-y-2 pt-2">
                {installSteps.map((step, si) => (
                  <div key={step} className="bg-surface rounded-card border border-outline shadow-elev-1 flex items-center gap-3 px-3 py-2.5">
                    <span className="bg-primary text-on-primary rounded-pill w-7 h-7 flex items-center justify-center text-xs font-bold shrink-0">
                      {si + 1}
                    </span>
                    <span className="font-semibold text-sm">{step}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Nav */}
        <div className="flex gap-3">
          {index > 0 && (
            <Button variant="soft" size="lg" onClick={() => setIndex((i) => i - 1)}>
              Back
            </Button>
          )}
          <Button size="lg" onClick={next} className="flex-1">
            {isLast ? "Start learning" : "Next"}
          </Button>
        </div>
      </div>
    </main>
  );
}
