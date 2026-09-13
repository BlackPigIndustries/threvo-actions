"""A lock-guarded merchant catalog with an eventually visible read model."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from .models import ApplyChangeResult, Product, StagedPriceChange


class MerchantCatalog:
    """Example target; production uses a transactional catalog and durable outbox."""

    def __init__(self) -> None:
        self._products: dict[str, Product] = {}
        self._changes: dict[str, StagedPriceChange] = {}
        self._effects: dict[str, ApplyChangeResult] = {}
        self._hidden_queries: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self.write_count = 0

    def seed(self, product: Product) -> None:
        self._products[product.listing_reference] = product

    def product(self, listing_reference: str) -> Product:
        product = self._products.get(listing_reference)
        if product is None:
            raise LookupError("listing_not_found")
        return product.model_copy(deep=True)

    def stage_price_update(
        self,
        *,
        change_reference: str,
        listing_reference: str,
        after_price: Decimal,
    ) -> StagedPriceChange:
        product = self.product(listing_reference)
        delta = abs(after_price - product.price) / product.price
        if after_price <= Decimal("0") or delta > Decimal("0.20"):
            raise ValueError("price_guardrail_refused")
        proposed = StagedPriceChange(
            change_reference=change_reference,
            listing_reference=listing_reference,
            before_price=product.price,
            after_price=after_price,
            currency=product.currency,
            source_revision=product.revision,
        )
        existing = self._changes.setdefault(change_reference, proposed)
        if existing != proposed:
            raise ValueError("change_binding_conflict")
        return existing

    def staged(self, change_reference: str) -> StagedPriceChange:
        change = self._changes.get(change_reference)
        if change is None:
            raise LookupError("change_not_found")
        return change

    def mutate_price(self, listing_reference: str, price: Decimal) -> None:
        current = self.product(listing_reference)
        self._products[listing_reference] = current.model_copy(
            update={"price": price, "revision": current.revision + 1}
        )

    def hide_effect_for_queries(self, effect_reference: str, count: int) -> None:
        self._hidden_queries[effect_reference] = count

    @staticmethod
    def precondition(product: Product) -> str:
        minor = int(product.price * Decimal("100"))
        return f"catalog:{product.listing_reference}:v{product.revision}:price-{minor}"

    async def apply(
        self,
        *,
        effect_reference: str,
        change: StagedPriceChange,
        expected_precondition: str,
    ) -> ApplyChangeResult | None:
        async with self._lock:
            existing = self._effects.get(effect_reference)
            if existing is not None:
                return existing
            current = self.product(change.listing_reference)
            if self.precondition(current) != expected_precondition:
                return None
            result = ApplyChangeResult(
                change_reference=change.change_reference,
                listing_reference=change.listing_reference,
                applied_price=change.after_price,
                currency=change.currency,
            )
            self._products[change.listing_reference] = current.model_copy(
                update={"price": change.after_price, "revision": current.revision + 1}
            )
            self._effects[effect_reference] = result
            self.write_count += 1
            return result

    async def query_effect(self, effect_reference: str) -> ApplyChangeResult | None:
        remaining = self._hidden_queries.get(effect_reference, 0)
        if remaining > 0:
            self._hidden_queries[effect_reference] = remaining - 1
            return None
        return self._effects.get(effect_reference)
