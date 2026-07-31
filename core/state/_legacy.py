"""Temporary holding module for not-yet-extracted StateStore domains.

This mixin shrinks as each domain (users, destinations, teams, drafts,
posts, recurring, stats) is split into its own dedicated module. Methods
here are moved verbatim; nothing in this file should be modified beyond
deletion once a domain is extracted.
"""

from __future__ import annotations


class _LegacyMixin:
    pass
