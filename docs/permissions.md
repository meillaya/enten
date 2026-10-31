# GitHub App permission contract

`packages/ts/app/github-app.yml` is the executable authority. The release
subscribes only to `pull_request` and requests exactly:

Before creating the App, replace the `example.com` webhook placeholder in that
manifest with the externally reachable HTTPS origin that routes to `/webhook`.
GitHub App manifests do not expand deployment environment variables.

| Permission | Level | Reachable operation |
|---|---:|---|
| `metadata` | read | Installation and repository routing |
| `pull_requests` | write | Read pull request metadata and create or update the bot comment |
| `checks` | write | Create or update a Check Run for the reviewed head SHA |

The App has no `contents`, `issues`, `actions`, `workflows`, administration,
deployment, or branch permission. The worker fetches public Git objects
anonymously. It cannot push a branch or commit.

Only `opened`, `reopened`, `synchronize`, and non-draft `ready_for_review`
actions enqueue work. Adding an event, action, endpoint, or permission requires
updating the parsed contract tests and this document in the same change.

## Verify

```bash
bash scripts/check-permissions.sh
pnpm --filter @enten/app test -- webhook.test.ts
```

The checks compare exact parsed sets. A substring grep is not proof of least
privilege.
