# SPDX-License-Identifier: Apache-2.0
"""Adaptateurs de tracker."""

from __future__ import annotations

from .azure_boards import AzureBoardsTracker
from .azure_boards_events import parse_azure_boards_event
from .github import GitHubTracker
from .github_events import parse_command, parse_github_event
from .gitlab import GitLabTracker
from .gitlab_events import parse_gitlab_event
from .jira import JiraTracker
from .jira_events import parse_jira_event

__all__ = [
    "AzureBoardsTracker",
    "GitHubTracker",
    "GitLabTracker",
    "JiraTracker",
    "parse_azure_boards_event",
    "parse_command",
    "parse_github_event",
    "parse_gitlab_event",
    "parse_jira_event",
]
