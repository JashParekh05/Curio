"""Application startup invokes no Staged_Migration step (Task 14.8).

Asserts the Req 8.2 guarantee: the Curio_System never executes any
Staged_Migration step automatically during application startup -- every step is
operator-run only. The destructive cleanup runs solely through the
``scripts.staged_migration`` runner's operator entrypoint, never from the app's
startup path.

The test spies on every step-applying function in the runner
(``apply_step`` / ``drop_column`` / ``reverse_step``) and on its SQL executor,
then drives the FastAPI application's registered startup handlers, and asserts
that none of those functions ran. It also asserts the runner's operator
entrypoint refuses to act without an explicit confirmation and that its default
executor is the no-op DRY-RUN, so importing or invoking the runner cannot, by
itself, apply DDL.

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_no_migration_on_startup.py``.

Validates: Requirements 8.2
"""
import asyncio

import app.main as app_main
from scripts import staged_migration as sm


class TestNoMigrationOnStartup:
    def test_startup_handlers_invoke_no_migration_step(self, monkeypatch):
        calls = []

        # Spy on every function that could apply or reverse a schema change.
        monkeypatch.setattr(
            sm, "apply_step",
            lambda *a, **k: calls.append("apply_step"),
        )
        monkeypatch.setattr(
            sm, "drop_column",
            lambda *a, **k: calls.append("drop_column"),
        )
        monkeypatch.setattr(
            sm, "reverse_step",
            lambda *a, **k: calls.append("reverse_step"),
        )
        monkeypatch.setattr(
            sm, "_dry_run_executor",
            lambda sql: calls.append("execute_sql"),
        )

        # Keep the embeddings warmup (the only real startup work) inert and
        # synchronous so the test neither loads the model nor leaks a thread.
        import app.services.embeddings as embeddings
        monkeypatch.setattr(embeddings, "get_model", lambda: calls.append("get_model"))

        # Drive every registered startup handler exactly as the ASGI server would.
        handlers = list(app_main.app.router.on_startup)
        assert handlers, "expected at least one startup handler to exercise"
        for handler in handlers:
            result = handler()
            if asyncio.iscoroutine(result):
                asyncio.run(result)

        # No Staged_Migration step ran during startup (Req 8.2).
        assert "apply_step" not in calls
        assert "drop_column" not in calls
        assert "reverse_step" not in calls
        assert "execute_sql" not in calls

    def test_operator_entrypoint_refuses_without_confirmation(self, monkeypatch):
        executed = []
        monkeypatch.setattr(sm, "_dry_run_executor", lambda sql: executed.append(sql))

        # No --confirm: the runner refuses and applies nothing.
        rc = sm.main([])
        assert rc == 1
        assert executed == []

    def test_confirm_runs_dry_run_only_and_applies_no_ddl(self, monkeypatch):
        executed = []
        # Even the forward SQL executor must not run any DDL from main(): main
        # only prints the plan; it never wires a real executor.
        monkeypatch.setattr(sm, "_dry_run_executor", lambda sql: executed.append(sql))

        rc = sm.main(["--confirm"])
        assert rc == 0
        # main() applies no steps -- it only logs the plan, so no SQL executes.
        assert executed == []
