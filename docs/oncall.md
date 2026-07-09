# enten On-Call Rotation

> This project is currently maintained by a single developer. The fields
> below are placeholders so the rotation can be expanded once the team
> grows.

## Rotation

| Role | Primary | Secondary | Escalation |
|------|---------|-----------|------------|
| Current | (single developer) | (same) | (project owner / email) |

## Contact

- **Primary:** ___________________
- **Secondary:** ___________________
- **Escalation:** ___________________
- **Slack/Discord:** ___________________
- **PagerDuty schedule:** ___________________

## Rotation rules

- **Frequency:** ___________________ (e.g. weekly)
- **Handoff time:** ___________________ (e.g. Monday 09:00 local time)
- **Handoff checklist:**
  1. Review open alerts from the previous rotation.
  2. Confirm `scripts/healthcheck-all.sh` passes.
  3. Check `scripts/error-budget-check.sh` for the last 24 hours.
  4. Note any in-flight incidents in the on-call log.

## Runbooks

- Incident response: `docs/runbook.md`
- Architecture and context: `docs/architecture.md`
