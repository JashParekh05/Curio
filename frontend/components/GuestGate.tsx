"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import {
  getGuestClips,
  isGateDismissed,
  dismissGate,
  GUEST_GATE_THRESHOLD,
  GUEST_CLIP_EVENT,
} from "@/lib/guest-progress";
import UpgradeModal from "./UpgradeModal";

// Non-blocking soft signup gate. Mounted once at the app root so it covers every
// screen. Shows a dismissible banner after a guest has watched enough clips, and
// opens the in-place account upgrade. Watching is never interrupted.
export default function GuestGate() {
  const { isGuest } = useAuth();
  const [show, setShow] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    if (!isGuest) {
      setShow(false);
      return;
    }
    const evaluate = () => setShow(!isGateDismissed() && getGuestClips() >= GUEST_GATE_THRESHOLD);
    evaluate();
    // The clip counter is bumped from telemetry (not React state); this event
    // lets the banner appear the moment the threshold is crossed.
    window.addEventListener(GUEST_CLIP_EVENT, evaluate);
    return () => window.removeEventListener(GUEST_CLIP_EVENT, evaluate);
  }, [isGuest]);

  const showBanner = isGuest && show && !modalOpen;

  return (
    <>
      {showBanner && (
        <div className="fixed bottom-4 inset-x-4 z-40 flex justify-center pointer-events-none">
          <div className="pointer-events-auto flex items-center gap-3 bg-white text-black rounded-2xl px-4 py-3 shadow-2xl max-w-sm w-full">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold">Save your progress</p>
              <p className="text-xs text-zinc-600">Create a free account to keep your history.</p>
            </div>
            <button
              onClick={() => setModalOpen(true)}
              className="bg-black text-white text-sm font-semibold px-3 py-2 rounded-xl hover:bg-zinc-800 transition shrink-0"
            >
              Sign up
            </button>
            <button
              onClick={() => {
                dismissGate();
                setShow(false);
              }}
              aria-label="Dismiss"
              className="text-zinc-400 hover:text-zinc-600 text-lg leading-none shrink-0"
            >
              ✕
            </button>
          </div>
        </div>
      )}
      {/* Kept mounted independent of isGuest so the success screen survives the
          guest→account flip that happens on a successful upgrade. */}
      <UpgradeModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </>
  );
}
