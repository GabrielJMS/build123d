#!/usr/bin/env python3
"""Safely synchronize a downstream default branch with its upstream.

The script fast-forwards an unmodified downstream branch, or maintains a
stable pull request when downstream-only commits make a fast-forward
impossible. Once upstream is present in the downstream branch, open pull
request branches are updated with GitHub's conflict-safe merge endpoint.
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


@dataclass(frozen=True)
class Divergence:
    """Commit counts unique to the downstream and upstream branches."""

    downstream_only: int
    upstream_only: int

    @property
    def action(self) -> str:
        if self.upstream_only == 0:
            return "already-current"
        if self.downstream_only == 0:
            return "fast-forward"
        return "pull-request"


@dataclass(frozen=True)
class Config:
    repository: str
    base_branch: str
    upstream_repository: str
    upstream_branch: str
    sync_branch: str
    target_remote: str
    upstream_remote: str
    reconcile_pull_requests: bool
    dry_run: bool

    @classmethod
    def from_environment(cls, *, dry_run: bool) -> "Config":
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

        for label, value in (
            ("GITHUB_REPOSITORY", repository),
            ("UPSTREAM_REPOSITORY", upstream_repository),
        ):
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

        return cls(
            repository=repository,
            base_branch=base_branch,
            upstream_repository=upstream_repository,
            upstream_branch=upstream_branch,
            sync_branch=sync_branch,
            target_remote=target_remote,
            upstream_remote=upstream_remote,
            reconcile_pull_requests=reconcile_pull_requests,
            dry_run=dry_run,
        )


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise AutomationError(f"Required environment variable {name} is not set")
    return value


def parse_bool(value: str) -> bool:
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
    match = re.search(
        r"(?:github\.com[/:])(?P<repository>[^/\s]+/[^/\s]+?)(?:\.git)?$", url
    )
    if not match:
        return None
    return match.group("repository").removesuffix(".git")


def ensure_remote(name: str, repository: str, *, allow_create: bool) -> None:
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
    result = run(["git", "check-ref-format", "--branch", branch], check=False)
    if result.returncode:
        raise AutomationError(f"Invalid branch name: {branch}")


def fetch_branches(config: Config) -> tuple[str, str]:
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
    fields = output.split()
    if len(fields) != 2:
        raise AutomationError(f"Unexpected rev-list count output: {output!r}")
    try:
        downstream_only, upstream_only = (int(field) for field in fields)
    except ValueError as exc:
        raise AutomationError(f"Unexpected rev-list count output: {output!r}") from exc
    return Divergence(downstream_only, upstream_only)


def divergence_for(base_sha: str, upstream_sha: str) -> Divergence:
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
    return (
        f"[automation] Sync {config.upstream_repository}/"
        f"{config.upstream_branch} into {config.base_branch}"
    )


def matching_sync_pulls(
    config: Config, pulls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
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


def update_pull_request_branch(
    config: Config,
    *,
    pull: dict[str, Any],
    actual_head_sha: str,
    base_sha: str,
) -> str:
    """Update a PR head through GitHub, with a safe Git merge fallback."""
    number = int(pull["number"])
    head = pull.get("head") or {}
    head_branch = str(head.get("ref", ""))
    head_repository = str((head.get("repo") or {}).get("full_name", ""))
    same_repository = head_repository.lower() == config.repository.lower()

    # GitHub's update-branch endpoint can have surprising lifecycle side
    # effects on cross-repository PRs. Use the explicit, non-force Git path for
    # editable fork heads and never call the endpoint for them.
    if not same_repository:
        if not pull.get("maintainer_can_modify", False):
            raise AutomationError("the fork branch does not allow maintainer updates")
        merge_base_into_pull_head(
            config,
            pull_number=number,
            head_repository=head_repository,
            head_branch=head_branch,
            head_sha=actual_head_sha,
            base_sha=base_sha,
        )
        return "direct Git merge for editable fork"

    try:
        api(
            "PUT",
            f"repos/{config.repository}/pulls/{number}/update-branch",
            {"expected_head_sha": actual_head_sha},
        )
        return "GitHub update-branch API"
    except ApiError as api_error:
        try:
            merge_base_into_pull_head(
                config,
                pull_number=number,
                head_repository=head_repository,
                head_branch=head_branch,
                head_sha=actual_head_sha,
                base_sha=base_sha,
            )
        except AutomationError as merge_error:
            raise AutomationError(
                f"{api_error}; direct merge fallback also failed: {merge_error}"
            ) from merge_error
        return "direct Git merge fallback"


def reconcile_open_pull_requests(
    config: Config, *, base_sha: str
) -> tuple[list[str], list[str], list[str]]:
    pulls = list_pull_requests(config, state="open", base=config.base_branch)
    current: list[str] = []
    updated: list[str] = []
    failures: list[str] = []

    for pull in pulls:
        number = int(pull["number"])
        head = pull.get("head") or {}
        head_ref = str(head.get("ref", ""))
        if head_ref == config.sync_branch:
            continue

        try:
            local_ref = pull_head_ref(config, number)
            actual_head_sha = run(["git", "rev-parse", local_ref]).stdout.strip()
        except AutomationError as exc:
            failures.append(f"PR #{number}: could not fetch its head ({exc})")
            continue

        if is_ancestor(base_sha, actual_head_sha):
            current.append(f"PR #{number} ({head_ref})")
            continue

        if config.dry_run:
            updated.append(f"PR #{number} ({head_ref}) [dry run]")
            continue

        try:
            update_method = update_pull_request_branch(
                config,
                pull=pull,
                actual_head_sha=actual_head_sha,
                base_sha=base_sha,
            )
            updated.append(f"PR #{number} ({head_ref}) via {update_method}")
        except AutomationError as exc:
            failures.append(
                f"PR #{number} ({head_ref}): GitHub could not merge "
                f"{config.base_branch} into the branch ({exc})"
            )

    return current, updated, failures


def write_summary(lines: list[str]) -> None:
    content = "\n".join(lines).rstrip() + "\n"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(content)
    print("\n" + content)


def execute(config: Config) -> int:
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

    current, updated, failures = reconcile_open_pull_requests(
        config, base_sha=effective_base_sha
    )
    summary.extend(
        [
            f"- PR branches already current: {len(current)}",
            f"- PR branches {'that would be updated' if config.dry_run else 'updated'}: {len(updated)}",
        ]
    )
    if updated:
        summary.extend(f"  - {entry}" for entry in updated)
    if failures:
        summary.append(f"- PR branches requiring attention: {len(failures)}")
        summary.extend(f"  - {failure}" for failure in failures)
        write_summary(summary)
        for failure in failures:
            print(f"::warning title=Upstream sync could not update a PR::{failure}")
        return 1

    write_summary(summary)
    return 0


def main() -> int:
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
