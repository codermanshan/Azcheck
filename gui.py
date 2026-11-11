"""Desktop GUI — auto-find amazon.ae ASINs you can list underneath (no typing required)."""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from categories import CATEGORIES
from filters import FilterOptions
from opportunity import list_underneath_summary
from parse import Product
from scanner import default_out_path, run_scan, write_csv, write_json


class AzCheckApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("azcheck — List Underneath Finder (amazon.ae)")
        self.geometry("1280x780")
        self.minsize(1000, 680)

        self.products: list[Product] = []
        self._worker: Optional[threading.Thread] = None
        self._build()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)
        ttk.Label(
            header,
            text="Auto-find listings you can sell under",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            header,
            text=(
                "Pick categories and click Search. We scan Amazon.ae Best Sellers for "
                "unbranded products where other sellers already share the ASIN — "
                "so you can list underneath. No keyword typing needed."
            ),
            foreground="#444",
            wraplength=1100,
        ).pack(anchor=tk.W, pady=(2, 10))

        body = ttk.Frame(root)
        body.pack(fill=tk.BOTH, expand=True)

        # ---- Left: categories + search button ----
        left = ttk.Frame(body, width=280)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        left.pack_propagate(False)

        ttk.Label(left, text="Categories to search", font=("Segoe UI", 11, "bold")).pack(
            anchor=tk.W
        )
        cat_frame = ttk.Frame(left)
        cat_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        self.cat_list = tk.Listbox(
            cat_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            height=16,
            width=34,
        )
        scroll = ttk.Scrollbar(cat_frame, orient=tk.VERTICAL, command=self.cat_list.yview)
        self.cat_list.configure(yscrollcommand=scroll.set)
        self.cat_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.LEFT, fill=tk.Y)

        self._slug_by_index: list[str] = []
        for slug, name in CATEGORIES.items():
            self._slug_by_index.append(slug)
            self.cat_list.insert(tk.END, name)

        btn_row = ttk.Frame(left)
        btn_row.pack(fill=tk.X, pady=4)
        ttk.Button(btn_row, text="Select all", command=self._select_all).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(btn_row, text="Clear", command=self._clear_cats).pack(side=tk.LEFT)

        self.run_btn = ttk.Button(
            left, text="Search Amazon.ae for list-underneath", command=self._start_scan
        )
        self.run_btn.pack(fill=tk.X, pady=(8, 4))
        ttk.Button(left, text="Save CSV", command=self._save_csv).pack(fill=tk.X)

        filt = ttk.LabelFrame(left, text="Filters", padding=8)
        filt.pack(fill=tk.X, pady=(10, 0))

        self.unbranded_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            filt, text="Unbranded only", variable=self.unbranded_var
        ).pack(anchor=tk.W)

        self.require_others_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            filt,
            text="Must have other sellers (list under)",
            variable=self.require_others_var,
        ).pack(anchor=tk.W)

        self.amazon_seller_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            filt, text="Exclude Amazon Buybox winner", variable=self.amazon_seller_var
        ).pack(anchor=tk.W)

        row = ttk.Frame(filt)
        row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(row, text="Min AED/mo").pack(side=tk.LEFT)
        self.min_rev_var = tk.StringVar(value="1000")
        ttk.Entry(row, textvariable=self.min_rev_var, width=8).pack(side=tk.LEFT, padx=4)

        row2 = ttk.Frame(filt)
        row2.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row2, text="Per category").pack(side=tk.LEFT)
        self.limit_var = tk.IntVar(value=20)
        ttk.Spinbox(row2, from_=5, to=50, textvariable=self.limit_var, width=6).pack(
            side=tk.LEFT, padx=4
        )

        # ---- Right: results ----
        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.status_var = tk.StringVar(
            value="Select categories (or Select all), then click Search Amazon.ae."
        )
        ttk.Label(right, textvariable=self.status_var, foreground="#333").pack(
            anchor=tk.W, pady=(0, 6)
        )

        mid = ttk.Panedwindow(right, orient=tk.VERTICAL)
        mid.pack(fill=tk.BOTH, expand=True)

        table_frame = ttk.Frame(mid)
        detail_frame = ttk.LabelFrame(
            mid, text="Selected: can you list underneath?", padding=8
        )
        mid.add(table_frame, weight=3)
        mid.add(detail_frame, weight=1)

        cols = (
            "list_under",
            "revenue",
            "others",
            "sold_by",
            "asin",
            "title",
            "price",
            "brand",
            "score",
            "category",
        )
        self.tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", selectmode="browse"
        )
        headings = {
            "list_under": ("List under?", 90),
            "revenue": ("Est. rev/mo", 90),
            "others": ("Other sellers", 90),
            "sold_by": ("Buybox seller", 120),
            "asin": ("ASIN", 100),
            "title": ("Product", 280),
            "price": ("Price", 70),
            "brand": ("Brand?", 90),
            "score": ("Score", 55),
            "category": ("Category", 100),
        }
        for key, (label, width) in headings.items():
            self.tree.heading(key, text=label)
            anchor = tk.W if key in ("title", "sold_by", "brand", "category") else tk.CENTER
            self.tree.column(key, width=width, anchor=anchor)

        yscroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.LEFT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._open_selected)

        self.detail = tk.Text(detail_frame, height=6, wrap=tk.WORD, font=("Segoe UI", 10))
        self.detail.pack(fill=tk.BOTH, expand=True)
        self.detail.insert(
            "1.0",
            "Click Search Amazon.ae.\n"
            "We pull Best Sellers, drop brands + Amazon-only, and keep ASINs "
            "where other sellers already list — those are list-underneath targets.",
        )
        self.detail.configure(state=tk.DISABLED)
        ttk.Button(detail_frame, text="Open on Amazon.ae", command=self._open_selected).pack(
            anchor=tk.W, pady=(6, 0)
        )

        self.log = tk.Text(right, height=5, wrap=tk.WORD, state=tk.DISABLED)
        self.log.pack(fill=tk.X, pady=(8, 0))

        # Default: pick a few useful categories so first search works immediately
        for name in ("Home", "Kitchen", "Beauty", "Office Products", "Pet Supplies"):
            try:
                idx = list(CATEGORIES.values()).index(name)
                self.cat_list.selection_set(idx)
            except ValueError:
                pass

    def _select_all(self) -> None:
        self.cat_list.selection_set(0, tk.END)

    def _clear_cats(self) -> None:
        self.cat_list.selection_clear(0, tk.END)

    def _selected_slugs(self) -> list[str]:
        return [self._slug_by_index[i] for i in self.cat_list.curselection()]

    def _append_log(self, msg: str) -> None:
        def _do() -> None:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, msg + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

        self.after(0, _do)

    def _parse_optional_float(self, raw: str) -> float | None:
        raw = raw.strip()
        if not raw:
            return None
        return float(raw)

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        self.detail.insert("1.0", text)
        self.detail.configure(state=tk.DISABLED)

    def _start_scan(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("Busy", "Search already running.")
            return

        slugs = self._selected_slugs()
        if not slugs:
            messagebox.showwarning(
                "Categories", "Select at least one category (or click Select all)."
            )
            return

        try:
            min_rev = self._parse_optional_float(self.min_rev_var.get())
        except ValueError:
            messagebox.showerror("Numbers", "Min AED/mo must be a number.")
            return

        opts = FilterOptions(
            exclude_noise=True,
            exclude_amazon_brands=True,
            exclude_amazon_sellers=self.amazon_seller_var.get(),
            unbranded_only=self.unbranded_var.get(),
            sellable_only=True,
            min_revenue=min_rev if min_rev is not None else 1000.0,
        )

        self.run_btn.configure(state=tk.DISABLED)
        self.status_var.set("Searching Amazon.ae Best Sellers for list-underneath ASINs...")
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._set_detail("Searching Amazon.ae...\nChecking who sells each product...")

        limit = int(self.limit_var.get())
        require_others = self.require_others_var.get()

        def work() -> None:
            try:
                products = run_scan(
                    category_slugs=slugs,
                    limit=limit,
                    enrich=True,
                    enrich_max=max(40, limit * len(slugs)),
                    filters=opts,
                    log=self._append_log,
                )
                if require_others:
                    products = [p for p in products if (p.other_new_sellers or 0) >= 1]
                # Prefer strongest list-underneath first
                products = sorted(
                    products,
                    key=lambda p: (
                        -(p.other_new_sellers or 0),
                        -(p.est_monthly_revenue or 0),
                        -(p.opportunity_score or 0),
                    ),
                )
                self.after(0, lambda: self._on_done(products, None))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._on_done([], exc))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _on_done(self, products: list[Product], error: Exception | None) -> None:
        self.run_btn.configure(state=tk.NORMAL)
        if error:
            self.status_var.set("Search failed.")
            messagebox.showerror("Search failed", str(error))
            return

        self.products = products
        for p in products:
            list_flag = (
                "YES"
                if p.sellable and (p.other_new_sellers or 0) >= 1
                else ("MAYBE" if p.sellable else "NO")
            )
            self.tree.insert(
                "",
                tk.END,
                values=(
                    list_flag,
                    f"{p.est_monthly_revenue:,.0f}"
                    if p.est_monthly_revenue is not None
                    else "-",
                    str(p.other_new_sellers)
                    if p.other_new_sellers is not None
                    else "-",
                    p.sold_by or "-",
                    p.asin,
                    p.title[:100],
                    f"{p.price_aed:.2f}" if p.price_aed is not None else "-",
                    "unbranded" if not p.is_branded else (p.detected_brand or "brand"),
                    f"{p.opportunity_score:.0f}" if p.opportunity_score is not None else "-",
                    p.category,
                ),
            )

        if products:
            top = products[0]
            self.status_var.set(
                f"Found {len(products)} list-underneath ASIN(s). "
                f"Top: {top.asin} — {top.title[:50]}"
            )
            self.tree.selection_set(self.tree.get_children()[0])
            self._set_detail(list_underneath_summary(top))
        else:
            self.status_var.set(
                "No list-underneath matches. Select more categories or lower Min AED/mo."
            )
            self._set_detail(
                "No matches this run.\n"
                "- Click Select all and search again\n"
                "- Lower Min AED/mo\n"
                "- Temporarily uncheck Unbranded only"
            )

    def _on_select(self, _event: object = None) -> None:
        product = self._selected_product()
        if product:
            self._set_detail(list_underneath_summary(product))

    def _selected_product(self) -> Product | None:
        sel = self.tree.selection()
        if not sel:
            return None
        idx = self.tree.index(sel[0])
        if 0 <= idx < len(self.products):
            return self.products[idx]
        return None

    def _open_selected(self, _event: object = None) -> None:
        product = self._selected_product()
        if product:
            webbrowser.open(product.url)
        else:
            messagebox.showinfo("Open", "Select a product row first.")

    def _save_csv(self) -> None:
        if not self.products:
            messagebox.showinfo("Save", "No results yet. Run a search first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=default_out_path(".csv").name,
        )
        if not path:
            return
        write_csv(Path(path), self.products)
        self.status_var.set(f"Saved CSV -> {path}")


def launch_gui() -> None:
    app = AzCheckApp()
    app.mainloop()


if __name__ == "__main__":
    launch_gui()
