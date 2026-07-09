# GitHub App Permission Scope

This document records the exact OAuth permission scope requested by the
`enten-bot` GitHub App. The scope is intentionally minimal: the bot only
reviews pull requests and posts check-run results. It never writes code,
commits, branches, workflow files, or issues.

## Granted permissions

| Permission | Level | Why it is needed |
|------------|-------|------------------|
| `pull_requests` | `write` | Post review comments and PR descriptions while reviewing pull requests. |
| `checks` | `write` | Create and update check runs via the Checks API so review results appear in the PR status area. |
| `metadata` | `read` | Read repository metadata (name, owner, visibility) required for GitHub App authentication and routing. |

## Explicitly forbidden permissions

The following permissions are **never** requested. Their absence is part of
the security model: a compromised bot token must not be able to modify source
files, execute CI pipelines, or open issues on its own.

| Permission | Level | Why it is denied |
|------------|-------|------------------|
| `contents` | `write` | The bot must not push commits, create branches, or modify repository files. Code changes are returned as review feedback, not direct writes. |
| `issues` | `write` | The bot does not create or edit issues. |
| `actions` | `write` | The bot does not trigger or modify GitHub Actions workflows. |

## Verification

Run the permission audit script to confirm no forbidden scope has been added:

```bash
bash scripts/check-permissions.sh
```

The script exits with code `1` if any forbidden pattern listed above is
found in the manifest, this document, or deployment configuration files.
