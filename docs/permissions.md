# GitHub App permission contract

`packages/ts/app/github-app.yml` is the executable authority. The release subscribes only to `pull_request` and requests exactly:

| Permission | Level | Reachable operation |
|---|---:|---|
| `metadata` | read | Installation/repository routing |
| `pull_requests` | write | Read PR metadata and create/update the bot's issue comment |
| `checks` | write | Create/update a Check Run for the reviewed head SHA |

No `contents`, `issues`, `actions`, `workflows`, administration, deployment, or branch permission is granted. The worker fetches public Git objects anonymously and cannot push a branch or commit.

Only `opened`, `reopened`, `synchronize`, and non-draft `ready_for_review` actions enqueue work. Adding an event, action, endpoint, or permission requires updating the parsed contract tests and this document in the same change.

## Verify

```bash
bash scripts/check-permissions.sh
pnpm --filter @enten/app test -- webhook.test.ts
```

The checks compare exact parsed sets; substring greps are not treated as proof of least privilege.
