# Runbook: OTP / notification delivery (notification-dispatcher)

Owner: messaging-platform (dev), L2 payments (first response)

## Flow
customer request -> `otp_messages.requested_at` -> queue (`enqueued_at`) -> dispatcher worker picks it (`dispatched_at`) -> SMS provider "Altavoz SMS" ack (`provider_ack_at`).

OTP codes expire after 5 minutes. Any dispatch lag above ~60 s causes visible customer failures (expired codes, retries, call-center spikes).

## Triage
- Split the latency: queue time (`dispatched_at - enqueued_at`) vs provider time (`provider_ack_at - dispatched_at`).
- If provider time is normal, the problem is on our side (consumer capacity, queue, deploys).
- Check `notification-dispatcher` metrics: `queue_depth`, `active_workers`, `dispatch_lag_p95_seconds`.
- Check `sms-gateway` metrics: `provider_latency_p95_ms`, `provider_error_rate_pct`.
- Altavoz publishes a status page; our `status-poller` logs changes. Note the affected regions: we only send traffic to LATAM routes.

## Known configuration
`consumer.max_workers` must be >= 10 during business hours (06:00-22:00). A night-time scale-down window exists for cost saving.

## Escalation
Escalate to **messaging-platform**. Urgent if lag > 120 s during business hours (customers cannot log in or pay).
