from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import BaggingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.pipeline import FeatureUnion
from sklearn.preprocessing import normalize
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import InterestModelRun, Paper
from .interest_evidence import paper_interest_level

INTEREST_TARGETS = {
    "ignored": 0.0,
    "low": 0.25,
    "neutral": 0.5,
    "high": 0.75,
    "interested": 1.0,
}
MODEL_SCHEMA_VERSION = 2
MODEL_ARTIFACT_DIR = "models"
RELEVANCE_THRESHOLD = 0.5
CONFIDENCE_THRESHOLD = 0.62
MIN_TRAINING_SAMPLES = 20


@dataclass(frozen=True)
class TrainingExample:
    paper_id: str
    document: str
    target: float
    source: str
    observed_at: datetime


@dataclass(frozen=True)
class RankedPaper:
    score: float
    confidence: float
    review_required: bool
    support: float
    ensemble_std: float
    reasons: list[dict[str, Any]]


def _label_for_paper(paper: Paper) -> tuple[str, str] | None:
    if paper.status == "completed":
        level = paper_interest_level(paper)
        return (level, "confirmed") if level in INTEREST_TARGETS else None
    if paper.status == "ignored":
        return "ignored", "ignored_status"
    screening = paper.screening_feedback or {}
    level = screening.get("interest_level")
    if level in INTEREST_TARGETS:
        return str(level), "screening"
    return None


def _document_for_paper(paper: Paper) -> str:
    metadata = paper.metadata_json or {}
    categories = metadata.get("categories")
    category_values = (
        " ".join(f"category_{str(value).replace('.', '_')}" for value in categories)
        if isinstance(categories, list)
        else ""
    )
    primary = metadata.get("primary_category")
    primary_token = (
        f" primary_{str(primary).replace('.', '_')}" if isinstance(primary, str) else ""
    )
    comment = metadata.get("comment")
    journal_ref = metadata.get("journal_ref")
    author_names = " ".join(
        str(author.get("name", ""))
        for author in (paper.authors or [])
        if isinstance(author, dict) and author.get("name")
    )
    title = " ".join((paper.title or "").split())
    abstract = " ".join((paper.abstract or "").split())
    # The title is intentionally repeated to give it more weight in the shared
    # sparse feature space without maintaining a second vectorizer.
    return " ".join(
        value
        for value in (
            title,
            title,
            abstract,
            category_values,
            primary_token,
            str(comment or ""),
            str(journal_ref or ""),
            author_names,
        )
        if value
    )


def collect_training_examples(session: Session) -> list[TrainingExample]:
    examples: list[TrainingExample] = []
    for paper in session.scalars(select(Paper).order_by(Paper.updated_at.asc())).all():
        label = _label_for_paper(paper)
        if label is None:
            continue
        level, source = label
        document = _document_for_paper(paper)
        if not document.strip():
            continue
        examples.append(
            TrainingExample(
                paper_id=paper.id,
                document=document,
                target=INTEREST_TARGETS[level],
                source=source,
                observed_at=paper.updated_at or paper.created_at,
            )
        )
    return examples


def _vectorizer() -> FeatureUnion:
    return FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    lowercase=True,
                    strip_accents="unicode",
                    ngram_range=(1, 2),
                    min_df=1,
                    max_df=0.95,
                    sublinear_tf=True,
                    max_features=14_000,
                ),
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    lowercase=True,
                    ngram_range=(3, 5),
                    min_df=2,
                    max_df=0.95,
                    sublinear_tf=True,
                    max_features=10_000,
                ),
            ),
        ],
        transformer_weights={"word": 1.0, "char": 0.55},
    )


def _digest_examples(examples: list[TrainingExample]) -> str:
    payload = [
        {
            "paper_id": example.paper_id,
            "target": example.target,
            "source": example.source,
        }
        for example in examples
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _temporal_mae(
    matrix: Any,
    targets: np.ndarray,
    examples: list[TrainingExample],
) -> tuple[float | None, float]:
    """Use chronological holdouts and return (MAE, residual scale)."""
    if len(examples) < 40:
        return None, 0.22
    sample_count = len(examples)
    first_test = max(MIN_TRAINING_SAMPLES // 2, int(sample_count * 0.6))
    errors: list[float] = []
    for test_start in range(first_test, sample_count, max(5, sample_count // 6)):
        test_end = min(sample_count, test_start + max(8, sample_count // 6))
        if test_start >= test_end:
            continue
        model = Ridge(alpha=0.8)
        model.fit(matrix[:test_start], targets[:test_start])
        predictions = np.clip(model.predict(matrix[test_start:test_end]), 0.0, 1.0)
        errors.extend((predictions - targets[test_start:test_end]).tolist())
    if not errors:
        return None, 0.22
    mae = float(np.mean(np.abs(errors)))
    residual_scale = float(max(0.08, math.sqrt(float(np.mean(np.square(errors))))))
    return round(mae, 4), round(residual_scale, 4)


def _reasons(model: BaggingRegressor, feature_names: np.ndarray, *, limit: int = 6) -> list[dict]:
    coefficients = [
        estimator.coef_
        for estimator in model.estimators_
        if hasattr(estimator, "coef_")
    ]
    if not coefficients:
        return []
    mean = np.mean(np.vstack(coefficients), axis=0)
    indices = np.argsort(mean)[::-1]
    positive = [
        {"term": str(feature_names[index]), "weight": round(float(mean[index]), 4)}
        for index in indices[:limit]
        if mean[index] > 0
    ]
    indices = np.argsort(mean)
    negative = [
        {"term": str(feature_names[index]), "weight": round(float(mean[index]), 4)}
        for index in indices[:limit]
        if mean[index] < 0
    ]
    return [*positive, *negative]


def _safe_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


class InterestRanker:
    def __init__(self, artifact: dict[str, Any]) -> None:
        self.artifact = artifact

    @property
    def version(self) -> str:
        return str(self.artifact["version"])

    @property
    def relevance_threshold(self) -> float:
        return _safe_float(self.artifact.get("relevance_threshold"), RELEVANCE_THRESHOLD)

    @property
    def confidence_threshold(self) -> float:
        return _safe_float(self.artifact.get("confidence_threshold"), CONFIDENCE_THRESHOLD)

    def score_document(self, document: str) -> RankedPaper:
        if not self.artifact.get("ready"):
            score = 0.5
            return RankedPaper(
                score=score,
                confidence=0.0,
                review_required=True,
                support=0.0,
                ensemble_std=0.0,
                reasons=[{"reason": "insufficient_training_labels"}],
            )
        vectorizer = self.artifact["vectorizer"]
        matrix = normalize(vectorizer.transform([document]), copy=False)
        estimator: BaggingRegressor = self.artifact["estimator"]
        member_predictions = np.asarray(
            [np.clip(model.predict(matrix), 0.0, 1.0) for model in estimator.estimators_]
        )
        score = float(np.clip(member_predictions.mean(), 0.0, 1.0))
        ensemble_std = float(member_predictions.std(axis=0)[0])
        training_matrix = self.artifact["training_matrix"]
        support = (
            float(np.asarray((matrix @ training_matrix.T).toarray()).max())
            if training_matrix.shape[0]
            else 0.0
        )
        residual_scale = _safe_float(self.artifact.get("residual_scale"), 0.22)
        support_component = float(np.clip(support / 0.12, 0.0, 1.0))
        stability_component = float(
            np.clip(1.0 - ensemble_std / max(residual_scale, 0.01), 0.0, 1.0)
        )
        boundary_component = float(
            np.clip(abs(score - self.relevance_threshold) / 0.25, 0.0, 1.0)
        )
        confidence = float(
            np.clip(
                0.35 * support_component
                + 0.25 * stability_component
                + 0.40 * boundary_component,
                0.0,
                1.0,
            )
        )
        review_required = (
            confidence < self.confidence_threshold
            or support < 0.08
        )
        return RankedPaper(
            score=round(score, 4),
            confidence=round(confidence, 4),
            review_required=review_required,
            support=round(support, 4),
            ensemble_std=round(ensemble_std, 4),
            reasons=[
                {
                    "support": round(support, 4),
                    "ensemble_std": round(ensemble_std, 4),
                    "residual_scale": residual_scale,
                },
                *list(self.artifact.get("reasons", []))[:6],
            ],
        )


def _artifact_path(version: str) -> Path:
    path = settings.data_dir / MODEL_ARTIFACT_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{version}.joblib"


def load_model_run(session: Session) -> InterestModelRun | None:
    return session.scalar(
        select(InterestModelRun)
        .where(InterestModelRun.status == "active")
        .order_by(InterestModelRun.trained_at.desc())
        .limit(1)
    )


def load_ranker(model_run: InterestModelRun | None = None) -> InterestRanker | None:
    if model_run is None:
        return None
    path = Path(model_run.artifact_path)
    if not path.exists():
        return None
    return InterestRanker(joblib.load(path))


def train_daily_ranker(
    session: Session,
    *,
    now: datetime | None = None,
) -> tuple[InterestRanker, InterestModelRun, dict[str, Any]]:
    """Fit and persist one deterministic model from the current human labels."""
    trained_at = now or datetime.now(UTC)
    examples = collect_training_examples(session)
    label_digest = _digest_examples(examples)
    version_seed = (
        f"{MODEL_SCHEMA_VERSION}:{label_digest}:{trained_at.date().isoformat()}"
    )
    version = "interest-v1-" + hashlib.sha256(version_seed.encode()).hexdigest()[:16]
    artifact_path = _artifact_path(version)
    existing = session.scalar(
        select(InterestModelRun).where(InterestModelRun.version == version).limit(1)
    )
    if existing is not None and artifact_path.exists():
        if existing.status != "active":
            session.execute(
                InterestModelRun.__table__.update()
                .where(InterestModelRun.status == "active")
                .values(status="retired")
            )
            existing.status = "active"
            session.commit()
        ranker = InterestRanker(joblib.load(artifact_path))
        return ranker, existing, ranker.artifact["metrics"]

    session.execute(
        InterestModelRun.__table__.update()
        .where(InterestModelRun.status == "active")
        .values(status="retired")
    )
    if len(examples) < MIN_TRAINING_SAMPLES:
        artifact = {
            "schema_version": MODEL_SCHEMA_VERSION,
            "version": version,
            "ready": False,
            "relevance_threshold": RELEVANCE_THRESHOLD,
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "metrics": {"sample_count": len(examples), "reason": "insufficient_labels"},
        }
        joblib.dump(artifact, artifact_path)
        model_run = InterestModelRun(
            version=version,
            status="active",
            sample_count=len(examples),
            validation_mae=None,
            residual_scale=None,
            relevance_threshold=RELEVANCE_THRESHOLD,
            confidence_threshold=CONFIDENCE_THRESHOLD,
            artifact_path=str(artifact_path),
            label_digest=label_digest,
            metadata_json=artifact["metrics"],
            trained_at=trained_at,
        )
        session.add(model_run)
        session.commit()
        return InterestRanker(artifact), model_run, artifact["metrics"]

    documents = [example.document for example in examples]
    targets = np.asarray([example.target for example in examples], dtype=float)
    vectorizer = _vectorizer()
    matrix = vectorizer.fit_transform(documents)
    normalized_matrix = normalize(matrix, copy=True)
    validation_mae, residual_scale = _temporal_mae(matrix, targets, examples)
    estimator = BaggingRegressor(
        estimator=Ridge(alpha=0.8),
        n_estimators=40,
        max_samples=0.8,
        bootstrap=True,
        random_state=42,
        n_jobs=1,
    )
    estimator.fit(matrix, targets)
    feature_names = vectorizer.get_feature_names_out()
    reasons = _reasons(estimator, feature_names)
    metrics = {
        "sample_count": len(examples),
        "validation_mae": validation_mae,
        "residual_scale": residual_scale,
        "label_counts": {
            level: sum(example.target == target for example in examples)
            for level, target in INTEREST_TARGETS.items()
        },
        "label_sources": {
            source: sum(example.source == source for example in examples)
            for source in sorted({example.source for example in examples})
        },
    }
    artifact = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "version": version,
        "ready": True,
        "vectorizer": vectorizer,
        "estimator": estimator,
        "training_matrix": normalized_matrix,
        "residual_scale": residual_scale,
        "relevance_threshold": RELEVANCE_THRESHOLD,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "reasons": reasons,
        "metrics": metrics,
    }
    joblib.dump(artifact, artifact_path)
    model_run = InterestModelRun(
        version=version,
        status="active",
        sample_count=len(examples),
        validation_mae=validation_mae,
        residual_scale=residual_scale,
        relevance_threshold=RELEVANCE_THRESHOLD,
        confidence_threshold=CONFIDENCE_THRESHOLD,
        artifact_path=str(artifact_path),
        label_digest=label_digest,
        metadata_json={**metrics, "reasons": reasons},
        trained_at=trained_at,
    )
    session.add(model_run)
    session.commit()
    return InterestRanker(artifact), model_run, metrics


def score_paper(ranker: InterestRanker, paper: Paper) -> RankedPaper:
    ranked = ranker.score_document(_document_for_paper(paper))
    metadata = dict(paper.metadata_json or {})
    metadata["interest_model"] = {
        "version": ranker.version,
        "score": ranked.score,
        "confidence": ranked.confidence,
        "review_required": ranked.review_required,
        "support": ranked.support,
        "ensemble_std": ranked.ensemble_std,
        "reasons": ranked.reasons,
        "scored_at": datetime.now(UTC).isoformat(),
    }
    paper.model_relevance_score = ranked.score
    paper.model_relevance_confidence = ranked.confidence
    paper.model_version = ranker.version
    paper.model_review_required = ranked.review_required
    paper.metadata_json = metadata
    if not ranked.review_required and paper.relevance_score is None:
        paper.relevance_score = ranked.score
    return ranked


def rescore_candidate_papers(
    session: Session,
    ranker: InterestRanker,
    *,
    paper_ids: list[str] | None = None,
) -> dict[str, Any]:
    statement = select(Paper).where(Paper.status == "candidate")
    if paper_ids is not None:
        statement = statement.where(Paper.id.in_(paper_ids))
    papers = list(session.scalars(statement).all())
    review_required = 0
    confident_relevant = 0
    confident_filtered = 0
    for paper in papers:
        ranked = score_paper(ranker, paper)
        if ranked.review_required:
            review_required += 1
        elif ranked.score >= ranker.relevance_threshold:
            confident_relevant += 1
        else:
            confident_filtered += 1
    session.commit()
    return {
        "model_version": ranker.version,
        "rescored": len(papers),
        "agent_review": review_required,
        "confident_relevant": confident_relevant,
        "confident_filtered": confident_filtered,
    }
