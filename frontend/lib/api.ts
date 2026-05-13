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
}

export interface FeedResponse {
  topic_slug: string;
  clips: Clip[];
  processing: boolean;
}

export async function createLearningPath(
  query: string,
  sessionId?: string
): Promise<LearningPath> {
  const res = await fetch(`${API_BASE}/api/topics/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, session_id: sessionId }),
  });
  if (!res.ok) throw new Error("Failed to create learning path");
  return res.json();
}

export async function getTopicFeed(
  topicSlug: string,
  offset = 0,
  limit = 10
): Promise<FeedResponse> {
  const res = await fetch(
    `${API_BASE}/api/feed/${topicSlug}?offset=${offset}&limit=${limit}`
  );
  if (!res.ok) throw new Error("Failed to fetch feed");
  return res.json();
}

export async function getPathFeed(sessionId: string): Promise<FeedResponse[]> {
  const res = await fetch(`${API_BASE}/api/feed/path/${sessionId}`);
  if (!res.ok) throw new Error("Failed to fetch path feed");
  return res.json();
}
