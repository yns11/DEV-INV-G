"""Statistical and machine-learning analyses of a campaign.

Every model here answers a question a stock manager actually asks, and every one
returns something a human can act on rather than a score to admire:

``abc_xyz``           where is the money, and where is the volatility?
``pareto``            the shortest list of articles covering most of the euro gap.
``detect_anomalies``  which variances do not look like the others?
``cluster_patterns``  which articles fail *the same way* (so one fix serves many)?
``benford_check``     do the counted quantities look transcribed or invented?
``digit_preference``  are counters estimating instead of counting?
``recount_priority``  which recount buys the most certainty per hour spent?
``compare_campaigns`` what changed since the previous inventory?

Model choice favours robustness over sophistication: the data set is a few
thousand rows with heavy tails and no labels, which is precisely the regime
where unsupervised, distribution-free methods beat tuned supervised ones.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

__all__ = [
    "AbcXyzResult",
    "AnomalyResult",
    "ClusterResult",
    "BenfordResult",
    "abc_xyz",
    "pareto_frontier",
    "detect_anomalies",
    "cluster_patterns",
    "benford_check",
    "digit_preference",
    "recount_priority",
    "compare_campaigns",
    "feature_matrix",
]

#: Numeric signals fed to the unsupervised models. Chosen to be scale-free where
#: possible so a 2 M€ stator and a 0.02 € screw are comparable.
_MODEL_FEATURES = [
    "variance_ratio",
    "abs_variance_value_log",
    "book_value_log",
    "wip_share",
    "movement_intensity",
    "counted_only_flag",
    "book_only_flag",
]


# --------------------------------------------------------------------------- #
# ABC / XYZ
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class AbcXyzResult:
    frame: pd.DataFrame
    summary: pd.DataFrame

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": self.frame.to_dict(orient="records"),
            "summary": self.summary.to_dict(orient="records"),
        }


def abc_xyz(
    frame: pd.DataFrame,
    *,
    a_cut: float = 0.80,
    b_cut: float = 0.95,
    x_cut: float = 0.01,
    y_cut: float = 0.05,
) -> AbcXyzResult:
    """Classic ABC (value) × XYZ (reliability) segmentation.

    * **ABC** on cumulated book value: A = top 80 %, B = next 15 %, C = the tail.
      This is where the money is.
    * **XYZ** on relative variance: X = counted reliably (≤ 1 %), Y = ≤ 5 %,
      Z = worse. This is where the *trust* is.

    The interesting cell is **AZ**: high value, low reliability. Those articles
    deserve cycle counting between full inventories, and they are exactly the
    ones the legacy top-variance list buried among hundreds of C-class rows.
    """
    if frame.empty:
        empty = pd.DataFrame(columns=["item_number", "abc", "xyz", "segment"])
        return AbcXyzResult(frame=empty, summary=empty)

    per_item = (
        frame.groupby("item_number", as_index=False)
        .agg(
            book_value=("book_value", "sum"),
            abs_variance_value=("abs_variance_value", "sum"),
            book_qty=("book_qty", "sum"),
            variance_qty=("variance_qty", "sum"),
            item_type=("item_type", "first"),
            category=("category", "first"),
            program=("program", "first"),
        )
        .sort_values("book_value", key=lambda s: s.abs(), ascending=False)
        .reset_index(drop=True)
    )

    total = float(per_item["book_value"].abs().sum())
    per_item["value_share"] = (
        per_item["book_value"].abs() / total if total else 0.0
    )
    per_item["cumulative_share"] = per_item["value_share"].cumsum()
    per_item["abc"] = np.select(
        [per_item["cumulative_share"] <= a_cut,
         per_item["cumulative_share"] <= b_cut],
        ["A", "B"],
        default="C",
    )

    with np.errstate(divide="ignore", invalid="ignore"):
        reliability_gap = np.where(
            per_item["book_qty"].abs() > 0,
            per_item["variance_qty"].abs() / per_item["book_qty"].abs(),
            np.nan,
        )
    per_item["reliability_gap"] = reliability_gap
    per_item["xyz"] = np.select(
        [per_item["reliability_gap"] <= x_cut, per_item["reliability_gap"] <= y_cut],
        ["X", "Y"],
        default="Z",
    )
    # No book quantity means no measurable reliability: mark it rather than
    # letting NaN fall into the worst bucket by accident.
    per_item.loc[per_item["reliability_gap"].isna(), "xyz"] = "Z"
    per_item["segment"] = per_item["abc"] + per_item["xyz"]

    summary = (
        per_item.groupby("segment", as_index=False)
        .agg(
            items=("item_number", "count"),
            book_value=("book_value", "sum"),
            abs_variance_value=("abs_variance_value", "sum"),
        )
        .sort_values("abs_variance_value", ascending=False)
        .reset_index(drop=True)
    )
    return AbcXyzResult(frame=per_item, summary=summary)


def pareto_frontier(frame: pd.DataFrame, *, coverage: float = 0.80) -> pd.DataFrame:
    """The shortest article list covering *coverage* of the absolute variance."""
    if frame.empty:
        return frame
    per_item = (
        frame.groupby("item_number", as_index=False)
        .agg(
            abs_variance_value=("abs_variance_value", "sum"),
            variance_value=("variance_value", "sum"),
            book_value=("book_value", "sum"),
            variance_qty=("variance_qty", "sum"),
            item_type=("item_type", "first"),
            category=("category", "first"),
            program=("program", "first"),
        )
        .sort_values("abs_variance_value", ascending=False)
        .reset_index(drop=True)
    )
    total = float(per_item["abs_variance_value"].sum())
    if total <= 0:
        return per_item.head(0)
    per_item["share"] = per_item["abs_variance_value"] / total
    per_item["cumulative_share"] = per_item["share"].cumsum()
    cut = int((per_item["cumulative_share"] < coverage).sum()) + 1
    return per_item.head(min(cut, len(per_item)))


# --------------------------------------------------------------------------- #
# Feature matrix shared by the unsupervised models
# --------------------------------------------------------------------------- #

def feature_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    """Numeric matrix plus the aligned frame, ready for scikit-learn.

    Log1p is applied to the heavy-tailed money columns so that one 2 M€ stator
    does not define the entire scale, and every column is imputed to a
    meaningful default rather than dropped.
    """
    work = frame.copy()
    work["abs_variance_value_log"] = np.log1p(work["abs_variance_value"].clip(lower=0))
    work["book_value_log"] = np.log1p(work["book_value"].abs().clip(lower=0))
    work["variance_ratio"] = work.get("variance_ratio", pd.Series(dtype=float)).fillna(1.0)
    work["wip_share"] = work.get("wip_share", pd.Series(0.0, index=work.index)).fillna(0.0)
    movement = work.get("movement_count", pd.Series(0, index=work.index)).fillna(0)
    work["movement_intensity"] = np.log1p(movement.astype(float))
    work["counted_only_flag"] = work["counted_only"].astype(float)
    work["book_only_flag"] = work["book_only"].astype(float)

    matrix = work[_MODEL_FEATURES].to_numpy(dtype=float)
    matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)
    return matrix, work


# --------------------------------------------------------------------------- #
# Anomaly detection
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class AnomalyResult:
    frame: pd.DataFrame
    contamination: float
    method: str = "isolation_forest"
    feature_names: list[str] = field(default_factory=lambda: list(_MODEL_FEATURES))


def detect_anomalies(
    frame: pd.DataFrame,
    *,
    contamination: float = 0.05,
    random_state: int = 42,
) -> AnomalyResult:
    """Flag variances whose *shape* is unusual, not merely whose size is large.

    An Isolation Forest is the right tool here: no labels exist, the data is
    heavy-tailed and mixed-scale, and the model isolates points cheaply without
    assuming any distribution. ``random_state`` is pinned so a campaign
    re-analysed tomorrow yields the same flags — reproducibility is a
    requirement of the specification, not a nicety.

    The score is turned into a percentile so the UI can say "top 3 % most
    atypical" rather than showing a raw, uninterpretable number.
    """
    if len(frame) < 20:
        # Below ~20 points an Isolation Forest is fitting noise. Say so instead
        # of returning confident nonsense.
        out = frame.copy()
        out["anomaly_score"] = 0.0
        out["anomaly_percentile"] = 0.0
        out["is_anomaly"] = False
        return AnomalyResult(frame=out, contamination=contamination, method="insufficient_data")

    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import RobustScaler

    matrix, work = feature_matrix(frame)
    # RobustScaler (median / IQR) rather than StandardScaler: the outliers we
    # are hunting must not be allowed to set the scale that hides them.
    scaled = RobustScaler().fit_transform(matrix)

    forest = IsolationForest(
        n_estimators=300,
        contamination=min(max(contamination, 0.005), 0.5),
        random_state=random_state,
        n_jobs=1,  # 2 vCPU container: parallelism here costs more than it saves
    )
    forest.fit(scaled)
    # decision_function: higher = more normal. Negate so higher = more anomalous.
    work["anomaly_score"] = -forest.decision_function(scaled)
    work["is_anomaly"] = forest.predict(scaled) == -1
    work["anomaly_percentile"] = work["anomaly_score"].rank(pct=True)
    return AnomalyResult(frame=work, contamination=contamination)


# --------------------------------------------------------------------------- #
# Clustering
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class ClusterResult:
    frame: pd.DataFrame
    profiles: pd.DataFrame
    n_clusters: int
    silhouette: float | None = None


def cluster_patterns(
    frame: pd.DataFrame,
    *,
    max_clusters: int = 6,
    random_state: int = 42,
) -> ClusterResult:
    """Group articles that fail in the same way.

    The number of clusters is chosen by silhouette score rather than fixed:
    a campaign with two failure modes should not be forced into six. K-means on
    robustly-scaled features is deliberate — the goal is an interpretable
    profile per group ("high value, WIP-driven, never recounted"), which
    density-based methods do not hand you.
    """
    if len(frame) < 30:
        out = frame.copy()
        out["cluster"] = -1
        return ClusterResult(frame=out, profiles=pd.DataFrame(), n_clusters=0)

    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import RobustScaler

    matrix, work = feature_matrix(frame)
    scaled = RobustScaler().fit_transform(matrix)

    best_k, best_score, best_labels = 0, -1.0, None
    for k in range(2, min(max_clusters, len(frame) // 10) + 1):
        model = KMeans(n_clusters=k, random_state=random_state, n_init=10)
        labels = model.fit_predict(scaled)
        if len(set(labels)) < 2:
            continue
        try:
            score = float(silhouette_score(scaled, labels))
        except ValueError:  # pragma: no cover - degenerate geometry
            continue
        if score > best_score:
            best_k, best_score, best_labels = k, score, labels

    if best_labels is None:
        out = work.copy()
        out["cluster"] = -1
        return ClusterResult(frame=out, profiles=pd.DataFrame(), n_clusters=0)

    work["cluster"] = best_labels
    profiles = (
        work.groupby("cluster")
        .agg(
            items=("item_number", "nunique"),
            lines=("item_number", "size"),
            median_variance_ratio=("variance_ratio", "median"),
            total_abs_variance=("abs_variance_value", "sum"),
            total_book_value=("book_value", "sum"),
            mean_wip_share=("wip_share", "mean") if "wip_share" in work else ("variance_ratio", "mean"),
            counted_only=("counted_only", "sum"),
            book_only=("book_only", "sum"),
        )
        .reset_index()
        .sort_values("total_abs_variance", ascending=False)
    )
    profiles["label"] = profiles.apply(_describe_cluster, axis=1)
    return ClusterResult(
        frame=work, profiles=profiles, n_clusters=best_k, silhouette=best_score
    )


def _describe_cluster(row: pd.Series) -> str:
    """A short, human-readable name for a cluster profile."""
    parts: list[str] = []
    ratio = row.get("median_variance_ratio")
    if pd.notna(ratio):
        if ratio >= 0.5:
            parts.append("écart massif")
        elif ratio >= 0.05:
            parts.append("écart significatif")
        else:
            parts.append("écart faible")
    if row.get("mean_wip_share", 0) >= 0.5:
        parts.append("piloté par le WIP")
    if row.get("counted_only", 0) > row.get("items", 1) * 0.3:
        parts.append("stock non connu de l'ERP")
    if row.get("book_only", 0) > row.get("items", 1) * 0.3:
        parts.append("stock livre non compté")
    return " · ".join(parts) or "profil mixte"


# --------------------------------------------------------------------------- #
# Data-quality forensics
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class BenfordResult:
    observed: list[float]
    expected: list[float]
    chi_square: float
    p_value: float
    sample_size: int
    conclusion: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "digits": list(range(1, 10)),
            "observed": self.observed,
            "expected": self.expected,
            "chiSquare": round(self.chi_square, 3),
            "pValue": round(self.p_value, 5),
            "sampleSize": self.sample_size,
            "conclusion": self.conclusion,
        }


def benford_check(quantities: Sequence[float], *, min_sample: int = 100) -> BenfordResult:
    """Compare leading digits of counted quantities to Benford's law.

    Genuinely *counted* quantities in a warehouse span several orders of
    magnitude and follow Benford closely. A significant departure does not prove
    anything on its own, but it is a cheap, well-established signal that a batch
    of numbers was estimated, copied or rounded rather than counted — worth a
    targeted recount of the zones concerned.

    Reported as a chi-square goodness-of-fit test with 8 degrees of freedom.
    """
    from scipy import stats

    values = np.asarray([abs(float(q)) for q in quantities if q not in (None, 0)])
    values = values[values >= 1]
    n = int(values.size)
    expected_ratio = np.log10(1 + 1 / np.arange(1, 10))

    if n < min_sample:
        return BenfordResult(
            observed=[0.0] * 9,
            expected=[round(float(r), 4) for r in expected_ratio],
            chi_square=0.0,
            p_value=1.0,
            sample_size=n,
            conclusion=(
                f"Échantillon insuffisant ({n} valeurs, minimum {min_sample}) : "
                "test non concluant."
            ),
        )

    leading = (values / np.power(10, np.floor(np.log10(values)))).astype(int)
    counts = np.array([(leading == d).sum() for d in range(1, 10)], dtype=float)
    expected_counts = expected_ratio * n
    chi2 = float(((counts - expected_counts) ** 2 / expected_counts).sum())
    p_value = float(stats.chi2.sf(chi2, df=8))

    if p_value >= 0.05:
        conclusion = (
            "La distribution des premiers chiffres est conforme à la loi de "
            "Benford : rien n'indique de saisie estimée."
        )
    elif p_value >= 0.01:
        conclusion = (
            "Écart modéré à la loi de Benford : quelques zones méritent un "
            "contrôle par sondage."
        )
    else:
        conclusion = (
            "Écart significatif à la loi de Benford : une partie des quantités a "
            "probablement été estimée ou recopiée plutôt que comptée. "
            "Recomptage ciblé recommandé."
        )

    return BenfordResult(
        observed=[round(float(c / n), 4) for c in counts],
        expected=[round(float(r), 4) for r in expected_ratio],
        chi_square=chi2,
        p_value=p_value,
        sample_size=n,
        conclusion=conclusion,
    )


def digit_preference(quantities: Sequence[float]) -> dict[str, Any]:
    """Measure rounding habits in counted quantities.

    A counter who estimates produces suspiciously many multiples of 10, 50 and
    100. Comparing the observed share against what a genuine count would give
    (≈10 % of multiples of 10 if the last digit is uniform) turns a gut feeling
    into a number a supervisor can act on.
    """
    values = np.asarray(
        [abs(float(q)) for q in quantities if q is not None], dtype=float
    )
    values = values[values > 0]
    n = int(values.size)
    if n == 0:
        return {"sampleSize": 0, "buckets": {}, "roundingIndex": 0.0, "conclusion":
                "Aucune quantité exploitable."}

    integral = values[np.isclose(values, np.round(values))]
    if integral.size == 0:
        return {"sampleSize": n, "buckets": {}, "roundingIndex": 0.0,
                "conclusion": "Quantités non entières : test non applicable."}

    rounded = np.round(integral).astype(np.int64)
    buckets = {
        "multiplesOf10": float((rounded % 10 == 0).mean()),
        "multiplesOf50": float((rounded % 50 == 0).mean()),
        "multiplesOf100": float((rounded % 100 == 0).mean()),
        "endingIn5": float((rounded % 10 == 5).mean()),
    }
    # Expected shares under a uniform last digit: 10 %, 2 %, 1 %, 10 %.
    excess = buckets["multiplesOf10"] - 0.10
    index = max(0.0, min(1.0, excess / 0.90))

    if index < 0.15:
        conclusion = "Pas de biais d'arrondi notable : comptage détaillé."
    elif index < 0.40:
        conclusion = (
            "Biais d'arrondi modéré : certaines zones comptent probablement par "
            "conditionnement plutôt qu'à l'unité."
        )
    else:
        conclusion = (
            "Fort biais d'arrondi : une part importante des quantités semble "
            "estimée. Vérifier les consignes de comptage des zones concernées."
        )

    return {
        "sampleSize": int(integral.size),
        "buckets": {k: round(v, 4) for k, v in buckets.items()},
        "roundingIndex": round(index, 4),
        "conclusion": conclusion,
    }


# --------------------------------------------------------------------------- #
# Recount prioritisation
# --------------------------------------------------------------------------- #

def recount_priority(frame: pd.DataFrame, *, top_n: int = 50) -> pd.DataFrame:
    """Rank recount candidates by expected value recovered per recount.

    The score is an expected-value calculation, not a ranking by size:

        priority = |variance €| × P(the variance is a counting error)

    ``P(counting error)`` is estimated from signals that genuinely discriminate:
    an article counted in many locations, with a large *relative* gap, flagged as
    atypical, and not yet recounted, is far more likely to be a count problem
    than an ERP consumption problem. Ranking by euro amount alone — what the
    legacy ``TOP ECARTS`` tab did — sends teams to recount articles whose gap is
    structural and will not move.
    """
    if frame.empty:
        return frame

    work = frame.copy()
    ratio = work.get("variance_ratio", pd.Series(np.nan, index=work.index)).fillna(1.0)
    anomaly = work.get("anomaly_percentile", pd.Series(0.5, index=work.index)).fillna(0.5)
    wip = work.get("wip_share", pd.Series(0.0, index=work.index)).fillna(0.0)
    movements = work.get("movement_count", pd.Series(0, index=work.index)).fillna(0)

    # A big *relative* gap points at counting; a WIP-driven gap points at BOM and
    # production declarations, which a recount will not fix.
    p_counting_error = (
        0.45 * np.clip(ratio, 0, 1)
        + 0.30 * anomaly
        + 0.25 * work["counted_only"].astype(float)
    ) * (1.0 - 0.5 * wip)
    # Already recounted: the marginal value of another pass drops sharply.
    p_counting_error *= np.where(movements > 0, 0.4, 1.0)

    work["p_counting_error"] = np.clip(p_counting_error, 0.0, 1.0)
    work["recount_expected_value"] = (
        work["abs_variance_value"] * work["p_counting_error"]
    )
    ranked = work.sort_values("recount_expected_value", ascending=False).head(top_n)
    return ranked[[
        c for c in (
            "item_number", "warehouse_id", "location_id", "item_type", "category",
            "program", "book_qty", "counted_qty", "variance_qty", "variance_value",
            "abs_variance_value", "variance_ratio", "wip_share", "movement_count",
            "p_counting_error", "recount_expected_value",
        ) if c in ranked.columns
    ]].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Campaign comparison
# --------------------------------------------------------------------------- #

def compare_campaigns(
    current: pd.DataFrame,
    previous: pd.DataFrame,
    *,
    movements_between: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compare two campaigns article by article.

    ``movements_between`` — the ERP stock transactions posted between the two
    count dates — closes the loop:

        expected_book_now = book_previous + movements_between

    An article whose *current* book stock does not match that expectation has a
    bookkeeping problem independent of counting, and an article whose variance
    persists across two campaigns with the same sign is a structural leak, not
    an accident. Both are invisible when campaigns are compared in isolation.
    """
    if current.empty:
        return current

    cur = (
        current.groupby("item_number", as_index=False)
        .agg(
            book_qty=("book_qty", "sum"),
            counted_qty=("counted_qty", "sum"),
            variance_qty=("variance_qty", "sum"),
            variance_value=("variance_value", "sum"),
            book_value=("book_value", "sum"),
            item_type=("item_type", "first"),
            category=("category", "first"),
            program=("program", "first"),
        )
    )
    if previous.empty:
        cur["previous_variance_qty"] = np.nan
        cur["previous_variance_value"] = np.nan
        cur["recurrence"] = "nouvelle campagne"
        return cur

    prev = (
        previous.groupby("item_number", as_index=False)
        .agg(
            previous_book_qty=("book_qty", "sum"),
            previous_counted_qty=("counted_qty", "sum"),
            previous_variance_qty=("variance_qty", "sum"),
            previous_variance_value=("variance_value", "sum"),
        )
    )
    merged = cur.merge(prev, on="item_number", how="outer")
    for column in (
        "book_qty", "counted_qty", "variance_qty", "variance_value", "book_value",
        "previous_book_qty", "previous_counted_qty", "previous_variance_qty",
        "previous_variance_value",
    ):
        if column in merged:
            merged[column] = merged[column].fillna(0.0)

    if movements_between is not None and not movements_between.empty:
        moves = movements_between.groupby("item_number", as_index=False)["qty"].sum()
        moves = moves.rename(columns={"qty": "movements_between_qty"})
        merged = merged.merge(moves, on="item_number", how="left")
        merged["movements_between_qty"] = merged["movements_between_qty"].fillna(0.0)
        merged["expected_book_qty"] = (
            merged["previous_book_qty"] + merged["movements_between_qty"]
        )
        merged["book_drift_qty"] = merged["book_qty"] - merged["expected_book_qty"]
    else:
        merged["movements_between_qty"] = np.nan
        merged["expected_book_qty"] = np.nan
        merged["book_drift_qty"] = np.nan

    same_sign = (
        np.sign(merged["variance_qty"]) == np.sign(merged["previous_variance_qty"])
    ) & (merged["variance_qty"] != 0)
    merged["recurrence"] = np.select(
        [
            same_sign & (merged["variance_qty"].abs()
                         >= merged["previous_variance_qty"].abs()),
            same_sign,
            (merged["previous_variance_qty"] != 0) & (merged["variance_qty"] == 0),
            (merged["previous_variance_qty"] == 0) & (merged["variance_qty"] != 0),
        ],
        ["récurrent — aggravé", "récurrent — atténué", "résolu", "nouveau"],
        default="ponctuel",
    )
    merged["variance_delta_value"] = (
        merged["variance_value"] - merged["previous_variance_value"]
    )
    return merged.sort_values(
        "variance_value", key=lambda s: s.abs(), ascending=False
    ).reset_index(drop=True)
