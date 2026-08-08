#!/usr/bin/env python3
"""Safely synchronize a downstream default branch with its upstream.

The script fast-forwards an unmodified downstream branch, or maintains a
stable pull request when downstream-only commits make a fast-forward
impossible. Once upstream is present in the downstream branch, open pull
request branches and the configured integration branch are advanced with
ordinary, non-force Git merges.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlencode


SYNC_MARKER = "<!-- daily-upstream-sync -->"
API_VERSION = "2022-11-28"


class AutomationError(RuntimeError):
    """A safe, actionable automation failure."""


class ApiError(AutomationError):
    """A GitHub API request failure."""


class PullRequestSkipped(AutomationError):
    """A safe PR-head update that policy intentionally declined."""


@dataclass(frozen=True)
class Divergence:
    """Commit counts unique to the downstream and upstream branches."""

    downstream_only: int
    upstream_only: int

    @property
    def action(self) -> str:
        """Return the safe synchronization action for these commit counts."""
        if self.upstream_only == 0:
            return "already-current"
        if self.downstream_only == 0:
            return "fast-forward"
        return "pull-request"


@dataclass(frozen=True)
class Config:
    """Validated runtime settings for one upstream synchronization run."""

    repository: str
    base_branch: str
    upstream_repository: str
    upstream_branch: str
    sync_branch: str
    target_remote: str
    upstream_remote: str
    reconcile_pull_requests: bool
    integration_branch: str | None
    trusted_fork_repositories: frozenset[str]
    dry_run: bool

    @classmethod
    def from_environment(cls, *, dry_run: bool) -> "Config":
        """Build and validate configuration from workflow environment values."""
        repository = require_env("GITHUB_REPOSITORY")
        base_branch = os.environ.get("BASE_BRANCH", "dev")
        upstream_repository = os.environ.get("UPSTREAM_REPOSITORY", "gumyr/build123d")
        upstream_branch = os.environ.get("UPSTREAM_BRANCH", base_branch)
        sync_branch = os.environ.get(
            "SYNC_BRANCH", f"automation/sync-upstream-{base_branch}"
        )
        target_remote = os.environ.get("TARGET_REMOTE", "origin")
        upstream_remote = os.environ.get("UPSTREAM_REMOTE", "upstream")
        reconcile_pull_requests = parse_bool(
            os.environ.get("RECONCILE_PULL_REQUESTS", "true")
        )
        integration_branch = os.environ.get("INTEGRATION_BRANCH", "").strip() or None
        trusted_fork_repositories = frozenset(
            item.strip().lower()
            for item in os.environ.get("TRUSTED_FORK_REPOSITORIES", "").split(",")
            if item.strip()
        )

        repository_values = [
            ("GITHUB_REPOSITORY", repository),
            ("UPSTREAM_REPOSITORY", upstream_repository),
        ]
        repository_values.extend(
            ("TRUSTED_FORK_REPOSITORIES", value) for value in trusted_fork_repositories
        )
        for label, value in repository_values:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
                raise AutomationError(
                    f"{label} is not an owner/repository value: {value}"
                )

        for label, value in (
            ("TARGET_REMOTE", target_remote),
            ("UPSTREAM_REMOTE", upstream_remote),
        ):
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
                raise AutomationError(f"{label} is not a safe Git remote name: {value}")

        if sync_branch == base_branch:
            raise AutomationError("SYNC_BRANCH must differ from BASE_BRANCH")
        if integration_branch in {base_branch, sync_branch}:
            raise AutomationError(
                "INTEGRATION_BRANCH must differ from BASE_BRANCH and SYNC_BRANCH"
            )

        return cls(
            repository=repository,
            base_branch=base_branch,
            upstream_repository=upstream_repository,
            upstream_branch=upstream_branch,
            sync_branch=sync_branch,
            target_remote=target_remote,
            upstream_remote=upstream_remote,
            reconcile_pull_requests=reconcile_pull_requests,
            integration_branch=integration_branch,
            trusted_fork_repositories=trusted_fork_repositories,
            dry_run=dry_run,
        )


def require_env(name: str) -> str:
    """Return one required environment value or raise an actionable error."""
    value = os.environ.get(name)
    if not value:
        raise AutomationError(f"Required environment variable {name} is not set")
    return value


def parse_bool(value: str) -> bool:
    """Parse a conventional environment boolean value."""
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise AutomationError(f"Invalid boolean value: {value}")


def run(
    args: Sequence[str],
    *,
    check: bool = True,
    input_text: str | None = None,
    show_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and convert checked failures into AutomationError."""
    print(f"+ {shlex.join(args)}")
    result = subprocess.run(
        list(args),
        check=False,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if show_output and result.stdout:
        print(result.stdout.rstrip())
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise AutomationError(
            f"Command failed ({result.returncode}): {shlex.join(args)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def api(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Call the GitHub REST API through the authenticated gh CLI."""
    args = [
        "gh",
        "api",
        "--method",
        method,
        "-H",
        "Accept: application/vnd.github+json",
        "-H",
        f"X-GitHub-Api-Version: {API_VERSION}",
        path,
    ]
    input_text = None
    if payload is not None:
        args.extend(["--input", "-"])
        input_text = json.dumps(payload)
    result = run(args, check=False, input_text=input_text)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise ApiError(f"GitHub API {method} {path} failed: {detail}")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def list_pull_requests(
    config: Config, *, state: str, base: str | None = None
) -> list[dict[str, Any]]:
    """Return every pull request matching the requested state and base."""
    pulls: list[dict[str, Any]] = []
    page = 1
    while True:
        query: dict[str, str | int] = {
            "state": state,
            "per_page": 100,
            "page": page,
        }
        if base:
            query["base"] = base
        batch = api("GET", f"repos/{config.repository}/pulls?{urlencode(query)}")
        if not isinstance(batch, list):
            raise AutomationError("GitHub returned a non-list pull request response")
        pulls.extend(batch)
        if len(batch) < 100:
            return pulls
        page += 1


def github_repository_from_url(url: str) -> str | None:
    """Extract an owner/repository slug from a supported GitHub remote URL."""
    match = re.search(
        r"(?:github\.com[/:])(?P<repository>[^/\s]+/[^/\s]+?)(?:\.git)?$", url
    )
    if not match:
        return None
    return match.group("repository").removesuffix(".git")


def ensure_remote(name: str, repository: str, *, allow_create: bool) -> None:
    """Verify a Git remote's repository, creating it only when permitted."""
    result = run(["git", "remote", "get-url", name], check=False)
    if result.returncode:
        if not allow_create:
            raise AutomationError(f"Required Git remote {name!r} does not exist")
        run(["git", "remote", "add", name, f"https://github.com/{repository}.git"])
        return

    actual_url = result.stdout.strip()
    actual_repository = github_repository_from_url(actual_url)
    if actual_repository is None or actual_repository.lower() != repository.lower():
        raise AutomationError(
            f"Git remote {name!r} points to {actual_url!r}, expected {repository}"
        )


def validate_branch(branch: str) -> None:
    """Reject values that are not valid Git branch names."""
    result = run(["git", "check-ref-format", "--branch", branch], check=False)
    if result.returncode:
        raise AutomationError(f"Invalid branch name: {branch}")


def fetch_branches(config: Config) -> tuple[str, str]:
    """Fetch and return the exact downstream and upstream branch heads."""
    ensure_remote(config.target_remote, config.repository, allow_create=False)
    ensure_remote(config.upstream_remote, config.upstream_repository, allow_create=True)
    validate_branch(config.base_branch)
    validate_branch(config.upstream_branch)
    validate_branch(config.sync_branch)

    run(
        [
            "git",
            "fetch",
            "--no-tags",
            config.target_remote,
            f"+refs/heads/{config.base_branch}:refs/remotes/"
            f"{config.target_remote}/{config.base_branch}",
        ]
    )
    run(
        [
            "git",
            "fetch",
            "--no-tags",
            config.upstream_remote,
            f"+refs/heads/{config.upstream_branch}:refs/remotes/"
            f"{config.upstream_remote}/{config.upstream_branch}",
        ]
    )
    base_ref = f"refs/remotes/{config.target_remote}/{config.base_branch}"
    upstream_ref = f"refs/remotes/{config.upstream_remote}/{config.upstream_branch}"
    base_sha = run(["git", "rev-parse", base_ref]).stdout.strip()
    upstream_sha = run(["git", "rev-parse", upstream_ref]).stdout.strip()

    merge_base = run(["git", "merge-base", base_sha, upstream_sha], check=False)
    if merge_base.returncode:
        raise AutomationError(
            "The downstream and upstream branches have unrelated histories; "
            "refusing to invent a synchronization strategy"
        )
    return base_sha, upstream_sha


def classify_divergence(output: str) -> Divergence:
    """Parse the two counts emitted by git rev-list --left-right --count."""
    fields = output.split()
    if len(fields) != 2:
        raise AutomationError(f"Unexpected rev-list count output: {output!r}")
    try:
        downstream_only, upstream_only = (int(field) for field in fields)
    except ValueError as exc:
        raise AutomationError(f"Unexpected rev-list count output: {output!r}") from exc
    return Divergence(downstream_only, upstream_only)


def divergence_for(base_sha: str, upstream_sha: str) -> Divergence:
    """Measure commits unique to the downstream and upstream heads."""
    result = run(
        [
            "git",
            "rev-list",
            "--left-right",
            "--count",
            f"{base_sha}...{upstream_sha}",
        ]
    )
    return classify_divergence(result.stdout)


def remote_branch_sha(config: Config, branch: str) -> str | None:
    """Return the target remote branch SHA when the branch exists."""
    result = run(
        [
            "git",
            "ls-remote",
            "--heads",
            config.target_remote,
            f"refs/heads/{branch}",
        ]
    )
    fields = result.stdout.split()
    return fields[0] if fields else None


def sync_pr_body(
    config: Config,
    *,
    base_sha: str,
    upstream_sha: str,
    divergence: Divergence,
) -> str:
    """Build the stable, auditable body for the upstream synchronization PR."""
    return f"""{SYNC_MARKER}
This pull request is maintained by the daily upstream-sync workflow.

`{config.repository}:{config.base_branch}` cannot be fast-forwarded because it
contains {divergence.downstream_only} downstream-only commit(s). Merging this PR
preserves those commits while incorporating {divergence.upstream_only} new
commit(s) from `{config.upstream_repository}:{config.upstream_branch}`.

- Downstream head before sync: `{base_sha}`
- Upstream head: `{upstream_sha}`
- Automation branch: `{config.sync_branch}`

The automation branch mirrors the exact upstream head. Please resolve any true
content conflicts in this PR rather than force-updating the downstream branch.
After this PR lands, the next daily run will merge the refreshed base into open
PR branches wherever GitHub can do so without choosing a conflict side.
"""


def sync_pr_title(config: Config) -> str:
    """Build the stable title for the upstream synchronization PR."""
    return (
        f"[automation] Sync {config.upstream_repository}/"
        f"{config.upstream_branch} into {config.base_branch}"
    )


def matching_sync_pulls(
    config: Config, pulls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Select PRs whose head is the configured automation branch."""
    matches = []
    for pull in pulls:
        head = pull.get("head") or {}
        head_repo = head.get("repo") or {}
        same_head = (
            head.get("ref") == config.sync_branch
            and str(head_repo.get("full_name", "")).lower() == config.repository.lower()
        )
        if same_head:
            matches.append(pull)
    return matches


def publish_sync_branch_and_pr(
    config: Config,
    *,
    base_sha: str,
    upstream_sha: str,
    divergence: Divergence,
) -> str:
    """Publish the exact upstream head and create or update its stable PR."""
    all_pulls = list_pull_requests(config, state="all", base=config.base_branch)
    matches = matching_sync_pulls(config, all_pulls)
    open_matches = [pull for pull in matches if pull.get("state") == "open"]
    if len(open_matches) > 1:
        numbers = ", ".join(f"#{pull['number']}" for pull in open_matches)
        raise AutomationError(f"Multiple open upstream-sync PRs found: {numbers}")

    existing_branch_sha = remote_branch_sha(config, config.sync_branch)
    known_automation_branch = bool(matches)
    if (
        existing_branch_sha
        and existing_branch_sha != upstream_sha
        and not known_automation_branch
    ):
        raise AutomationError(
            f"Refusing to overwrite unrecognized branch {config.sync_branch!r} "
            f"at {existing_branch_sha}"
        )

    if config.dry_run:
        operation = "update" if open_matches else "create"
        return (
            f"Would point {config.sync_branch} at {upstream_sha} and {operation} "
            "the upstream-sync pull request"
        )

    if existing_branch_sha != upstream_sha:
        push_args = ["git", "push", config.target_remote]
        if existing_branch_sha:
            push_args.append(
                f"--force-with-lease=refs/heads/{config.sync_branch}:"
                f"{existing_branch_sha}"
            )
        push_args.append(f"{upstream_sha}:refs/heads/{config.sync_branch}")
        run(push_args)

    title = sync_pr_title(config)
    body = sync_pr_body(
        config,
        base_sha=base_sha,
        upstream_sha=upstream_sha,
        divergence=divergence,
    )
    if open_matches:
        pull = api(
            "PATCH",
            f"repos/{config.repository}/pulls/{open_matches[0]['number']}",
            {"title": title, "body": body},
        )
        return f"Updated upstream-sync PR #{pull['number']}: {pull['html_url']}"

    try:
        pull = api(
            "POST",
            f"repos/{config.repository}/pulls",
            {
                "title": title,
                "head": config.sync_branch,
                "base": config.base_branch,
                "body": body,
                "maintainer_can_modify": True,
            },
        )
    except ApiError as exc:
        raise AutomationError(
            f"Could not create the upstream-sync PR. Configure the optional "
            f"UPSTREAM_SYNC_TOKEN secret, or enable 'Allow GitHub Actions to "
            f"create and approve pull requests' in repository Actions settings. "
            f"Original error: {exc}"
        ) from exc
    return f"Created upstream-sync PR #{pull['number']}: {pull['html_url']}"


def fast_forward_base(config: Config, *, base_sha: str, upstream_sha: str) -> str:
    """Advance an unpolluted downstream branch to the exact upstream head."""
    if config.dry_run:
        return (
            f"Would fast-forward {config.repository}:{config.base_branch} "
            f"from {base_sha} to {upstream_sha}"
        )
    run(
        [
            "git",
            "push",
            config.target_remote,
            f"{upstream_sha}:refs/heads/{config.base_branch}",
        ]
    )
    return (
        f"Fast-forwarded {config.repository}:{config.base_branch} "
        f"from {base_sha} to {upstream_sha}"
    )


def pull_head_ref(config: Config, pull_number: int) -> str:
    """Fetch one immutable pull-request head and return its local ref name."""
    local_ref = f"refs/remotes/{config.target_remote}/pull/{pull_number}/head"
    run(
        [
            "git",
            "fetch",
            "--no-tags",
            config.target_remote,
            f"+refs/pull/{pull_number}/head:{local_ref}",
        ]
    )
    return local_ref


def is_ancestor(ancestor: str, descendant: str) -> bool:
    """Return whether one commit is an ancestor of another commit."""
    result = run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise AutomationError(
            f"Could not compare ancestry between {ancestor} and {descendant}"
        )
    return result.returncode == 0


def merge_base_into_pull_head(
    config: Config,
    *,
    pull_number: int,
    head_repository: str,
    head_branch: str,
    head_sha: str,
    base_sha: str,
) -> str:
    """Create and push a normal merge when GitHub cannot update the branch."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", head_repository):
        raise AutomationError(
            f"PR #{pull_number} returned an invalid head repository: "
            f"{head_repository!r}"
        )
    validate_branch(head_branch)

    with tempfile.TemporaryDirectory(
        prefix=f"upstream-sync-pr-{pull_number}-"
    ) as temporary_directory:
        worktree = Path(temporary_directory) / "worktree"
        worktree_added = False
        try:
            run(
                [
                    "git",
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree),
                    head_sha,
                ]
            )
            worktree_added = True
            merge = run(
                [
                    "git",
                    "-C",
                    str(worktree),
                    "merge",
                    "--no-edit",
                    base_sha,
                ],
                check=False,
            )
            if merge.returncode:
                detail = (merge.stderr or merge.stdout).strip()
                raise AutomationError(
                    f"PR #{pull_number} has a true content conflict"
                    + (f": {detail}" if detail else "")
                )

            merged_sha = run(
                ["git", "-C", str(worktree), "rev-parse", "HEAD"]
            ).stdout.strip()
            push_destination = (
                config.target_remote
                if head_repository.lower() == config.repository.lower()
                else f"https://github.com/{head_repository}.git"
            )
            # A normal push makes a concurrent head change fail safely. Never
            # force-update contributor branches.
            run(
                [
                    "git",
                    "-C",
                    str(worktree),
                    "push",
                    push_destination,
                    f"HEAD:refs/heads/{head_branch}",
                ]
            )
            return merged_sha
        finally:
            if worktree_added:
                run(
                    ["git", "worktree", "remove", "--force", str(worktree)],
                    check=False,
                )


def order_pull_requests(pulls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order open PRs so a parent head is refreshed before its child PR."""
    pending = list(pulls)
    ordered: list[dict[str, Any]] = []
    while pending:
        pending_same_repo_heads = {
            str((pull.get("head") or {}).get("ref", ""))
            for pull in pending
            if str(
                ((pull.get("head") or {}).get("repo") or {}).get("full_name", "")
            ).lower()
            == str(
                ((pull.get("base") or {}).get("repo") or {}).get("full_name", "")
            ).lower()
        }
        ready = [
            pull
            for pull in pending
            if str((pull.get("base") or {}).get("ref", ""))
            not in pending_same_repo_heads
        ]
        if not ready:
            # A cycle should not be possible on GitHub, but keep any malformed
            # graph deterministic and let ancestry/push safeguards reject it.
            ready = [min(pending, key=lambda pull: int(pull["number"]))]
        ready.sort(key=lambda pull: int(pull["number"]))
        ordered.extend(ready)
        ready_numbers = {int(pull["number"]) for pull in ready}
        pending = [pull for pull in pending if int(pull["number"]) not in ready_numbers]
    return ordered


def fetch_pull_base(config: Config, pull: dict[str, Any]) -> tuple[str, str]:
    """Fetch a PR base branch and return its name and exact head SHA."""
    base_branch = str((pull.get("base") or {}).get("ref", ""))
    validate_branch(base_branch)
    base_ref = f"refs/remotes/{config.target_remote}/{base_branch}"
    run(
        [
            "git",
            "fetch",
            "--no-tags",
            config.target_remote,
            f"+refs/heads/{base_branch}:{base_ref}",
        ]
    )
    return base_branch, run(["git", "rev-parse", base_ref]).stdout.strip()


def update_pull_request_branch(
    config: Config,
    *,
    pull: dict[str, Any],
    actual_head_sha: str,
    base_sha: str,
) -> str:
    """Update a PR head with an ordinary merge and concurrency-safe push."""
    number = int(pull["number"])
    head = pull.get("head") or {}
    head_branch = str(head.get("ref", ""))
    head_repository = str((head.get("repo") or {}).get("full_name", ""))
    same_repository = head_repository.lower() == config.repository.lower()

    if not same_repository:
        if head_repository.lower() not in config.trusted_fork_repositories:
            raise PullRequestSkipped(
                f"fork {head_repository} is not in TRUSTED_FORK_REPOSITORIES"
            )
        detailed_pull = api("GET", f"repos/{config.repository}/pulls/{number}")
        detailed_head = detailed_pull.get("head") or {}
        detailed_repository = str(
            (detailed_head.get("repo") or {}).get("full_name", "")
        )
        if (
            detailed_repository.lower() != head_repository.lower()
            or str(detailed_head.get("ref", "")) != head_branch
            or str(detailed_head.get("sha", "")) != actual_head_sha
        ):
            raise AutomationError(
                f"PR #{number} head changed while validating fork permissions"
            )
        if not detailed_pull.get("maintainer_can_modify", False):
            raise PullRequestSkipped(
                "the allowlisted fork branch does not allow maintainer updates"
            )
    merge_base_into_pull_head(
        config,
        pull_number=number,
        head_repository=head_repository,
        head_branch=head_branch,
        head_sha=actual_head_sha,
        base_sha=base_sha,
    )
    return (
        "direct Git merge" if same_repository else "direct Git merge for editable fork"
    )


def reconcile_open_pull_requests(
    config: Config,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Merge current bases into stale PR heads in dependency order."""
    pulls = order_pull_requests(list_pull_requests(config, state="open"))
    current: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    failures: list[str] = []

    for pull in pulls:
        number = int(pull["number"])
        head = pull.get("head") or {}
        head_ref = str(head.get("ref", ""))
        if head_ref == config.sync_branch or head_ref == config.integration_branch:
            continue

        try:
            base_branch, base_sha = fetch_pull_base(config, pull)
            local_ref = pull_head_ref(config, number)
            actual_head_sha = run(["git", "rev-parse", local_ref]).stdout.strip()
        except AutomationError as exc:
            failures.append(f"PR #{number}: could not fetch its base or head ({exc})")
            continue

        label = f"PR #{number} ({head_ref} <- {base_branch})"
        if is_ancestor(base_sha, actual_head_sha):
            current.append(label)
            continue

        head_repository = str((head.get("repo") or {}).get("full_name", ""))
        if (
            head_repository.lower() != config.repository.lower()
            and head_repository.lower() not in config.trusted_fork_repositories
        ):
            skipped.append(
                f"{label}: fork {head_repository} is not allowlisted for writes"
            )
            continue

        if config.dry_run:
            updated.append(f"{label} [dry run]")
            continue

        try:
            update_method = update_pull_request_branch(
                config,
                pull=pull,
                actual_head_sha=actual_head_sha,
                base_sha=base_sha,
            )
            updated.append(f"{label} via {update_method}")
        except PullRequestSkipped as exc:
            skipped.append(f"{label}: {exc}")
        except AutomationError as exc:
            failures.append(
                f"{label}: could not merge {base_branch} into the branch ({exc})"
            )

    return current, updated, skipped, failures


def refresh_integration_branch(config: Config) -> str:
    """Fast-forward the integration branch after merging every open PR head."""
    if not config.integration_branch:
        return "Integration branch refresh disabled"
    integration_branch = config.integration_branch
    validate_branch(integration_branch)
    integration_ref = f"refs/remotes/{config.target_remote}/{integration_branch}"
    run(
        [
            "git",
            "fetch",
            "--no-tags",
            config.target_remote,
            f"+refs/heads/{integration_branch}:{integration_ref}",
        ]
    )
    integration_sha = run(["git", "rev-parse", integration_ref]).stdout.strip()

    base_ref = f"refs/remotes/{config.target_remote}/{config.base_branch}"
    run(
        [
            "git",
            "fetch",
            "--no-tags",
            config.target_remote,
            f"+refs/heads/{config.base_branch}:{base_ref}",
        ]
    )
    candidates: list[tuple[str, str]] = [
        (config.base_branch, run(["git", "rev-parse", base_ref]).stdout.strip())
    ]
    pulls = order_pull_requests(list_pull_requests(config, state="open"))
    for pull in pulls:
        number = int(pull["number"])
        head_ref = str((pull.get("head") or {}).get("ref", ""))
        if head_ref in {integration_branch, config.sync_branch}:
            continue
        pull_ref = pull_head_ref(config, number)
        candidates.append(
            (
                f"PR #{number} ({head_ref})",
                run(["git", "rev-parse", pull_ref]).stdout.strip(),
            )
        )

    missing = [
        (label, sha)
        for label, sha in candidates
        if not is_ancestor(sha, integration_sha)
    ]
    if not missing:
        return f"{integration_branch} already contains dev and every open PR head"
    if config.dry_run:
        return (
            f"Would fast-forward {integration_branch} after merging "
            f"{len(missing)} missing head(s)"
        )

    with tempfile.TemporaryDirectory(
        prefix=f"upstream-sync-{integration_branch}-"
    ) as temporary_directory:
        worktree = Path(temporary_directory) / "worktree"
        worktree_added = False
        merged_labels: list[str] = []
        try:
            run(
                [
                    "git",
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree),
                    integration_sha,
                ]
            )
            worktree_added = True
            for label, candidate_sha in candidates:
                current_sha = run(
                    ["git", "-C", str(worktree), "rev-parse", "HEAD"]
                ).stdout.strip()
                if is_ancestor(candidate_sha, current_sha):
                    continue
                merge = run(
                    [
                        "git",
                        "-C",
                        str(worktree),
                        "merge",
                        "--no-edit",
                        candidate_sha,
                    ],
                    check=False,
                )
                if merge.returncode:
                    detail = (merge.stderr or merge.stdout).strip()
                    raise AutomationError(
                        f"{integration_branch} conflicts while merging {label}"
                        + (f": {detail}" if detail else "")
                    )
                merged_labels.append(label)

            final_sha = run(
                ["git", "-C", str(worktree), "rev-parse", "HEAD"]
            ).stdout.strip()
            for label, candidate_sha in candidates:
                if not is_ancestor(candidate_sha, final_sha):
                    raise AutomationError(
                        f"{integration_branch} is missing {label} after integration"
                    )
            run(
                [
                    "git",
                    "-C",
                    str(worktree),
                    "push",
                    config.target_remote,
                    f"HEAD:refs/heads/{integration_branch}",
                ]
            )
        finally:
            if worktree_added:
                run(
                    ["git", "worktree", "remove", "--force", str(worktree)],
                    check=False,
                )

    return (
        f"Fast-forwarded {integration_branch} from {integration_sha} to "
        f"{final_sha} after merging {len(merged_labels)} head(s)"
    )


def write_summary(lines: list[str]) -> None:
    """Print a run summary and append it to GitHub's step summary when set."""
    content = "\n".join(lines).rstrip() + "\n"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(content)
    print("\n" + content)


def execute(config: Config) -> int:
    """Execute one complete upstream, PR-head, and integration-branch run."""
    base_sha, upstream_sha = fetch_branches(config)
    divergence = divergence_for(base_sha, upstream_sha)
    summary = [
        "## Daily upstream sync",
        "",
        f"- Downstream: `{config.repository}:{config.base_branch}` at `{base_sha}`",
        f"- Upstream: `{config.upstream_repository}:{config.upstream_branch}` at `{upstream_sha}`",
        f"- Divergence: {divergence.downstream_only} downstream-only / "
        f"{divergence.upstream_only} upstream-only commit(s)",
    ]

    if divergence.action == "fast-forward":
        sync_result = fast_forward_base(
            config, base_sha=base_sha, upstream_sha=upstream_sha
        )
        effective_base_sha = upstream_sha
    elif divergence.action == "pull-request":
        sync_result = publish_sync_branch_and_pr(
            config,
            base_sha=base_sha,
            upstream_sha=upstream_sha,
            divergence=divergence,
        )
        effective_base_sha = base_sha
    else:
        sync_result = "Downstream already contains the current upstream head"
        effective_base_sha = base_sha
    summary.append(f"- Result: {sync_result}")

    upstream_is_in_base = is_ancestor(upstream_sha, effective_base_sha)
    if not config.reconcile_pull_requests:
        summary.append("- PR reconciliation: disabled")
        write_summary(summary)
        return 0
    if not upstream_is_in_base:
        summary.append(
            "- PR reconciliation: deferred until the upstream-sync PR is merged"
        )
        write_summary(summary)
        return 0

    current, updated, skipped, failures = reconcile_open_pull_requests(config)
    summary.extend(
        [
            f"- PR branches already current: {len(current)}",
            f"- PR branches {'that would be updated' if config.dry_run else 'updated'}: {len(updated)}",
        ]
    )
    if updated:
        summary.extend(f"  - {entry}" for entry in updated)
    if skipped:
        summary.append(f"- PR branches skipped by fork policy: {len(skipped)}")
        summary.extend(f"  - {entry}" for entry in skipped)
    if failures:
        summary.append(f"- PR branches requiring attention: {len(failures)}")
        summary.extend(f"  - {failure}" for failure in failures)
        write_summary(summary)
        for failure in failures:
            print(f"::warning title=Upstream sync could not update a PR::{failure}")
        return 1

    integration_result = refresh_integration_branch(config)
    summary.append(f"- Integration: {integration_result}")

    write_summary(summary)
    return 0


def main() -> int:
    """Parse command-line options and report safe automation failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report intended changes without pushing or updating PRs",
    )
    args = parser.parse_args()
    try:
        return execute(Config.from_environment(dry_run=args.dry_run))
    except (AutomationError, json.JSONDecodeError) as exc:
        write_summary(["## Daily upstream sync", "", f"- Failure: {exc}"])
        print(f"::error title=Daily upstream sync failed::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
