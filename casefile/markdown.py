"""Render a verified casefile as Markdown, ready to paste into Jira / GitHub / Slack."""
from __future__ import annotations

MARK = {"SUPPORTED": "✅", "PARTIAL": "⚠️", "UNSUPPORTED": "❌"}


def casefile_markdown(result: dict) -> str:
    cf, ver = result["casefile"], result["verification"]["claims"]
    ev = {e["id"]: e for e in result["evidence"]}

    def cite(cid: str, refs: list[str]) -> str:
        v = ver.get(cid, {}).get("verdict", "")
        return f" {MARK.get(v, '')} _[{', '.join(refs)}]_" if refs else ""

    lines = [f"# {cf.get('title', 'Casefile')}", "",
             f"**Severity:** {cf.get('severity')} · **Decision:** {cf.get('decision')} · **Owner:** {cf.get('owner_team')}",
             "", "## Summary", (cf.get("summary") or {}).get("text", "") + cite("summary", (cf.get("summary") or {}).get("evidence", [])),
             "", "## Impact"]
    lines += [f"- {it['text']}{cite(f'impact.{i}', it.get('evidence', []))}" for i, it in enumerate(cf.get("impact", []))]
    lines += ["", "## Timeline (UTC-5)"]
    lines += [f"- **{it.get('time')}** {it['text']}{cite(f'timeline.{i}', it.get('evidence', []))}"
              for i, it in enumerate(cf.get("timeline", []))]
    rc = cf.get("root_cause") or {}
    lines += ["", f"## Probable root cause (confidence {round(float(rc.get('confidence', 0)) * 100)}%)",
              rc.get("text", "") + cite("root_cause", rc.get("evidence", []))]
    if cf.get("ruled_out"):
        lines += ["", "## Ruled out"]
        lines += [f"- {it['text']}{cite(f'ruled_out.{i}', it.get('evidence', []))}" for i, it in enumerate(cf["ruled_out"])]
    if cf.get("next_actions"):
        lines += ["", "## Next actions"] + [f"- **{a.get('owner')}**: {a.get('text')}" for a in cf["next_actions"]]
    if cf.get("verification_queries"):
        lines += ["", "## How to verify"] + [f"```\n{q}\n```" for q in cf["verification_queries"]]
    if cf.get("open_questions"):
        lines += ["", "## Open questions"] + [f"- {q}" for q in cf["open_questions"]]
    cited = sorted({r for c in ver.values() for r in c.get("evidence", []) if r in ev}, key=lambda x: int(x[1:]))
    lines += ["", "## Evidence"]
    for rid in cited:
        e = ev[rid]
        lines.append(f"- **{rid}** · {e['title']} — {e['summary']}")
    counts = result["verification"]["counts"]
    lines += ["", f"_Verified by {result['verification']['verifier']}: {counts['SUPPORTED']} supported, "
              f"{counts['PARTIAL']} partial, {counts['UNSUPPORTED']} unsupported. "
              f"Investigated by {result['models']['agent']} with {result['stats']['tool_calls']} tool calls._"]
    return "\n".join(lines) + "\n"
