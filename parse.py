"""Parse amazon.ae Best Sellers and product detail HTML."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from categories import BASE_URL

BOUGHT_RE = re.compile(
    r"([\d,]+(?:\.\d+)?[KkMm]?\+?)\s+bought in (?:past|the last) month",
    re.IGNORECASE,
)
PRICE_RE = re.compile(r"AED\s*([\d,]+(?:\.\d+)?)")
RATING_RE = re.compile(r"([\d.]+)\s+out of\s+5")
REVIEWS_RE = re.compile(r"([\d,]+)\s+rating")


@dataclass
class Product:
    category: str
    rank: int
    asin: str
    title: str
    price_aed: Optional[float]
    rating: Optional[float]
    review_count: Optional[int]
    url: str
    bought_past_month: Optional[str] = None
    image_url: Optional[str] = None
    # Other strong products in the same Best Sellers list / similar ASINs.
    top_competitors: Optional[str] = None
    # Buybox / offer sellers (from Amazon "Other sellers" / AOD).
    sold_by: Optional[str] = None
    is_amazon_seller: Optional[bool] = None
    other_new_sellers: Optional[int] = None
    other_sellers: Optional[str] = None
    # Helium-style opportunity estimates (approximate).
    est_monthly_units: Optional[int] = None
    est_monthly_revenue: Optional[float] = None
    estimate_source: Optional[str] = None
    is_branded: Optional[bool] = None
    detected_brand: Optional[str] = None
    opportunity_score: Optional[float] = None
    sellable: Optional[bool] = None
    sellable_reason: Optional[str] = None
    trend: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Broad match: any Buybox / "Sold by" string that is Amazon retail (not a 3P name).
AMAZON_SELLER_RE = re.compile(
    r"(amazon\.ae|amazon\.com|amazon\.sa|amazon\.eg|"
    r"amazon\s*uae|amazon\s*retail|"
    r"^\s*amazon\s*$|"
    r"ships from and sold by amazon|"
    r"sold by\s*amazon)",
    re.I,
)


def is_amazon_seller_name(name: str | None) -> bool:
    """True when the Buybox / sold-by name is Amazon retail."""
    if not name:
        return False
    cleaned = re.sub(r"\s+", " ", name).strip()
    if not cleaned:
        return False
    # Avoid treating unrelated words; require Amazon as the seller identity.
    if AMAZON_SELLER_RE.search(cleaned):
        return True
    # Bare "Amazon" with optional marketplace suffix
    if re.fullmatch(r"amazon([\s.\-_]?(ae|com|sa|eg|uae|retail))?", cleaned, re.I):
        return True
    return False


def has_amazon_buybox(product: "Product") -> bool:
    """True if this ASIN's Buybox winner is Amazon (flag or sold_by text)."""
    if product.is_amazon_seller is True:
        return True
    if is_amazon_seller_name(product.sold_by):
        return True
    return False


def _seller_name_from_aod_block(el) -> Optional[str]:
    for a in el.select("a"):
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        low = text.lower()
        if low in {"sold by", "seller"}:
            continue
        if "rating" in low or "%" in text or "positive" in low:
            continue
        if re.match(r"^\(?\d", text):
            continue
        return text
    blob = el.get_text(" ", strip=True)
    m = re.match(
        r"Sold by\s+(.+?)(?:\s+Seller rating|\s+\(\d|\s+\d+%|$)",
        blob,
        re.I,
    )
    if m:
        return m.group(1).strip()
    return None


def parse_aod_sellers(html: str) -> tuple[Optional[str], list[str], Optional[int]]:
    """
    Parse Amazon All Offers Display HTML.
    Returns (buybox_sold_by, unique_other_third_party_sellers, other_options_count).
    Amazon.ae is excluded from the 'other' list.
    """
    soup = BeautifulSoup(html, "lxml")
    ordered: list[str] = []

    # Pinned / featured offer is the Buybox winner — check it first.
    for sel in (
        "#aod-pinned-offer #aod-offer-soldBy",
        "#pinned-offer-top-id #aod-offer-soldBy",
        "#aod-offer #aod-offer-soldBy",
    ):
        el = soup.select_one(sel)
        if el:
            name = _seller_name_from_aod_block(el)
            if name:
                ordered.append(name)
                break

    for el in soup.select("#aod-offer-soldBy"):
        name = _seller_name_from_aod_block(el)
        if not name:
            continue
        ordered.append(name)

    if not ordered:
        # Fallback: plain-text scan
        for m in re.finditer(
            r"Sold by\s+([^\n<]+?)(?:\s+Seller rating|\s+\(\d|$)",
            html,
            re.I,
        ):
            ordered.append(m.group(1).strip())

    # If HTML clearly says Amazon wins Buybox / ships from and sold by Amazon
    buybox = ordered[0] if ordered else None
    if not buybox and re.search(
        r"Ships from and sold by Amazon\.ae|Sold by\s+Amazon\.ae",
        html,
        re.I,
    ):
        buybox = "Amazon.ae"
        ordered.insert(0, buybox)
    elif buybox and not is_amazon_seller_name(buybox):
        # Prefer pinned offer if it is Amazon.ae
        pinned = soup.select_one("#aod-pinned-offer #aod-offer-soldBy")
        pinned_name = _seller_name_from_aod_block(pinned) if pinned else None
        if pinned_name and is_amazon_seller_name(pinned_name):
            buybox = "Amazon.ae"
            ordered.insert(0, buybox)

    others: list[str] = []
    seen: set[str] = set()
    for name in ordered[1:] if ordered else []:
        key = name.casefold()
        if key in seen:
            continue
        if is_amazon_seller_name(name):
            continue
        if buybox:
            b = buybox.casefold()
            if key == b or key in b or b in key:
                continue
        seen.add(key)
        others.append(name)

    other_opts = None
    count_el = soup.select_one("#aod-filter-offer-count-string, #aod-total-offer-count")
    if count_el:
        m = re.search(r"(\d+)\s+other", count_el.get_text(" ", strip=True), re.I)
        if m:
            other_opts = int(m.group(1))
    if other_opts is None:
        m = re.search(r"New\s*\((\d+)\)", html, re.I)
        if m:
            other_opts = int(m.group(1))

    return buybox, others, other_opts


def aod_url(asin: str) -> str:
    return (
        f"{BASE_URL}/gp/product/ajax/aodAjaxMain"
        f"?asin={asin}&pc=dp&experienceId=aodAjaxMain"
    )


def short_label(title: str, max_len: int = 36) -> str:
    title = re.sub(r"\s+", " ", (title or "").strip())
    if len(title) <= max_len:
        return title
    return title[: max_len - 3].rstrip() + "..."


def parse_product_extras(html: str, self_asin: str, limit: int = 4) -> tuple[Optional[str], list[str]]:
    """Return (bought_past_month, competitor labels) from a product detail page."""
    bought = parse_bought_past_month(html)
    competitors: list[str] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "lxml")

    # Prefer non-sponsored related / also-viewed carousels.
    sections = []
    for heading in soup.select("h2, .a-carousel-heading"):
        text = heading.get_text(" ", strip=True).lower()
        if any(
            key in text
            for key in (
                "customers who viewed",
                "products related",
                "compare with similar",
                "similar items",
            )
        ):
            root = heading.find_parent("div", class_=re.compile(r"a-section|a-carousel"))
            if root is None:
                root = heading.parent
            if root is not None:
                sections.append(root)

    # Comparison widgets
    for el in soup.select(
        "[id*='HLCXComparison'] [data-asin], "
        "#HLCXComparisonTable [data-asin], "
        "[id*='sims-consolidated'] [data-asin], "
        "[id*='purchase-sims'] [data-asin]"
    ):
        sections.append(el)

    cards: list = []
    for section in sections:
        cards.extend(section.select("[data-asin]"))

    # Fallback: any dp links near "related" text chunks in raw HTML.
    if not cards:
        for match in re.finditer(
            r"(?:Customers who viewed|Products related to this item|"
            r"Compare with similar items)(.{0,10000})",
            html,
            flags=re.I | re.S,
        ):
            chunk = match.group(0)
            for asin in re.findall(r"/dp/([A-Z0-9]{10})", chunk):
                if asin == self_asin or asin in seen:
                    continue
                seen.add(asin)
                alt_m = re.search(
                    rf'data-asin="{asin}"[^>]{{0,400}}alt="([^"]+)"|'
                    rf'alt="([^"]+)"[^>]{{0,400}}/dp/{asin}',
                    chunk,
                    flags=re.I | re.S,
                )
                title = ""
                if alt_m:
                    title = alt_m.group(1) or alt_m.group(2) or ""
                competitors.append(
                    f"{short_label(title) or asin} ({asin})"
                    if title
                    else asin
                )
                if len(competitors) >= limit:
                    return bought, competitors

    for card in cards:
        asin = (card.get("data-asin") or "").strip()
        if not asin or asin == self_asin or asin in seen:
            continue
        # Skip obvious sponsored-ad-only blocks when parent id is sp_detail*
        parent_ids = " ".join(
            (p.get("id") or "") for p in card.parents if getattr(p, "get", None)
        )
        if re.search(r"\bsp_detail\d*\b", parent_ids):
            continue
        seen.add(asin)
        img = card.select_one("img[alt]")
        title = (img.get("alt") if img else "") or ""
        if not title:
            title_el = card.select_one(".a-truncate-full, .a-size-base-plus, .a-link-normal")
            title = title_el.get_text(" ", strip=True) if title_el else ""
        competitors.append(f"{short_label(title) or asin} ({asin})")
        if len(competitors) >= limit:
            break

    return bought, competitors


def _parse_price(text: str) -> Optional[float]:
    match = PRICE_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _parse_rank(badge: Optional[str]) -> Optional[int]:
    if not badge:
        return None
    match = re.search(r"#?\s*(\d+)", badge)
    return int(match.group(1)) if match else None


def parse_bestsellers_page(html: str, category: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    products: list[Product] = []
    seen: set[str] = set()

    cards = soup.select("[data-asin]")
    for card in cards:
        asin = (card.get("data-asin") or "").strip()
        if not asin or asin in seen:
            continue
        seen.add(asin)

        rank_el = card.select_one(".zg-bdg-text")
        rank = _parse_rank(rank_el.get_text(strip=True) if rank_el else None)
        if rank is None:
            continue

        title_el = card.select_one(
            "div[class*='p13n-sc-css-line-clamp'], "
            ".p13n-sc-truncate, "
            "img.p13n-product-image"
        )
        if title_el and title_el.name == "img":
            title = (title_el.get("alt") or "").strip()
        else:
            title = title_el.get_text(" ", strip=True) if title_el else ""

        if not title:
            img = card.select_one("img.p13n-product-image, img[alt]")
            title = (img.get("alt") or "").strip() if img else ""

        link = card.select_one("a.a-link-normal[href*='/dp/']")
        href = link.get("href") if link else f"/dp/{asin}"
        url = urljoin(BASE_URL, href.split("?")[0])

        price_el = card.select_one(
            "span[class*='p13n-sc-price'], "
            "span.a-color-price, "
            "span.a-price > span.a-offscreen"
        )
        if price_el:
            price = _parse_price(price_el.get_text(" ", strip=True))
        else:
            # Avoid matching "AED0 to AED99" style text inside product titles.
            price = None
            for chunk in card.stripped_strings:
                if "bought" in chunk.lower():
                    continue
                if re.search(r"AED\s*[\d,]", chunk) and " to " not in chunk.lower():
                    price = _parse_price(chunk)
                    if price is not None:
                        break

        rating = None
        review_count = None
        rating_link = card.select_one("a[aria-label*='out of 5']")
        if rating_link:
            label = rating_link.get("aria-label") or ""
            rm = RATING_RE.search(label)
            if rm:
                rating = float(rm.group(1))
            rvm = re.search(r"([\d,]+)\s+ratings?", label, re.I)
            if rvm:
                review_count = int(rvm.group(1).replace(",", ""))

        img = card.select_one("img.p13n-product-image, img[src]")
        image_url = img.get("src") if img else None

        products.append(
            Product(
                category=category,
                rank=rank,
                asin=asin,
                title=title,
                price_aed=price,
                rating=rating,
                review_count=review_count,
                url=url,
                image_url=image_url,
            )
        )

    products.sort(key=lambda p: p.rank)
    return products


def parse_bought_past_month(html: str) -> Optional[str]:
    match = BOUGHT_RE.search(html)
    if match:
        return f"{match.group(1)} bought in past month"
    soup = BeautifulSoup(html, "lxml")
    for el in soup.select("#socialProofingAsinFaceout_feature_div, .social-proofing-faceout-title-text"):
        text = el.get_text(" ", strip=True)
        m = BOUGHT_RE.search(text)
        if m:
            return f"{m.group(1)} bought in past month"
    return None


def next_bestsellers_page_url(html: str, current_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    link = soup.select_one("li.a-last a, ul.a-pagination li.a-last a")
    if not link or not link.get("href"):
        return None
    return urljoin(current_url, link["href"])
