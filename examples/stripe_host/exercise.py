"""Compatibility import for the installed PostgreSQL host exercise adapter."""

from threvo_actions.integrations.stripe.postgres_conformance import (
    ExercisePool,
    PostgresStripeHostExerciseAdapter,
)

__all__ = ["ExercisePool", "PostgresStripeHostExerciseAdapter"]
