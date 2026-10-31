# Maintainer response policy

The project is community-run. Response depends on maintainer availability, so
treat this repository as a community project rather than an emergency service.
There is currently no staffed rotation, SLA, PagerDuty schedule, or private
escalation channel.

## Reporting

- Use the repository's GitHub Security Advisory flow for security-sensitive
  reports. Do not paste credentials, webhook payloads, private keys, or raw
  scanner matches into a public issue.
- File operational defects as GitHub issues with redacted logs, the release
  commit SHA, Compose version, Docker version, and the failing runbook command.
- Revoke or rotate GitHub App credentials through GitHub first. An issue is not
  containment.

## Maintainer checklist

1. Reproduce on a disposable repository and an isolated Compose project.
2. Preserve the job `run_id`, trace ID, claim generation, and redacted effect
   and checkpoint rows.
3. Stop the worker before reconciling an `indeterminate` external effect.
4. Run the relevant playbook in [`runbook.md`](runbook.md).
5. Add a regression test before the repair, then run `make check` before
   release.

A real rotation can be added when named responders, contact paths, handoff
time, and response expectations exist. Placeholder contacts are intentionally
absent.
