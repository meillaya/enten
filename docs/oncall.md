# Maintainer response policy

`enten` is currently a community project without a staffed on-call rotation, SLA, PagerDuty schedule, or private escalation channel. Do not rely on this repository for an emergency response commitment.

## Reporting

- Security-sensitive reports should use the repository's GitHub Security Advisory flow when available; do not paste credentials, webhook payloads, private keys, or raw scanner matches into a public issue.
- Operational defects may be filed as GitHub issues with redacted logs, the release commit SHA, Compose version, Docker version, and the failing runbook command.
- GitHub App credentials must be revoked or rotated through GitHub first; opening an issue is not containment.

## Maintainer checklist

1. Reproduce on a disposable repository and isolated Compose project.
2. Preserve the job `run_id`, trace ID, claim generation, and redacted effect/checkpoint rows.
3. Stop the worker before reconciling an `indeterminate` external effect.
4. Run the relevant playbook in [`runbook.md`](runbook.md).
5. Add a regression test before the repair and run `make check` before release.

Future maintainers may add a real rotation only when named responders, contact paths, handoff time, and response expectations exist. Placeholder contact information is intentionally absent.
