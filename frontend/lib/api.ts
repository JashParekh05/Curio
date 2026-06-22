const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface Topic {
  slug: string;
  name: string;
  difficulty: "beginner" | "intermediate" | "advanced";
  prerequisites: string[];
  rationale: string;
}

// One Level of the serialized LeveledPath (Foundations -> Core -> Advanced).
// Returned per FeedResponse item alongside the feed so the client can render the
// Level -> Topic -> Beat stepper. A NULL/absent server projection degrades to a
// single implicit level (legacy single-list behavior).
export interface FeedLevel {
  ordinal: number;
  name: string;
  topic_slugs: string[];
}

// The planned curriculum returned by createLearningPath. `levels` is additive
// and, when present, drives the Level -> Topic -> Beat stepper grouping.
export interface LearningPath {
  session_id: string;
  user_query: string;
  topics: Topic[];
  summary: string;
  familiarity_prompt: string | null;
  suggested_start_index: number;
  // Additive: the serialized LeveledPath when available. Absent on the
  // create-path response today, so page.tsx falls back to the flat topic list.
  levels?: FeedLevel[];
}

export interface Clip {
  id: string;
  topic_slug: string;
  title: string;
  description: string | null;
  video_url: string;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  transcript: string | null;
  source_url: string | null;
  source_platform: string | null;
  hook_score: number;
  final_score: number | null;
  section_index: number | null;
}

export interface TopicSection {
  section_index: number;
  title: string;
  description: string;
  search_query: string;
}

// A soft, always-skippable quiz checkpoint woven into a topic's clip scroll.
// `after_clip_index` points within that topic's served clip list: the card is
// shown right AFTER that clip. `skippable` is always true (the feed never
// blocks). Absent/empty `checkpoints` means the topic renders exactly as before.
export interface Checkpoint {
  stage: "check" | "post";
  after_clip_index: number;
  topic_slug: string;
  section_index: number | null;
  skippable: boolean;
}

export interface FeedResponse {
  topic_slug: string;
  clips: Clip[];
  processing: boolean;
  failed?: boolean;
  checkpoints?: Checkpoint[];
  // Additive (Phase 1): the serialized LeveledPath for the Level -> Topic ->
  // Beat stepper. Each FeedResponse item carries the full LeveledPath; a
  // NULL/absent server projection degrades to a single implicit level.
  levels?: FeedLevel[];
}

function authHeaders(token: string): Record<string, string> {
  return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
}

export async function createLearningPath(
  query: string,
  userId: string,
  token: string,
): Promise<LearningPath> {
  const res = await fetch(`${API_BASE}/api/topics/`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ query, user_id: userId }),
  });
  if (!res.ok) throw new Error("Failed to create learning path");
  return res.json();
}

export interface LearningPathSummary {
  session_id: string;
  user_query: string;
  topic_slugs: string[];
  topic_count: number;
  created_at: string;
}

export async function getTopicSections(slug: string, token: string): Promise<TopicSection[]> {
  const res = await fetch(`${API_BASE}/api/topics/${encodeURIComponent(slug)}/sections`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return [];
  return res.json();
}

export async function getUserHistory(userId: string, token: string): Promise<LearningPathSummary[]> {
  const res = await fetch(`${API_BASE}/api/topics/history/${encodeURIComponent(userId)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return [];
  return res.json();
}

export async function getTopicFeed(
  topicSlug: string,
  token: string,
  offset = 0,
  limit = 10,
): Promise<FeedResponse> {
  const res = await fetch(
    `${API_BASE}/api/feed/${topicSlug}?offset=${offset}&limit=${limit}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!res.ok) throw new Error("Failed to fetch feed");
  return res.json();
}

export async function getPathFeed(sessionId: string, token: string): Promise<FeedResponse[]> {
  const res = await fetch(`${API_BASE}/api/feed/path/${sessionId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error("Failed to fetch path feed");
  return res.json();
}

// Fetch a single clip's current metadata for an on-return refresh of the active
// clip's overlay — no whole-feed refetch. Returns null on 404 so the caller can
// surface the unavailable/skip path (Req 7.6); any other non-ok response throws
// so the caller keeps the existing overlay and the clip stays playable (Req 7.4).
export async function getClipMetadata(clipId: string, token: string): Promise<Clip | null> {
  const res = await fetch(`${API_BASE}/api/feed/clip/${encodeURIComponent(clipId)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("Failed to fetch clip metadata");
  return res.json();
}

export interface TopicRecommendation {
  slug: string;
  name: string;
  difficulty: "beginner" | "intermediate" | "advanced";
  clip_count: number;
  rationale: string;
}

export async function getRecommendations(sessionId: string, token: string): Promise<TopicRecommendation[]> {
  const res = await fetch(`${API_BASE}/api/feed/recommendations/${sessionId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return [];
  return res.json();
}

export interface UserProfile {
  user_id: string;
  interests: string[];
  onboarding_complete: boolean;
}

export async function getUserProfile(userId: string, token: string): Promise<UserProfile> {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/profile`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return { user_id: userId, interests: [], onboarding_complete: false };
  return res.json();
}

export async function setUserInterests(userId: string, interests: string[], token: string, gradeLevel?: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/interests`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ interests, grade_level: gradeLevel }),
  });
  if (!res.ok) throw new Error(`Failed to save interests: ${res.status}`);
}

export interface DiscoverFeed {
  clips: Clip[];
  // True when the library has no level/interest match yet and cold-start
  // seeding is still running in the background — the client should keep polling.
  processing: boolean;
}

export async function getDiscoverFeed(userId: string, token: string): Promise<DiscoverFeed> {
  const res = await fetch(`${API_BASE}/api/feed/discover/${encodeURIComponent(userId)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return { clips: [], processing: false };
  const data = await res.json();
  // The endpoint returns a { clips, processing } envelope. Stay tolerant of an
  // older bare-array response shape just in case a stale backend is deployed.
  if (Array.isArray(data)) return { clips: data, processing: false };
  return { clips: data.clips ?? [], processing: Boolean(data.processing) };
}

export function recordClipEvent(
  clipId: string,
  watchMs: number,
  completed: boolean,
  sessionId: string | null | undefined,
  replayCount: number | undefined,
  feedback: "want_more" | "already_know" | null | undefined,
  token: string,
  // keepalive lets the request complete during page unload / route change so
  // the last clip's event isn't dropped. fetch keepalive keeps the
  // Authorization header (unlike navigator.sendBeacon), so auth is unchanged.
  keepalive = false,
): void {
  fetch(`${API_BASE}/api/feed/${clipId}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      watch_ms: watchMs,
      completed,
      session_id: sessionId ?? null,
      replay_count: replayCount ?? 0,
      feedback: feedback ?? null,
    }),
    keepalive,
  }).catch((err) => console.warn("[recordClipEvent] failed:", err));
}

// --- quiz ------------------------------------------------------------------

export interface QuizQuestion {
  id: string;
  question: string;
  options: string[];
  correct_index: number;
  explanation: string;
}

export interface TopicMastery {
  answered: number;
  correct: number;
  correct_rate: number;
  points: number;
  mastered: boolean;
}

export interface QuizMastery {
  topics: Record<string, TopicMastery>;
  total_points: number;
}

export async function getQuiz(
  topicSlug: string,
  token: string,
  // Optional checkpoint anchor. `stage`/`section_index` select beat-anchored
  // (`check`) or topic-wide (`post`/`pre`) questions. These query params are
  // added server-side in a later phase; until then the backend ignores them and
  // returns the topic-wide questions, so callers degrade gracefully.
  opts?: { stage?: string; sectionIndex?: number | null },
): Promise<QuizQuestion[]> {
  const params = new URLSearchParams();
  if (opts?.stage) params.set("stage", opts.stage);
  if (opts?.sectionIndex != null) params.set("section_index", String(opts.sectionIndex));
  const query = params.toString();
  const url = `${API_BASE}/api/quiz/${encodeURIComponent(topicSlug)}${query ? `?${query}` : ""}`;
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return [];
  return res.json();
}

export async function getQuizMastery(userId: string, token: string): Promise<QuizMastery> {
  const res = await fetch(`${API_BASE}/api/quiz/mastery/${encodeURIComponent(userId)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return { topics: {}, total_points: 0 };
  return res.json();
}

export function recordQuizAnswer(
  questionId: string,
  chosenIndex: number,
  sessionId: string | null | undefined,
  streak: number,
  token: string,
): void {
  // Fire-and-forget — the client already graded and revealed; the server
  // recomputes correctness and records the result off the critical path.
  fetch(`${API_BASE}/api/quiz/${encodeURIComponent(questionId)}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ chosen_index: chosenIndex, session_id: sessionId ?? null, streak }),
  }).catch((err) => console.warn("[recordQuizAnswer] failed:", err));
}

// --- placement -------------------------------------------------------------
//
// A short, optional pre-feed diagnostic. Maps a quick quiz result to a starting
// Content_Level and an entry point on the prerequisite-ordered path, so the
// curriculum can meet the learner at their level. Always skippable; a skip (or
// any failure) defers to the learner's grade_level and enters the feed as today.

// One submitted diagnostic answer. Graded server-side via the existing quiz
// grader (spoof-proof) — the client only collects the chosen option index.
export interface PlacementAnswer {
  question_id: string;
  chosen_index: number;
}

// Body for POST /api/placement/{session_id}. A diagnostic result may be supplied
// three ways (priority order): `skip` -> defer to grade_level; `answers` ->
// graded server-side; `correct`+`total` -> pre-counted. Nothing usable also
// defers to grade_level (a total of 0).
export interface PlacementSubmission {
  skip?: boolean;
  answers?: PlacementAnswer[];
  correct?: number;
  total?: number;
}

// Result of placing the learner on the leveled path. `entry_index` is the index
// of the entry topic in the session's prerequisite-ordered path (null when
// placement isn't possible); `starting_level` is the mapped Content_Level and
// `est_minutes` the estimated time from that entry point onward.
export interface PlacementResult {
  starting_level: string;
  entry_index: number | null;
  est_minutes: number;
}

// Fetch ~5-8 `pre` diagnostic questions for a session's path. Returns [] when
// none are cached yet (the backend self-heals generation in the background) or
// on any failure, so the caller can skip the placement screen entirely and enter
// the feed exactly as before (no regression).
export async function getPlacementQuestions(sessionId: string, token: string): Promise<QuizQuestion[]> {
  const res = await fetch(`${API_BASE}/api/placement/${encodeURIComponent(sessionId)}/questions`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return [];
  return res.json();
}

// Submit the diagnostic (or skip) and place the learner on the leveled path.
// Returns null on failure so the caller can fall back to entering the feed
// unseeded — placement is best-effort and never blocks entry to the feed.
export async function submitPlacement(
  sessionId: string,
  body: PlacementSubmission,
  token: string,
): Promise<PlacementResult | null> {
  try {
    const res = await fetch(`${API_BASE}/api/placement/${encodeURIComponent(sessionId)}`, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

// --- progression, soft gating, remediation (Phase 3) -----------------------
//
// Real mastery-driven progress for the per-level bar + mastery badges, plus the
// soft "rewatch these clips" remediation suggestion. Everything here is advisory
// (Soft_Unlock has no locked/blocking state) and best-effort: any failure
// degrades to a null/empty result so the feed never blocks and the lightweight
// feed-position progress keeps working (no regression).

// One topic's advisory status from GET /api/progress. `unlock` is purely
// advisory — "available" | "recommended" | "mastered" — and NEVER blocking.
export interface ProgressTopic {
  mastery_score: number;          // [0,1]
  status: string;                 // not_started | in_progress | mastered
  mastered: boolean;
  unlock: "available" | "recommended" | "mastered";
  level: string | null;
}

// A topic entry as it appears inside a level (carries its own slug).
export interface ProgressLevelTopic extends ProgressTopic {
  topic_slug: string;
}

// One level's advisory progress. `percent_complete` is bounded [0,100] and
// equals 100 iff every topic in a non-empty level is mastered.
export interface ProgressLevel {
  ordinal: number;
  name: string;
  percent_complete: number;       // [0,100]
  all_mastered: boolean;
  topics: ProgressLevelTopic[];
}

// The learner's full progress projection. `topics` is a flat slug -> status map;
// `levels` carries the per-level bar + per-topic badges for the stepper.
export interface LearnerProgress {
  subject: string;
  levels: ProgressLevel[];
  topics: Record<string, ProgressTopic>;
  total_points: number;
}

// Fetch the learner's per-level progress + per-topic mastery/unlock badges.
// Owner-only on the server (subject must be the caller's own user id). Returns
// null on any failure (incl. the additive Phase 3 table not yet existing), so
// callers fall back to the lightweight feed-position progress with no regression.
export async function getProgress(subject: string, token: string): Promise<LearnerProgress | null> {
  try {
    const res = await fetch(`${API_BASE}/api/progress/${encodeURIComponent(subject)}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

// One already-seen clip the learner is gently nudged to rewatch after a weak
// checkpoint. Mirrors the Remediation_Select core's RewatchClip (clip_id +
// section_index + role_ordinal + final_score); the server enriches it with the
// display fields (title/thumbnail) where available.
export interface RewatchClip {
  clip_id: string;
  section_index: number;          // the weak beat (0..3)
  role_ordinal: number | null;
  final_score: number;
  title?: string | null;
  thumbnail_url?: string | null;
  video_url?: string | null;
}

// Fetch the soft "rewatch these clips" suggestion for a weak beat of a topic.
// `sectionIndex` is the beat the learner was weak on; omit (null) to let the
// server pick the weakest beat. Returns [] on any failure (incl. the seam not
// being wired yet), so the end-card simply shows nothing and never blocks
// advancing. This is purely advisory — it only suggests, never gates.
export async function getRemediation(
  sessionId: string,
  topicSlug: string,
  sectionIndex: number | null,
  token: string,
): Promise<RewatchClip[]> {
  try {
    const params = new URLSearchParams({ topic_slug: topicSlug });
    if (sectionIndex != null) params.set("section_index", String(sectionIndex));
    const res = await fetch(
      `${API_BASE}/api/feed/remediation/${encodeURIComponent(sessionId)}?${params.toString()}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}
