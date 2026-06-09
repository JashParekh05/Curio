"use client";

import { useState } from "react";
import type { ClipQuiz } from "@/lib/api";

interface Props {
  quiz: ClipQuiz;
  onAnswer: (correct: boolean) => void;
  onContinue: () => void;
  onSkip: () => void;
}

export default function QuizCard({ quiz, onAnswer, onContinue, onSkip }: Props) {
  const [selected, setSelected] = useState<number | null>(null);
  const answered = selected !== null;
  const correct = selected === quiz.correct_index;

  return (
    <div className="absolute inset-0 z-30 bg-black/85 backdrop-blur-sm flex items-center justify-center px-5">
      <div className="w-full max-w-sm space-y-4">
        <p className="text-zinc-500 text-xs font-medium tracking-wide uppercase">Quick check</p>
        <p className="text-white font-semibold text-lg leading-snug">{quiz.question}</p>

        <div className="space-y-2">
          {quiz.options.map((option, i) => {
            const isCorrect = i === quiz.correct_index;
            const isSelected = i === selected;
            let style = "bg-zinc-900 border-zinc-800 text-white hover:bg-zinc-800";
            if (answered) {
              if (isCorrect) style = "bg-emerald-500/20 border-emerald-500 text-emerald-300";
              else if (isSelected) style = "bg-red-500/20 border-red-500 text-red-300";
              else style = "bg-zinc-900 border-zinc-800 text-zinc-500";
            }
            return (
              <button
                key={i}
                disabled={answered}
                onClick={() => {
                  setSelected(i);
                  onAnswer(i === quiz.correct_index);
                }}
                className={`w-full text-left border rounded-2xl px-4 py-3 text-sm transition active:scale-[0.98] disabled:cursor-default ${style}`}
              >
                {option}
              </button>
            );
          })}
        </div>

        {answered ? (
          <div className="space-y-3">
            <p className={`text-sm font-medium ${correct ? "text-emerald-400" : "text-red-400"}`}>
              {correct ? "Nice — you got it." : "Not quite."}
            </p>
            {quiz.explanation && (
              <p className="text-zinc-400 text-sm leading-snug">{quiz.explanation}</p>
            )}
            <button
              onClick={onContinue}
              className="w-full bg-white text-black font-semibold px-6 py-3 rounded-2xl text-sm hover:bg-zinc-100 transition active:scale-[0.98]"
            >
              Continue →
            </button>
          </div>
        ) : (
          <button
            onClick={onSkip}
            className="w-full text-zinc-500 text-sm py-1 hover:text-zinc-300 transition"
          >
            Skip
          </button>
        )}
      </div>
    </div>
  );
}
