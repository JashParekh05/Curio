"""Pure Embed_Reference normalization core for the Content_Provider abstraction.

Every Content_Provider surfaces candidate content as a ``SourceItem`` carrying an
``EmbedReference`` (see ``content_provider``). Before any Clip is admitted, that
candidate must be normalized into a *valid* embed-based descriptor: a
provider-tagged ``EmbedReference`` plus a start/end timestamp pair bounded by the
item's reported duration plus an exactly 384-dimension embedding. A Clip is never
hosted media -- it is always an Embed_Reference plus timestamps (Req 4.1, 4.2).

This module is a set of *pure* decision functions -- no DB, no clock, no global
mutation, deterministic in their inputs -- mirroring ``provider_dedup.py`` and
``quota_pool.py``. The thin I/O shell that persists admitted clips lives
elsewhere; this module never touches it (Req 9.5).

The rules ``normalize`` enforces, checked in a fixed deterministic order so an
invalid candidate always maps to the same specific reason (Req 4.5, 4.6):

  1. ``missing_provider_id``     -- the embed reference has no Provider_Id.
  2. ``missing_external_id``     -- the embed reference has no external item id.
  3. ``missing_playback_params`` -- the embed reference has no playback params.
  4. ``missing_duration``        -- the item reports no total duration (Req 4.6).
  5. ``bad_embedding_dim``       -- the embedding is not exactly 384 dims (Req 4.2).
  6. ``bad_timestamps``          -- NOT ``0 <= start < end <= duration`` (Req 4.3).

A normalized Embed_Reference round-trips: rendering it back into provider playback
coordinates reproduces its Provider_Id and external item identifier without
modification (Req 4.4). ASCII only.

Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 9.5
"""
from __future__ import annotations

from typing import Literal

from dataclasses import dataclass

from app.services.content_provider import EmbedReference, SourceItem

# ---------------------------------------------------------------------------
# Constants and error labels
# ---------------------------------------------------------------------------

#: The exact number of numeric dimensions every Clip embedding must carry (Req 4.2).
EMBEDDING_DIM = 384

#: The specific reasons ``normalize`` discards a candidate (Req 4.5, 4.6).
NormalizeError = Literal[
    "missing_provider_id",
    "missing_external_id",
    "missing_playback_params",
    "bad_embedding_dim",
    "bad_timestamps",
    "missing_duration",
]


# ---------------------------------------------------------------------------
# Immutable value models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaybackCoords:
    """Provider playback coordinates rendered back from an Embed_Reference.

    The minimal pair needed to locate the original item at its source provider:
    the Provider_Id and the provider's external item identifier. Rendering an
    Embed_Reference into these coordinates reproduces the original identity
    without modification (the round-trip property, Req 4.4).

    Attributes:
        provider_id: The originating Content_Provider's Provider_Id.
        external_id: The provider's external item identifier.
    """

    provider_id: str
    external_id: str


# ---------------------------------------------------------------------------
# The normalization decision
# ---------------------------------------------------------------------------


def normalize(
    item: SourceItem,
    *,
    start: float,
    end: float,
    embedding: list[float],
) -> tuple[EmbedReference | None, NormalizeError | None]:
    """Normalize a candidate Source_Item into a valid Embed_Reference for a Clip.

    Builds a provider-tagged ``EmbedReference`` from the candidate's embed
    descriptor and validates the proposed Clip timestamps and embedding. On
    success returns ``(embed_ref, None)``; otherwise returns ``(None, error)`` and
    the candidate is discarded with no Clip admitted (Req 4.5).

    The validation is applied in a fixed, deterministic order so an invalid
    candidate always maps to the same specific reason:

      1. ``missing_provider_id``     when the embed reference Provider_Id is empty.
      2. ``missing_external_id``     when the embed reference external id is empty.
      3. ``missing_playback_params`` when the embed reference has no playback
         parameters (Req 4.5).
      4. ``missing_duration``        when ``item.duration_seconds is None`` (Req 4.6).
      5. ``bad_embedding_dim``       when ``len(embedding) != 384`` (Req 4.2).
      6. ``bad_timestamps``          when NOT ``0 <= start < end <= duration`` (Req 4.3).

    The returned Embed_Reference reproduces the candidate's Provider_Id, external
    item identifier, and playback parameters, so a subsequent ``render_coords``
    round-trips to the original identity (Req 4.1, 4.4).

    Args:
        item: The candidate Source_Item to normalize.
        start: Proposed Clip start timestamp, in seconds.
        end: Proposed Clip end timestamp, in seconds.
        embedding: The Clip embedding vector; must be exactly 384 dims (Req 4.2).

    Returns:
        ``(embed_ref, None)`` when the candidate is valid, else ``(None, error)``
        with ``error`` the specific ``NormalizeError`` for the first failed check.

    Pure and total: depends only on its inputs, performs no I/O.

    Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 9.5
    """
    embed = item.embed_ref

    # 1. Provider_Id must be present (Req 4.5).
    if not embed.provider_id:
        return None, "missing_provider_id"

    # 2. External item identifier must be present (Req 4.5).
    if not embed.external_id:
        return None, "missing_external_id"

    # 3. Playback parameters required to render the embed must be present (Req 4.5).
    if not embed.playback_params:
        return None, "missing_playback_params"

    # 4. A total duration must be reported to bound the timestamps (Req 4.6).
    duration = item.duration_seconds
    if duration is None:
        return None, "missing_duration"

    # 5. The embedding must carry exactly 384 numeric dimensions (Req 4.2).
    if len(embedding) != EMBEDDING_DIM:
        return None, "bad_embedding_dim"

    # 6. Timestamps must satisfy 0 <= start < end <= duration (Req 4.3).
    if not (0 <= start < end <= duration):
        return None, "bad_timestamps"

    return (
        EmbedReference(
            provider_id=embed.provider_id,
            external_id=embed.external_id,
            playback_params=dict(embed.playback_params),
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Round-trip rendering
# ---------------------------------------------------------------------------


def render_coords(ref: EmbedReference) -> PlaybackCoords:
    """Render an Embed_Reference back into provider playback coordinates.

    Reproduces the Provider_Id and external item identifier the Embed_Reference
    was built from, without modification, so a normalized reference round-trips
    to its original source identity (Req 4.1, 4.4).

    Args:
        ref: The Embed_Reference to render.

    Returns:
        The ``PlaybackCoords`` carrying ``ref.provider_id`` and
        ``ref.external_id`` unchanged.

    Pure and total: depends only on its input, performs no I/O.
    """
    return PlaybackCoords(provider_id=ref.provider_id, external_id=ref.external_id)


def roundtrip_preserves(ref: EmbedReference) -> bool:
    """Return True iff rendering ``ref`` reproduces its identity unchanged.

    The round-trip identity asserted by Req 4.4: normalizing a provider item into
    an Embed_Reference and rendering it back into provider playback coordinates
    reproduces the original Provider_Id and external item identifier without
    modification.

    Args:
        ref: The Embed_Reference to round-trip.

    Returns:
        True iff ``render_coords(ref)`` reproduces ``ref.provider_id`` and
        ``ref.external_id`` unchanged.

    Pure and total: depends only on its input, performs no I/O.
    """
    coords = render_coords(ref)
    return coords.provider_id == ref.provider_id and coords.external_id == ref.external_id
