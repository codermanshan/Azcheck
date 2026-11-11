"""Noise / competitor filters for Amazon.ae Best Sellers results."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from parse import Product, has_amazon_buybox, is_amazon_seller_name

# Non-product / service listings that clutter Best Sellers.
NOISE_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"extended warranty",
        r"warranty plan",
        r"protection plan",
        r"accident protection",
        r"email delivery",
        r"no physical delivery",
        r"gift card",
        r"e-?gift",
        r"digital code",
        r"subscription",
        r"renewal plan",
        r"service plan",
    ]
]

# Amazon / 1P house brands — often not useful as "competitor products" to copy.
AMAZON_BRAND_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"^amazon basics\b",
        r"\bamazon basics\b",
        r"^amazon\.ae\b",
        r"^amazon\b.*\b(echo|kindle|fire tv|ring |blink |eero)\b",
        r"\bamazon essentials\b",
        r"\bsolimo\b",
        r"\bwrenbury\b",
        r"\bpresto!\b",
        r"\bamazon brand\b",
    ]
]


@dataclass
class FilterOptions:
    exclude_noise: bool = True
    exclude_amazon_brands: bool = True
    # Drop listings where Amazon.ae wins the Buybox (checked after AOD enrich).
    exclude_amazon_sellers: bool = True
    # Helium-style opportunity filters (applied after estimates).
    unbranded_only: bool = True
    sellable_only: bool = True
    min_revenue: float | None = 3000.0
    min_score: float | None = None
    exclude_brands: list[str] = field(default_factory=list)
    min_price: float | None = None
    max_price: float | None = None
    min_rating: float | None = None

    def brand_needles(self) -> list[str]:
        return [b.strip().lower() for b in self.exclude_brands if b.strip()]


def _matches_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def should_keep(product: Product, opts: FilterOptions) -> bool:
    title = product.title or ""
    lower = title.lower()

    if opts.exclude_noise and _matches_any(title, NOISE_PATTERNS):
        return False

    if opts.exclude_amazon_brands and _matches_any(title, AMAZON_BRAND_PATTERNS):
        return False

    for brand in opts.brand_needles():
        if brand in lower:
            return False

    if opts.min_price is not None:
        if product.price_aed is None or product.price_aed < opts.min_price:
            return False

    if opts.max_price is not None:
        if product.price_aed is None or product.price_aed > opts.max_price:
            return False

    if opts.min_rating is not None:
        if product.rating is None or product.rating < opts.min_rating:
            return False

    # Drop any listing where Amazon.ae wins the Buybox.
    if opts.exclude_amazon_sellers and has_amazon_buybox(product):
        return False

    return True


def apply_filters(products: list[Product], opts: FilterOptions) -> list[Product]:
    return [p for p in products if should_keep(p, opts)]


def drop_amazon_buybox(products: list[Product]) -> list[Product]:
    """Hard filter: never keep an ASIN whose Buybox winner is Amazon."""
    kept: list[Product] = []
    for p in products:
        # Sync flag from sold_by text in case detection was missed earlier.
        if is_amazon_seller_name(p.sold_by):
            p.is_amazon_seller = True
            p.sold_by = p.sold_by or "Amazon.ae"
        if has_amazon_buybox(p):
            continue
        # Also drop if sold_by literally contains amazon.ae / amazon retail
        if p.sold_by and is_amazon_seller_name(p.sold_by):
            continue
        kept.append(p)
    return kept
