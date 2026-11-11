"""Local web UI matching the dark checklist look + Amazon.ae list-underneath search."""

from __future__ import annotations

import csv
import io
import json
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from categories import CATEGORIES
from filters import FilterOptions, drop_amazon_buybox
from parse import has_amazon_buybox
from scanner import run_scan

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
HOST = "127.0.0.1"
PORT = 8765

_jobs: dict[str, dict] = {}
_last_products: list[dict] = []
_lock = threading.Lock()


def _product_dict(p) -> dict:
    return p.to_dict() if hasattr(p, "to_dict") else dict(p)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self._send(code, data, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            html = (WEB / "index.html").read_text(encoding="utf-8")
            html = html.replace(
                "__CATEGORIES__",
                json.dumps(CATEGORIES, ensure_ascii=False),
            )
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path.startswith("/api/scan/"):
            job_id = path.rsplit("/", 1)[-1]
            with _lock:
                job = _jobs.get(job_id)
                if not job:
                    self._json(404, {"ok": False, "error": "Unknown job"})
                    return
                # drain log lines
                lines = job["log"][:]
                job["log"].clear()
                payload = {
                    "ok": True,
                    "status": job["status"],
                    "message": job.get("message"),
                    "error": job.get("error"),
                    "log_lines": lines,
                    "products": job.get("products") if job["status"] == "done" else [],
                }
            self._json(200, payload)
            return
        if path == "/api/last.csv":
            with _lock:
                rows = list(_last_products)
            if not rows:
                self._json(404, {"ok": False, "error": "No results"})
                return
            buf = io.StringIO()
            fields = list(rows[0].keys())
            writer = csv.DictWriter(buf, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            data = buf.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=azcheck_list_under.csv")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/scan":
            self._json(404, {"ok": False, "error": "Not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "Invalid JSON"})
            return

        categories = body.get("categories") or []
        if not categories:
            self._json(400, {"ok": False, "error": "Select at least one category"})
            return

        job_id = uuid.uuid4().hex[:12]
        with _lock:
            _jobs[job_id] = {
                "status": "running",
                "message": "Searching Amazon.ae...",
                "log": [],
                "products": [],
                "error": None,
            }

        def work() -> None:
            def log(msg: str) -> None:
                with _lock:
                    _jobs[job_id]["log"].append(msg)
                    _jobs[job_id]["message"] = msg

            try:
                opts = FilterOptions(
                    exclude_noise=True,
                    exclude_amazon_brands=True,
                    exclude_amazon_sellers=bool(body.get("skip_amazon_only", True)),
                    unbranded_only=bool(body.get("unbranded_only", True)),
                    sellable_only=True,
                    min_revenue=float(body.get("min_revenue") or 1000),
                )
                limit = int(body.get("limit") or 15)
                products = run_scan(
                    category_slugs=categories,
                    limit=limit,
                    enrich=True,
                    enrich_max=max(40, limit * max(1, len(categories))),
                    filters=opts,
                    log=log,
                )
                # Always strip Amazon Buybox winners before returning to UI.
                if opts.exclude_amazon_sellers:
                    products = drop_amazon_buybox(products)
                if body.get("require_others", True):
                    products = [p for p in products if (p.other_new_sellers or 0) >= 1]
                products = [
                    p
                    for p in products
                    if not has_amazon_buybox(p)
                    and not (p.sold_by or "").lower().startswith("amazon")
                ]
                products = sorted(
                    products,
                    key=lambda p: (
                        -(p.other_new_sellers or 0),
                        -(p.est_monthly_revenue or 0),
                        -(p.opportunity_score or 0),
                    ),
                )
                rows = [_product_dict(p) for p in products]
                with _lock:
                    global _last_products
                    _last_products = rows
                    _jobs[job_id]["products"] = rows
                    _jobs[job_id]["status"] = "done"
                    _jobs[job_id]["message"] = f"Found {len(rows)} ASIN(s)"
            except Exception as exc:  # noqa: BLE001
                with _lock:
                    _jobs[job_id]["status"] = "error"
                    _jobs[job_id]["error"] = str(exc)

        threading.Thread(target=work, daemon=True).start()
        self._json(200, {"ok": True, "job_id": job_id})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"azcheck web UI -> {url}")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    server.serve_forever()


if __name__ == "__main__":
    main()
