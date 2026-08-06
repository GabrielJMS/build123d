# Daily upstream synchronization

[`sync-upstream.yml`](workflows/sync-upstream.yml) runs once per day at
09:17 UTC and can also be started manually. In this repository, `dev` is the
default branch and therefore serves as the operational main branch.

The workflow synchronizes `gumyr/build123d:dev` into
`Normal-Company/build123d:dev` with these guards:

1. If downstream `dev` has no downstream-only commits, it is fast-forwarded to
   the exact upstream head.
2. If the histories have diverged, the workflow points the stable
   `automation/sync-upstream-dev` branch at the exact upstream head and creates
   or updates one marked pull request into `dev`. It never force-updates
   polluted `dev`.
3. Once the upstream head is in downstream `dev`, the workflow asks GitHub to
   merge the refreshed base into each stale open PR branch. GitHub performs
   only clean branch updates. Maintainer-editable fork PRs bypass that API and
   use an ordinary Git merge plus non-force push directly; same-repository PRs
   use the same fallback if the API fails. True content conflicts and
   inaccessible fork branches are reported in the workflow summary and fail
   the job so a person can resolve them without the automation discarding
   either side.

The stable automation branch is force-updated only with an exact
`--force-with-lease`. An existing branch that is not recognizable as belonging
to this automation is never overwritten.

## Authentication

The workflow requests only `contents: write` and `pull-requests: write`. It
uses the repository `GITHUB_TOKEN` by default. For that token to create the
upstream-sync PR, enable **Settings → Actions → General → Allow GitHub Actions
to create and approve pull requests**.

For fully unattended CI on automation-created/updated PRs, or to update an
allowed PR head in another repository, configure an `UPSTREAM_SYNC_TOKEN`
repository secret backed by a narrowly scoped GitHub App token or fine-grained
PAT. It needs Contents and Pull requests write access to
`Normal-Company/build123d`; cross-repository head updates additionally require
write access to that head repository. The workflow falls back to
`github.token` when the secret is absent.

## Local dry run

From a checkout with the `normal` and `upstream` remotes configured:

```bash
GITHUB_REPOSITORY=Normal-Company/build123d \
TARGET_REMOTE=normal \
python .github/scripts/sync_upstream.py --dry-run
```

The dry run fetches current refs and pull-request metadata, but does not push,
create a PR, or update any PR branch.
