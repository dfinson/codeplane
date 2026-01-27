"""Internal pygit2 constants - keeps trivia out of public modules."""

from __future__ import annotations

import pygit2

# Commit walking
SORT_TIME = pygit2.GIT_SORT_TIME

# Working tree status flags
STATUS_WT_NEW = pygit2.GIT_STATUS_WT_NEW
STATUS_WT_MODIFIED = pygit2.GIT_STATUS_WT_MODIFIED
STATUS_WT_DELETED = pygit2.GIT_STATUS_WT_DELETED

# Reset modes
RESET_SOFT = pygit2.GIT_RESET_SOFT
RESET_MIXED = pygit2.GIT_RESET_MIXED
RESET_HARD = pygit2.GIT_RESET_HARD

# Merge analysis flags
MERGE_UP_TO_DATE = pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE
MERGE_FASTFORWARD = pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD
MERGE_NORMAL = pygit2.GIT_MERGE_ANALYSIS_NORMAL
