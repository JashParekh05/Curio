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

export async function getDiscoverFeed(userId: string, token: string, excludeIds: string[] = []): Promise<DiscoverFeed> {
  // Send already-seen clip ids (capped) so load-more never re-returns clips the
  // user is already scrolling — their telemetry may not be flushed/written yet.
  const exclude = excludeIds.length ? `?exclude=${encodeURIComponent(excludeIds.slice(-200).join(","))}` : "";
  const res = await fetch(`${API_BASE}/api/feed/discover/${encodeURIComponent(userId)}${exclude}`, {
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

// One topic's advisory status. `unlock` is purely advisory — "available" |
// "recommended" | "mastered" — and NEVER blocking. Retained as the optional
// `progressTopics` prop type for PlanPanel's mastery-badge fallback; the
// GET /api/progress route that once populated it was removed during
// decommissioning, so the feed now relies on the lightweight feed-position
// signal and PlanPanel falls back to the quiz-mastery signal.
export interface ProgressTopic {
  mastery_score: number;          // [0,1]
  status: string;                 // not_started | in_progress | mastered
  mastered: boolean;
  unlock: "available" | "recommended" | "mastered";
  level: string | null;
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

// --- adaptive learning game ------------------------------------------------
//
// The Play_Surface (frontend/app/play/page.tsx) drives the adaptive loop over
// HTTP via the Game_Router (`/api/game/*`, Req 13). These three functions mirror
// the backend wire contract in `backend/app/api/game.py` and reuse the same
// `API_BASE = NEXT_PUBLIC_API_URL` + `Authorization: Bearer <token>` pattern as
// the rest of this module (Req 22.6). The shared wire types (ProbeQuestion,
// NodeClip, DecideResponse) live in `lib/game-progress.ts` alongside the
// localStorage codec; they are imported type-only here so this module stays a
// plain (non-client) lib.

import type { ProbeQuestion, NodeClip, DecideResponse } from "./game-progress";

// Response of POST /api/game/session — a started session plus its 6-question
// placement probe (Req 13.3, 2.1).
export interface SessionStartResponse {
  session_id: string;
  goal: string; // the Goal_Node (the entered topic)
  current_node: string; // == goal at start
  probe: ProbeQuestion[]; // exactly 6
}

// Body of POST /api/game/decide — the graded quiz just taken plus the session
// context the stateless backend needs to band + decide (Req 13.4).
export interface DecideGameRequest {
  goal: string;
  current_node: string;
  path: string[]; // nodes already visited (Req 6.3)
  questions: ProbeQuestion[]; // the quiz just taken
  answers: number[]; // chosen option index per question
}

// Response of POST /api/game/node — a node's intuition + clip + checkpoint quiz
// (Req 13.5, 7, 9–11). `clip` is null when no clip is affordable (Req 10.4).
export interface NodeResponse {
  node: string;
  hook: string; // Intuition_Card
  clip: NodeClip | null;
  quiz: ProbeQuestion[]; // 3 validated MCQs
}

// Start a session for a topic and fetch the placement probe (Req 1, 2, 13.3).
// Throws on a non-ok response (e.g. a 502 `probe_generation_failed`) so the
// Play_Surface can surface a retry affordance.
export async function startGameSession(topic: string, token: string): Promise<SessionStartResponse> {
  const res = await fetch(`${API_BASE}/api/game/session`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ topic }),
  });
  if (!res.ok) throw new Error("Failed to start game session");
  return res.json();
}

// Grade the answers server-side, compute the Score_Band in code, and return the
// banded next-step decision from `decide_next` (Req 3–6, 8, 13.4).
export async function decideGame(req: DecideGameRequest, token: string): Promise<DecideResponse> {
  const res = await fetch(`${API_BASE}/api/game/decide`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error("Failed to decide next step");
  return res.json();
}

// Deliver a node: its Intuition_Card hook, a short clip (or null), and a fresh
// 3-question checkpoint quiz (Req 7, 9–11, 13.5).
export async function deliverGameNode(node: string, goal: string, token: string): Promise<NodeResponse> {
  const res = await fetch(`${API_BASE}/api/game/node`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ node, goal }),
  });
  if (!res.ok) throw new Error("Failed to deliver node");
  return res.json();
}

// Fetch the candidate next nodes for learner-chosen branching forks (Req 14.1,
// 14.3) over the additive, read-only `GET /api/game/paths` endpoint. The backend
// returns a `{ candidates: string[] }` envelope — 2–3 valid, band-consistent,
// not-in-`path`, on-goal next nodes the engine could advance to. `path` is sent
// as repeated `path=` query params to match the backend's
// `list[str] = Query(...)` contract; `current_node` is the snake_cased query key.
//
// This is purely best-effort: it returns `[]` on ANY failure — a non-ok status,
// a network error, an unexpected shape, or the endpoint being unavailable — so
// the Play_Surface always falls back to the single engine-chosen `next_node`
// with full backward compatibility (Req 14.3). It never throws.
export async function getGamePaths(
  goal: string,
  currentNode: string,
  path: string[],
  token: string,
): Promise<string[]> {
  try {
    const params = new URLSearchParams({ goal, current_node: currentNode });
    for (const node of path) params.append("path", node);
    const res = await fetch(`${API_BASE}/api/game/paths?${params.toString()}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data?.candidates) ? (data.candidates as string[]) : [];
  } catch {
    return [];
  }
}
