import os
import json
import uuid
import anthropic
from app.models.schemas import Topic, LearningPath

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


SYSTEM_PROMPT = """You are a curriculum designer for an educational short-form video platform.
When a user describes what they want to learn, you:
1. Extract specific topics from their query
2. Order them from foundational to advanced (prerequisites first)
3. Assign difficulty levels
4. Return a structured JSON learning path

Always return valid JSON matching the schema exactly. Slugs must be lowercase with hyphens (e.g. "binary-search-trees").
"""

TOPIC_SCHEMA = """
{
  "summary": "one sentence describing the learning path",
  "topics": [
    {
      "slug": "topic-slug",
      "name": "Human Readable Name",
      "difficulty": "beginner|intermediate|advanced",
      "prerequisites": ["slug-of-prereq"],
      "rationale": "why this topic is ordered here"
    }
  ]
}
"""


def parse_learning_path(query: str, session_id: str | None = None) -> LearningPath:
    client = get_client()
    sid = session_id or str(uuid.uuid4())

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"User wants to learn: {query}\n\nReturn JSON matching this schema:\n{TOPIC_SCHEMA}",
            }
        ],
    )

    raw = response.content[0].text
    # Strip markdown code fences if present
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    data = json.loads(raw.strip())

    topics = [Topic(**t) for t in data["topics"]]
    return LearningPath(
        session_id=sid,
        user_query=query,
        topics=topics,
        summary=data["summary"],
    )
