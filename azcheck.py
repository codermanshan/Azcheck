#!/usr/bin/env python3
"""
azcheck — Amazon.ae product opportunity finder (Helium-style estimates).

Estimates monthly units/revenue from Best Sellers rank + bought-past-month badges.
Filters Amazon-sold and branded products, scores sellability for 3P sellers.
These are approximations — not official Amazon or Helium 10 data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from categories import CATEGORIES
from filters import FilterOptions
from scanner import default_out_path, run_scan, write_csv, write_json

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

console = Console(force_terminal=True)


def print_table(products: list) -> None:
    table = Table(title="Amazon.ae Opportunities (est. revenue sorted)")
    table.add_column("Trend")
    table.add_column("Score", justify="right")
    table.add_column("Est Rev", justify="right")
    table.add_column("Units", justify="right")
    table.add_column("Cat", style="cyan", overflow="fold")
    table.add_column("#", justify="right")
    table.add_column("ASIN")
    table.add_column("Title", overflow="fold", max_width=28)
    table.add_column("Price", justify="right")
    table.add_column("Sell?")
    table.add_column("Others", justify="right")

    for p in products:
        table.add_row(
            p.trend or "-",
            f"{p.opportunity_score:.0f}" if p.opportunity_score is not None else "-",
            f"{p.est_monthly_revenue:,.0f}" if p.est_monthly_revenue is not None else "-",
            str(p.est_monthly_units) if p.est_monthly_units is not None else "-",
            p.category,
            str(p.rank),
            p.asin,
            p.title[:60],
            f"{p.price_aed:.2f}" if p.price_aed is not None else "-",
            "YES" if p.sellable else "NO",
            str(p.other_new_sellers) if p.other_new_sellers is not None else "-",
        )
    console.print(table)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Amazon.ae Helium-style opportunity scanner: trending bestsellers, "
            "estimated revenue, unbranded + sellable filters."
        )
    )
    parser.add_argument("--gui", action="store_true", help="Open the desktop GUI")
    parser.add_argument(
        "--category", "-c", nargs="+", default=["all"], help="Category slug(s), or all"
    )
    parser.add_argument("--limit", "-n", type=int, default=30)
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Also fetch product page bought-past-month badges",
    )
    parser.add_argument("--enrich-max", type=int, default=50)
    parser.add_argument("--delay", type=float, default=1.2)
    parser.add_argument("--out", "-o", type=Path, default=None)
    parser.add_argument("--list-categories", action="store_true")
    parser.add_argument("--keep-noise", action="store_true")
    parser.add_argument("--keep-amazon-brands", action="store_true")
    parser.add_argument("--keep-amazon-sellers", action="store_true")
    parser.add_argument(
        "--keep-branded",
        action="store_true",
        help="Do not filter known brands (Apple, Anker, etc.)",
    )
    parser.add_argument(
        "--keep-unsellable",
        action="store_true",
        help="Keep products marked not sellable for 3P",
    )
    parser.add_argument(
        "--min-revenue",
        type=float,
        default=3000.0,
        help="Minimum estimated monthly revenue AED (default 3000)",
    )
    parser.add_argument(
        "--exclude-brand",
        action="append",
        default=[],
        help="Extra brand to hide (repeatable)",
    )
    parser.add_argument("--min-price", type=float, default=None)
    parser.add_argument("--max-price", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gui or (argv is None and len(sys.argv) == 1):
        # Dark web UI (screenshot-style checklist + Amazon.ae search)
        from webapp import main as launch_web

        launch_web()
        return 0

    if args.list_categories:
        console.print("[bold]Available categories (large appliances excluded):[/]")
        for slug, name in CATEGORIES.items():
            console.print(f"  {slug:20} {name}")
        return 0

    opts = FilterOptions(
        exclude_noise=not args.keep_noise,
        exclude_amazon_brands=not args.keep_amazon_brands,
        exclude_amazon_sellers=not args.keep_amazon_sellers,
        unbranded_only=not args.keep_branded,
        sellable_only=not args.keep_unsellable,
        min_revenue=args.min_revenue,
        exclude_brands=list(args.exclude_brand or []),
        min_price=args.min_price,
        max_price=args.max_price,
    )

    console.print(
        "[bold]Amazon.ae opportunities[/] — est. revenue, unbranded, sellable "
        "(Helium-style approximations).\n"
    )

    products = run_scan(
        category_slugs=args.category,
        limit=args.limit,
        enrich=args.enrich,
        enrich_max=args.enrich_max,
        delay=args.delay,
        filters=opts,
        log=lambda m: console.print(m),
    )

    if not products:
        console.print(
            "[red]No products after filters.[/] Try --min-revenue 0 or --keep-branded."
        )
        return 1

    print_table(products)

    out = args.out or default_out_path(".csv")
    out = out.resolve()
    if out.suffix.lower() == ".json":
        write_json(out, products)
    else:
        if out.suffix.lower() != ".csv":
            out = out.with_suffix(".csv")
        write_csv(out, products)

    console.print(f"\n[green]Saved[/] {len(products)} opportunities -> {out}")
    console.print(
        "[dim]Estimates are approximate. Validate trademarks before sourcing/selling.[/]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
