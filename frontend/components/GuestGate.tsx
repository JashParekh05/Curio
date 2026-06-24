"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import {
  getGuestClips,
  isGateDismissed,
  dismissGate,
  isHardGated,
  GUEST_GATE_THRESHOLD,
  GUEST_CLIP_EVENT,
} from "@/lib/guest-progress";
import UpgradeModal from "./UpgradeModal";
import { Button } from "@/components/pop/Button";

// Non-blocking soft signup gate + hard wall. Mounted once at the app root so it
// covers every screen. After a few clips a guest sees a dismissible nudge banner;
// after the hard limit, a non-dismissible modal blocks further watching until they
// create an account. Their progress carries over (same user_id on upgrade).
export default function GuestGate() {
  const { isGuest } = useAuth();
  const [show, setShow] = useState(false);
  const [hardGated, setHardGated] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    if (!isGuest) {
      setShow(false);
      setHardGated(false);
      return;
    }
    const evaluate = () => {
      const clips = getGuestClips();
      setHardGated(isHardGated());
      setShow(!isGateDismissed() && clips >= GUEST_GATE_THRESHOLD);
    };
    evaluate();
    // The clip counter is bumped from telemetry (not React state); this event
    // lets the banner/wall appear the moment a threshold is crossed.
    window.addEventListener(GUEST_CLIP_EVENT, evaluate);
    return () => window.removeEventListener(GUEST_CLIP_EVENT, evaluate);
  }, [isGuest]);

  // Hard wall takes precedence over the dismissible banner.
  const showBanner = isGuest && show && !hardGated && !modalOpen;
  const showWall = isGuest && hardGated;

  return (
    <>
      {showBanner && (
        <div className="fixed bottom-4 inset-x-4 z-40 flex justify-center pointer-events-none">
          <div className="pointer-events-auto flex items-center gap-3 bg-surface text-on-surface rounded-xl2 px-4 py-3 shadow-elev-3 max-w-sm w-full">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-display font-extrabold">Save your progress</p>
              <p className="text-xs text-on-surface-muted font-medium">Create a free account to keep your history.</p>
            </div>
            <Button size="sm" onClick={() => setModalOpen(true)} className="shrink-0">
              Sign up
            </Button>
            <button
              onClick={() => {
                dismissGate();
                setShow(false);
              }}
              aria-label="Dismiss"
              className="text-on-surface-muted hover:text-on-surface text-lg leading-none shrink-0 px-1"
            >
              ✕
            </button>
          </div>
        </div>
      )}
      {/* Kept mounted independent of isGuest so the success screen survives the
          guest→account flip that happens on a successful upgrade. The hard wall
          forces the modal open in non-dismissible "blocking" mode. */}
      <UpgradeModal
        open={modalOpen || showWall}
        blocking={showWall}
        onClose={() => setModalOpen(false)}
      />
    </>
  );
}
