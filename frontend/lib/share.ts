/**
 * Share a URL via the native share sheet when available (mobile), falling back
 * to copying the link to the clipboard. Returns what happened so the caller can
 * show appropriate feedback.
 */
export async function shareOrCopy(
  url: string,
  title: string,
): Promise<"shared" | "copied" | "failed"> {
  if (typeof navigator !== "undefined" && navigator.share) {
    try {
      await navigator.share({ title, url });
      return "shared";
    } catch (e) {
      // User dismissed the share sheet — treat as a no-op, don't also copy.
      if ((e as Error)?.name === "AbortError") return "shared";
      // Any other share failure: fall through to clipboard.
    }
  }
  try {
    await navigator.clipboard.writeText(url);
    return "copied";
  } catch {
    return "failed";
  }
}

/** Absolute URL for a topic deep link, optionally landing on a specific clip. */
export function topicShareUrl(topicSlug: string, clipId?: string | null): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const clip = clipId ? `&clip=${encodeURIComponent(clipId)}` : "";
  return `${origin}/feed?topic=${encodeURIComponent(topicSlug)}${clip}`;
}
