// biomes.ts — the `worldFor(index, total)` biome helper (Req 3.5).
//
// A pure, deterministic function that buckets ordered Stages along the quest
// trail into themed Worlds (biomes) by their position on the journey from the
// learner's starting camp to the Dragon's Keep:
//
//   Candyland → Forest → Caverns → Sky Kingdom → Dragon's Keep
//
// In Phase 1 this is used ONLY for background/biome tinting on the World_Map —
// it carries no backend state and never influences the adaptive engine. Full
// themed Worlds with transition beats are Phase 2 (Req 15); this helper is the
// lightweight, position-only stand-in the design calls for ("tiles are tinted
// by their position band along the trail … a pure function of trail index — no
// new state").
//
// `index` is the Stage's position along the JOURNEY (0 = the start / Candyland,
// `total - 1` = the goal / Dragon's Keep). Callers whose own ordering runs the
// other way (e.g. `WorldMap`, which lists the Dragon's Keep first) convert to
// journey position before calling.
//
// The five biomes map 1:1 onto the `--pq-biome-*` palette tokens already
// defined in `frontend/app/retro.css` (candyland, forest, caverns, sky, keep).

// The themed Worlds, ordered from the start of the journey to the goal. Index in
// this array is the biome's "band" along the trail.
export const BIOME_ORDER = [
  "candyland",
  "forest",
  "caverns",
  "sky",
  "keep",
] as const;

export type Biome = (typeof BIOME_ORDER)[number];

// Human-facing World names for each biome (adventure framing).
const BIOME_LABELS: Record<Biome, string> = {
  candyland: "Candyland",
  forest: "Forest",
  caverns: "Caverns",
  sky: "Sky Kingdom",
  keep: "Dragon's Keep",
};

// The `--pq-biome-*` CSS custom property backing each biome. Kept in lockstep
// with the tokens in `frontend/app/retro.css`.
const BIOME_TOKENS: Record<Biome, string> = {
  candyland: "--pq-biome-candyland",
  forest: "--pq-biome-forest",
  caverns: "--pq-biome-caverns",
  sky: "--pq-biome-sky",
  keep: "--pq-biome-keep",
};

// Display name for a biome (e.g. "Sky Kingdom").
export function biomeLabel(biome: Biome): string {
  return BIOME_LABELS[biome];
}

// `var(--pq-biome-*)` CSS value for a biome, ready to drop into a style prop.
export function biomeColor(biome: Biome): string {
  return `var(${BIOME_TOKENS[biome]})`;
}

// A DISTINCT pixel-art backdrop per themed World (Req 15.1). Where `biomeColor`
// returns a single flat tint, `biomeBackdrop` layers that tint with a
// biome-specific dither/pattern so each World reads as its own place — candy
// dapples, forest canopy stripes, cavern gloom, sky clouds, keep embers — while
// still being built only from the `--pq-biome-*` tokens (no new assets). Pure
// and deterministic: same biome in, same CSS string out. Designed to sit at low
// opacity behind the Stage tiles so the trail stays readable (Req 1.5).
export function biomeBackdrop(biome: Biome): string {
  const base = biomeColor(biome);
  switch (biome) {
    case "candyland":
      // Soft candy dapples — scattered lighter dots over pink.
      return (
        `radial-gradient(circle at 25% 30%, rgba(255,255,255,0.45) 0 6px, transparent 7px),` +
        `radial-gradient(circle at 70% 65%, rgba(255,255,255,0.35) 0 5px, transparent 6px),` +
        `${base}`
      );
    case "forest":
      // Canopy stripes — vertical darker bands like tree trunks.
      return (
        `repeating-linear-gradient(90deg, rgba(26,20,38,0.22) 0 6px, transparent 6px 22px),` +
        `${base}`
      );
    case "caverns":
      // Gloom — a diagonal dark dither that deepens the cavern.
      return (
        `repeating-linear-gradient(45deg, rgba(26,20,38,0.30) 0 4px, transparent 4px 12px),` +
        `${base}`
      );
    case "sky":
      // Drifting clouds — soft horizontal light bands.
      return (
        `repeating-linear-gradient(180deg, rgba(255,255,255,0.30) 0 5px, transparent 5px 20px),` +
        `${base}`
      );
    case "keep":
    default:
      // Ember haze — a hot radial glow over the keep's dark red.
      return (
        `radial-gradient(ellipse at 50% 80%, rgba(255,122,47,0.45) 0%, transparent 60%),` +
        `${base}`
      );
  }
}

// Bucket an ordered Stage into its themed biome by position on the journey.
//
// `index`  — the Stage's 0-based position from the start (0 = Candyland) toward
//            the goal (`total - 1` = Dragon's Keep).
// `total`  — the number of Stages on the trail.
//
// The trail is divided into five equal position bands, one per biome, so the
// first Stage is always Candyland and the final Stage is always the Dragon's
// Keep. The function is total and deterministic: out-of-range indices are
// clamped, and a degenerate trail (`total <= 1`) resolves to the Dragon's Keep
// (the lone Stage IS the goal). It performs no I/O and reads no state, so the
// same inputs always yield the same biome (test-stable, Property-friendly).
export function worldFor(index: number, total: number): Biome {
  const bandCount = BIOME_ORDER.length; // 5

  // Degenerate trails: a single Stage (or fewer) is the goal itself.
  if (!Number.isFinite(total) || total <= 1) {
    return "keep";
  }

  // Clamp the index into [0, total - 1] so callers can't fall off either end.
  const safeIndex = Math.min(Math.max(Math.trunc(index), 0), total - 1);

  // Map the position onto one of `bandCount` equal bands. Using `total - 1` as
  // the denominator guarantees the final Stage lands in the last band (keep)
  // and the first Stage lands in the first band (candyland).
  const band = Math.floor((safeIndex / (total - 1)) * bandCount);
  const clampedBand = Math.min(band, bandCount - 1);

  return BIOME_ORDER[clampedBand];
}
