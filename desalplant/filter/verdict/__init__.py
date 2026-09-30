"""Bed-resistance verdicts from multi-flow pressure samples.

Pipeline
--------
1. :class:`BedSample` readings at different flows and temperatures are
   viscosity-normalised (``viscosity``) and least-squares fitted to the bed's
   own ``drop = a*flow + b*flow^2`` signature (``fit``).
2. The fitted resistance at a reference flow is compared against an immutable
   clean-bed baseline version (``baseline``).
3. ``decision`` turns that ratio into wash / replace / abstain / normal.
4. ``service`` owns the round lifecycle, pins every round to the baseline
   version active when it opened, and queues version changes for the next
   round boundary.
5. Committed :class:`VerdictRecord` rows are hash-chained and audited read-only
   by ``audit``; the console and external feed project the same rows.
"""

from .audit import AuditFinding, AuditReport, audit_verdicts
from .baseline import BaselineRegistry, BaselineVersion
from .decision import Decision, decide
from .fit import evaluate
from .models import (
    BedSample,
    DecisionThresholds,
    FitOutcome,
    FitRejection,
    FitThresholds,
    Reason,
    ResistanceCurve,
    RoundKind,
    Verdict,
)
from .round import Round
from .service import ResistanceService
from .verdict import VerdictRecord, hash_payload, verify_chain
from .viscosity import REFERENCE_TEMPERATURE_C, temperature_factor, viscosity_mPa_s

__all__ = [
    "AuditFinding",
    "AuditReport",
    "BaselineRegistry",
    "BaselineVersion",
    "BedSample",
    "Decision",
    "DecisionThresholds",
    "FitOutcome",
    "FitRejection",
    "FitThresholds",
    "Reason",
    "ResistanceCurve",
    "ResistanceService",
    "Round",
    "RoundKind",
    "Verdict",
    "VerdictRecord",
    "REFERENCE_TEMPERATURE_C",
    "audit_verdicts",
    "decide",
    "evaluate",
    "hash_payload",
    "temperature_factor",
    "verify_chain",
    "viscosity_mPa_s",
]
