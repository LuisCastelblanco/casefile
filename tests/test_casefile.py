import json
import os

os.environ["LLM_PROVIDER"] = "mock"

import pytest  # noqa: E402

from casefile.agent import Investigation, flatten_claims, ungrounded_numbers  # noqa: E402
from casefile.cli import make_llm  # noqa: E402
from casefile.config import SANDBOX_DIR  # noqa: E402
from casefile.markdown import casefile_markdown  # noqa: E402
from casefile.tools import SandboxTools  # noqa: E402

tools = SandboxTools()  # builds the sandbox on first run
SCENARIOS = {s["id"]: s for s in json.loads((SANDBOX_DIR / "scenarios.json").read_text(encoding="utf-8"))}
TRUTH = json.loads((SANDBOX_DIR / "data" / "ground_truth.json").read_text(encoding="utf-8"))


def test_sql_is_read_only():
    assert "error" in tools.run_sql("DELETE FROM payouts")
    assert "error" in tools.run_sql("SELECT 1; DROP TABLE payouts")
    assert "error" in tools.run_sql("WITH x AS (SELECT 1) INSERT INTO merchants VALUES ('a','b','c')")


def test_sql_rows_are_capped():
    r = tools.run_sql("SELECT * FROM otp_messages")
    assert r["truncated"] and len(r["rows"]) == 40


def test_ground_truth_is_discoverable():
    r = tools.run_sql("SELECT COUNT(*) FROM recon_exceptions WHERE run_date='2026-10-31'")
    assert r["rows"][0][0] == TRUTH["recon"]["exceptions"]
    r = tools.run_sql("SELECT COUNT(*), SUM(amount_cop) FROM (SELECT payout_id, amount_cop FROM ledger_postings "
                      "GROUP BY payout_id HAVING COUNT(*) > 1)")
    assert r["rows"][0] == [TRUTH["payouts"]["duplicates"], TRUTH["payouts"]["dup_total_cop"]]
    deps = tools.list_deployments(service="notification-dispatcher")["deployments"]
    assert any(c["key"] == "consumer.max_workers" and c["new"] == "2" for d in deps for c in d["config_changes"])


def test_number_grounding():
    ev = json.dumps({"exceptions": 635, "at": "2026-10-30T22:05:11-05:00", "total": 902990000})
    assert ungrounded_numbers("635 exceptions after D-9107 at 22:05, 902.990.000 COP", ev) == []
    assert ungrounded_numbers("700 exceptions, 26.1% of volume", ev) == ["700", "26.1"]


def test_flatten_claims():
    cf = {"summary": {"text": "s", "evidence": ["E1"]}, "impact": [{"text": "i", "evidence": []}],
          "timeline": [{"time": "08:22", "text": "t", "evidence": ["E2"]}],
          "root_cause": {"text": "r", "confidence": 0.8, "evidence": ["E3"]}, "ruled_out": []}
    ids = [c["id"] for c in flatten_claims(cf)]
    assert ids == ["summary", "impact.0", "timeline.0", "root_cause"]


@pytest.mark.parametrize("sid", sorted(SCENARIOS))
def test_end_to_end_mock(sid):
    events = []
    inv = Investigation(SCENARIOS[sid], make_llm("AGENT"), make_llm("VERIFIER"), make_llm("VISION"), tools, events.append)
    result = inv.run({"text": "voice note"}, b"\x89PNG")
    types = [e["type"] for e in events]
    assert types[-1] == "final"
    assert "verification" in types
    ids = [e["id"] for e in result["evidence"]]
    assert ids == [f"E{i}" for i in range(1, len(ids) + 1)]
    assert "# " in casefile_markdown(result)


def test_unknown_evidence_triggers_revision():
    events = []
    inv = Investigation(SCENARIOS["recon-spike"], make_llm("AGENT"), make_llm("VERIFIER"), make_llm("VISION"),
                        tools, events.append)
    inv.run({"text": "x"}, None)
    first = next(e for e in events if e["type"] == "verification")["report"]
    assert first["claims"]["ruled_out.0"]["verdict"] == "UNSUPPORTED"
    assert any(e["type"] == "revision" for e in events)
