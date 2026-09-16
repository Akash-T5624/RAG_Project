"""Shared lightweight run record (cacheable) for week-8 trajectory evals."""


class RunDump:
    """Cacheable run record mirroring the agent/workflow fields the trajectory
    metrics need: .steps, .output, .termination_reason, .cost, .tokens,
    .latency_ms, .pass_result, .failures, .model."""

    def __init__(self, claim_id, system, steps, output, termination_reason,
                 cost, tokens, latency_ms, pass_result, failures, model):
        self.claim_id = claim_id
        self.system = system
        self.steps = steps
        self.output = output
        self.termination_reason = termination_reason
        self.cost = cost
        self.tokens = tokens
        self.latency_ms = latency_ms
        self.pass_result = pass_result
        self.failures = failures
        self.model = model

    def to_dict(self):
        return {
            "claim_id": self.claim_id,
            "system": self.system,
            "steps": [{
                "tool": s.get("tool"),
                "args": s.get("args"),
                "status": s.get("status"),
            } for s in self.steps],
            "output": self.output,
            "termination_reason": self.termination_reason,
            "cost": self.cost,
            "tokens": self.tokens,
            "latency_ms": self.latency_ms,
            "pass_result": self.pass_result,
            "failures": self.failures,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            claim_id=d["claim_id"], system=d["system"],
            steps=d["steps"], output=d.get("output"),
            termination_reason=d.get("termination_reason", "completed"),
            cost=d.get("cost", 0.0), tokens=d.get("tokens", 0),
            latency_ms=d.get("latency_ms", 0.0),
            pass_result=d.get("pass_result"),
            failures=d.get("failures", []), model=d.get("model", ""),
        )