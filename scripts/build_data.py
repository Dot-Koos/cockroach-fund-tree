"""Build the data files for the Cockroach Fund tree page.

Reads:
  data/trades/cockroach_trades_v*.csv   trades and sizes from the RECM letters (one file per volume)
  data/fund/merchant_west_fund_data.json fund, benchmark and category growth from the factsheet
  data/prices/<TICKER>.csv               optional weekly or daily closes, columns: date,close

Writes (next to index.html):
  data.js    holdings and their events
  fund.js    fund growth line and factsheet figures
  prices.js  real prices by ticker (empty until you add files to data/prices)

Run from the repo root:  python scripts/build_data.py
"""
import csv
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = lambda *p: os.path.join(ROOT, *p)

# Display names for assets whose CSV name is long or technical
NAME = {
    "Gold (physical)": "Gold (SPDR Gold Trust)",
    "US Treasury bonds (short-dated)": "US Treasuries 0–1yr (IB01)",
    "South African value stocks": "MWI Value Fund",
    "SA government bonds (long-dated)": "SA government bonds",
    "VanEck EM Local Currency Bond ETF": "VanEck EM Local Bond ETF",
    "Nestle": "Nestlé",
    "Hermes": "Hermès",
    "Merchant West Enhanced Income Fund": "MW Enhanced Income Fund",
    "CMSG": "Consensus Mining (CMSG)",
}
BUCKET = {"cash": "cash", "bonds": "bonds", "equities": "equity", "hard assets": "hard"}

# Holdings missing from the full breakdown in Vol 4 no 32 although no sale was reported.
# Drawn with a dotted end and a "?". Edit this list as new letters clear things up.
GONE = {"Wheaton Precious Metals", "Exor", "Westaim", "British American Tobacco",
        "US dollar cash", "South African rand cash"}
# Owned but not drawn (marked to zero, no prices)
SKIP = {"Russian equities (frozen)"}
# Forever stocks drawn at an estimated size from a group figure
GROUP_ESTIMATE = {
    "date": "2026-09-10", "ref": "Vol 4 no 32", "url": "https://recm.co.za/letters/v4-32", "size": 2.0,
    "quote": "14% in 6 of my 10 Stocks, Forever",
    "note": "Estimate: the 14% group split evenly (about 2% each). Single sizes not given.",
    "assets": ["Berkshire Hathaway", "Nestle", "LSE Group", "Walt Disney", "DSM-Firmenich", "Hermes"],
}


def build_holdings():
    rows = []
    for f in sorted(glob.glob(D("data", "trades", "cockroach_trades_v*.csv"))):
        with open(f, encoding="utf-8-sig") as fh:
            rows += list(csv.DictReader(fh))
    rows.sort(key=lambda r: r["date"])
    assets = {}
    for r in rows:
        a = r["asset"]
        if a in SKIP:
            continue
        x = assets.setdefault(a, {"id": re.sub(r"[^a-z0-9]", "", a.lower())[:14], "name": NAME.get(a, a),
                                  "tk": r["ticker"], "b": BUCKET[r["bucket"]], "ev": []})
        x["tk"] = r["ticker"] or x["tk"]
        x["b"] = BUCKET[r["bucket"]]
        size = float(r["size_after"]) if r["size_after"] else None
        x["ev"].append({"d": r["date"], "ref": r["letter_ref"], "url": r["letter_url"], "a": r["action"],
                        "s": size, "q": r["quote"], "n": r["note"]})
    g = GROUP_ESTIMATE
    for a in g["assets"]:
        if a in assets:
            assets[a]["ev"].append({"d": g["date"], "ref": g["ref"], "url": g["url"], "a": "est",
                                    "s": g["size"], "q": g["quote"], "n": g["note"]})
    for a, x in assets.items():
        x["ev"].sort(key=lambda e: e["d"])
        x["gone"] = a in GONE
        x["sold"] = any(e["a"] == "sold" for e in x["ev"])
    return {"assets": list(assets.values())}, len(rows)


def build_fund():
    with open(D("data", "fund", "merchant_west_fund_data.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    g = d["growth_chart"]["data"]
    return {"source": d["source"],
            "growth": [[r["date"], r["fund"], r["benchmark"], r["asisa_category_avg"]] for r in g],
            "perf": d["performance"], "risk": d["risk_3y_rolling"]}


def build_prices():
    out = {}
    for f in sorted(glob.glob(D("data", "prices", "*.csv"))):
        ticker = os.path.splitext(os.path.basename(f))[0]
        with open(f, encoding="utf-8-sig") as fh:
            pts = []
            for r in csv.DictReader(fh):
                r = {k.strip().lower(): (v or "").strip() for k, v in r.items()}
                close = r.get("close") or r.get("adj close") or r.get("adj_close")
                if r.get("date") and close:
                    try:
                        pts.append([r["date"][:10], float(close)])
                    except ValueError:
                        pass
        if pts:
            out[ticker] = sorted(pts)
    return out


def write_js(name, var, obj):
    with open(D(name), "w", encoding="utf-8") as fh:
        fh.write(f"const {var}=" + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ";\n")


if __name__ == "__main__":
    holdings, nrows = build_holdings()
    write_js("data.js", "DATA", holdings)
    write_js("fund.js", "FUNDDATA", build_fund())
    prices = build_prices()
    write_js("prices.js", "REAL_PRICES", prices)
    print(f"{nrows} trade rows -> {len(holdings['assets'])} holdings; prices for {len(prices)} tickers")
    missing = sorted({x["tk"] for x in holdings["assets"] if x["tk"] and x["tk"] not in prices})
    if missing:
        print("No price file yet for:", ", ".join(missing))
