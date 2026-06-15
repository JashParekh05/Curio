"""Generate and store 4-section teaching plans per topic.

Each topic gets four ordered sections: hook → what-is-it → how-it-works → outcomes.

Rather than one monolithic LLM call, planning is decomposed into stages so each
gets focused attention, with an LLM-as-judge gating the result:

    outline → detail → judge → (revise weak sections → judge)* → store

The judge scores every section on role fit, title specificity, and — most
importantly for downstream clip quality — how specific each YouTube search_query
is. Only sections the judge flags are regenerated, up to MAX_REVISIONS rounds.
Every stage degrades gracefully: any failure falls back to the best result so
far (ultimately the static defaults), so content generation is never blocked.

Sections are cached in topic_sections — subsequent calls skip the LLM if rows
exist. The OpenAI import is lazy so this module stays importable (and the pure
helpers stay unit-testable) without the openai package installed.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"
MAX_REVISIONS = 2          # judge/revise rounds before accepting the plan as-is
JUDGE_PASS_SCORE = 0.7     # logged; per-section `ok` flags drive revision

# The fixed pedagogical arc. The LLM tailors titles/queries to the topic; the
# roles themselves never change.
SECTION_ROLES = [
    (0, "Hook", "Why should I care? A surprising or counterintuitive angle that creates curiosity."),
    (1, "What is it", "Precise definition and core concept in plain language."),
    (2, "How it works", "The mechanics, key examples, the real substance."),
    (3, "Outcomes", "What understanding this unlocks; real-world significance."),
]


def _client():
    """Lazily construct the OpenAI client (keeps the module import-light)."""
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _roles_block(indices: list[int] | None = None) -> str:
    rows = [r for r in SECTION_ROLES if indices is None or r[0] in indices]
    return "\n".join(f"{i}. {name} — {desc}" for i, name, desc in rows)


# --- pure helpers (no LLM, unit-testable) ----------------------------------

def _strip_json(raw: str) -> str:
    """Strip markdown code fences the model sometimes wraps JSON in."""
    raw = (raw or "").strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _default_sections(topic_name: str) -> list[dict]:
    return [
        {"section_index": 0, "title": f"Why {topic_name} Matters", "description": "The context and motivation for learning this topic.", "search_query": f"{topic_name} why it matters"},
        {"section_index": 1, "title": f"What Is {topic_name}", "description": "Core definition and key concepts.", "search_query": f"{topic_name} explained simply"},
        {"section_index": 2, "title": f"How {topic_name} Works", "description": "The mechanics and key examples.", "search_query": f"{topic_name} how it works in depth"},
        {"section_index": 3, "title": f"{topic_name} in Practice", "description": "Real-world significance and applications.", "search_query": f"{topic_name} real world examples applications"},
    ]


def _normalize_sections(raw: list, topic_name: str) -> list[dict]:
    """Coerce arbitrary LLM output into exactly 4 sections indexed 0–3, filling
    any missing/blank fields from the static defaults."""
    defaults = _default_sections(topic_name)
    by_index: dict[int, dict] = {}
    for i, s in enumerate(raw or []):
        if not isinstance(s, dict):
            continue
        try:
            idx = int(s.get("section_index", i))
        except (TypeError, ValueError):
            idx = i
        if 0 <= idx <= 3 and idx not in by_index:
            by_index[idx] = s
    result = []
    for idx in range(4):
        s = by_index.get(idx, {})
        d = defaults[idx]
        result.append({
            "section_index": idx,
            "title": str(s.get("title") or d["title"]).strip()[:120],
            "description": str(s.get("description") or d["description"]).strip(),
            "search_query": str(s.get("search_query") or d["search_query"]).strip(),
        })
    return result


def _sections_needing_revision(judge: dict | None) -> list[int]:
    """Indices the judge marked not-ok. Missing/empty judge → revise nothing."""
    if not judge:
        return []
    weak = []
    for s in judge.get("sections", []):
        if not isinstance(s, dict):
            continue
        try:
            idx = int(s.get("section_index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx <= 3 and not s.get("ok", True):
            weak.append(idx)
    return weak


def _merge_revisions(sections: list[dict], revised: list) -> list[dict]:
    """Replace only the revised indices; preserve the rest. Returns 4 sections."""
    by_index = {s["section_index"]: s for s in sections}
    for r in revised or []:
        if not isinstance(r, dict):
            continue
        try:
            idx = int(r.get("section_index"))
        except (TypeError, ValueError):
            continue
        if idx in by_index:
            cur = by_index[idx]
            by_index[idx] = {
                "section_index": idx,
                "title": str(r.get("title") or cur["title"]).strip()[:120],
                "description": str(r.get("description") or cur["description"]).strip(),
                "search_query": str(r.get("search_query") or cur["search_query"]).strip(),
            }
    return [by_index[i] for i in range(4)]


# --- LLM stages ------------------------------------------------------------

def _generate_outline(topic_name: str, difficulty: str, path_context: list[str] | None) -> list:
    context_line = f"\nAlready covered earlier in this path: {', '.join(path_context)}." if path_context else ""
    prompt = f'''You are designing a 4-part micro-lesson on "{topic_name}" (level: {difficulty}).{context_line}

The four parts always follow this pedagogical arc:
{_roles_block()}

Write a compelling, specific title for each part — a curiosity-gap phrase, max 8 words, concrete to THIS topic (not generic). Titles must build on each other and must not give away later parts.

Return ONLY a JSON array:
[{{"section_index": 0, "title": "..."}}, {{"section_index": 1, "title": "..."}}, {{"section_index": 2, "title": "..."}}, {{"section_index": 3, "title": "..."}}]'''
    resp = _client().chat.completions.create(model=MODEL, max_tokens=400, messages=[{"role": "user", "content": prompt}])
    return json.loads(_strip_json(resp.choices[0].message.content))


def _detail_sections(topic_name: str, difficulty: str, outline: list) -> list:
    outline_json = json.dumps([{"section_index": s.get("section_index"), "title": s.get("title")} for s in outline])
    prompt = f'''Topic: "{topic_name}" (level: {difficulty}).
Part roles:
{_roles_block()}

Agreed outline (section_index + title):
{outline_json}

For EACH section, write:
- description: 1-2 sentences on exactly what this part teaches.
- search_query: a precise YouTube search string to find ONE focused 5-10 minute video covering THIS sub-concept specifically — NOT the broad topic. Include the distinguishing concept words; avoid generic queries like just the topic name, and don't reuse the same query across sections.

Return ONLY a JSON array of 4 objects:
[{{"section_index": 0, "title": "...", "description": "...", "search_query": "..."}}, ...]'''
    resp = _client().chat.completions.create(model=MODEL, max_tokens=800, messages=[{"role": "user", "content": prompt}])
    return json.loads(_strip_json(resp.choices[0].message.content))


def _judge_sections(topic_name: str, difficulty: str, sections: list[dict]) -> dict:
    sections_json = json.dumps(sections, indent=2)
    prompt = f'''You are a strict instructional-design reviewer. Evaluate this 4-part micro-lesson plan for "{topic_name}" (level: {difficulty}).

Plan:
{sections_json}

Judge EACH section against ALL of these criteria:
1. Role fit — section 0 hooks/motivates, 1 defines, 2 explains mechanics, 3 covers outcomes/significance.
2. Title — curiosity-gap, <= 8 words, specific to this topic (not generic), and does not spoil later parts.
3. Description — teaches the right sub-concept for its role, at the right level.
4. search_query — specific enough to retrieve ONE focused 5-10 min video on that exact sub-concept; NOT just the broad topic name; not redundant with other sections' queries.

Mark a section "ok": false if it clearly fails ANY criterion. Be demanding about search_query specificity and title concreteness — those most affect quality.

Return ONLY JSON:
{{"overall_score": 0.0, "sections": [{{"section_index": 0, "ok": true, "issue": ""}}, {{"section_index": 1, "ok": true, "issue": ""}}, {{"section_index": 2, "ok": true, "issue": ""}}, {{"section_index": 3, "ok": true, "issue": ""}}]}}
overall_score is 0-1; "issue" briefly states the problem (empty when ok).'''
    resp = _client().chat.completions.create(model=MODEL, max_tokens=600, messages=[{"role": "user", "content": prompt}])
    return json.loads(_strip_json(resp.choices[0].message.content))


def _revise_sections(topic_name: str, difficulty: str, sections: list[dict], weak: list[int], judge: dict) -> list:
    issues = {}
    for s in judge.get("sections", []):
        try:
            idx = int(s.get("section_index"))
        except (TypeError, ValueError):
            continue
        if idx in weak:
            issues[idx] = s.get("issue", "")
    to_fix = [
        {**{k: sec[k] for k in ("section_index", "title", "description", "search_query")},
         "issue": issues.get(sec["section_index"], "")}
        for sec in sections if sec["section_index"] in weak
    ]
    prompt = f'''Topic: "{topic_name}" (level: {difficulty}). Fix ONLY these weak sections of a 4-part lesson, addressing each reviewer issue.

Roles for the sections you're fixing:
{_roles_block(weak)}

Sections to fix (with the reviewer's issue):
{json.dumps(to_fix, indent=2)}

Rewrite each so it fully satisfies: role fit; title curiosity-gap <=8 words and specific; description teaches the right sub-concept; search_query specific enough to find ONE focused 5-10 min video on that exact sub-concept (not the broad topic).

Return ONLY a JSON array of the corrected sections (same section_index values):
[{{"section_index": N, "title": "...", "description": "...", "search_query": "..."}}, ...]'''
    resp = _client().chat.completions.create(model=MODEL, max_tokens=600, messages=[{"role": "user", "content": prompt}])
    return json.loads(_strip_json(resp.choices[0].message.content))


# --- orchestration ---------------------------------------------------------

def _plan_sections(topic_name: str, difficulty: str = "intermediate", path_context: list[str] | None = None) -> list[dict]:
    """Run outline → detail → judge → (revise → judge)* and return 4 sections.
    Best-effort: any stage failure returns the best plan so far (or defaults)."""
    try:
        outline = _generate_outline(topic_name, difficulty, path_context)
        detailed = _detail_sections(topic_name, difficulty, outline)
        sections = _normalize_sections(detailed, topic_name)
    except Exception as exc:
        logger.warning(f"[section_planner] generation failed for '{topic_name}': {exc}")
        return _default_sections(topic_name)

    for attempt in range(MAX_REVISIONS):
        try:
            judge = _judge_sections(topic_name, difficulty, sections)
        except Exception as exc:
            logger.warning(f"[section_planner] judge failed for '{topic_name}': {exc}")
            break
        weak = _sections_needing_revision(judge)
        if weak:
            issues = {s.get("section_index"): s.get("issue", "")
                      for s in judge.get("sections", []) if not s.get("ok", True)}
            logger.info(f"[section_planner] '{topic_name}' judge round {attempt + 1}: "
                        f"score={judge.get('overall_score')} weak={weak} issues={issues}")
        else:
            logger.info(f"[section_planner] '{topic_name}' judge round {attempt + 1}: "
                        f"score={judge.get('overall_score')} all ok")
        if not weak:
            break
        try:
            revised = _revise_sections(topic_name, difficulty, sections, weak, judge)
            sections = _normalize_sections(_merge_revisions(sections, revised), topic_name)
        except Exception as exc:
            logger.warning(f"[section_planner] revision failed for '{topic_name}': {exc}")
            break

    return sections


def plan_and_store_sections(
    topic_slug: str,
    topic_name: str,
    difficulty: str = "intermediate",
    path_context: list[str] | None = None,
) -> list[dict]:
    """Plan 4 sections (decomposed + judged) and store them in topic_sections.
    Returns the section dicts. Cached: returns existing rows without calling the LLM."""
    from app.db.supabase import get_client
    db = get_client()

    existing = (
        db.table("topic_sections")
        .select("section_index,title,description,search_query")
        .eq("topic_slug", topic_slug)
        .order("section_index")
        .execute()
    )
    if existing.data:
        return existing.data

    sections = _plan_sections(topic_name, difficulty, path_context)

    stored: list[dict] = []
    for s in sections[:4]:
        row = {
            "topic_slug": topic_slug,
            "section_index": int(s.get("section_index", len(stored))),
            "title": str(s.get("title", f"Section {len(stored)}")),
            "description": str(s.get("description", "")),
            "search_query": str(s.get("search_query", f"{topic_name} explained")),
        }
        try:
            db.table("topic_sections").upsert(row, on_conflict="topic_slug,section_index").execute()
            stored.append(row)
        except Exception as exc:
            logger.warning(f"[section_planner] Failed to store section {row['section_index']} for {topic_slug}: {exc}")

    logger.info(f"[section_planner] {len(stored)} sections stored for {topic_slug}")
    return stored
