"""Shared scan / export logic for CLI and GUI."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from categories import BASE_URL, BESTSELLERS_PATH, CATEGORIES, EXCLUDED_CATEGORY_SLUGS
from client import AmazonClient
from filters import FilterOptions, apply_filters, drop_amazon_buybox
from opportunity import (
    apply_opportunity_filters,
    apply_trend_labels,
    enrich_opportunity,
    save_history_snapshot,
    sort_by_opportunity,
)
from parse import (
    Product,
    aod_url,
    has_amazon_buybox,
    is_amazon_seller_name,
    next_bestsellers_page_url,
    parse_aod_sellers,
    parse_bestsellers_page,
    parse_product_extras,
    short_label,
)
from search import suggest_categories, title_matches_keyword, try_amazon_search

LogFn = Callable[[str], None]


def resolve_categories(selected: list[str] | None) -> dict[str, str]:
    if not selected or selected == ["all"]:
        return dict(CATEGORIES)
    out: dict[str, str] = {}
    for slug in selected:
        key = slug.strip().lower()
        if key in EXCLUDED_CATEGORY_SLUGS:
            continue
        if key not in CATEGORIES:
            raise ValueError(f"Unknown category: {key}")
        out[key] = CATEGORIES[key]
    if not out:
        raise ValueError("No valid categories selected.")
    return out


def fetch_category(
    client: AmazonClient,
    slug: str,
    label: str,
    limit: int,
    log: LogFn | None = None,
) -> list[Product]:
    products: list[Product] = []
    url = f"{BASE_URL}{BESTSELLERS_PATH.format(slug=slug)}"
    pages = 0
    max_pages = max(1, (limit + 49) // 50)

    while url and len(products) < limit and pages < max_pages + 1:
        pages += 1
        if log:
            log(f"Fetching {label} page {pages}...")
        html = client.get(url)
        batch = parse_bestsellers_page(html, label)
        if not batch:
            break
        products.extend(batch)
        if len(products) >= limit:
            break
        url = next_bestsellers_page_url(html, url)

    by_asin: dict[str, Product] = {}
    for p in products:
        if p.asin not in by_asin or p.rank < by_asin[p.asin].rank:
            by_asin[p.asin] = p
    return sorted(by_asin.values(), key=lambda p: p.rank)[:limit]


def assign_category_competitors(products: list[Product], top_n: int = 3) -> None:
    """Fill top_competitors from other Best Sellers in the same category."""
    by_cat: dict[str, list[Product]] = {}
    for p in products:
        by_cat.setdefault(p.category, []).append(p)

    for items in by_cat.values():
        ranked = sorted(items, key=lambda x: x.rank)
        for product in ranked:
            peers = [x for x in ranked if x.asin != product.asin][:top_n]
            if not peers:
                product.top_competitors = None
                continue
            product.top_competitors = " | ".join(
                f"#{x.rank} {short_label(x.title)} ({x.asin})" for x in peers
            )


def enrich_bought(
    client: AmazonClient,
    products: list[Product],
    max_enrich: int,
    log: LogFn | None = None,
    check_sellers: bool = True,
    fetch_bought: bool = True,
) -> None:
    targets = products[:max_enrich]
    if log:
        log(
            f"Enriching sellers"
            f"{' + bought-past-month' if fetch_bought else ''} "
            f"for {len(targets)} products..."
        )
    for i, product in enumerate(targets, 1):
        try:
            if check_sellers:
                aod_html = client.get(aod_url(product.asin))
                sold_by, others, other_opts = parse_aod_sellers(aod_html)
                product.sold_by = sold_by
                product.is_amazon_seller = is_amazon_seller_name(sold_by)
                if product.is_amazon_seller and not product.sold_by:
                    product.sold_by = "Amazon.ae"
                product.other_sellers = " | ".join(others) if others else None
                # Prefer unique third-party seller count; fall back to AOD "other options".
                product.other_new_sellers = (
                    len(others) if others else (other_opts if other_opts is not None else 0)
                )
                if product.is_amazon_seller and other_opts is not None and not others:
                    # Amazon buybox with only "N other options" and parse miss — keep count.
                    product.other_new_sellers = other_opts

            if fetch_bought:
                html = client.get(product.url)
                if len(html) >= 20_000 and "captcha" not in html.lower():
                    bought, page_comps = parse_product_extras(html, product.asin)
                    product.bought_past_month = bought
                    if page_comps:
                        product.top_competitors = " | ".join(page_comps)

            if log:
                seller_bit = (
                    f"sold_by={product.sold_by or '?'}; "
                    f"amazon={product.is_amazon_seller}; "
                    f"other_sellers={product.other_new_sellers}"
                )
                log(f"  [{i}/{len(targets)}] {product.asin}: {seller_bit}")
        except Exception as exc:  # noqa: BLE001
            if log:
                log(f"  [{i}/{len(targets)}] {product.asin}: skip ({exc})")


def run_scan(
    category_slugs: list[str],
    limit: int = 30,
    enrich: bool = True,
    enrich_max: int = 50,
    delay: float = 1.2,
    filters: FilterOptions | None = None,
    log: LogFn | None = None,
    fetch_multiplier: float = 2.5,
    competitor_count: int = 3,
) -> list[Product]:
    """
    Fetch bestsellers, oversample a bit so filters still leave ~limit items,
    then enrich (sellers + bought) and drop Amazon-sold listings if requested.
    """
    opts = filters or FilterOptions()
    cats = resolve_categories(category_slugs)
    # Pull extra rows so title + Amazon-seller filters still fill the table.
    mult = fetch_multiplier
    if opts.exclude_amazon_sellers:
        mult = max(mult, 3.0)
    fetch_limit = max(limit, int(limit * mult))
    all_products: list[Product] = []

    with AmazonClient(delay=delay) as client:
        for slug, label in cats.items():
            if log:
                log(f"Category: {label}")
            try:
                batch = fetch_category(client, slug, label, fetch_limit, log=log)
            except Exception as exc:  # noqa: BLE001
                if log:
                    log(f"  Failed: {exc}")
                continue
            if log:
                log(f"  Raw: {len(batch)} products")
            # Title/price filters first (seller flag unknown yet).
            pre_seller_opts = FilterOptions(
                exclude_noise=opts.exclude_noise,
                exclude_amazon_brands=opts.exclude_amazon_brands,
                exclude_amazon_sellers=False,
                unbranded_only=False,
                sellable_only=False,
                min_revenue=None,
                min_score=None,
                exclude_brands=list(opts.exclude_brands),
                min_price=opts.min_price,
                max_price=opts.max_price,
                min_rating=opts.min_rating,
            )
            filtered = apply_filters(batch, pre_seller_opts)[:fetch_limit]
            if log:
                log(f"  After title filters: {len(filtered)}")
            all_products.extend(filtered)

        assign_category_competitors(all_products, top_n=competitor_count)

        if all_products:
            # Seller/AOD check is required to ignore Amazon-sold listings.
            enrich_bought(
                client,
                all_products,
                max(enrich_max, len(all_products)),
                log=log,
                check_sellers=True,
                fetch_bought=enrich,
            )

        if opts.exclude_amazon_sellers:
            before = len(all_products)
            all_products = drop_amazon_buybox(all_products)
            if log:
                log(
                    f"Excluded Amazon Buybox winners: {before - len(all_products)} "
                    f"(kept {len(all_products)})"
                )

        # Helium-style estimates + opportunity ranking
        if all_products:
            enrich_opportunity(all_products)
            apply_trend_labels(all_products)
            before = len(all_products)
            all_products = apply_opportunity_filters(
                all_products,
                unbranded_only=opts.unbranded_only,
                sellable_only=opts.sellable_only,
                min_revenue=opts.min_revenue,
                min_score=opts.min_score,
            )
            if log:
                log(
                    f"Opportunity filters: {before} -> {len(all_products)} "
                    f"(unbranded={opts.unbranded_only}, sellable={opts.sellable_only}, "
                    f"min_revenue={opts.min_revenue})"
                )
            # Final safety: never return Amazon Buybox winners
            if opts.exclude_amazon_sellers:
                all_products = drop_amazon_buybox(all_products)
            all_products = sort_by_opportunity(all_products)
            try:
                save_history_snapshot(all_products)
            except Exception as exc:  # noqa: BLE001
                if log:
                    log(f"History save skipped: {exc}")

        # Cap per category to requested limit after filters.
        capped: list[Product] = []
        per_cat: dict[str, int] = {}
        for p in all_products:
            if opts.exclude_amazon_sellers and has_amazon_buybox(p):
                continue
            n = per_cat.get(p.category, 0)
            if n >= limit:
                continue
            per_cat[p.category] = n + 1
            capped.append(p)
        # Keep global revenue order across categories
        all_products = sort_by_opportunity(capped)
        if opts.exclude_amazon_sellers:
            all_products = drop_amazon_buybox(all_products)

    return all_products


def write_csv(path: Path, products: list[Product]) -> None:
    fields = [
        "category",
        "rank",
        "asin",
        "title",
        "price_aed",
        "est_monthly_units",
        "est_monthly_revenue",
        "estimate_source",
        "opportunity_score",
        "trend",
        "sellable",
        "sellable_reason",
        "is_branded",
        "detected_brand",
        "sold_by",
        "is_amazon_seller",
        "other_new_sellers",
        "other_sellers",
        "bought_past_month",
        "top_competitors",
        "rating",
        "review_count",
        "url",
        "image_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for p in products:
            writer.writerow(p.to_dict())


def write_json(path: Path, products: list[Product]) -> None:
    payload = {
        "marketplace": "amazon.ae",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Helium-style estimates: monthly units/revenue are APPROXIMATIONS. "
            "Prefer bought-in-past-month badges when present; otherwise rank model. "
            "Not official Amazon or Helium 10 data. Always validate trademarks before sourcing."
        ),
        "excluded": [
            "appliances",
            "large-appliances",
            "noise",
            "amazon-buybox",
            "branded",
        ],
        "products": [p.to_dict() for p in products],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_out_path(ext: str = ".csv") -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return Path(f"azcheck_{stamp}{ext}")


def run_list_underneath_search(
    keyword: str,
    category_slugs: list[str] | None = None,
    limit: int = 20,
    enrich: bool = True,
    delay: float = 1.2,
    filters: FilterOptions | None = None,
    log: LogFn | None = None,
) -> list[Product]:
    """
    Find unbranded ASINs matching a keyword where you can likely list underneath.
    Tries Amazon search first; falls back to Best Sellers + title keyword match.
    """
    keyword = (keyword or "").strip()
    if not keyword:
        raise ValueError("Enter a product keyword to search (e.g. velvet hangers).")

    opts = filters or FilterOptions(
        unbranded_only=True,
        sellable_only=True,
        exclude_amazon_sellers=True,
        min_revenue=1000.0,
    )
    # List-underneath mode defaults: keep more candidates before revenue cut.
    if opts.min_revenue is None:
        opts.min_revenue = 1000.0

    products: list[Product] = []
    with AmazonClient(delay=delay) as client:
        # 1) Try live Amazon search
        try:
            if log:
                log(f"Searching amazon.ae for: {keyword}")
            products = try_amazon_search(client, keyword, limit=max(limit * 2, 24))
            if log:
                log(f"  Search hits: {len(products)}")
        except Exception as exc:  # noqa: BLE001
            if log:
                log(f"  Amazon search unavailable ({exc}). Using Best Sellers + keyword...")

        # 2) Fallback / supplement from Best Sellers
        if len(products) < max(8, limit):
            slugs = category_slugs
            if not slugs:
                slugs = suggest_categories(keyword)
            cats = resolve_categories(slugs)
            if log:
                log(f"  Scanning Best Sellers in: {', '.join(cats.values())}")
            for slug, label in cats.items():
                try:
                    batch = fetch_category(
                        client, slug, label, max(limit * 2, 40), log=log
                    )
                except Exception as exc:  # noqa: BLE001
                    if log:
                        log(f"  Failed {label}: {exc}")
                    continue
                matched = [p for p in batch if title_matches_keyword(p.title, keyword)]
                if log:
                    log(f"  {label}: {len(matched)} title matches")
                products.extend(matched)

        # Deduplicate by ASIN
        by_asin: dict[str, Product] = {}
        for p in products:
            if p.asin not in by_asin or p.rank < by_asin[p.asin].rank:
                by_asin[p.asin] = p
        products = list(by_asin.values())

        # Title/noise filters before seller enrich
        pre = FilterOptions(
            exclude_noise=opts.exclude_noise,
            exclude_amazon_brands=opts.exclude_amazon_brands,
            exclude_amazon_sellers=False,
            unbranded_only=False,
            sellable_only=False,
            min_revenue=None,
            exclude_brands=list(opts.exclude_brands),
            min_price=opts.min_price,
            max_price=opts.max_price,
        )
        products = apply_filters(products, pre)
        if keyword:
            products = [p for p in products if title_matches_keyword(p.title, keyword)]

        assign_category_competitors(products, top_n=3)

        if products:
            enrich_bought(
                client,
                products,
                max(len(products), limit),
                log=log,
                check_sellers=True,
                fetch_bought=enrich,
            )

        if opts.exclude_amazon_sellers:
            before_amz = len(products)
            products = drop_amazon_buybox(products)
            if log:
                log(
                    f"Excluded Amazon Buybox winners: {before_amz - len(products)} "
                    f"(kept {len(products)})"
                )

        enrich_opportunity(products)
        apply_trend_labels(products)
        before = len(products)
        products = apply_opportunity_filters(
            products,
            unbranded_only=opts.unbranded_only,
            sellable_only=opts.sellable_only,
            min_revenue=opts.min_revenue,
            min_score=opts.min_score,
        )
        if opts.exclude_amazon_sellers:
            products = drop_amazon_buybox(products)
        # Prefer true list-underneath (other sellers >= 1)
        strong = [p for p in products if (p.other_new_sellers or 0) >= 1]
        if strong:
            products = strong
        if log:
            log(
                f"List-underneath filter: {before} -> {len(products)} "
                f"(unbranded + can list under)"
            )

        products = drop_amazon_buybox(sort_by_opportunity(products)[:limit])
        try:
            save_history_snapshot(products)
        except Exception:  # noqa: BLE001
            pass

    return products
