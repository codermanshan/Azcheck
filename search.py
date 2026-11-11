"""Amazon.ae search + keyword matching for list-underneath finder."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from categories import BASE_URL
from client import AmazonClient
from parse import Product, PRICE_RE, RATING_RE

# Rough keyword -> category hints so a search still works if Amazon search HTML is blocked.
KEYWORD_CATEGORY_HINTS: dict[str, list[str]] = {
    "hanger": ["home", "kitchen"],
    "hangers": ["home", "kitchen"],
    "dehumidifier": ["home", "kitchen"],
    "organizer": ["home", "kitchen", "office-products"],
    "storage": ["home", "kitchen"],
    "cable": ["electronics", "computers", "office-products"],
    "charger": ["electronics", "mobile", "computers"],
    "earbud": ["electronics"],
    "earbuds": ["electronics"],
    "beauty": ["beauty", "hpc"],
    "shampoo": ["beauty", "hpc"],
    "cream": ["beauty", "hpc"],
    "baby": ["baby"],
    "toy": ["toys"],
    "pet": ["pet-supplies"],
    "kitchen": ["kitchen", "home"],
    "tool": ["home-improvement", "automotive"],
    "phone": ["mobile", "electronics"],
    "case": ["mobile", "electronics"],
}


def suggest_categories(keyword: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", keyword.lower())
    found: list[str] = []
    for w in words:
        for slug in KEYWORD_CATEGORY_HINTS.get(w, []):
            if slug not in found:
                found.append(slug)
    return found or ["home", "kitchen", "beauty", "electronics", "office-products"]


def title_matches_keyword(title: str, keyword: str) -> bool:
    if not keyword.strip():
        return True
    title_l = title.lower()
    tokens = [t for t in re.findall(r"[a-z0-9]+", keyword.lower()) if len(t) > 1]
    if not tokens:
        return keyword.lower() in title_l
    # Match if most tokens appear (allows "velvet hangers" -> velvet + hanger/hangers)
    hits = 0
    for t in tokens:
        if t in title_l or (t.endswith("s") and t[:-1] in title_l) or (t + "s") in title_l:
            hits += 1
    return hits >= max(1, (len(tokens) + 1) // 2)


def search_url(keyword: str) -> str:
    return f"{BASE_URL}/s?k={quote_plus(keyword)}"


def parse_search_results(html: str, keyword: str, limit: int = 30) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    products: list[Product] = []
    seen: set[str] = set()

    cards = soup.select('div[data-component-type="s-search-result"][data-asin]')
    rank = 0
    for card in cards:
        asin = (card.get("data-asin") or "").strip()
        if not asin or asin in seen:
            continue
        title_el = card.select_one("h2 a span, h2 span")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:
            continue
        if not title_matches_keyword(title, keyword):
            # Keep Amazon search hits even if our token filter is strict
            pass
        seen.add(asin)
        rank += 1

        price = None
        price_el = card.select_one(".a-price .a-offscreen, span.a-price-whole")
        if price_el:
            raw = price_el.get_text(" ", strip=True)
            m = PRICE_RE.search(raw) or re.search(r"([\d,]+(?:\.\d+)?)", raw.replace(",", ""))
            if m:
                try:
                    price = float(m.group(1).replace(",", ""))
                except ValueError:
                    price = None

        rating = None
        review_count = None
        rating_el = card.select_one("[aria-label*='out of 5']")
        if rating_el:
            label = rating_el.get("aria-label") or ""
            rm = RATING_RE.search(label)
            if rm:
                rating = float(rm.group(1))
            rvm = re.search(r"([\d,]+)\s+ratings?", label, re.I)
            if rvm:
                review_count = int(rvm.group(1).replace(",", ""))

        link = card.select_one("h2 a[href*='/dp/'], a.a-link-normal[href*='/dp/']")
        href = link.get("href") if link else f"/dp/{asin}"
        url = urljoin(BASE_URL, href.split("?")[0])

        img = card.select_one("img.s-image")
        image_url = img.get("src") if img else None

        products.append(
            Product(
                category=f"Search: {keyword}",
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
        if len(products) >= limit:
            break
    return products


def try_amazon_search(
    client: AmazonClient,
    keyword: str,
    limit: int = 30,
) -> list[Product]:
    html = client.get(search_url(keyword))
    if len(html) < 20_000 or "captcha" in html.lower() or "s-search-result" not in html:
        raise RuntimeError("Amazon search page blocked or empty")
    return parse_search_results(html, keyword, limit=limit)
