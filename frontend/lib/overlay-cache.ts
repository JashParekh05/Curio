import type { Clip } from "./api";

/**
 * Per-clip display data shown over a Player (Req 6.6). `description` mirrors
 * `Clip.description` in `./api` which is `string | null`.
 */
export interface OverlayMetadata {
  title: string;
  description: string | null;
  topicLabel: string;
}

/**
 * In-memory, Map-backed Overlay_Cache keyed by Clip `id`. Scoped to the feed
 * session and not persisted (Req 11.4). Stores only OverlayMetadata and never
 * player instances or DOM nodes.
 */
export type OverlayCache = Map<string, OverlayMetadata>;

/** Create an empty Overlay_Cache. */
export function createOverlayCache(): OverlayCache {
  return new Map();
}

/**
 * Get-after-set: return the most recently stored value for `id`, or `undefined`
 * on a miss so the caller can resolve the metadata (Req 6.4, 6.5, 12.3).
 */
export function getOverlay(cache: OverlayCache, id: string): OverlayMetadata | undefined {
  return cache.get(id);
}

/**
 * Idempotent set: store `meta` under `id`, replacing any prior value (Req 6.1,
 * 6.7). Stores a frozen shallow copy (`Object.freeze({ ...meta })`) so a later
 * caller mutation cannot retro-edit the cached entry, keeping get-after-set
 * equality stable. Setting the same value twice leaves a field-equal value
 * (Req 6.3, 12.4). Returns the same cache reference for chaining.
 */
export function setOverlay(
  cache: OverlayCache,
  id: string,
  meta: OverlayMetadata,
): OverlayCache {
  cache.set(id, Object.freeze({ ...meta }));
  return cache;
}

/** True when the cache has an entry for `id` (Req 6.5). */
export function hasOverlay(cache: OverlayCache, id: string): boolean {
  return cache.has(id);
}

/**
 * Derive a human-readable topic label from a slug: split on '-', Title-Case
 * each word, and join with a space (e.g. "linear-algebra" -> "Linear Algebra").
 */
export function topicLabelFromSlug(slug: string): string {
  return slug
    .split("-")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/**
 * Derive the OverlayMetadata for a Clip given the topic slug it belongs to.
 * Pure: title and description come straight from the Clip, topicLabel is derived
 * from `topicSlug` via `topicLabelFromSlug`.
 */
export function deriveOverlay(clip: Clip, topicSlug: string): OverlayMetadata {
  return {
    title: clip.title,
    description: clip.description,
    topicLabel: topicLabelFromSlug(topicSlug),
  };
}
