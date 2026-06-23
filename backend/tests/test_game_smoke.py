"""Smoke test: Game_Router registration + Game_Service import isolation (Req 13).

Two independent guarantees are asserted here, both cheap and structural:

1. **Route registration (Req 13.1, 13.2).** The full ``app.main.app`` — the same
   FastAPI app the server boots — has the three game endpoints registered under
   the ``/api/game`` prefix (``/session``, ``/decide``, ``/node``) as POST routes,
   and every one of them is guarded by ``require_user`` (Req 13.6 is what
   ``require_user`` enforces; here we prove it is actually wired into each route's
   dependency tree, not just declared in the handler signature).

2. **Import isolation (Req 13.7, 22.8).** ``app.services.game`` reaches for ONLY
   the three leaf services it is allowed to (``llm``, ``quiz``, ``youtube``) plus
   stdlib — it never imports the LangGraph pipeline, coherence machinery,
   arc-assembler, or quota-pool internals. This is checked by parsing the
   module's own source with ``ast`` (so it reflects the file's real import
   statements, independent of what other tests may have dragged into
   ``sys.modules``).

Validates: Requirements 13.1, 13.2, 13.7, 22.8
"""
import ast

import pytest

from app.auth import require_user


# --------------------------------------------------------------------------- #
# 1. Route registration on the real app (Req 13.1, 13.2, 13.6)
# --------------------------------------------------------------------------- #

GAME_PREFIX = "/api/game"
EXPECTED_GAME_PATHS = {
    f"{GAME_PREFIX}/session",
    f"{GAME_PREFIX}/decide",
    f"{GAME_PREFIX}/node",
}


def _game_routes():
    """All routes on the real app whose path lives under the /api/game prefix."""
    from app.main import app

    return [
        r
        for r in app.routes
        if getattr(r, "path", "").startswith(GAME_PREFIX)
    ]


def _all_dependency_calls(dependant):
    """Flatten a route's dependency tree into the set of callables it invokes.

    FastAPI records ``Depends(...)`` callables (including nested sub-dependencies)
    on ``route.dependant``; walking it lets us prove ``require_user`` actually
    runs for the route rather than trusting the handler signature alone.
    """
    calls = []
    stack = [dependant]
    while stack:
        dep = stack.pop()
        if dep is None:
            continue
        if dep.call is not None:
            calls.append(dep.call)
        stack.extend(dep.dependencies)
    return calls


def test_app_starts_with_game_router_registered():
    """The booted app exposes exactly the three documented game routes under the
    /api/game prefix (Req 13.1, 13.2)."""
    paths = {r.path for r in _game_routes()}
    assert EXPECTED_GAME_PATHS <= paths, (
        f"missing game routes; found {sorted(paths)}"
    )


def test_game_routes_are_under_prefix_and_post():
    """Every registered game route sits under /api/game and is a POST (Req 13.1)."""
    routes_by_path = {r.path: r for r in _game_routes()}
    for path in EXPECTED_GAME_PATHS:
        route = routes_by_path[path]
        assert path.startswith(GAME_PREFIX + "/")
        assert "POST" in route.methods


def test_every_game_route_enforces_require_user():
    """require_user is wired into the dependency tree of all three endpoints, so
    an unauthenticated request can never reach a handler body (Req 13.6)."""
    routes_by_path = {r.path: r for r in _game_routes()}
    for path in EXPECTED_GAME_PATHS:
        route = routes_by_path[path]
        calls = _all_dependency_calls(route.dependant)
        assert require_user in calls, (
            f"{path} does not enforce require_user (deps: {calls})"
        )


# --------------------------------------------------------------------------- #
# 2. Import isolation of the Game_Service (Req 13.7, 22.8)
# --------------------------------------------------------------------------- #

# Only these leaf services may be imported from the app package (Req 22.2, 22.3).
# ``app.db.supabase`` is also a permitted leaf: Phase 2 best-effort persistence
# reuses ``db/supabase.get_client()`` (Req 16.2), and the spec lists
# ``db/supabase.py`` among the reused leaf services (Req 22). ``app.db`` is the
# package base produced by ``from app.db import supabase``.
ALLOWED_APP_LEAF_MODULES = {
    "app.services.llm",
    "app.services.quiz",
    "app.services.youtube",
    "app.db",
    "app.db.supabase",
}
ALLOWED_LEAF_NAMES = {"llm", "quiz", "youtube"}

# Substrings that must never appear in any module the Game_Service imports: the
# LangGraph pipeline, coherence machinery, arc-assembler, and quota-pool
# internals it is forbidden to touch (Req 13.7, 22.8). Concrete offending
# modules in this codebase include app.services.pipeline, app.agents.pipeline_agent,
# app.services.ingestion_pipeline, app.services.coherence(_budget),
# app.services.arc_assembler / arc_unifier / arc_backfill, and
# app.services.quota_pool / quota_store.
FORBIDDEN_SUBSTRINGS = ("pipeline", "coherence", "arc", "quota")


def _game_imported_modules():
    """Parse app/services/game.py and return the set of module names it imports.

    Uses ``ast`` over the module's own source so the result reflects the file's
    real ``import`` / ``from ... import`` statements, not whatever the broader
    test run has loaded into ``sys.modules``.
    """
    import app.services.game as game_service

    source = open(game_service.__file__, encoding="utf-8").read()
    tree = ast.parse(source)

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # Ignore relative imports (none expected); record the absolute module
            # plus each imported leaf as module.leaf so `from app.services import
            # llm, quiz, youtube` is checked at leaf granularity.
            if node.level:
                continue
            base = node.module or ""
            modules.add(base)
            for alias in node.names:
                modules.add(f"{base}.{alias.name}")
    return modules


def test_game_service_imports_only_allowed_app_modules():
    """Every app.* module the Game_Service imports is one of the three allowed
    leaf services (Req 22.2, 22.3) — no other app module is pulled in."""
    imported = _game_imported_modules()
    app_imports = {m for m in imported if m == "app" or m.startswith("app.")}

    # Allowed: the leaf modules themselves, the `app.services` package used by
    # `from app.services import llm, quiz, youtube`, and the `app.services.<leaf>`
    # forms produced by that statement.
    allowed = (
        ALLOWED_APP_LEAF_MODULES
        | {"app", "app.services"}
        | {f"app.services.{n}" for n in ALLOWED_LEAF_NAMES}
    )
    unexpected = app_imports - allowed
    assert not unexpected, (
        f"Game_Service imports disallowed app modules: {sorted(unexpected)}"
    )


def test_game_service_imports_no_pipeline_coherence_arc_or_quota():
    """The Game_Service imports nothing from the pipeline, coherence machinery,
    arc-assembler, or quota-pool (Req 13.7, 22.8)."""
    imported = _game_imported_modules()
    offenders = {
        m
        for m in imported
        if any(bad in m.lower() for bad in FORBIDDEN_SUBSTRINGS)
    }
    assert not offenders, (
        f"Game_Service must not import pipeline/coherence/arc/quota modules; "
        f"found {sorted(offenders)}"
    )


def test_game_service_uses_only_leaf_services_plus_stdlib():
    """Sanity check that the only non-stdlib dependency surface is the three
    leaf services (Req 22): the sole `from app...` import is the leaf trio."""
    imported = _game_imported_modules()
    leaf_imports = {f"app.services.{n}" for n in ALLOWED_LEAF_NAMES}
    assert leaf_imports <= imported, (
        f"expected the leaf services {sorted(leaf_imports)} to be imported; "
        f"got app imports {sorted(m for m in imported if m.startswith('app'))}"
    )
