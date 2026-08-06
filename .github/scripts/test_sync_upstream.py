#!/usr/bin/env python3
"""Focused unit tests for the daily upstream synchronization policy."""

from __future__ import annotations

import importlib.util
import sys
import unittest
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
            dry_run=False,
        )
        self.pull = {
            "number": 1,
            "maintainer_can_modify": True,
            "head": {
                "ref": "contributor/change",
                "repo": {"full_name": "contributor/build123d"},
            },
        }

    def test_editable_fork_uses_direct_merge_without_api(self) -> None:
        with (
            patch.object(sync_upstream, "api") as api,
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
        api.assert_not_called()
        merge.assert_called_once()

    def test_locked_fork_uses_neither_api_nor_direct_push(self) -> None:
        self.pull["maintainer_can_modify"] = False
        with (
            patch.object(sync_upstream, "api") as api,
            patch.object(sync_upstream, "merge_base_into_pull_head") as merge,
        ):
            with self.assertRaises(sync_upstream.AutomationError):
                sync_upstream.update_pull_request_branch(
                    self.config,
                    pull=self.pull,
                    actual_head_sha="a" * 40,
                    base_sha="b" * 40,
                )

        api.assert_not_called()
        merge.assert_not_called()

    def test_same_repository_api_failure_uses_direct_merge_fallback(self) -> None:
        self.pull["head"]["repo"]["full_name"] = self.config.repository
        with (
            patch.object(
                sync_upstream,
                "api",
                side_effect=sync_upstream.ApiError("API unavailable"),
            ),
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

        self.assertEqual(method, "direct Git merge fallback")
        merge.assert_called_once()


if __name__ == "__main__":
    unittest.main()
