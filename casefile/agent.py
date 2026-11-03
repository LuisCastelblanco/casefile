"""The Casefile investigation loop: intake -> agentic investigation -> independent verification -> casefile."""
from __future__ import annotations

import base64
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .config import MAX_AGENT_TURNS, MAX_REVISIONS
from .llm import LLM, LLMError, extract_json
from .prompts import AGENT_SYSTEM, VERIFIER_SYSTEM, VISION_PROMPT
from .tools import TOOL_SCHEMAS, SandboxTools

Emit = Callable[[dict], None]


# ------------------------------------------------------------------ evidence
@dataclass
class Evidence:
    id: str
    kind: str  # ticket | voice | screenshot | sql | logs | metric | deployments | runbook | environment
    title: str
    summary: str
    content: object
    source: str  # which tool or model produced it
    args: dict = field(default_factory=dict)

    def text(self) -> str:
        return self.content if isinstance(self.content, str) else json.dumps(self.content, ensure_ascii=False)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "summary": self.summary,
                "content": self.content, "source": self.source, "args": self.args}


class Ledger:
    def __init__(self) -> None:
        self.items: dict[str, Evidence] = {}

    def add(self, kind: str, title: str, summary: str, content: object, source: str, args: dict | None = None) -> Evidence:
        ev = Evidence(f"E{len(self.items) + 1}", kind, title, summary, content, source, args or {})
        self.items[ev.id] = ev
        return ev


TOOL_KIND = {"run_sql": "sql", "search_logs": "logs", "get_metric": "metric", "list_deployments": "deployments",
             "read_runbook": "runbook", "describe_environment": "environment"}


def summarize_result(name: str, args: dict, result: dict) -> tuple[str, str]:
    """Short title + summary for the UI evidence list."""
    if "error" in result:
        return f"{name} failed", result["error"][:160]
    if name == "run_sql":
        q = re.sub(r"\s+", " ", args.get("query", ""))
        rows = result.get("rows", [])
        preview = json.dumps(rows[:3], ensure_ascii=False)
        return f"SQL · {q[:90]}", f"{result.get('row_count_returned', 0)} row(s){' (truncated)' if result.get('truncated') else ''}: {preview[:160]}"
    if name == "search_logs":
        f = result.get("filters", {})
        detail = " ".join(f"{k}={v}" for k, v in f.items())
        first = result.get("lines", [{}])[0].get("msg", "") if result.get("lines") else ""
        return f"Logs · {args.get('service')} {detail}"[:110], f"{result.get('total_matches', 0)} match(es). {first}"[:200]
    if name == "get_metric":
        s = result.get("summary", {})
        return f"Metric · {args.get('service')}/{args.get('metric')}", (
            f"first {s.get('first')} → last {s.get('last')}, min {s.get('min')}, max {s.get('max')} at {s.get('max_at', '')[11:16]}")
    if name == "list_deployments":
        deps = result.get("deployments", [])
        brief = "; ".join(f"{d['id']} {d['service']} {d['version']} {d['deployed_at'][5:16]}" for d in deps[:4])
        return f"Deployments{' · ' + args['service'] if args.get('service') else ''}", f"{len(deps)} found. {brief}"[:200]
    if name == "read_runbook":
        return f"Runbook · {args.get('name')}", result.get("content", "")[:160].replace("\n", " ")
    if name == "describe_environment":
        return "Environment map", f"{len(result.get('database_tables', {}))} tables, {len(result.get('services_with_logs', {}))} log streams, runbooks: {', '.join(result.get('runbooks', []))}"
    return name, json.dumps(result)[:160]


# --------------------------------------------------------------- grounding
NUM_RE = re.compile(r"(?<![A-Za-z\-\d.,:_])\d(?:[\d,.:]*\d)?(?![A-Za-z_\-]|[.,:]?\d)")


def _norm_number(tok: str) -> list[str]:
    tok = tok.strip(".,:")
    variants = {tok}
    if ":" not in tok:
        plain = tok.replace(",", "")
        variants.add(plain)
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", tok):  # 902.990.000 (es-CO thousands)
            variants.add(tok.replace(".", ""))
        if re.fullmatch(r"\d+\.0+", plain):
            variants.add(plain.split(".")[0])
    return [v for v in variants if v]


def ungrounded_numbers(text: str, evidence_text: str) -> list[str]:
    """Numbers in a claim that appear nowhere in its cited evidence (ignoring single digits)."""
    hay = evidence_text.replace(",", "")
    hay_raw = evidence_text
    missing = []
    for tok in NUM_RE.findall(text):
        t = tok.strip(".,:")
        if len(t.replace(",", "").replace(".", "").replace(":", "")) < 2:
            continue
        if not any(v in hay or v in hay_raw for v in _norm_number(t)):
            missing.append(t)
    return missing


def flatten_claims(cf: dict) -> list[dict]:
    claims = []

    def add(cid, section, item, label=None):
        if isinstance(item, dict) and item.get("text"):
            text = f"{item['time']} — {item['text']}" if item.get("time") else item["text"]
            claims.append({"id": cid, "section": section, "label": label or section, "text": text,
                           "evidence": [str(e).strip() for e in item.get("evidence", []) if str(e).strip()]})

    add("summary", "summary", cf.get("summary"))
    for i, it in enumerate(cf.get("impact", []) or []):
        add(f"impact.{i}", "impact", it)
    for i, it in enumerate(cf.get("timeline", []) or []):
        add(f"timeline.{i}", "timeline", it)
    add("root_cause", "root_cause", cf.get("root_cause"))
    for i, it in enumerate(cf.get("ruled_out", []) or []):
        add(f"ruled_out.{i}", "ruled_out", it)
    return claims


# ------------------------------------------------------------------- agent
class Investigation:
    def __init__(self, scenario: dict, agent_llm: LLM, verifier_llm: LLM, vision_llm: LLM,
                 tools: SandboxTools, emit: Emit) -> None:
        self.scenario = scenario
        self.agent = agent_llm
        self.verifier = verifier_llm
        self.vision = vision_llm
        self.tools = tools
        self.emit = emit
        self.ledger = Ledger()
        self.stats = {"model_calls": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "verifier_rounds": 0, "started": time.time()}

    # bookkeeping ---------------------------------------------------------
    def _usage(self, reply) -> None:
        self.stats["model_calls"] += 1
        self.stats["prompt_tokens"] += int(reply.usage.get("prompt_tokens") or 0)
        self.stats["completion_tokens"] += int(reply.usage.get("completion_tokens") or 0)

    def _evidence(self, ev: Evidence) -> Evidence:
        self.emit({"type": "evidence", "evidence": ev.to_dict()})
        return ev

    # intake ----------------------------------------------------------------
    def intake(self, transcript: dict | None, screenshot_png: bytes | None) -> list[dict]:
        self.emit({"type": "stage", "stage": "intake"})
        sc = self.scenario
        self._evidence(self.ledger.add("ticket", "Ticket", sc["ticket"][:200], sc["ticket"], "ticketing system"))
        if transcript and transcript.get("text"):
            src = transcript.get("source", "muse-voice-transcribe-1.0")
            self._evidence(self.ledger.add("voice", "Voice note from reporter", transcript["text"][:200],
                                           transcript["text"], src))
        else:
            self._evidence(self.ledger.add("voice", "Voice note from reporter", "(no voice note)", "(none)", "n/a"))
        image_part = None
        if screenshot_png:
            data_url = "data:image/png;base64," + base64.b64encode(screenshot_png).decode()
            image_part = {"type": "image_url", "image_url": {"url": data_url}}
            self.emit({"type": "status", "text": f"Reading screenshot with {self.vision.label}"})
            try:
                reply = self.vision.chat([{"role": "user", "content": [{"type": "text", "text": VISION_PROMPT},
                                                                       image_part]}], max_tokens=1500)
                self._usage(reply)
                facts = reply.content.strip()
            except LLMError as exc:
                facts = f"(screenshot could not be read: {exc})"
            self._evidence(self.ledger.add("screenshot", "Screenshot facts (vision)", facts[:200], facts,
                                           self.vision.label))
        else:
            self._evidence(self.ledger.add("screenshot", "Screenshot facts (vision)", "(no screenshot)", "(none)", "n/a"))

        text = (
            f"Incident assigned to you. Investigation clock: {sc['now']} (local, UTC-5).\n\n"
            f"E1 · Ticket:\n{sc['ticket']}\n\n"
            f"E2 · Voice note from the reporter (Muse Voice Transcribe):\n{self.ledger.items['E2'].text()}\n\n"
            f"E3 · Facts read from the attached screenshot:\n{self.ledger.items['E3'].text()}\n\n"
            "The screenshot itself is attached. Investigate and submit the casefile."
        )
        content: list[dict] = [{"type": "text", "text": text}]
        if image_part:
            content.append(image_part)
        return [{"role": "system", "content": AGENT_SYSTEM}, {"role": "user", "content": content}]

    # tool execution --------------------------------------------------------
    def _run_tool(self, call) -> dict:
        args = call.parsed()
        if "__invalid_json__" in args:
            return {"error": "Arguments were not valid JSON."}
        return self.tools.execute(call.name, args)

    # verification ----------------------------------------------------------
    def verify(self, cf: dict) -> dict:
        self.emit({"type": "stage", "stage": "verify"})
        self.emit({"type": "status", "text": f"Independent verification with {self.verifier.label}"})
        self.stats["verifier_rounds"] += 1
        claims = flatten_claims(cf)
        results: dict[str, dict] = {}
        blocks = []
        for c in claims:
            unknown = [e for e in c["evidence"] if e not in self.ledger.items]
            cited = [self.ledger.items[e] for e in c["evidence"] if e in self.ledger.items]
            ev_text = "\n".join(e.text() for e in cited)
            results[c["id"]] = {
                "text": c["text"], "section": c["section"], "evidence": c["evidence"],
                "unknown_evidence": unknown,
                "ungrounded_numbers": ungrounded_numbers(c["text"], ev_text) if cited else [],
                "verdict": "UNSUPPORTED" if not cited else None, "reason": "No valid evidence cited." if not cited else "",
            }
            if cited:
                ev_blob = "\n".join(f"[{e.id}] ({e.kind}: {e.title})\n{e.text()[:6000]}" for e in cited)
                blocks.append(f"### Claim {c['id']}\n{c['text']}\n--- cited evidence ---\n{ev_blob}")
        if blocks:
            msgs = [{"role": "system", "content": VERIFIER_SYSTEM},
                    {"role": "user", "content": "\n\n".join(blocks)}]
            try:
                reply = self.verifier.chat(msgs, json_mode=True, max_tokens=4000)
                self._usage(reply)
                verdicts = extract_json(reply.content).get("verdicts", [])
            except (LLMError, ValueError, json.JSONDecodeError) as exc:
                verdicts = []
                self.emit({"type": "status", "text": f"Verifier error: {exc}"})
            for v in verdicts:
                r = results.get(str(v.get("id")))
                if r and r["verdict"] is None:
                    verdict = str(v.get("verdict", "")).upper()
                    r["verdict"] = verdict if verdict in ("SUPPORTED", "PARTIAL", "UNSUPPORTED") else "PARTIAL"
                    r["reason"] = v.get("reason", "")
        for r in results.values():
            if r["verdict"] is None:
                r["verdict"], r["reason"] = "PARTIAL", "Verifier returned no verdict for this claim."
            # deterministic check can only downgrade
            if r["verdict"] == "SUPPORTED" and r["ungrounded_numbers"]:
                r["verdict"] = "PARTIAL"
                r["reason"] = (r["reason"] + " " if r["reason"] else "") + \
                    f"Numbers not found in cited evidence: {', '.join(r['ungrounded_numbers'])}."
        counts = {k: sum(1 for r in results.values() if r["verdict"] == k) for k in ("SUPPORTED", "PARTIAL", "UNSUPPORTED")}
        passed = counts["UNSUPPORTED"] == 0 and counts["PARTIAL"] <= max(1, len(results) // 6)
        report = {"claims": results, "counts": counts, "passed": passed, "verifier": self.verifier.label}
        self.emit({"type": "verification", "report": report})
        return report

    # main loop -------------------------------------------------------------
    def run(self, transcript: dict | None, screenshot_png: bytes | None) -> dict:
        messages = self.intake(transcript, screenshot_png)
        self.emit({"type": "stage", "stage": "investigate"})
        casefile, report, attempts = None, None, 0
        nudged = False
        for _turn in range(MAX_AGENT_TURNS):
            self.emit({"type": "status", "text": f"{self.agent.label} is thinking"})
            reply = self.agent.chat(messages, tools=TOOL_SCHEMAS)
            self._usage(reply)
            messages.append(reply.message)
            if reply.content.strip():
                self.emit({"type": "model_text", "text": reply.content.strip()})
            if not reply.tool_calls:
                if nudged:
                    break
                nudged = True
                messages.append({"role": "user", "content": "Continue the investigation with tool calls, "
                                 "then finish by calling submit_casefile."})
                continue

            submit = [c for c in reply.tool_calls if c.name == "submit_casefile"]
            regular = [c for c in reply.tool_calls if c.name != "submit_casefile"]
            for c in regular:
                self.emit({"type": "tool_call", "call_id": c.id, "name": c.name, "args": c.parsed()})
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(self._run_tool, regular))
            for c, result in zip(regular, results):  # evidence IDs follow call order
                args = c.parsed()
                title, summary = summarize_result(c.name, args, result)
                ev = self.ledger.add(TOOL_KIND.get(c.name, c.name), title, summary, result, c.name, args)
                result = {"evidence_id": ev.id, **result}
                call_id = c.id
                self.stats["tool_calls"] += 1
                self.emit({"type": "tool_result", "call_id": call_id, "evidence_id": ev.id})
                self._evidence(ev)
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": json.dumps(result, ensure_ascii=False)[:20000]})
            for c in submit:
                attempts += 1
                casefile = c.parsed()
                self.emit({"type": "casefile", "casefile": casefile, "attempt": attempts})
                report = self.verify(casefile)
                if report["passed"] or attempts > MAX_REVISIONS:
                    messages.append({"role": "tool", "tool_call_id": c.id,
                                     "content": json.dumps({"status": "accepted"})})
                    return self._finish(casefile, report)
                problems = [
                    {"claim": cid, "verdict": r["verdict"], "reason": r["reason"],
                     **({"numbers_not_in_evidence": r["ungrounded_numbers"]} if r["ungrounded_numbers"] else {}),
                     **({"unknown_evidence_ids": r["unknown_evidence"]} if r["unknown_evidence"] else {})}
                    for cid, r in report["claims"].items() if r["verdict"] != "SUPPORTED"
                ]
                self.emit({"type": "stage", "stage": "investigate"})
                self.emit({"type": "revision", "problems": problems})
                messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps({
                    "status": "rejected_by_verifier",
                    "problems": problems,
                    "instructions": "Fix every problem: gather the missing evidence with tools (compute numbers with SQL), "
                                    "correct or drop unsupported statements, move unprovable ones to open_questions, "
                                    "then call submit_casefile again with the complete casefile."})})
        if casefile:
            return self._finish(casefile, report or self.verify(casefile))
        raise LLMError("The agent stopped without submitting a casefile.")

    def _finish(self, casefile: dict, report: dict) -> dict:
        self.stats["duration_s"] = round(time.time() - self.stats.pop("started"), 1)
        result = {
            "scenario": self.scenario["id"],
            "casefile": casefile,
            "verification": report,
            "evidence": [e.to_dict() for e in self.ledger.items.values()],
            "stats": self.stats,
            "models": {"agent": self.agent.label, "verifier": self.verifier.label, "vision": self.vision.label},
        }
        self.emit({"type": "stage", "stage": "done"})
        self.emit({"type": "final", "result": result})
        return result
