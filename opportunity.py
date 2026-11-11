"""
Helium-style opportunity layer for amazon.ae.

Estimates are NOT official Amazon figures:
- Prefer Amazon's "bought in past month" badge when present (range midpoint).
- Otherwise estimate from Best Sellers category rank (log curve calibrated for AE).
- Revenue = estimated monthly units x price.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from parse import Product, has_amazon_buybox

HISTORY_PATH = Path(__file__).resolve().parent / "data" / "scan_history.json"

# Known / obvious brands common on amazon.ae (private-label hunters usually avoid these).
KNOWN_BRANDS = [
    "apple", "samsung", "sony", "lg", "huawei", "xiaomi", "oppo", "vivo", "nokia",
    "nike", "adidas", "puma", "under armour", "reebok",
    "anker", "soundcore", "ugreen", "baseus", "belkin", "logitech", "jbl", "bose",
    "duracell", "energizer", "panasonic", "toshiba", "philips", "braun", "oral-b",
    "nivea", "loreal", "l'oreal", "garnier", "maybelline", "neutrogena", "dove",
    "himalaya", "johnson", "johnson's", "pampers", "huggies", "kleenex", "fine",
    "ariel", "persil", "fairy", "dettol", "harpic", "cif",
    "black+decker", "black & decker", "dewalt", "bosch", "makita", "dyson",
    "tupperware", "locknlock", "pyrex", "tefal", "logitech", "tp-link", "tp link",
    "kindle", "echo", "fire tv", "ring ", "microsoft", "google", "meta",
    "nintendo", "playstation", "xbox", "lego", "barbie", "hot wheels",
    "ikea", "nescafé", "nescafe", "lipton", "coca-cola", "pepsi",
    "oral b", "colgate", "sensodyne", "listerine",
    "casio", "rolex", "fossil", "ray-ban", "oakley",
    "canon", "nikon", "gopro", "dji",
    "hp ", "hewlett", "dell", "lenovo", "asus", "acer", "msi",
    "intel", "amd", "nvidia", "kingston", "sandisk", "seagate", "western digital",
]

BOUGHT_NUM_RE = re.compile(
    r"([\d,.]+)\s*([KkMm])?\+?\s*bought in (?:past|the last) month",
    re.I,
)


def parse_bought_units(bought_past_month: str | None) -> Optional[int]:
    """Convert '4K+ bought in past month' -> midpoint estimate (e.g. 4500)."""
    if not bought_past_month:
        return None
    m = BOUGHT_NUM_RE.search(bought_past_month)
    if not m:
        return None
    raw = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").upper()
    if suffix == "K":
        raw *= 1000
    elif suffix == "M":
        raw *= 1_000_000
    # Badge is usually a floor ("4K+"); use a modest uplift as midpoint.
    return int(raw * 1.15)


def estimate_units_from_rank(rank: int, category: str = "") -> int:
    """
    Rough monthly units from Best Sellers list rank (1 = hottest in that list).
    Tuned as a comparative model for amazon.ae category bestseller pages, not US BSR.
    """
    rank = max(1, int(rank))
    # Rank 1 ~ high hundreds/thousands; rank 50 ~ dozens; rank 100 ~ low dozens.
    # monthly ~= A / rank^B
    units = 2800.0 / (rank ** 0.85)
    # Soft category nudges (AE traffic differs by niche).
    cat = (category or "").lower()
    if any(k in cat for k in ("beauty", "grocery", "health", "baby")):
        units *= 1.25
    elif any(k in cat for k in ("electronics", "computers", "mobile")):
        units *= 1.1
    elif any(k in cat for k in ("books", "videogames")):
        units *= 0.7
    return max(5, int(round(units)))


def detect_brand(title: str, strict: bool = False) -> tuple[bool, Optional[str]]:
    """Return (is_branded, matched_brand_or_None)."""
    text = f" {title.lower()} "
    for brand in KNOWN_BRANDS:
        needle = brand.lower().strip()
        if len(needle) <= 2:
            continue
        if (
            f" {needle} " in text
            or text.strip().startswith(needle + " ")
            or text.strip().startswith(needle + ",")
            or text.strip().startswith(needle + ":")
        ):
            return True, brand.strip()

    if strict:
        m = re.match(r"^([A-Z][A-Za-z0-9][A-Za-z0-9'\-]{1,24})\b", title.strip())
        if m:
            token = m.group(1)
            if token.lower() not in {
                "the", "new", "pack", "set", "premium", "generic", "portable",
                "wireless", "usb", "led", "mini", "pro", "best", "original",
            }:
                return True, token
    return False, None


def competition_score(other_sellers: int | None) -> float:
    """Sweet spot: a few sellers prove the listing is open; too many = crowded."""
    if other_sellers is None:
        return 40.0
    n = other_sellers
    if n == 0:
        return 35.0  # possible gate / exclusive
    if 1 <= n <= 4:
        return 95.0
    if 5 <= n <= 12:
        return 75.0
    if 13 <= n <= 25:
        return 50.0
    return 25.0


def score_opportunity(product: Product) -> float:
    revenue = product.est_monthly_revenue or 0.0
    units = product.est_monthly_units or 0
    # Revenue component (cap around 50k AED/mo for scoring)
    rev_score = min(100.0, (revenue / 50_000.0) * 100.0)
    units_score = min(100.0, (units / 2000.0) * 100.0)
    comp = competition_score(product.other_new_sellers)
    brand_score = 15.0 if product.is_branded else 95.0
    # Best-seller rank boost (lower rank = hotter)
    rank_score = max(0.0, 100.0 - (product.rank - 1) * 2.5)

    weighted = (
        rev_score * 0.35
        + units_score * 0.15
        + comp * 0.20
        + brand_score * 0.20
        + rank_score * 0.10
    )
    return round(min(100.0, max(0.0, weighted)), 1)


def assess_sellable(product: Product) -> tuple[bool, str]:
    """
    Can you list underneath this ASIN as a 3P seller?

    Strong YES = unbranded + other sellers already on the listing.
    """
    if product.is_branded:
        return False, f"No - branded ({product.detected_brand}). Listing under risks trademark issues."

    if has_amazon_buybox(product):
        return False, "No - Amazon.ae owns the Buybox. Excluded."

    others = product.other_new_sellers or 0
    if others >= 1:
        tip = (
            f"YES - list underneath ASIN {product.asin}. "
            f"{others} other new seller(s) already share this listing "
            f"(buybox: {product.sold_by or '3P'})."
        )
        return True, tip

    if product.sold_by and not product.is_amazon_seller:
        return True, (
            f"MAYBE - 3P buybox ({product.sold_by}) but no other offers yet. "
            f"You can try listing underneath ASIN {product.asin}; check category approval."
        )

    return False, "No clear list-underneath signal yet."


def list_underneath_summary(product: Product) -> str:
    """Short plain-English tip for the UI detail panel."""
    brand = "unbranded" if not product.is_branded else f"branded ({product.detected_brand})"
    rev = (
        f"~AED {product.est_monthly_revenue:,.0f}/mo est."
        if product.est_monthly_revenue
        else "revenue n/a"
    )
    others = product.other_new_sellers if product.other_new_sellers is not None else "?"
    return (
        f"{product.title[:90]}\n"
        f"ASIN: {product.asin} | {brand} | {rev} | other sellers: {others}\n"
        f"{product.sellable_reason or ''}"
    )


def enrich_opportunity(products: list[Product]) -> None:
    for p in products:
        units = parse_bought_units(p.bought_past_month)
        source = "bought_badge"
        if units is None:
            units = estimate_units_from_rank(p.rank, p.category)
            source = "bsr_rank_model"

        price = p.price_aed or 0.0
        revenue = round(units * price, 2) if price else 0.0

        branded, brand = detect_brand(p.title)
        p.est_monthly_units = units
        p.est_monthly_revenue = revenue
        p.estimate_source = source
        p.is_branded = branded
        p.detected_brand = brand
        p.opportunity_score = score_opportunity(p)
        sellable, reason = assess_sellable(p)
        p.sellable = sellable
        p.sellable_reason = reason
        # Trend label filled later from history
        if not p.trend:
            p.trend = "new"


def apply_opportunity_filters(
    products: list[Product],
    *,
    unbranded_only: bool = True,
    sellable_only: bool = True,
    min_revenue: float | None = None,
    min_score: float | None = None,
) -> list[Product]:
    out: list[Product] = []
    for p in products:
        if unbranded_only and p.is_branded:
            continue
        if sellable_only and p.sellable is False:
            continue
        if min_revenue is not None and (p.est_monthly_revenue or 0) < min_revenue:
            continue
        if min_score is not None and (p.opportunity_score or 0) < min_score:
            continue
        out.append(p)
    return out


def sort_by_opportunity(products: list[Product]) -> list[Product]:
    return sorted(
        products,
        key=lambda p: (
            -(p.est_monthly_revenue or 0),
            -(p.opportunity_score or 0),
            p.rank,
        ),
    )


def load_history() -> dict:
    if not HISTORY_PATH.exists():
        return {"scans": []}
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"scans": []}


def save_history_snapshot(products: list[Product]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = load_history()
    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "items": [
            {
                "asin": p.asin,
                "rank": p.rank,
                "est_monthly_units": p.est_monthly_units,
                "est_monthly_revenue": p.est_monthly_revenue,
                "category": p.category,
            }
            for p in products
        ],
    }
    scans = data.get("scans", [])
    scans.append(snapshot)
    data["scans"] = scans[-30:]  # keep last 30 runs
    HISTORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_trend_labels(products: list[Product]) -> None:
    data = load_history()
    scans = data.get("scans", [])
    if len(scans) < 1:
        for p in products:
            p.trend = "new"
        return

    prev = {item["asin"]: item for item in scans[-1].get("items", [])}
    for p in products:
        old = prev.get(p.asin)
        if not old:
            p.trend = "rising"
            continue
        old_rank = old.get("rank") or 999
        old_rev = old.get("est_monthly_revenue") or 0
        new_rev = p.est_monthly_revenue or 0
        if p.rank < old_rank - 2 or new_rev > old_rev * 1.15:
            p.trend = "rising"
        elif p.rank > old_rank + 2 or new_rev < old_rev * 0.85:
            p.trend = "falling"
        else:
            p.trend = "stable"
