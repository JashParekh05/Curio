"use client";

// Play_Surface — the full-screen Adaptive Learning Game route (Req 21, 22.6).
//
// This page IS the Game_State_Machine (Req 21.1). It owns all session state and
// drives the loop:
//
//   topic-entry → probe → grading → decision → node-delivery → outcome → loop
//
// It reuses `useAuth()` for the guest token and the `lib/api.ts` fetch helpers
// (startGameSession / decideGame / deliverGameNode) which speak to `/api/game/*`
// with `API_BASE = NEXT_PUBLIC_API_URL` and `Authorization: Bearer <token>`
// (Req 22.6). Empty/whitespace topics are rejected before any session starts
// (Req 1.3). Session state is persisted to localStorage via the `game-progress`
// codec on every change (Req 21.2) and restored on reload (Req 21.3); if storage
// is unavailable the session simply continues in memory (Req 21.4).
//
// Presentation is composed from the components built in earlier tasks:
// SkillTreeMap, IntuitionCard, XpHud, OutcomeCard, ClipOverlay, and the QuizCard
// MCQ list. The medieval-pixel-art / TikTok framing lives inside those
// components; this page only orchestrates the flow.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import {
  startGameSession,
  decideGame,
  deliverGameNode,
  type DecideGameRequest,
  type NodeResponse,
} from "@/lib/api";
import {
  persistGameSession,
  restoreGameSession,
  setActiveGameSessionId,
  getActiveGameSessionId,
  clearActiveGameSessionId,
  type GameSessionState,
  type GamePhase,
  type NodeView,
  type ProbeQuestion,
  type DecideResponse,
} from "@/lib/game-progress";
import {
  QuizQuestionList,
  POINTS_PER_CORRECT,
  type AnswerState,
} from "@/components/QuizCard";
import { levelForXp } from "@/components/XpHud";
import type { QuizQuestion } from "@/lib/api";
import SkillTreeMap from "@/components/SkillTreeMap";
import IntuitionCard from "@/components/IntuitionCard";
import XpHud from "@/components/XpHud";
import OutcomeCard from "@/components/OutcomeCard";
import ClipOverlay from "@/components/ClipOverlay";

// ---------------------------------------------------------------------------
// Pure state helpers
// ---------------------------------------------------------------------------

// The sub-beats within the node-delivery phase: the Intuition_Card, then the
// clip, then the checkpoint quiz. Held in local component state (not persisted)
// — a reload restores to the start of the node-delivery beat.
type NodeBeat = "intuition" | "clip" | "quiz";

// Build a fresh session for a started topic. The probe runs on the Goal_Node, so
// the goal is both the goal and the starting current node, and the path begins
// with it (Req 1.1).
function newSession(
  sessionId: string,
  goal: string,
  currentNode: string,
  probe: ProbeQuestion[],
): GameSessionState {
  return {
    version: 1,
    session_id: sessionId,
    goal,
    current_node: currentNode,
    path: [currentNode],
    floor_node: null,
    phase: "probe",
    xp: 0,
    level: 1,
    nodes: {
      [goal]: { node: goal, state: "NEXT", is_goal: true },
    },
    active_probe: probe,
    active_node: null,
    last_decision: null,
    updated_at: Date.now(),
  };
}

// Upsert a node into the discovered map, preserving any existing fields.
function withNode(
  nodes: Record<string, NodeView>,
  name: string,
  patch: Partial<NodeView>,
  goal: string,
): Record<string, NodeView> {
  const prev = nodes[name] ?? {
    node: name,
    state: "LOCKED" as const,
    is_goal: name === goal,
  };
  return { ...nodes, [name]: { ...prev, ...patch, node: name, is_goal: name === goal } };
}

// Apply a banded decision to the session, returning the next session state, the
// node to deliver next (null when the goal is reached), and whether the session
// is finished. This is the deterministic client-side mirror of the loop: it
// marks the current node, advances/descends/holds per band, and sets the
// Floor_Node once on the first CLIMB out of a descent (Req 6.2, 8).
function applyDecision(
  s: GameSessionState,
  d: DecideResponse,
): { state: GameSessionState; deliver: string | null; done: boolean } {
  const cur = s.current_node;
  const base: GameSessionState = { ...s, last_decision: d, updated_at: Date.now() };

  if (d.band === "CLIMB") {
    // The current node is cleared. Reaching the goal ends the session.
    let nodes = withNode(
      s.nodes,
      cur,
      { state: cur === s.goal ? "MASTERED" : "CLEARED" },
      s.goal,
    );
    if (d.reached_goal || cur === s.goal) {
      nodes = withNode(nodes, s.goal, { state: "MASTERED" }, s.goal);
      return { state: { ...base, nodes }, deliver: null, done: true };
    }
    // First CLIMB out of a descent marks the Floor_Node ("You start here").
    const floor_node =
      s.floor_node == null && s.path.length > 1 ? cur : s.floor_node;
    const next = d.next_node ?? cur;
    nodes = withNode(nodes, next, { state: "NEXT" }, s.goal);
    const path = s.path.includes(next) ? s.path : [...s.path, next];
    return {
      state: { ...base, nodes, floor_node, current_node: next, path },
      deliver: next,
      done: false,
    };
  }

  if (d.band === "DESCEND") {
    // Drop to the chosen prerequisite; it becomes the new current node.
    const next = d.next_node ?? cur;
    const nodes = withNode(s.nodes, next, { state: "NEXT" }, s.goal);
    const path = s.path.includes(next) ? s.path : [...s.path, next];
    return {
      state: { ...base, nodes, current_node: next, path },
      deliver: next,
      done: false,
    };
  }

  // REINFORCE — hold on the current node and re-deliver it.
  return { state: { ...base, current_node: cur }, deliver: cur, done: false };
}

// On a reload, transient in-flight phases (grading / outcome) can't be resumed
// faithfully, so coerce them to the nearest resumable phase based on what state
// is present.
function resumablePhase(s: GameSessionState): GamePhase {
  if (s.phase === "grading" || s.phase === "outcome") {
    if (s.last_decision) return "decision";
    if (s.active_node) return "node-delivery";
    if (s.active_probe) return "probe";
    return "topic-entry";
  }
  return s.phase;
}

// ---------------------------------------------------------------------------
// Quiz runner (reuses the QuizCard MCQ list, Req 22.5)
// ---------------------------------------------------------------------------

// Maps the game's ProbeQuestion wire shape onto the QuizCard's QuizQuestion
// shape (which keys answers by a stable `id`).
function toQuizQuestions(questions: ProbeQuestion[]): QuizQuestion[] {
  return questions.map((q, i) => ({
    id: String(i),
    question: q.question,
    options: q.options,
    correct_index: q.correct_index,
    explanation: q.explanation,
  }));
}

function QuizRunner({
  questions,
  heading,
  submitLabel,
  onComplete,
}: {
  questions: ProbeQuestion[];
  heading: string;
  submitLabel: string;
  onComplete: (answers: number[], correctCount: number) => void;
}) {
  const qs = useMemo(() => toQuizQuestions(questions), [questions]);
  const [answers, setAnswers] = useState<Record<string, AnswerState>>({});

  // Reset the answer map whenever a fresh quiz is handed in.
  useEffect(() => {
    setAnswers({});
  }, [questions]);

  function handleAnswer(q: QuizQuestion, choiceIndex: number) {
    setAnswers((prev) =>
      prev[q.id]
        ? prev
        : {
            ...prev,
            [q.id]: { chosen: choiceIndex, correct: choiceIndex === q.correct_index },
          },
    );
  }

  const allAnswered = qs.length > 0 && qs.every((q) => answers[q.id]);

  function submit() {
    const ordered = qs.map((q) => answers[q.id]?.chosen ?? -1);
    const correct = qs.reduce(
      (n, q) => n + (answers[q.id]?.correct ? 1 : 0),
      0,
    );
    onComplete(ordered, correct);
  }

  return (
    <section className="brutal-card bg-paper text-ink px-4 py-4 sm:px-5 sm:py-5">
      <div className="flex items-center gap-2 mb-3">
        <span aria-hidden className="text-lg" style={{ imageRendering: "pixelated" }}>
          ⚔️
        </span>
        <h2 className="text-sm font-black uppercase tracking-widest">{heading}</h2>
      </div>
      <QuizQuestionList questions={qs} answers={answers} onAnswer={handleAnswer} />
      <button
        type="button"
        disabled={!allAnswered}
        onClick={submit}
        className="brutal-btn bg-ink text-paper w-full mt-4 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {submitLabel}
      </button>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Play_Surface
// ---------------------------------------------------------------------------

export default function PlayPage() {
  const { session: authSession } = useAuth();
  const token = authSession?.access_token ?? "";

  const [game, setGame] = useState<GameSessionState | null>(null);
  const [topic, setTopic] = useState("");
  const [topicError, setTopicError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [beat, setBeat] = useState<NodeBeat>("intuition");

  // Keep the latest game state in a ref so persistence + restore effects don't
  // need it in their dependency arrays.
  const gameRef = useRef<GameSessionState | null>(null);
  gameRef.current = game;

  // Prefetched next node: kicked off as soon as a decision arrives (while the
  // learner reads the outcome card), so "Continue" can use the in-flight result
  // instead of waiting on a fresh round-trip. Keyed by the node name so a stale
  // prefetch (e.g. after a different decision) is ignored.
  const prefetchRef = useRef<{ node: string; promise: Promise<NodeResponse> } | null>(null);

  // Restore an in-progress session on mount (Req 21.3). Runs once on the client.
  useEffect(() => {
    const activeId = getActiveGameSessionId();
    if (activeId) {
      const restored = restoreGameSession(activeId);
      if (restored) {
        const phase = resumablePhase(restored);
        if (phase === "node-delivery") setBeat("intuition");
        setGame({ ...restored, phase });
        return;
      }
    }
    // No in-progress session — carry a topic passed from the launcher
    // (`/play?topic=...`) into the entry field. Read from the URL directly to
    // avoid a Suspense boundary requirement around `useSearchParams`.
    try {
      const t = new URLSearchParams(window.location.search).get("topic");
      if (t && t.trim()) setTopic(t);
    } catch {
      // window unavailable (SSR) or malformed URL — leave the entry blank.
    }
  }, []);

  // Persist on every state change (Req 21.2) and keep the active-session pointer
  // current so the next reload can find this session (Req 21.3).
  const commit = useCallback((next: GameSessionState) => {
    const stamped = { ...next, updated_at: Date.now() };
    setGame(stamped);
    setActiveGameSessionId(stamped.session_id);
    persistGameSession(stamped);
  }, []);

  // -- topic entry → probe (Req 1, 2) ---------------------------------------

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = topic.trim();
    // Reject empty/whitespace-only topics and do not start a session (Req 1.3).
    if (!trimmed) {
      setTopicError("Enter a topic to begin.");
      return;
    }
    setTopicError("");
    setError("");
    setBusy(true);
    try {
      const res = await startGameSession(trimmed, token);
      commit(newSession(res.session_id, res.goal, res.current_node, res.probe));
    } catch {
      setError("We couldn't start that quest. Try again.");
    } finally {
      setBusy(false);
    }
  }

  // -- shared: grade a quiz → decide (Req 3–6, 8) ---------------------------

  // Called when either the probe or a node checkpoint quiz is completed. Awards
  // XP for correct answers (Req 20.1), posts to /decide, and shows the outcome.
  const gradeAndDecide = useCallback(
    async (
      s: GameSessionState,
      questions: ProbeQuestion[],
      answers: number[],
      correctCount: number,
      inFlightPhase: GamePhase, // "grading" (probe) | "outcome" (node quiz)
    ) => {
      const xp = s.xp + correctCount * POINTS_PER_CORRECT;
      const grading: GameSessionState = {
        ...s,
        xp,
        level: levelForXp(xp),
        phase: inFlightPhase,
      };
      commit(grading);
      setBusy(true);
      setError("");
      try {
        const req: DecideGameRequest = {
          goal: s.goal,
          current_node: s.current_node,
          path: s.path,
          questions,
          answers,
        };
        const decision = await decideGame(req, token);
        commit({ ...grading, last_decision: decision, phase: "decision" });

        // Prefetch the next node now (during the outcome card) so Continue is
        // instant. applyDecision is pure — we only read the node it would
        // deliver; the actual state transition still happens on Continue.
        const { deliver, done } = applyDecision(
          { ...grading, last_decision: decision },
          decision,
        );
        if (!done && deliver) {
          prefetchRef.current = {
            node: deliver,
            // Swallow rejection here so it doesn't surface as an unhandled
            // promise; Continue re-checks and falls back to a fresh fetch.
            promise: deliverGameNode(deliver, grading.goal, token),
          };
          prefetchRef.current.promise.catch(() => {});
        } else {
          prefetchRef.current = null;
        }
      } catch {
        // Leave the loop where it was so the learner can retry (Req 5.11).
        commit({ ...grading, phase: inFlightPhase === "grading" ? "probe" : "outcome" });
        setError("We couldn't read that result. Try submitting again.");
      } finally {
        setBusy(false);
      }
    },
    [commit, token],
  );

  function handleProbeComplete(answers: number[], correctCount: number) {
    const s = gameRef.current;
    if (!s || !s.active_probe) return;
    gradeAndDecide(s, s.active_probe, answers, correctCount, "grading");
  }

  function handleNodeQuizComplete(answers: number[], correctCount: number) {
    const s = gameRef.current;
    if (!s || !s.active_node?.quiz) return;
    gradeAndDecide(s, s.active_node.quiz, answers, correctCount, "outcome");
  }

  // -- decision → node delivery (Req 7, 9–11) -------------------------------

  // Fetch a node's intuition + clip + quiz and store it as the active node.
  // Shared by the decision→delivery transition and the retry affordance, so a
  // transient delivery hiccup (e.g. a checkpoint that came back short) never
  // hard-blocks the loop — the learner can always re-fetch the node. When a
  // prefetch for this node is in flight (kicked off when the decision arrived),
  // it is awaited instead of starting a fresh round-trip — making Continue feel
  // instant. A prefetch that rejects falls back to a fresh fetch.
  const fetchAndSetNode = useCallback(
    async (deliver: string, base: GameSessionState) => {
      const pending: GameSessionState = { ...base, phase: "node-delivery", active_node: null };
      commit(pending);
      setBeat("intuition");
      setBusy(true);
      setError("");

      // Use a matching in-flight prefetch when available; clear it either way.
      const prefetch = prefetchRef.current;
      prefetchRef.current = null;

      try {
        let node: NodeResponse;
        if (prefetch && prefetch.node === deliver) {
          try {
            node = await prefetch.promise;
          } catch {
            node = await deliverGameNode(deliver, pending.goal, token);
          }
        } else {
          node = await deliverGameNode(deliver, pending.goal, token);
        }
        const view: NodeView = {
          ...(pending.nodes[deliver] ?? {
            node: deliver,
            state: "NEXT",
            is_goal: deliver === pending.goal,
          }),
          hook: node.hook,
          clip: node.clip ?? undefined,
          quiz: node.quiz,
        };
        commit({
          ...pending,
          active_node: view,
          nodes: { ...pending.nodes, [deliver]: view },
        });
      } catch {
        setError("We couldn't load that node. Try again.");
      } finally {
        setBusy(false);
      }
    },
    [commit, token],
  );

  async function handleContinueFromOutcome() {
    const s = gameRef.current;
    if (!s || !s.last_decision) return;
    const { state, deliver, done } = applyDecision(s, s.last_decision);

    if (done || deliver == null) {
      // Goal reached — mark finished and keep the celebratory card up.
      commit({ ...state, phase: "decision" });
      return;
    }

    // Move to node delivery and fetch the node's intuition + clip + quiz.
    await fetchAndSetNode(deliver, state);
  }

  // Re-fetch the current node after a delivery failure or an empty checkpoint,
  // so a transient generation hiccup never strands the learner mid-loop.
  function retryNodeDelivery() {
    const s = gameRef.current;
    if (!s) return;
    fetchAndSetNode(s.current_node, s);
  }

  // Advance the node-delivery sub-beats: intuition → clip (if any) → quiz.
  function advanceFromIntuition() {
    const s = gameRef.current;
    setBeat(s?.active_node?.clip ? "clip" : "quiz");
  }

  function startOver() {
    const s = gameRef.current;
    if (s) clearActiveGameSessionId();
    setGame(null);
    setTopic("");
    setTopicError("");
    setError("");
    setBeat("intuition");
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  // Topic entry (no active session).
  if (!game) {
    return (
      <main className="min-h-screen bg-paper text-ink flex items-center justify-center px-4 py-10">
        <div className="w-full max-w-md">
          <div className="mb-6 text-center">
            <span className="brutal bg-accent-yellow text-ink text-[11px] font-extrabold uppercase tracking-widest px-2 py-1">
              Adaptive Quest
            </span>
            <h1 className="mt-4 text-2xl sm:text-3xl font-black leading-tight">
              What do you want to learn?
            </h1>
            <p className="mt-2 text-ink/70 text-sm font-medium">
              Type any topic. We&apos;ll find exactly where you stand and build the climb.
            </p>
          </div>
          <form onSubmit={handleStart} className="brutal-card bg-white px-4 py-4 sm:px-5 sm:py-5">
            <label htmlFor="topic" className="block text-ink/70 text-[10px] font-black uppercase tracking-widest mb-2">
              Your topic
            </label>
            <input
              id="topic"
              type="text"
              value={topic}
              onChange={(e) => {
                setTopic(e.target.value);
                if (topicError) setTopicError("");
              }}
              placeholder="e.g. backtracking, the French Revolution"
              className="w-full border-2 border-ink rounded-none px-3 py-2 text-sm font-medium focus:outline-none focus:bg-accent-yellow/40"
              autoFocus
            />
            {topicError && (
              <p className="text-accent-pink text-xs font-bold mt-2">{topicError}</p>
            )}
            {error && <p className="text-accent-pink text-xs font-bold mt-2">{error}</p>}
            <button
              type="submit"
              disabled={busy}
              className="brutal-btn bg-ink text-paper w-full mt-4 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {busy ? "Summoning the map…" : "Start the quest"}
            </button>
          </form>
        </div>
      </main>
    );
  }

  const decision = game.last_decision;
  const reachedGoal = !!decision?.reached_goal && game.phase === "decision";

  // Full-screen clip beat during node delivery.
  if (game.phase === "node-delivery" && beat === "clip" && game.active_node?.clip) {
    return (
      <main className="fixed inset-0 bg-black">
        <ClipOverlay
          clip={game.active_node.clip}
          hook={game.active_node.hook}
          node={game.active_node.node}
          onEnded={() => setBeat("quiz")}
          onContinue={() => setBeat("quiz")}
        />
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-paper text-ink px-4 py-6">
      <div className="mx-auto w-full max-w-2xl space-y-5">
        {/* HUD always on top so XP/level reads as the persistent game chrome. */}
        <div className="flex items-center justify-between gap-3">
          <XpHud xp={game.xp} level={game.level} compact />
          <button
            type="button"
            onClick={startOver}
            className="brutal-btn bg-white text-ink text-xs px-3 py-2 shrink-0"
          >
            New quest
          </button>
        </div>

        {/* The map of discovered nodes — always visible as the home surface. */}
        <SkillTreeMap
          nodes={game.nodes}
          goal={game.goal}
          currentNode={game.current_node}
          floorNode={game.floor_node}
          path={game.path}
        />

        {error && (
          <p className="brutal bg-accent-pink text-white text-xs font-bold px-3 py-2">
            {error}
          </p>
        )}

        {/* Probe quiz. */}
        {game.phase === "probe" && game.active_probe && (
          <QuizRunner
            questions={game.active_probe}
            heading={`Placement probe · ${game.goal}`}
            submitLabel={busy ? "Reading the runes…" : "Submit placement"}
            onComplete={handleProbeComplete}
          />
        )}

        {/* In-flight decide. */}
        {(game.phase === "grading" || game.phase === "outcome") && (
          <p className="brutal-card bg-white text-ink px-4 py-4 text-sm font-bold">
            Reading your result…
          </p>
        )}

        {/* Outcome card with the diagnosis (Req 20.4). */}
        {game.phase === "decision" && decision && (
          <>
            <OutcomeCard
              decision={decision}
              onContinue={reachedGoal ? undefined : handleContinueFromOutcome}
            />
            {reachedGoal && (
              <button
                type="button"
                onClick={startOver}
                className="brutal-btn bg-accent-lime text-ink w-full"
              >
                Start a new quest
              </button>
            )}
          </>
        )}

        {/* Node delivery: intuition beat, then (clip handled full-screen above),
            then the checkpoint quiz. */}
        {game.phase === "node-delivery" && game.active_node && (
          <>
            {beat === "intuition" && (
              <>
                <IntuitionCard
                  hook={game.active_node.hook ?? ""}
                  node={game.active_node.node}
                />
                <button
                  type="button"
                  onClick={advanceFromIntuition}
                  className="brutal-btn bg-ink text-paper w-full"
                >
                  {game.active_node.clip ? "Watch the clip" : "Take the checkpoint"}
                </button>
              </>
            )}
            {beat === "quiz" &&
              (game.active_node.quiz && game.active_node.quiz.length > 0 ? (
                <QuizRunner
                  questions={game.active_node.quiz}
                  heading={`Checkpoint · ${game.active_node.node}`}
                  submitLabel={busy ? "Reading the runes…" : "Submit checkpoint"}
                  onComplete={handleNodeQuizComplete}
                />
              ) : (
                // Soft checkpoint: the node came back without a usable quiz.
                // Never hard-block — let the learner re-fetch the node.
                <div className="brutal-card bg-white text-ink px-4 py-4 space-y-3">
                  <p className="text-sm font-bold">
                    We couldn&apos;t build a checkpoint for this node just now.
                  </p>
                  <button
                    type="button"
                    onClick={retryNodeDelivery}
                    disabled={busy}
                    className="brutal-btn bg-ink text-paper w-full disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {busy ? "Retrying…" : "Try again"}
                  </button>
                </div>
              ))}
          </>
        )}

        {/* Node delivery loading (active_node not yet fetched). On a delivery
            failure, surface a retry so the loop is never stranded. */}
        {game.phase === "node-delivery" && !game.active_node && (
          <div className="brutal-card bg-white text-ink px-4 py-4 space-y-3">
            <p className="text-sm font-bold">
              {error ? error : "Loading the next node…"}
            </p>
            {error && !busy && (
              <button
                type="button"
                onClick={retryNodeDelivery}
                className="brutal-btn bg-ink text-paper w-full"
              >
                Try again
              </button>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
