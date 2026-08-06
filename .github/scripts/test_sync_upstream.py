#!/usr/bin/env python3
"""Focused unit tests for the daily upstream synchronization policy."""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("sync_upstream.py")
SPEC = importlib.util.spec_from_file_location("sync_upstream", MODULE_PATH)
assert SPEC and SPEC.loader
sync_upstream = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync_upstream
SPEC.loader.exec_module(sync_upstream)


class DivergenceTests(unittest.TestCase):
    def test_already_current_when_upstream_has_no_unique_commits(self) -> None:
        divergence = sync_upstream.classify_divergence("7\t0\n")
        self.assertEqual(divergence.action, "already-current")

    def test_fast_forward_when_only_upstream_has_unique_commits(self) -> None:
        divergence = sync_upstream.classify_divergence("0 19")
        self.assertEqual(divergence.action, "fast-forward")

    def test_pull_request_when_histories_diverged(self) -> None:
        divergence = sync_upstream.classify_divergence("3 19")
        self.assertEqual(divergence.action, "pull-request")

    def test_rejects_unexpected_rev_list_output(self) -> None:
        with self.assertRaises(sync_upstream.AutomationError):
            sync_upstream.classify_divergence("19")


class FormattingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = sync_upstream.Config(
            repository="Normal-Company/build123d",
            base_branch="dev",
            upstream_repository="gumyr/build123d",
            upstream_branch="dev",
            sync_branch="automation/sync-upstream-dev",
            target_remote="origin",
            upstream_remote="upstream",
            reconcile_pull_requests=True,
            integration_branch="nightly",
            trusted_fork_repositories=frozenset(),
            dry_run=True,
        )

    def test_sync_body_is_stable_and_auditable(self) -> None:
        body = sync_upstream.sync_pr_body(
            self.config,
            base_sha="a" * 40,
            upstream_sha="b" * 40,
            divergence=sync_upstream.Divergence(3, 19),
        )
        self.assertIn(sync_upstream.SYNC_MARKER, body)
        self.assertIn("3 downstream-only", body)
        self.assertIn("19 new", body)
        self.assertIn("a" * 40, body)
        self.assertIn("b" * 40, body)

    def test_title_names_both_branches(self) -> None:
        self.assertEqual(
            sync_upstream.sync_pr_title(self.config),
            "[automation] Sync gumyr/build123d/dev into dev",
        )

    def test_marker_on_unrelated_pull_request_is_not_claimed(self) -> None:
        pulls = [
            {
                "body": sync_upstream.SYNC_MARKER,
                "head": {
                    "ref": "contributor/change",
                    "repo": {"full_name": "someone-else/build123d"},
                },
            }
        ]
        self.assertEqual(sync_upstream.matching_sync_pulls(self.config, pulls), [])


class ParsingTests(unittest.TestCase):
    def test_boolean_values(self) -> None:
        self.assertTrue(sync_upstream.parse_bool("YES"))
        self.assertFalse(sync_upstream.parse_bool("off"))
        with self.assertRaises(sync_upstream.AutomationError):
            sync_upstream.parse_bool("sometimes")

    def test_github_remote_formats(self) -> None:
        self.assertEqual(
            sync_upstream.github_repository_from_url(
                "https://github.com/Normal-Company/build123d.git"
            ),
            "Normal-Company/build123d",
        )
        self.assertEqual(
            sync_upstream.github_repository_from_url(
                "git@github.com:Normal-Company/build123d.git"
            ),
            "Normal-Company/build123d",
        )

    def test_integration_branch_and_fork_allowlist_from_environment(self) -> None:
        environment = {
            "GITHUB_REPOSITORY": "Normal-Company/build123d",
            "INTEGRATION_BRANCH": "nightly",
            "TRUSTED_FORK_REPOSITORIES": (
                "Contributor/build123d, trusted-owner/trusted-repo"
            ),
        }
        with patch.dict(os.environ, environment, clear=True):
            config = sync_upstream.Config.from_environment(dry_run=True)

        self.assertEqual(config.integration_branch, "nightly")
        self.assertEqual(
            config.trusted_fork_repositories,
            frozenset({"contributor/build123d", "trusted-owner/trusted-repo"}),
        )


class DocumentationTests(unittest.TestCase):
    def test_production_classes_and_functions_have_docstrings(self) -> None:
        module = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        undocumented = [
            node.name
            for node in ast.walk(module)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and ast.get_docstring(node) is None
        ]

        self.assertEqual(undocumented, [])


class PullRequestUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = sync_upstream.Config(
            repository="Normal-Company/build123d",
            base_branch="dev",
            upstream_repository="gumyr/build123d",
            upstream_branch="dev",
            sync_branch="automation/sync-upstream-dev",
            target_remote="origin",
            upstream_remote="upstream",
            reconcile_pull_requests=True,
            integration_branch="nightly",
            trusted_fork_repositories=frozenset({"contributor/build123d"}),
            dry_run=False,
        )
        self.pull = {
            "number": 1,
            "head": {
                "ref": "contributor/change",
                "repo": {"full_name": "contributor/build123d"},
            },
        }

    def test_editable_fork_uses_direct_merge(self) -> None:
        detailed_pull = {
            "maintainer_can_modify": True,
            "head": {
                "ref": "contributor/change",
                "sha": "a" * 40,
                "repo": {"full_name": "contributor/build123d"},
            },
        }
        with (
            patch.object(sync_upstream, "api", return_value=detailed_pull) as api,
            patch.object(
                sync_upstream,
                "merge_base_into_pull_head",
                return_value="c" * 40,
            ) as merge,
        ):
            method = sync_upstream.update_pull_request_branch(
                self.config,
                pull=self.pull,
                actual_head_sha="a" * 40,
                base_sha="b" * 40,
            )

        self.assertEqual(method, "direct Git merge for editable fork")
        api.assert_called_once_with("GET", "repos/Normal-Company/build123d/pulls/1")
        merge.assert_called_once()

    def test_locked_fork_does_not_push(self) -> None:
        detailed_pull = {
            "maintainer_can_modify": False,
            "head": {
                "ref": "contributor/change",
                "sha": "a" * 40,
                "repo": {"full_name": "contributor/build123d"},
            },
        }
        with (
            patch.object(sync_upstream, "api", return_value=detailed_pull),
            patch.object(sync_upstream, "merge_base_into_pull_head") as merge,
        ):
            with self.assertRaises(sync_upstream.PullRequestSkipped):
                sync_upstream.update_pull_request_branch(
                    self.config,
                    pull=self.pull,
                    actual_head_sha="a" * 40,
                    base_sha="b" * 40,
                )

        merge.assert_not_called()

    def test_untrusted_fork_is_never_queried_or_pushed(self) -> None:
        config = replace(
            self.config,
            trusted_fork_repositories=frozenset(),
        )
        with (
            patch.object(sync_upstream, "api") as api,
            patch.object(sync_upstream, "merge_base_into_pull_head") as merge,
        ):
            with self.assertRaises(sync_upstream.PullRequestSkipped):
                sync_upstream.update_pull_request_branch(
                    config,
                    pull=self.pull,
                    actual_head_sha="a" * 40,
                    base_sha="b" * 40,
                )

        api.assert_not_called()
        merge.assert_not_called()

    def test_same_repository_uses_direct_merge(self) -> None:
        self.pull["head"]["repo"]["full_name"] = self.config.repository
        with patch.object(
            sync_upstream,
            "merge_base_into_pull_head",
            return_value="c" * 40,
        ) as merge:
            method = sync_upstream.update_pull_request_branch(
                self.config,
                pull=self.pull,
                actual_head_sha="a" * 40,
                base_sha="b" * 40,
            )

        self.assertEqual(method, "direct Git merge")
        merge.assert_called_once()

    def test_stacked_pull_requests_are_ordered_parent_first(self) -> None:
        parent = {
            "number": 4,
            "base": {
                "ref": "dev",
                "repo": {"full_name": self.config.repository},
            },
            "head": {
                "ref": "feature",
                "repo": {"full_name": self.config.repository},
            },
        }
        child = {
            "number": 5,
            "base": {
                "ref": "feature",
                "repo": {"full_name": self.config.repository},
            },
            "head": {
                "ref": "feature-child",
                "repo": {"full_name": self.config.repository},
            },
        }

        ordered = sync_upstream.order_pull_requests([child, parent])

        self.assertEqual([pull["number"] for pull in ordered], [4, 5])


class IntegrationBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = sync_upstream.Config(
            repository="Normal-Company/build123d",
            base_branch="dev",
            upstream_repository="gumyr/build123d",
            upstream_branch="dev",
            sync_branch="automation/sync-upstream-dev",
            target_remote="origin",
            upstream_remote="upstream",
            reconcile_pull_requests=True,
            integration_branch="nightly",
            trusted_fork_repositories=frozenset(),
            dry_run=False,
        )

    def test_refresh_pushes_a_normal_fast_forward_from_existing_tip(self) -> None:
        integration_sha = "1" * 40
        base_sha = "2" * 40
        pull_sha = "3" * 40
        final_sha = "4" * 40
        pull = {
            "number": 12,
            "base": {
                "ref": "dev",
                "repo": {"full_name": self.config.repository},
            },
            "head": {
                "ref": "feature",
                "repo": {"full_name": self.config.repository},
            },
        }
        head_reads = 0

        def fake_run(
            args: list[str],
            *,
            check: bool = True,
            input_text: str | None = None,
            show_output: bool = False,
        ) -> sync_upstream.subprocess.CompletedProcess[str]:
            nonlocal head_reads
            del check, input_text, show_output
            stdout = ""
            if args[:2] == ["git", "rev-parse"]:
                if args[-1].endswith("/nightly"):
                    stdout = integration_sha
                elif args[-1].endswith("/dev"):
                    stdout = base_sha
                else:
                    stdout = pull_sha
            elif "rev-parse" in args and args[-1] == "HEAD":
                head_reads += 1
                stdout = final_sha if head_reads == 3 else integration_sha
            return sync_upstream.subprocess.CompletedProcess(
                args, 0, stdout=stdout + ("\n" if stdout else ""), stderr=""
            )

        with (
            patch.object(sync_upstream, "run", side_effect=fake_run) as run,
            patch.object(sync_upstream, "list_pull_requests", return_value=[pull]),
            patch.object(
                sync_upstream,
                "pull_head_ref",
                return_value="refs/remotes/origin/pull/12/head",
            ),
            patch.object(
                sync_upstream,
                "is_ancestor",
                side_effect=[False, False, False, False, True, True],
            ),
        ):
            result = sync_upstream.refresh_integration_branch(self.config)

        push_calls = [
            call.args[0] for call in run.call_args_list if "push" in call.args[0]
        ]
        self.assertEqual(len(push_calls), 1)
        self.assertIn("HEAD:refs/heads/nightly", push_calls[0])
        self.assertFalse(
            any(argument.startswith("--force") for argument in push_calls[0])
        )
        self.assertIn(integration_sha, result)
        self.assertIn(final_sha, result)


if __name__ == "__main__":
    unittest.main()
