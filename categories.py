"""Amazon.ae Best Sellers category slugs (Appliances / large appliances excluded)."""

from __future__ import annotations

# Top-level Best Sellers departments on amazon.ae, excluding Appliances.
# Kitchen is included but its "large-appliances" subcategory is skipped in fetch.
CATEGORIES: dict[str, str] = {
    "amazon-devices": "Amazon Devices & Accessories",
    "automotive": "Automotive",
    "baby": "Baby Products",
    "beauty": "Beauty",
    "books": "Books",
    "computers": "Computers",
    "electronics": "Electronics",
    "fashion": "Fashion",
    "grocery": "Grocery",
    "hpc": "Health",  # Health & Personal Care
    "home": "Home",
    "kitchen": "Kitchen",
    "mobile": "Mobile Phones & Communication",
    "office-products": "Office Products",
    "pet-supplies": "Pet Supplies",
    "sports": "Sporting Goods",
    "home-improvement": "Tools & Home Improvement",
    "toys": "Toys",
    "videogames": "Videogames",
}

# Explicitly excluded (large appliances and related).
EXCLUDED_CATEGORY_SLUGS = frozenset(
    {
        "appliances",
        "large-appliances",
    }
)

BASE_URL = "https://www.amazon.ae"
BESTSELLERS_PATH = "/gp/bestsellers/{slug}"
