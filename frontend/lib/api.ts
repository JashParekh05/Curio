const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface Topic {
  slug: string;
  name: string;
  difficulty: "beginner" | "intermediate" | "advanced";
  prerequisites: string[];
  rationale: string;
}

export interface LearningPath {
  session_id: string;
  user_query: string;
  topics: Topic[];
  summary: string;
  familiarity_prompt: string | null;
  suggested_start_index: number;
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

export interface FeedResponse {
  topic_slug: string;
  clips: Clip[];
  processing: boolean;
  failed?: boolean;
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

export async function getQuiz(topicSlug: string, token: string): Promise<QuizQuestion[]> {
  const res = await fetch(`${API_BASE}/api/quiz/${encodeURIComponent(topicSlug)}`, {
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
