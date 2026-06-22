"""Content_Provider implementations for the alternative-content-streams feature.

Each module in this package implements the uniform ``ContentProvider`` contract
defined in ``app/services/content_provider.py``, normalizing a single source's
output into the shared embed-based ``SourceItem`` model. The YouTube provider
(``youtube_provider.py``) is a behavior-preserving adapter over the existing
``app/services/youtube.py`` charge site; additional providers (Vimeo, podcast
feeds, Khan Academy) plug in here behind the same contract.
"""
