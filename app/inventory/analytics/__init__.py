"""Statistics and machine learning over a campaign's reconciled variances.

The layer is a pure function of the domain output: give it the same variance
lines and it produces the same numbers, on any machine, in any order. Random
seeds are pinned for exactly that reason.
"""

from .features import (
    FEATURE_COLUMNS,
    attach_movement_features,
    attach_wip_features,
    build_frame,
)
from .models import (
    AbcXyzResult,
    AnomalyResult,
    BenfordResult,
    ClusterResult,
    abc_xyz,
    benford_check,
    cluster_patterns,
    compare_campaigns,
    detect_anomalies,
    digit_preference,
    feature_matrix,
    pareto_frontier,
    recount_priority,
)

__all__ = [
    "FEATURE_COLUMNS", "attach_movement_features", "attach_wip_features",
    "build_frame",
    "AbcXyzResult", "AnomalyResult", "BenfordResult", "ClusterResult",
    "abc_xyz", "benford_check", "cluster_patterns", "compare_campaigns",
    "detect_anomalies", "digit_preference", "feature_matrix", "pareto_frontier",
    "recount_priority",
]
