"""Scripted offline stand-in for Muse Spark, used ONLY by tests and UI development.

It follows a fixed investigation per scenario so the pipeline (tools, evidence
ledger, verifier, revision loop, UI events) can be exercised without network.
Real runs use Muse Spark / Muse Spark Open; see README.
"""
from __future__ import annotations

import json
import re

from .llm import Reply, ToolCall

SCRIPTS = {
    "INC-30412": [
        [("describe_environment", {})],
        [("run_sql", {"query": "SELECT run_date, exceptions, status FROM recon_runs ORDER BY run_date"}),
         ("run_sql", {"query": "SELECT reason, COUNT(*) AS n FROM recon_exceptions WHERE run_date='2026-10-31' GROUP BY reason"}),
         ("list_deployments", {"service": "settlement-ingest"})],
        [("run_sql", {"query": "SELECT substr(l.ledger_ts_local,12,2) AS hour, COUNT(*) AS n FROM recon_exceptions e "
                               "JOIN ledger_entries l ON l.txn_ref=e.txn_ref WHERE e.run_date='2026-10-31' GROUP BY hour"}),
         ("search_logs", {"service": "recon-engine"}),
         ("read_runbook", {"name": "settlement-reconciliation"})],
    ],
    "INC-30455": [
        [("describe_environment", {})],
        [("get_metric", {"service": "notification-dispatcher", "metric": "active_workers"}),
         ("get_metric", {"service": "sms-gateway", "metric": "provider_latency_p95_ms"}),
         ("list_deployments", {"service": "notification-dispatcher"})],
        [("search_logs", {"service": "status-poller"})],
    ],
    "INC-30471": [
        [("describe_environment", {})],
        [("run_sql", {"query": "SELECT COUNT(*) AS duplicated_payouts, SUM(amount_cop) AS duplicated_cop FROM "
                               "(SELECT payout_id, amount_cop FROM ledger_postings GROUP BY payout_id HAVING COUNT(*) > 1)"}),
         ("list_deployments", {"service": "payout-service"}),
         ("search_logs", {"service": "ledger-api"})],
    ],
}


def _casefile(inc: str, ev: list[str], broken: bool) -> dict:
    e = lambda i: ev[i] if i < len(ev) else "E99"  # noqa: E731
    if inc == "INC-30412":
        return {
            "title": "Recon run 2026-10-31: settlement records shifted to next business date after settlement-ingest 2.14.0",
            "severity": "SEV2", "decision": "escalate", "owner_team": "payments-data",
            "summary": {"text": "All 635 exceptions are NO_MATCH_SAME_BUSINESS_DATE, introduced by the timezone change in D-9107.",
                        "evidence": [e(2)]},
            "impact": [{"text": "635 exceptions against a threshold of 25.", "evidence": [e(1)]}],
            "timeline": [{"time": "2026-10-30 22:05", "text": "settlement-ingest 2.14.0 deployed (D-9107).",
                          "evidence": [e(3)]}],
            "root_cause": {"text": "D-9107 changed parser.timestamp_source_tz from UTC to America/Bogota, so evening "
                                   "transactions land on the next business date.", "confidence": 0.9,
                           "evidence": [e(3), e(4)]},
            "ruled_out": [{"text": "The late processor file did not cause it: recon waited for the file.",
                           "evidence": ["E99"] if broken else [e(5)]}],
            "next_actions": [{"owner": "payments-data", "text": "Revert parser.timestamp_source_tz and reprocess the file."}],
            "verification_queries": ["SELECT reason, COUNT(*) FROM recon_exceptions WHERE run_date='2026-10-31' GROUP BY reason"],
            "open_questions": [],
        }
    if inc == "INC-30455":
        return {
            "title": "OTP dispatch lag: consumer pool cut from 12 to 2 workers by CHG-4447",
            "severity": "SEV1", "decision": "escalate", "owner_team": "messaging-platform",
            "summary": {"text": "Dispatcher workers dropped from 12 to 2 while provider latency stayed flat.",
                        "evidence": [e(1), e(2)]},
            "impact": [{"text": "Active workers fell from 12 to 2.", "evidence": [e(1)]}],
            "timeline": [{"time": "2026-11-02 08:22", "text": "D-9119 set consumer.max_workers to 2.", "evidence": [e(3)]}],
            "root_cause": {"text": "CHG-4447 applied the night scale-down window permanently.", "confidence": 0.85,
                           "evidence": [e(3)]},
            "ruled_out": [{"text": "Altavoz degradation only affected APAC.", "evidence": [e(4)]}],
            "next_actions": [{"owner": "messaging-platform", "text": "Restore consumer.max_workers to 12."}],
            "verification_queries": [], "open_questions": [],
        }
    return {
        "title": "Batch B-20261102 double-posted payouts after payout-service 3.1.0",
        "severity": "SEV1", "decision": "escalate", "owner_team": "merchant-platform",
        "summary": {"text": "202 payouts were posted twice, 902990000 COP in total.", "evidence": [e(1)]},
        "impact": [{"text": "202 duplicated payouts totalling 902990000 COP.", "evidence": [e(1)]}],
        "timeline": [{"time": "2026-11-02 18:31", "text": "payout-service 3.1.0 deployed (D-9123).", "evidence": [e(2)]}],
        "root_cause": {"text": "3.1.0 renamed the idempotency header that ledger-api does not read, and enabled retries.",
                       "confidence": 0.9, "evidence": [e(2), e(3)]},
        "ruled_out": [],
        "next_actions": [{"owner": "merchant-platform", "text": "Pause tonight's batch."}],
        "verification_queries": [], "open_questions": [],
    }


class MockModel:
    def __init__(self, role: str) -> None:
        self.role = role
        self.n = 0

    def chat(self, messages, tools=None, json_mode=False) -> Reply:
        self.n += 1
        if self.role == "vision":
            return self._text("- (mock) screenshot facts would appear here")
        if self.role == "verifier":
            ids = re.findall(r"### Claim (\S+)", messages[-1]["content"])
            return self._text(json.dumps({"verdicts": [{"id": i, "verdict": "SUPPORTED", "reason": "mock"} for i in ids]}))
        first = messages[1]["content"]
        first = first[0]["text"] if isinstance(first, list) else first
        inc = re.search(r"INC-\d+", first).group(0)
        script = SCRIPTS[inc]
        step = sum(1 for m in messages if m["role"] == "assistant")
        ev = [json.loads(m["content"]).get("evidence_id") for m in messages if m["role"] == "tool"]
        ev = [x for x in ev if x]
        if step < len(script):
            calls = [ToolCall(f"call_{step}_{i}", name, json.dumps(args)) for i, (name, args) in enumerate(script[step])]
            return self._calls(f"Step {step + 1}: checking {', '.join(c.name for c in calls)}.", calls)
        rejected = any("rejected_by_verifier" in str(m.get("content")) for m in messages if m["role"] == "tool")
        cf = _casefile(inc, ev, broken=not rejected)
        return self._calls("Submitting casefile.", [ToolCall(f"call_submit_{step}", "submit_casefile", json.dumps(cf))])

    def _text(self, text: str) -> Reply:
        return Reply(text, [], {"role": "assistant", "content": text}, {"prompt_tokens": 0, "completion_tokens": 0})

    def _calls(self, text: str, calls: list[ToolCall]) -> Reply:
        msg = {"role": "assistant", "content": text, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls]}
        return Reply(text, calls, msg, {"prompt_tokens": 0, "completion_tokens": 0})
