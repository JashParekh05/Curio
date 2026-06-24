// Component tests for `ClipCutscene` — the retro reskin of `ClipOverlay` that
// wraps the reused `ReelPlayer` in a pixel "vision/cutscene" frame (Task 4.3,
// Req 6.2, 6.3).
//
// Pinned behaviors:
//   - With a playable clip, the cutscene mounts the reused `ReelPlayer` with the
//     clip mapped through `nodeClipToClip` (id ← video_id, video_url passed
//     through), and always shows a soft "Continue" affordance (Req 6.2, 6.4).
//   - With an absent or empty clip, the cutscene NEVER renders a broken player:
//     it shows the static fallback panel instead, while Continue stays present
//     so the beat never hard-blocks the learner (Req 6.3, 6.4).
//
// `ClipCutscene` reads `useRetroSettings()`, so every render is wrapped in
// `RetroThemeProvider`. `ReelPlayer` (which mounts a YouTube iframe) is mocked
// so the test can assert the props it receives without real embed machinery.

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import type { NodeClip } from "@/lib/game-progress";
import ClipCutscene from "./ClipCutscene";
import { RetroThemeProvider } from "./RetroThemeProvider";

// Capture the props `ReelPlayer` is mounted with. `vi.hoisted` makes the spy
// available inside the hoisted `vi.mock` factory.
const { reelPlayerSpy } = vi.hoisted(() => ({ reelPlayerSpy: vi.fn() }));

vi.mock("@/components/ReelPlayer", () => ({
  default: (props: Record<string, unknown>) => {
    reelPlayerSpy(props);
    return <div data-testid="reel-player">mock reel player</div>;
  },
}));

afterEach(() => {
  cleanup();
  reelPlayerSpy.mockClear();
});

// A representative `NodeClip` (the `/api/game/node` wire shape). `video_url` is
// already the canonical `youtube.com/embed/...` form.
function makeClip(overrides: Partial<NodeClip> = {}): NodeClip {
  return {
    video_id: "abc123",
    video_url: "https://www.youtube.com/embed/abc123",
    title: "Hash Maps in 3 minutes",
    channel_title: "RetroCS",
    duration_seconds: 180,
    has_caption: true,
    view_count: 4200,
    thumbnail_url: "https://img.example/abc123.jpg",
    description: "A quick tour of hash maps.",
    ...overrides,
  };
}

function renderCutscene(ui: React.ReactElement) {
  return render(<RetroThemeProvider>{ui}</RetroThemeProvider>);
}

describe("ClipCutscene with a playable clip (Req 6.2)", () => {
  it("mounts the reused ReelPlayer with the clip mapped via nodeClipToClip", () => {
    const clip = makeClip();
    const onEnded = vi.fn();

    renderCutscene(<ClipCutscene clip={clip} onEnded={onEnded} />);

    expect(screen.getByTestId("reel-player")).toBeInTheDocument();
    expect(reelPlayerSpy).toHaveBeenCalledTimes(1);

    // The mapped clip uses video_id as the id and passes video_url through.
    const props = reelPlayerSpy.mock.calls[0][0];
    expect(props.clip).toMatchObject({
      id: clip.video_id,
      video_url: clip.video_url,
      title: clip.title,
    });
  });

  it("shows an always-present Continue button that advances the flow", () => {
    const onContinue = vi.fn();

    renderCutscene(
      <ClipCutscene clip={makeClip()} onEnded={vi.fn()} onContinue={onContinue} />,
    );

    const button = screen.getByRole("button", { name: /continue/i });
    expect(button).toBeInTheDocument();

    fireEvent.click(button);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("renders the hook as the caption beat over the vision", () => {
    const hook = "A hash map is a coat check for your data.";

    renderCutscene(
      <ClipCutscene clip={makeClip()} hook={hook} node="Hash Maps" onEnded={vi.fn()} />,
    );

    expect(screen.getByText(hook)).toBeInTheDocument();
  });
});

describe("ClipCutscene with an absent/empty clip (Req 6.3)", () => {
  it("never mounts a broken player and shows the static fallback panel", () => {
    const clip = makeClip({ video_url: "   " });

    renderCutscene(<ClipCutscene clip={clip} onEnded={vi.fn()} />);

    // The reused player is NOT mounted when there is nothing playable.
    expect(reelPlayerSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId("reel-player")).not.toBeInTheDocument();

    // The static fallback panel renders instead.
    expect(screen.getByText(/the vision is clouded/i)).toBeInTheDocument();
  });

  it("keeps Continue present even when the clip is unplayable", () => {
    const onEnded = vi.fn();
    const clip = makeClip({ video_url: "" });

    renderCutscene(<ClipCutscene clip={clip} onEnded={onEnded} />);

    const button = screen.getByRole("button", { name: /continue/i });
    expect(button).toBeInTheDocument();

    // With no onContinue, Continue falls back to onEnded (soft advance).
    fireEvent.click(button);
    expect(onEnded).toHaveBeenCalledTimes(1);
  });
});
