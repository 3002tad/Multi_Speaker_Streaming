from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class EnrollmentProfile:
    centroid: np.ndarray
    prototypes: tuple[np.ndarray, ...]
    total_embeddings: int
    retained_embeddings: int
    median_centroid_similarity: float
    minimum_centroid_similarity: float


@dataclass(frozen=True)
class SpeakerDecision:
    accepted: bool
    label: str | None
    score: float | None
    runner_up_score: float | None
    margin: float | None
    consensus: float
    observations: int
    required_score: float
    reason: str


def can_early_accept_speaker(
    decision: SpeakerDecision,
    *,
    score_buffer: float,
    margin_threshold: float,
    margin_buffer: float,
) -> bool:
    """Allow a second runtime window to be skipped only for a clear match."""
    if (
        not decision.accepted
        or decision.score is None
        or decision.score
        < decision.required_score + max(0.0, score_buffer)
    ):
        return False
    if decision.runner_up_score is None:
        return True
    return (
        decision.margin is not None
        and decision.margin
        >= margin_threshold + max(0.0, margin_buffer)
    )


def adaptive_absolute_threshold(
    *,
    base_floor: float,
    single_profile_threshold: float,
    profile_count: int,
    max_profile_similarity: float | None,
    margin_threshold: float,
    maximum_threshold: float = 0.96,
) -> float:
    """Raise the absolute gate when enrolled voices form a close cohort.

    The base floor protects against low-confidence matches.  It is not the
    final threshold once several enrolled profiles exist: the closest pair of
    enrolled centroids determines how high the gate must be raised.
    """
    if profile_count <= 1 or max_profile_similarity is None:
        return max(base_floor, single_profile_threshold)
    cohort_guard = max_profile_similarity + margin_threshold
    return min(
        maximum_threshold,
        max(base_floor, single_profile_threshold * 0.96, cohort_guard),
    )


def normalize_embedding(embedding: np.ndarray) -> np.ndarray:
    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-8:
        raise ValueError("Speaker embedding is empty or invalid")
    return vector / norm


def build_enrollment_profile(
    embeddings: Sequence[np.ndarray],
    *,
    max_prototypes: int = 5,
    minimum_similarity: float = 0.78,
) -> EnrollmentProfile:
    """Build a robust centroid and retain representative, non-outlier samples."""
    if len(embeddings) < 3:
        raise ValueError("Enrollment requires at least 3 clean speech windows")

    matrix = np.stack([normalize_embedding(item) for item in embeddings])
    initial_centroid = normalize_embedding(matrix.mean(axis=0))
    similarities = matrix @ initial_centroid

    median = float(np.median(similarities))
    mad = float(np.median(np.abs(similarities - median)))
    robust_floor = median - max(0.025, 2.5 * mad)
    keep_floor = max(minimum_similarity, robust_floor)
    keep_mask = similarities >= keep_floor
    retained = matrix[keep_mask]

    minimum_retained = max(3, int(np.ceil(len(matrix) * 0.60)))
    if len(retained) < minimum_retained:
        raise ValueError(
            "Enrollment audio is inconsistent; use one speaker in a quieter recording"
        )

    centroid = normalize_embedding(retained.mean(axis=0))
    retained_similarities = retained @ centroid
    retained_median = float(np.median(retained_similarities))
    retained_minimum = float(np.min(retained_similarities))
    if retained_median < minimum_similarity:
        raise ValueError(
            "Enrollment voice windows are not similar enough to form a profile"
        )

    # Keep the centroid plus real windows closest to it. Real prototypes make
    # matching less sensitive to one averaged vector while excluding outliers.
    ordered = np.argsort(retained_similarities)[::-1]
    sample_count = min(len(ordered), max(1, max_prototypes - 1))
    representative_positions = np.linspace(
        0, len(ordered) - 1, num=sample_count, dtype=int
    )
    selected = tuple(
        retained[ordered[position]].copy()
        for position in representative_positions
    )
    return EnrollmentProfile(
        centroid=centroid,
        prototypes=(centroid.copy(), *selected),
        total_embeddings=len(matrix),
        retained_embeddings=len(retained),
        median_centroid_similarity=retained_median,
        minimum_centroid_similarity=retained_minimum,
    )


def decide_open_set_speaker(
    observations: Sequence[Mapping[str, float]],
    *,
    absolute_threshold: float,
    margin_threshold: float,
    consensus_threshold: float,
    single_profile_threshold: float | None = None,
) -> SpeakerDecision:
    """Accept a known speaker only when several windows agree confidently."""
    clean_observations = [
        {
            str(label): float(score)
            for label, score in item.items()
            if label and np.isfinite(score)
        }
        for item in observations
        if item
    ]
    if not clean_observations:
        return SpeakerDecision(
            accepted=False,
            label=None,
            score=None,
            runner_up_score=None,
            margin=None,
            consensus=0.0,
            observations=0,
            required_score=absolute_threshold,
            reason="no_candidates",
        )

    labels = sorted(
        {label for observation in clean_observations for label in observation}
    )
    aggregate_scores = {
        label: float(
            np.median(
                [
                    observation.get(label, -1.0)
                    for observation in clean_observations
                ]
            )
        )
        for label in labels
    }
    ranking = sorted(
        aggregate_scores.items(), key=lambda item: item[1], reverse=True
    )
    winner, winner_score = ranking[0]
    runner_score = ranking[1][1] if len(ranking) > 1 else None
    margin = (
        winner_score - runner_score if runner_score is not None else None
    )

    votes = sum(
        max(observation.items(), key=lambda item: item[1])[0] == winner
        for observation in clean_observations
    )
    consensus = votes / len(clean_observations)

    # One short observation or a database with only one known speaker is an
    # open-set trap: there is no meaningful runner-up. Demand a stronger
    # absolute match instead of accepting the nearest profile by default.
    required_score = absolute_threshold
    if len(clean_observations) == 1:
        required_score += 0.03
    if len(labels) == 1:
        required_score = max(
            required_score + 0.02,
            (
                single_profile_threshold
                if single_profile_threshold is not None
                else required_score + 0.02
            ),
        )

    if winner_score < required_score:
        reason = "score_below_threshold"
    elif runner_score is not None and margin < margin_threshold:
        reason = "ambiguous_top_two"
    elif consensus < consensus_threshold:
        reason = "inconsistent_windows"
    else:
        reason = "accepted"

    return SpeakerDecision(
        accepted=reason == "accepted",
        label=winner if reason == "accepted" else None,
        score=winner_score,
        runner_up_score=runner_score,
        margin=margin,
        consensus=consensus,
        observations=len(clean_observations),
        required_score=required_score,
        reason=reason,
    )
