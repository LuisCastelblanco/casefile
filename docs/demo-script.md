# Demo video script (3:00)

Record the screen at 1600×1000 or larger, browser zoom 90%. Use the **double-payout** incident as the main story (money moving twice is the clearest stakes) and show **otp-delay** briefly. Do a full real run first and keep it saved: if the live run is slow, use "Replay a saved investigation", which replays a real run with its real timing compressed.

---

**0:00 – 0:20 · The problem** (face cam or voice over the UI with the ticket visible)

> "I work in L2 production support for payment platforms. When something breaks, I don't fix the code. I gather the evidence, figure out what changed, and escalate to developers. A merchant was just paid twice. The report I get is a voice note and a screenshot. Finding out how many merchants, how much money, and why takes an hour of SQL, logs and deploy history. Casefile does it in about a minute, and proves every sentence."

**0:20 – 0:45 · Intake with Muse Voice Transcribe and vision**

- Click **Merchants paid twice**. Point at the screenshot (two identical deposits).
- Click the red mic and read the voice note out loud (the text in the box). Stop.
- Say: "Muse Voice Transcribe turns the voice note into text, biased toward payments vocabulary like idempotency and ledger."
- Click **Investigate**.

**0:45 – 1:35 · The agent investigates**

- Let the trace run. Narrate over it:
  > "Muse Spark reads the ticket, the transcript and the screenshot, then investigates on its own with parallel tool calls: SQL against a read-only replica, logs, metrics, deployments with config diffs. Every result becomes a numbered piece of evidence."
- Click one SQL evidence marker to open it: "This query counted the duplicated payouts and the total amount."
- Point at the deployment evidence: "And here's the change: payout-service 3.1.0 turned on automatic retries and renamed the idempotency header, the one the ledger actually reads."

**1:35 – 2:15 · The verifier (the key moment)**

- When the casefile appears with the "Checking" stamp, slow down:
  > "Before anything is escalated, an independent verifier checks every claim against only the evidence it cites. It runs on Muse Spark Open, a different model, and a deterministic check rejects any number that isn't in the evidence."
- If a rejection happens: "Here it rejected two claims. The agent goes back, runs the missing query, and resubmits." (If your best run has no rejection, show the replay of one that does, or skip this line.)
- Hover an evidence marker so the claim highlights.

**2:15 – 2:40 · The result**

- Scroll the casefile: impact with numbers, timeline, root cause with confidence, ruled out, next actions per team, verification queries.
- Click **Copy as Markdown**: "This goes straight into Jira. The developer doesn't need to ask me anything."
- Quick cut to the OTP incident finished: "Here the call center was sure the SMS provider was down. The evidence says the provider was fine; a config change cut the workers from 12 to 2. Escalating the wrong guess would have cost an hour."

**2:40 – 3:00 · Close**

> "Casefile: Muse Voice Transcribe for intake, Muse Spark with vision and tool calling for the investigation, and Muse Spark Open as an independent verifier. Everything you saw was built during the hackathon, on a fully synthetic payments sandbox. From alert to a verified escalation, every claim backed by evidence."

---

Checklist before recording
- [ ] `python scripts/smoke_test.py` passes
- [ ] One complete real run per incident, saved
- [ ] Header shows real model names (not "mock")
- [ ] Notifications off, browser bookmarks bar hidden
