"""Distributed Run Intel workflow (durable task graph).

Implementation of docs/ai/RUN_INTEL_DISTRIBUTED_WORKFLOW.md. One explicit
Run Intel click creates one durable session, a frozen per-ticker scope and a
generic durable task queue; an in-process worker supervisor executes the graph
outside any HTTP request. The browser only observes status.

Hard boundaries (enforced by tests/test_distributed_architecture_boundary.py):
  * no module in this package references the retired orchestrator execution
    path (agent pipeline, bounded drain, full-portfolio analyst adapter,
    legacy session flow);
  * collectors never call an LLM; specialists never call a provider;
  * deterministic decision_policy_v1.decide() is the only action authority.
"""
