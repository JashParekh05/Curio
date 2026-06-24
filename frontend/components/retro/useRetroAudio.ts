"use client";

// useRetroAudio — optional chiptune background music + arcade SFX for the Pixel
// Quest Play_Surface (Req 16.1, 16.2, Phase 2).
//
// What it provides
//   - A tiny Web Audio synthesizer (oscillator/gain only — no media assets, so
//     it stays lightweight per the performance constraint Req 20.3) that plays:
//       • a looping square-wave chiptune background track, and
//       • four short SFX: "move", "battle-win", "retreat", "level-up".
//   - The hook reads `audioOn` from `useRetroSettings()` (the existing toggle on
//     RetroThemeProvider, muted by default — Req 16.2). When `audioOn` is false
//     NOTHING is ever scheduled and the AudioContext is left suspended/closed.
//
// Autoplay safety (Req 16.2 — "never autoplay without a user gesture")
//   The browser's own autoplay policy forbids audio before a user gesture, and
//   this module enforces the same contract explicitly rather than relying on it:
//     1. The AudioContext is created LAZILY — never at import or on mount.
//     2. `audioOn` defaults to false and only ever becomes true through the
//        learner pressing the audio toggle, which is itself a user gesture; the
//        flip false→true starts sound from within that gesture's activation
//        window.
//     3. On reload with a PERSISTED `audioOn === true`, the hook does NOT start
//        sound on mount (there has been no gesture yet) — it arms one-shot
//        pointer/key/touch listeners and only begins playback on the first real
//        interaction.
//   So sound is impossible without a deliberate user action in every path.
//
// SSR / test safety: every Web Audio access is guarded (`typeof window`, feature
// detection, try/catch). On a server render or in jsdom (no AudioContext) the
// hook is an inert no-op and never throws.

import { useCallback, useEffect, useRef } from "react";
import { useRetroSettings } from "./RetroThemeProvider";

// The arcade sound effects the quest can trigger (Req 16.1).
export type RetroSfx = "move" | "battle-win" | "retreat" | "level-up";

// What the hook hands back to callers: the current audio flag plus a generic
// `play(sfx)` and named convenience triggers for the gameplay beats.
export interface RetroAudioControls {
  // Mirrors `useRetroSettings().audioOn` for convenience (muted by default).
  audioOn: boolean;
  // Play any SFX by name. A no-op while muted or before a user gesture.
  play: (sfx: RetroSfx) => void;
  // Knight moves one Stage along the trail.
  playMove: () => void;
  // A checkpoint resolved to CLIMB / the Dragon falls.
  playBattleWin: () => void;
  // A DESCEND — framed as a tactical retreat, so this cue is soft, never a buzzer.
  playRetreat: () => void;
  // XP gain crossed a level / rank boundary.
  playLevelUp: () => void;
}

// Resolve the AudioContext constructor across browsers without leaking `any`.
type AudioContextCtor = typeof AudioContext;
function getAudioContextCtor(): AudioContextCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as Window &
    typeof globalThis & { webkitAudioContext?: AudioContextCtor };
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

// A note in a chiptune sequence: frequency in Hz (null = a rest) and a beat
// length multiplier.
interface ChiptuneNote {
  freq: number | null;
  beats: number;
}

// The looping background melody — a simple, cheerful arcade phrase. Frequencies
// are an A-minor-ish pentatonic walk so it loops without feeling jarring.
const CHIPTUNE_SEQUENCE: ChiptuneNote[] = [
  { freq: 440.0, beats: 1 }, // A4
  { freq: 523.25, beats: 1 }, // C5
  { freq: 659.25, beats: 1 }, // E5
  { freq: 587.33, beats: 1 }, // D5
  { freq: 523.25, beats: 1 }, // C5
  { freq: 659.25, beats: 1 }, // E5
  { freq: 783.99, beats: 1 }, // G5
  { freq: null, beats: 1 }, // rest
  { freq: 698.46, beats: 1 }, // F5
  { freq: 587.33, beats: 1 }, // D5
  { freq: 523.25, beats: 1 }, // C5
  { freq: 440.0, beats: 1 }, // A4
  { freq: 493.88, beats: 1 }, // B4
  { freq: 523.25, beats: 1 }, // C5
  { freq: 440.0, beats: 2 }, // A4 (held)
  { freq: null, beats: 1 }, // rest
];

// SFX note sequences (frequency Hz + duration seconds). DESCEND/"retreat" is a
// gentle descending two-tone, deliberately NOT a harsh failure buzzer (Req 5.3).
const SFX_SEQUENCES: Record<RetroSfx, Array<{ freq: number; dur: number }>> = {
  move: [{ freq: 659.25, dur: 0.07 }],
  "battle-win": [
    { freq: 523.25, dur: 0.08 },
    { freq: 659.25, dur: 0.08 },
    { freq: 783.99, dur: 0.08 },
    { freq: 1046.5, dur: 0.16 },
  ],
  retreat: [
    { freq: 392.0, dur: 0.1 },
    { freq: 261.63, dur: 0.16 },
  ],
  "level-up": [
    { freq: 523.25, dur: 0.07 },
    { freq: 659.25, dur: 0.07 },
    { freq: 783.99, dur: 0.07 },
    { freq: 1046.5, dur: 0.07 },
    { freq: 1318.51, dur: 0.18 },
  ],
};

// Tempo for the background loop (seconds per beat) and gain levels. Master gain
// is kept low so the chiptune sits politely under the UI.
const SECONDS_PER_BEAT = 0.18;
const MUSIC_GAIN = 0.05;
const SFX_GAIN = 0.12;
// Lookahead scheduler cadence: how often we wake to queue notes, and how far
// ahead we schedule, in seconds.
const SCHEDULER_INTERVAL_MS = 60;
const SCHEDULE_AHEAD_S = 0.2;

// A self-contained Web Audio engine. One instance is created lazily per hook
// consumer the first time playback is actually permitted (post-gesture).
class RetroAudioEngine {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private schedulerTimer: ReturnType<typeof setInterval> | null = null;
  // Index into CHIPTUNE_SEQUENCE and the absolute context time of the next note.
  private seqIndex = 0;
  private nextNoteTime = 0;

  // Lazily build the AudioContext + master gain. Returns false (inert) when Web
  // Audio is unavailable (SSR/jsdom/unsupported). Never throws.
  private ensure(): boolean {
    if (this.ctx && this.masterGain) return true;
    const Ctor = getAudioContextCtor();
    if (!Ctor) return false;
    try {
      this.ctx = new Ctor();
      this.masterGain = this.ctx.createGain();
      this.masterGain.gain.value = 1;
      this.masterGain.connect(this.ctx.destination);
      return true;
    } catch {
      this.ctx = null;
      this.masterGain = null;
      return false;
    }
  }

  // Resume a suspended context (browsers start contexts suspended until a
  // gesture). Safe to call repeatedly; swallows rejection if still gesture-gated.
  private resume(): void {
    if (!this.ctx) return;
    if (this.ctx.state === "suspended") {
      void this.ctx.resume().catch(() => {});
    }
  }

  // Schedule a single tone (oscillator → short ADSR-ish gain → master).
  private scheduleTone(
    freq: number,
    startAt: number,
    duration: number,
    peakGain: number,
    type: OscillatorType = "square",
  ): void {
    if (!this.ctx || !this.masterGain) return;
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, startAt);

    // Tiny attack/decay envelope to avoid clicks.
    const attack = 0.005;
    gain.gain.setValueAtTime(0.0001, startAt);
    gain.gain.exponentialRampToValueAtTime(peakGain, startAt + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + duration);

    osc.connect(gain);
    gain.connect(this.masterGain);
    osc.start(startAt);
    osc.stop(startAt + duration + 0.02);
  }

  // Lookahead scheduler tick: queue any background notes due within the window.
  private tick = (): void => {
    if (!this.ctx) return;
    while (this.nextNoteTime < this.ctx.currentTime + SCHEDULE_AHEAD_S) {
      const note = CHIPTUNE_SEQUENCE[this.seqIndex];
      const dur = note.beats * SECONDS_PER_BEAT;
      if (note.freq !== null) {
        // Stop a hair early so consecutive notes articulate.
        this.scheduleTone(
          note.freq,
          this.nextNoteTime,
          dur * 0.9,
          MUSIC_GAIN,
          "square",
        );
      }
      this.nextNoteTime += dur;
      this.seqIndex = (this.seqIndex + 1) % CHIPTUNE_SEQUENCE.length;
    }
  };

  // Begin (or no-op if already running) the looping background chiptune. Caller
  // must only invoke this from a gesture-permitted path.
  startMusic(): void {
    if (!this.ensure()) return;
    this.resume();
    if (this.schedulerTimer !== null) return; // already playing
    if (!this.ctx) return;
    this.nextNoteTime = this.ctx.currentTime + 0.1;
    this.tick();
    this.schedulerTimer = setInterval(this.tick, SCHEDULER_INTERVAL_MS);
  }

  // Stop the background loop. SFX already scheduled fade out naturally.
  stopMusic(): void {
    if (this.schedulerTimer !== null) {
      clearInterval(this.schedulerTimer);
      this.schedulerTimer = null;
    }
  }

  // Fire a one-shot SFX. Caller is responsible for the muted/gesture gate; this
  // schedules immediately on the live context.
  playSfx(sfx: RetroSfx): void {
    if (!this.ensure()) return;
    this.resume();
    if (!this.ctx) return;
    const start = this.ctx.currentTime + 0.01;
    let cursor = start;
    for (const step of SFX_SEQUENCES[sfx]) {
      this.scheduleTone(step.freq, cursor, step.dur, SFX_GAIN, "square");
      cursor += step.dur;
    }
  }

  // Tear everything down (stop loop + close context). Used on unmount/disable.
  dispose(): void {
    this.stopMusic();
    if (this.ctx) {
      try {
        void this.ctx.close();
      } catch {
        /* ignore */
      }
    }
    this.ctx = null;
    this.masterGain = null;
    this.seqIndex = 0;
    this.nextNoteTime = 0;
  }
}

export function useRetroAudio(): RetroAudioControls {
  const { audioOn } = useRetroSettings();
  const engineRef = useRef<RetroAudioEngine | null>(null);
  // Tracks the previous audioOn so we can tell a deliberate toggle (false→true,
  // a fresh user gesture) from a persisted-true value present at mount.
  const prevAudioOnRef = useRef<boolean | null>(null);

  // Lazily construct the engine (no Web Audio touched until a play path runs).
  const getEngine = useCallback((): RetroAudioEngine => {
    if (!engineRef.current) engineRef.current = new RetroAudioEngine();
    return engineRef.current;
  }, []);

  // Drive background music + the autoplay-safety gate off `audioOn`.
  useEffect(() => {
    const prev = prevAudioOnRef.current;
    prevAudioOnRef.current = audioOn;

    if (!audioOn) {
      // Muted: ensure nothing is playing. (No context is created here.)
      engineRef.current?.stopMusic();
      return;
    }

    const engine = getEngine();

    // false→true after mount = the learner just pressed the toggle. That click
    // is a fresh user gesture, so it's safe to start sound right away.
    if (prev === false) {
      engine.startMusic();
      return;
    }

    // audioOn was already true at mount (persisted) — there has been NO gesture
    // yet, so we must NOT autoplay. Arm one-shot listeners and start only on the
    // first real interaction (Req 16.2).
    let armed = true;
    const onGesture = () => {
      if (!armed) return;
      armed = false;
      engine.startMusic();
      remove();
    };
    const events: Array<keyof WindowEventMap> = [
      "pointerdown",
      "keydown",
      "touchstart",
    ];
    const remove = () => {
      if (typeof window === "undefined") return;
      for (const ev of events) window.removeEventListener(ev, onGesture);
    };
    if (typeof window !== "undefined") {
      for (const ev of events) {
        window.addEventListener(ev, onGesture, { once: false });
      }
    }
    return () => {
      armed = false;
      remove();
      engineRef.current?.stopMusic();
    };
  }, [audioOn, getEngine]);

  // Dispose the whole engine when the consumer unmounts.
  useEffect(() => {
    return () => {
      engineRef.current?.dispose();
      engineRef.current = null;
    };
  }, []);

  // SFX trigger: a hard no-op while muted, so nothing ever sounds when audio is
  // off. When on, these are only called in response to gameplay events that
  // themselves follow a user gesture.
  const play = useCallback(
    (sfx: RetroSfx) => {
      if (!audioOn) return;
      getEngine().playSfx(sfx);
    },
    [audioOn, getEngine],
  );

  const playMove = useCallback(() => play("move"), [play]);
  const playBattleWin = useCallback(() => play("battle-win"), [play]);
  const playRetreat = useCallback(() => play("retreat"), [play]);
  const playLevelUp = useCallback(() => play("level-up"), [play]);

  return { audioOn, play, playMove, playBattleWin, playRetreat, playLevelUp };
}
