"""Phase 3.6 validation harness guard tests.

The Phase 3.6 operator diagnostics HTTP endpoint
(`/api/v1/diagnostics/intel-v3/research-workers/validate` in
`app/routers/diagnostics.py`) was removed in the lean-product refactor, so all
endpoint-layer tests (gating, secret rejection, ticker cap, response shape,
snapshot-write guards) were deleted with it.

What remains are the pure static guards on the KEPT validation harness module
(`app.services.intelligence.research_workers.validation_harness`) and the kept
intel_v3 router:

  - validation_harness must not import or call decide() / decision_policy_v1.
  - validation_harness must not import recommendation_engine / insight cards.
  - validation_harness must not import IntelV3Service.
  - the intel_v3 router must not have absorbed the removed validation endpoint.
"""
from __future__ import annotations

import inspect


class TestPhase36NoDecideDependency:
    """Criterion 11: validation_harness.py must not import or call decide()."""

    def _ast_decide_calls(self, src: str) -> list:
        """Return list of AST Call nodes calling 'decide'."""
        import ast
        tree = ast.parse(src)
        return [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "decide")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "decide")
            )
        ]

    def test_validation_harness_does_not_call_decide(self):
        """AST check: no call to decide() in validation_harness.py."""
        import app.services.intelligence.research_workers.validation_harness as harness_mod
        src = inspect.getsource(harness_mod)
        assert self._ast_decide_calls(src) == [], (
            "validation_harness.py must not call decide()"
        )

    def test_validation_harness_does_not_import_decision_policy(self):
        """No import of decision_policy_v1 in validation_harness.py."""
        import re
        import app.services.intelligence.research_workers.validation_harness as harness_mod
        src = inspect.getsource(harness_mod)
        assert not re.search(r"^\s*(import|from)\s+.*decision_policy_v1", src, re.MULTILINE), (
            "validation_harness.py must not import decision_policy_v1"
        )


class TestPhase36NoRecommendationEngineInHarness:
    """Criterion 12: validation_harness.py must not import/call recommendation_engine."""

    def test_validation_harness_does_not_import_recommendation_engine(self):
        import app.services.intelligence.research_workers.validation_harness as harness_mod
        src = inspect.getsource(harness_mod)
        assert "recommendation_engine" not in src
        assert "get_insight_cards" not in src
        assert "_compute_insight_cards" not in src

    def test_validation_harness_does_not_import_intel_v3_service(self):
        import app.services.intelligence.research_workers.validation_harness as harness_mod
        src = inspect.getsource(harness_mod)
        assert "IntelV3Service" not in src


class TestPhase36Constants:

    def test_endpoint_is_not_imported_from_intel_v3_router(self):
        """Verify the removed validation endpoint did not land in intel_v3.py."""
        import app.routers.intel_v3 as intel_v3_mod
        src = inspect.getsource(intel_v3_mod)
        assert "research-workers/validate" not in src
        assert "validate_research_workers_dark_run" not in src
