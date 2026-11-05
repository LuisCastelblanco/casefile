# Submission text (Cerebral Valley form)

**Project name:** Casefile

**Track:** Indie Developer · Problem statement: Build for Your World

**One-liner:** An autonomous L2 incident investigator for payment platforms that turns a voice note and a screenshot into a verified escalation where every claim cites the evidence that proves it.

**Description**

I work in L2 production support for payment platforms. When a payment system breaks, L2 doesn't fix code: we gather evidence, find what changed, measure impact and escalate to developers. Reports arrive as voice notes and screenshots, the investigation is an hour of manual SQL, logs and deploy history, and the escalation is often a guess that sends developers the wrong way.

Casefile automates that job end to end:
- **Muse Voice Transcribe** transcribes the reporter's voice note with keyword biasing for payments jargon; **Muse Spark** image understanding turns the attached screenshot into citable facts.
- A **Muse Spark** agent investigates autonomously with parallel tool calls over a read-only replica (SQL, logs, metrics, deployments with config diffs, runbooks), quantifies impact, finds the triggering change, and tries to disprove the reporter's hypothesis.
- Every tool result becomes numbered evidence, and every statement in the casefile must cite it.
- An **independent verifier**, a separate Muse Spark 1.3 pass via the Meta Model API, judges each claim only against its cited evidence, and a deterministic check rejects numbers that do not appear in the evidence. Rejected claims go back to the agent, which investigates further and resubmits.
- The result is an escalation developers can act on immediately: impact, timeline, root cause with confidence, what was ruled out, next actions per team and verification queries, exported as Markdown.

The demo runs on a fully synthetic payments company with three incidents, each with a misleading first report: a reconciliation spike blamed on a late file (really a timezone change in a parser), OTP delays blamed on the SMS provider (really a config change from 12 to 2 workers), and merchants paid twice (retries plus a renamed idempotency header).

**Built during the hackathon:** everything in the repository (sandbox, agent, tools, evidence ledger, verifier, voice and vision intake, server, UI), November 3–4, 2026.

**Meta models:** Muse Spark 1.3 via the Meta Model API (agent, tool calling, vision, verifier), Muse Voice Transcribe 1.0. Any role can be switched to Muse Spark Open on Baseten with one env var.

**Bonus prizes to flag:** Best Agentic Use-Case; Best Build on Meta Open Weight Models (the pipeline runs on Muse Spark Open with `LLM_PROVIDER=baseten`).

---

# X post (for Most Viral)

> A merchant gets paid twice. L2 support gets a voice note and a screenshot.
>
> I built Casefile for the @Meta AI hackathon: Muse Spark investigates on its own (SQL, logs, deploys), and an independent verifier rejects any claim the evidence doesn't prove.
>
> 60 seconds from alert to a ticket devs can act on. 🧵👇 [video]

Reply 1: "The trick: every sentence in the casefile cites numbered evidence. A deterministic check rejects any number that isn't in the cited query result. Hallucinated escalations don't get through."

Reply 2: "All three incidents start with the wrong guess (late file, SMS outage, isolated merchant). The agent has to prove the real cause. Repo: <link>"
