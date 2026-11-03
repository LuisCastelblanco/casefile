# L2 Payments: escalation standard

An escalation to a dev team is accepted only if it includes:
1. Customer/business impact with numbers (how many, how much, since when).
2. A timeline with timestamps (local time, UTC-5).
3. The evidence: queries run, log lines, metrics, deployment/config changes.
4. What was ruled out and why.
5. A probable root cause with confidence, and the team that owns it.
6. Reproduction or verification steps the dev team can run.

Severity guide: SEV1 money movement errors or customers unable to authenticate at scale; SEV2 degraded service with workaround; SEV3 internal/reporting impact; SEV4 cosmetic.
