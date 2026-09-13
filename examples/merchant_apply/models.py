"""Strict merchant-change boundary models."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from threvo_actions.models import ExperimentalModel, SafeReference


class Product(ExperimentalModel):
    listing_reference: SafeReference
    price: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    revision: Annotated[int, Field(ge=1)] = 1


class StagedPriceChange(ExperimentalModel):
    change_reference: SafeReference
    listing_reference: SafeReference
    before_price: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    after_price: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    source_revision: Annotated[int, Field(ge=1)]
    status: Literal["staged"] = "staged"


class ApplyChangeCommand(ExperimentalModel):
    change_reference: SafeReference


class ApplyChangeSnapshot(ExperimentalModel):
    change_reference: SafeReference
    listing_reference: SafeReference
    before_price: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    after_price: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    source_revision: Annotated[int, Field(ge=1)]


class ApplyChangePreview(ExperimentalModel):
    change_reference: SafeReference
    listing_reference: SafeReference
    before_price: Decimal
    after_price: Decimal
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class ApplyChangeResult(ExperimentalModel):
    change_reference: SafeReference
    listing_reference: SafeReference
    applied_price: Decimal
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    status: Literal["applied"] = "applied"
