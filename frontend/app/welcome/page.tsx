"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { markIntroSeen } from "@/lib/intro";

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
  // Optional numbered steps rendered as boxes.
  steps?: string[];
}

const SLIDES: Slide[] = [
  {
    tag: "WHAT IS THIS",
    tagColor: "bg-accent-pink text-white",
    title: "Learning, as a feed.",
    body: "Curio turns any topic into a feed of short videos — like a scrolling app, but every clip teaches you something.",
  },
  {
    tag: "HOW IT WORKS",
    tagColor: "bg-accent-cyan text-ink",
    title: "Tell us what to learn.",
    body: "Type a topic and we build you a path. Swipe through it. The more you watch, the better it gets at picking what's next.",
    steps: ["Search a topic", "Swipe the video path", "We adapt to your taste"],
  },
  {
    tag: "LEVEL UP",
    tagColor: "bg-accent-lime text-ink",
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
    <main className="min-h-screen bg-paper text-ink flex flex-col px-4 py-8">
      <div className="w-full max-w-md mx-auto flex-1 flex flex-col">
        {/* Top bar: brand + skip */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-black tracking-tight">
            Curio<span className="text-accent-pink">.</span>
          </h1>
          <button
            onClick={finish}
            className="text-ink/50 hover:text-ink text-sm font-bold transition"
          >
            Skip
          </button>
        </div>

        {/* Progress dots — square, brutalist */}
        <div className="flex gap-2 mt-6">
          {Array.from({ length: totalSlides }).map((_, i) => (
            <div
              key={i}
              className={`h-2 flex-1 border-2 border-ink transition-colors ${
                i <= index ? "bg-ink" : "bg-white"
              }`}
            />
          ))}
        </div>

        {/* Slide body */}
        <div className="flex-1 flex flex-col justify-center py-8">
          {!isInstallSlide ? (
            <div className="space-y-5">
              <span className={`inline-block brutal ${SLIDES[index].tagColor} text-xs font-black px-2 py-1`}>
                {SLIDES[index].tag}
              </span>
              <h2 className="text-4xl font-black leading-tight">{SLIDES[index].title}</h2>
              <p className="text-ink/70 text-base font-medium leading-relaxed">{SLIDES[index].body}</p>
              {SLIDES[index].steps && (
                <div className="space-y-2 pt-2">
                  {SLIDES[index].steps!.map((step, si) => (
                    <div key={step} className="brutal bg-white flex items-center gap-3 px-3 py-2.5">
                      <span className="bg-accent-yellow brutal w-7 h-7 flex items-center justify-center text-xs font-black shrink-0">
                        {si + 1}
                      </span>
                      <span className="font-bold text-sm">{step}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-5">
              <span className="inline-block brutal bg-accent-orange text-ink text-xs font-black px-2 py-1">
                ADD TO HOME SCREEN
              </span>
              <h2 className="text-4xl font-black leading-tight">Use it like a real app.</h2>
              <p className="text-ink/70 text-base font-medium leading-relaxed">
                Curio runs full-screen, no app store needed. Add it to your home screen so it's one tap away.
              </p>
              <div className="space-y-2 pt-2">
                {installSteps.map((step, si) => (
                  <div key={step} className="brutal bg-white flex items-center gap-3 px-3 py-2.5">
                    <span className="bg-accent-purple text-white brutal w-7 h-7 flex items-center justify-center text-xs font-black shrink-0">
                      {si + 1}
                    </span>
                    <span className="font-bold text-sm">{step}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Nav */}
        <div className="flex gap-3">
          {index > 0 && (
            <button
              onClick={() => setIndex((i) => i - 1)}
              className="brutal-btn bg-white text-ink px-5 py-3 text-sm"
            >
              Back
            </button>
          )}
          <button
            onClick={next}
            className="brutal-btn flex-1 bg-accent-yellow text-ink py-3 text-base"
          >
            {isLast ? "Start learning" : "Next"}
          </button>
        </div>
      </div>
    </main>
  );
}
