# SPDX-License-Identifier: Apache-2.0
"""Where a knowledge base's documents come from (S33-04, ADR 0051): SharePoint and OneDrive through
Microsoft Graph, an allow-listed web site, a git repository.

Every source answers the same two questions: what changed since this cursor (`changes`), and what
are the bytes of this item (`fetch`). A source that cannot say what was deleted lists everything
(`full=True`): what it does not list any more was deleted at the source.
"""

from __future__ import annotations

from .base import Changes, DocumentSource, SourceItem
from .git import GitSource
from .graph import GraphDriveSource
from .web import WebCrawlSource

__all__ = ["Changes", "DocumentSource", "GitSource", "GraphDriveSource", "SourceItem", "WebCrawlSource"]
