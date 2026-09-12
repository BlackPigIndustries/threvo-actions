"""Experimental gradual-reveal authoring API.

This namespace may change independently while it is evaluated. The expert
root API remains the stable runtime contract.
"""

from ..evidence import (
    ActionEvidenceBundle,
    EvidenceValidationReport,
    render_evidence_html,
    validate_evidence_bundle,
)
from .application import (
    ActionApplication,
    ActionApplicationError,
    ActionComponents,
    ActionIssueCode,
    ActionRecipe,
    ActionSpec,
    BoundAction,
    DependencyScopeFactory,
    RegisteredAction,
)
from .inspection import (
    ActionInspection,
    ActionOwnershipInspection,
    ActionSettingsInspection,
    BoundaryModelInspection,
)

__all__ = [
    "ActionApplication",
    "ActionApplicationError",
    "ActionComponents",
    "ActionEvidenceBundle",
    "ActionIssueCode",
    "ActionInspection",
    "ActionOwnershipInspection",
    "ActionRecipe",
    "ActionSpec",
    "ActionSettingsInspection",
    "BoundaryModelInspection",
    "BoundAction",
    "DependencyScopeFactory",
    "EvidenceValidationReport",
    "RegisteredAction",
    "render_evidence_html",
    "validate_evidence_bundle",
]
