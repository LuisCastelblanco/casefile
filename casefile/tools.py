"""Read-only investigation tools over the synthetic Kestrel Pay sandbox.

Every call returns plain JSON. The agent never gets write access: SQL runs on a
read-only connection with an authorizer that only allows SELECT.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import SANDBOX_DIR

DATA = SANDBOX_DIR / "data"
RUNBOOKS = SANDBOX_DIR / "runbooks"
MAX_ROWS = 40
MAX_LOG_LINES = 40


def ensure_sandbox() -> None:
    """Generate the synthetic data on first run (it is not committed to git)."""
    if not (DATA / "kestrel.db").exists():
        from sandbox.build_sandbox import build
        build()
    if not (SANDBOX_DIR / "screenshots" / "recon.png").exists():
        from sandbox.render_screenshots import main as render
        render()


class SandboxTools:
    def __init__(self, data_dir: Path = DATA, runbook_dir: Path = RUNBOOKS) -> None:
        if data_dir == DATA:
            ensure_sandbox()
        self.data_dir = data_dir
        self.runbook_dir = runbook_dir
        self._metrics = json.loads((data_dir / "metrics.json").read_text(encoding="utf-8"))
        self._logs: dict[str, list[dict]] = {}
        for f in sorted((data_dir / "logs").glob("*.jsonl")):
            self._logs[f.stem] = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ---------------------------------------------------------------- sql
    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.data_dir / 'kestrel.db'}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)

        def authorizer(action, *_):
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer)
        deadline = time.monotonic() + 5
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        return conn

    def describe_environment(self) -> dict:
        conn = self._connect()
        conn.set_authorizer(None)
        tables = {}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            cols = [f"{r[1]} {r[2]}" for r in conn.execute(f"PRAGMA table_info({name})")]
            count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            tables[name] = {"columns": cols, "rows": count}
        conn.close()
        return {
            "company": "Kestrel Pay (synthetic sandbox)",
            "timezone": "All local times are America/Bogota (UTC-5). Log timestamps carry their offset.",
            "services_with_logs": {svc: len(lines) for svc, lines in self._logs.items()},
            "metrics": {svc: sorted(series) for svc, series in self._metrics.items()},
            "database_tables": tables,
            "runbooks": sorted(p.stem for p in self.runbook_dir.glob("*.md")),
        }

    def run_sql(self, query: str) -> dict:
        q = query.strip().rstrip(";")
        if not re.match(r"(?is)^\s*(select|with)\b", q):
            return {"error": "Only SELECT/WITH queries are allowed (read-only)."}
        if ";" in q:
            return {"error": "One statement per call."}
        conn = self._connect()
        try:
            cur = conn.execute(q)
            cols = [c[0] for c in cur.description or []]
            rows = cur.fetchmany(MAX_ROWS + 1)
        except sqlite3.Error as exc:
            return {"error": f"SQL error: {exc}"}
        finally:
            conn.close()
        truncated = len(rows) > MAX_ROWS
        return {
            "query": q,
            "columns": cols,
            "rows": [list(r) for r in rows[:MAX_ROWS]],
            "row_count_returned": min(len(rows), MAX_ROWS),
            "truncated": truncated,
            **({"hint": "Result truncated; aggregate with COUNT/SUM/GROUP BY instead of listing rows."} if truncated else {}),
        }

    # ---------------------------------------------------------------- logs
    def search_logs(self, service: str, contains: str | None = None, level: str | None = None,
                    start: str | None = None, end: str | None = None, limit: int = 25) -> dict:
        if service not in self._logs and service != "*":
            return {"error": f"Unknown service {service!r}.", "services": sorted(self._logs)}
        pool = [e for svc, lines in self._logs.items() if service in ("*", svc) for e in lines]
        pool.sort(key=lambda e: e["ts"])
        needle = (contains or "").lower()
        out = []
        for e in pool:
            if level and e["level"].upper() != level.upper():
                continue
            if start and e["ts"] < start:
                continue
            if end and e["ts"] > end:
                continue
            if needle and needle not in json.dumps(e).lower():
                continue
            out.append(e)
        limit = max(1, min(int(limit or 25), MAX_LOG_LINES))
        return {
            "service": service,
            "filters": {k: v for k, v in {"contains": contains, "level": level, "start": start, "end": end}.items() if v},
            "total_matches": len(out),
            "lines": out[:limit],
            "truncated": len(out) > limit,
        }

    # ------------------------------------------------------------- metrics
    def get_metric(self, service: str, metric: str, start: str | None = None, end: str | None = None) -> dict:
        series = self._metrics.get(service, {}).get(metric)
        if series is None:
            return {"error": f"Unknown metric {service}/{metric}.",
                    "available": {s: sorted(m) for s, m in self._metrics.items()}}
        pts = [p for p in series if (not start or p[0] >= start) and (not end or p[0] <= end)]
        if not pts:
            return {"service": service, "metric": metric, "points": [], "note": "no points in range"}
        def r1(x):
            v = round(x, 1)
            return int(v) if v == int(v) else v

        vals = [p[1] for p in pts]
        step = max(1, len(pts) // 24)
        peak = max(pts, key=lambda p: p[1])
        return {
            "service": service, "metric": metric,
            "range": [pts[0][0], pts[-1][0]],
            "summary": {"first": r1(vals[0]), "last": r1(vals[-1]), "min": r1(min(vals)), "max": r1(max(vals)),
                        "max_at": peak[0], "points": len(vals)},
            "sampled_points": [[p[0], r1(p[1])] for p in pts[::step]],
        }

    # --------------------------------------------------------- deployments
    def list_deployments(self, service: str | None = None, since: str | None = None, until: str | None = None) -> dict:
        conn = self._connect()
        conn.set_authorizer(None)
        sql = "SELECT * FROM deployments WHERE 1=1"
        args: list[Any] = []
        if service:
            sql += " AND service = ?"
            args.append(service)
        if since:
            sql += " AND deployed_at >= ?"
            args.append(since)
        if until:
            sql += " AND deployed_at <= ?"
            args.append(until)
        sql += " ORDER BY deployed_at"
        cols = ["id", "service", "version", "deployed_at", "author", "change_ticket", "summary"]
        deps = [dict(zip(cols, r)) for r in conn.execute(sql, args)]
        for d in deps:
            d["config_changes"] = [
                {"key": k, "old": o, "new": n}
                for k, o, n in conn.execute(
                    "SELECT key, old_value, new_value FROM config_changes WHERE deployment_id = ?", (d["id"],))
            ]
        conn.close()
        return {"filters": {"service": service, "since": since, "until": until}, "deployments": deps}

    # ------------------------------------------------------------ runbooks
    def read_runbook(self, name: str) -> dict:
        path = self.runbook_dir / f"{Path(name).stem}.md"
        if not path.exists():
            return {"error": f"No runbook {name!r}.", "available": sorted(p.stem for p in self.runbook_dir.glob('*.md'))}
        return {"runbook": path.stem, "content": path.read_text(encoding="utf-8")}

    # ------------------------------------------------------------ dispatch
    def execute(self, name: str, args: dict) -> dict:
        fn = {
            "describe_environment": self.describe_environment,
            "run_sql": self.run_sql,
            "search_logs": self.search_logs,
            "get_metric": self.get_metric,
            "list_deployments": self.list_deployments,
            "read_runbook": self.read_runbook,
        }.get(name)
        if fn is None:
            return {"error": f"Unknown tool {name!r}."}
        try:
            return fn(**args)
        except TypeError as exc:
            return {"error": f"Bad arguments for {name}: {exc}"}


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}


EVIDENCE_REF = {"type": "array", "items": {"type": "string"},
                "description": "Evidence IDs (e.g. E3, E7) whose content directly supports this statement."}
CLAIM = {"type": "object", "properties": {"text": {"type": "string"}, "evidence": EVIDENCE_REF},
         "required": ["text", "evidence"], "additionalProperties": False}

TOOL_SCHEMAS = [
    _fn("describe_environment",
        "List services with logs, available metrics, database tables (columns and row counts) and runbooks. Call first.",
        {}, []),
    _fn("run_sql",
        "Run ONE read-only SQLite SELECT against the production replica. Returns at most 40 rows; aggregate with "
        "COUNT/SUM/GROUP BY/MIN/MAX to measure impact. Timestamps are ISO-8601 text with offset.",
        {"query": {"type": "string"}}, ["query"]),
    _fn("search_logs",
        "Search structured logs of one service (or '*' for all). Filters are optional; start/end are ISO-8601 "
        "with offset, e.g. 2026-11-02T08:00:00-05:00.",
        {"service": {"type": "string"}, "contains": {"type": "string"},
         "level": {"type": "string", "enum": ["DEBUG", "INFO", "WARN", "ERROR"]},
         "start": {"type": "string"}, "end": {"type": "string"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 40}},
        ["service"]),
    _fn("get_metric", "Get a metric time series for a service with summary statistics.",
        {"service": {"type": "string"}, "metric": {"type": "string"},
         "start": {"type": "string"}, "end": {"type": "string"}},
        ["service", "metric"]),
    _fn("list_deployments", "List deployments and their config changes (old -> new values), optionally filtered.",
        {"service": {"type": "string"}, "since": {"type": "string"}, "until": {"type": "string"}}, []),
    _fn("read_runbook", "Read an internal runbook by name.", {"name": {"type": "string"}}, ["name"]),
    _fn("submit_casefile",
        "Submit the finished escalation casefile. Every factual statement must cite evidence IDs. "
        "It will be checked by an independent verifier before it is escalated.",
        {
            "title": {"type": "string"},
            "severity": {"type": "string", "enum": ["SEV1", "SEV2", "SEV3", "SEV4"]},
            "decision": {"type": "string", "enum": ["escalate", "resolve_in_l2", "monitor"]},
            "owner_team": {"type": "string", "description": "Team that should own the fix."},
            "summary": CLAIM,
            "impact": {"type": "array", "items": CLAIM},
            "timeline": {"type": "array", "items": {
                "type": "object", "properties": {"time": {"type": "string"}, "text": {"type": "string"},
                                                 "evidence": EVIDENCE_REF},
                "required": ["time", "text", "evidence"], "additionalProperties": False}},
            "root_cause": {"type": "object", "properties": {
                "text": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence": EVIDENCE_REF}, "required": ["text", "confidence", "evidence"],
                "additionalProperties": False},
            "ruled_out": {"type": "array", "items": CLAIM,
                          "description": "Hypotheses investigated and rejected, with the evidence that rejects them."},
            "next_actions": {"type": "array", "items": {
                "type": "object", "properties": {"owner": {"type": "string"}, "text": {"type": "string"}},
                "required": ["owner", "text"], "additionalProperties": False}},
            "verification_queries": {"type": "array", "items": {"type": "string"},
                                     "description": "SQL or log searches the dev team can run to confirm the finding."},
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
        ["title", "severity", "decision", "owner_team", "summary", "impact", "timeline", "root_cause",
         "ruled_out", "next_actions", "verification_queries", "open_questions"]),
]
