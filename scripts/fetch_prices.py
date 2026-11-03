"""Fetch total return prices, in rand, for every holding on the Cockroach Fund tree.

For each ticker in data/trades/ it downloads daily closes and dividends from Yahoo Finance,
reinvests each dividend in the holding on its ex-date, converts to rand at each day's
exchange rate, and writes Friday values to data/prices/<TICKER>.csv (date,close).
Currency tickers such as JPY=X are held as cash: their value is the rand price of the currency.

Run from the repo root (needs internet):  python scripts/fetch_prices.py
Then:                                     python scripts/build_data.py
"""
import csv
import datetime as dt
import glob
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = lambda *p: os.path.join(ROOT, *p)
START = dt.date(2020, 9, 1)

# Yahoo quote currencies; sub-units (cents, pence) are converted to whole units
SUB_UNITS = {"ZAc": ("ZAR", 0.01), "ZAC": ("ZAR", 0.01), "GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ILA": ("ILS", 0.01)}
SUFFIX = {".JO": "ZAc", ".L": "GBp", ".HK": "HKD", ".T": "JPY", ".TO": "CAD", ".V": "CAD", ".AS": "EUR", ".PA": "EUR",
          ".DE": "EUR", ".MI": "EUR", ".SW": "CHF", ".AX": "AUD", ".CO": "DKK", ".ST": "SEK", ".OL": "NOK"}


def tickers():
    out = {}
    for f in sorted(glob.glob(D("data", "trades", "cockroach_trades_v*.csv"))):
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                t = (r.get("ticker") or "").strip()
                if t:
                    out[t] = r.get("asset", "")
    return out


def to_daily(s):
    if s is None:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = s.dropna()
    if s.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s.sort_index()


def despike(s, tol):
    """Drop zero/negative prints and one-off spikes that jump away and straight back."""
    s = s[s > 0]
    if len(s) < 5:
        return s
    med = s.rolling(7, center=True, min_periods=3).median()
    return s[~((s / med - 1).abs() > tol)]


def fix_units(s):
    """Undo Yahoo switching a share between cents and rand (a ~100x jump that sticks)."""
    if len(s) < 2:
        return s
    vals, factor = s.values.copy(), 1.0
    for i in range(len(vals) - 2, -1, -1):
        ratio = s.values[i + 1] / s.values[i] if s.values[i] > 0 else 0
        if 80 <= ratio <= 125:
            factor *= 100
        elif 0.008 <= ratio <= 0.0125:
            factor /= 100
        vals[i] = s.values[i] * factor
    return pd.Series(vals, index=s.index)


def div_yields(close, adj, divs):
    """Dividends as a yield on each ex-date (dividend / previous close).

    Uses the larger of Yahoo's dividend list and the step in Adj Close / Close on the
    ex-date: Yahoo gets the adjusted close 100x too small for JSE shares, and the
    dividend list 100x too small for some London shares."""
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    c = to_daily(close)
    c = c[c > 0]
    if len(c) < 2:
        return empty
    a = to_daily(adj)
    both = pd.concat([c, a[a > 0]], axis=1, keys=["c", "a"]).dropna()
    y_adj = empty
    if len(both) >= 2:
        ratio = both["a"] / both["c"]
        y_adj = (ratio / ratio.shift(1) - 1).dropna()
        y_adj = y_adj[y_adj > 0.0005]
    d = to_daily(divs)
    d = d[d > 0]
    prev = c.shift(1)
    pairs = [(t, v / prev.asof(t - pd.Timedelta(seconds=1))) for t, v in d.items()
             if pd.notna(prev.asof(t - pd.Timedelta(seconds=1)))]
    y = pd.Series([v for _, v in pairs], index=pd.DatetimeIndex([t for t, _ in pairs]), dtype=float)
    for t, v in y_adj.items():
        near = y.index[abs(y.index - t) <= pd.Timedelta(days=3)] if len(y) else []
        if len(near):
            y[near[0]] = max(y[near[0]], v)
        else:
            y.loc[t] = v
    y.index = pd.DatetimeIndex(y.index)
    y = y.sort_index()
    return y[(y > 0) & (y < 0.5)]


def total_return(s, y):
    if s.empty or y.empty:
        return s
    y = y[(y.index > s.index.min()) & (y.index <= s.index.max())]
    cum = (1 + y.sort_index()).cumprod()
    return s * cum.reindex(cum.index.union(s.index)).ffill().reindex(s.index).fillna(1.0)


def main():
    import yfinance as yf
    tks = tickers()
    end = dt.date.today()
    shares = [t for t in tks if not t.endswith("=X")]
    cash = [t for t in tks if t.endswith("=X")]          # currency held as cash, e.g. JPY=X

    # quote currency of each share, as Yahoo reports it
    ccy = {}
    for t in shares:
        try:
            ccy[t] = yf.Ticker(t).fast_info.get("currency")
        except Exception:
            ccy[t] = None
        if not ccy[t]:
            ccy[t] = next((v for k, v in SUFFIX.items() if t.upper().endswith(k)), "USD")
    units = {t: SUB_UNITS.get(c, (c.upper(), 1.0)) for t, c in ccy.items()}
    currencies = {u[0] for u in units.values()} | {t[:-2] for t in cash} | {"USD"}
    fx_syms = {c: f"{c}ZAR=X" for c in currencies if c != "ZAR"}

    raw = yf.download(sorted(set(shares) | set(fx_syms.values()) | {"ZAR=X"}), start=START,
                      end=end + dt.timedelta(days=1), interval="1d", auto_adjust=False, actions=True,
                      progress=False, group_by="column")
    col = lambda name, s: raw[name][s] if (name in raw.columns.get_level_values(0) and s in raw[name]) else None

    fx = {"ZAR": None}
    for c, sym in fx_syms.items():
        fx[c] = despike(to_daily(col("Close", sym)), 0.08)
    if fx.get("USD") is None or fx["USD"].empty:
        fx["USD"] = despike(to_daily(col("Close", "ZAR=X")), 0.08)

    def in_rand(s, c):
        if c == "ZAR":
            return s
        r = fx.get(c)
        if r is None or r.empty:
            return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        r = r.reindex(r.index.union(s.index)).ffill().reindex(s.index)
        return (s * r).dropna()

    os.makedirs(D("data", "prices"), exist_ok=True)
    fridays = pd.date_range(START + dt.timedelta(days=(4 - START.weekday()) % 7), end, freq="W-FRI")
    written, missing = [], []
    for t in sorted(tks):
        if t in cash:
            c = t[:-2]
            s = fx.get(c)
            s = s.copy() if s is not None else pd.Series(dtype=float)
            note = "currency"
        else:
            c, mult = units[t]
            close = to_daily(col("Close", t))
            if close.empty:
                missing.append(f"{t} ({tks[t]})")
                continue
            s = despike(fix_units(close * mult), 0.25)
            y = div_yields(col("Close", t), col("Adj Close", t), col("Dividends", t))
            s = in_rand(total_return(s, y), c)
            note = f"{c}, {len(y)} dividends"
        if s.empty:
            missing.append(f"{t} ({tks[t]}): no exchange rate for {c}")
            continue
        w = s.resample("W-FRI").last().reindex(fridays).ffill(limit=2)
        w = w[w.index <= s.index.max() + pd.Timedelta(days=7)].dropna()
        with open(D("data", "prices", f"{t}.csv"), "w", newline="") as fh:
            out = csv.writer(fh)
            out.writerow(["date", "close"])
            out.writerows((d.date().isoformat(), round(float(v), 6)) for d, v in w.items())
        written.append(t)
        print(f"{t}: {len(w)} weeks to {w.index.max().date()} ({note})")
    print(f"Wrote total return prices in rand for {len(written)} tickers")
    for m in missing:
        print("NO PRICES:", m)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        import yfinance as yf
        for t in ["0P0000M02W", "0P0000M02W.JO", "0P0000M02R.JO", "WED.TO", "WED", "WEDXF"]:
            h = yf.download(t, start="2026-01-01", auto_adjust=False, actions=True, progress=False)
            if h is None or h.empty:
                print("PROBE", t, "nothing")
                continue
            c = h["Close"].iloc[:, 0].dropna(); a = h["Adj Close"].iloc[:, 0].dropna()
            dv = h["Dividends"].iloc[:, 0] if "Dividends" in h else None
            print("PROBE", t, len(c), "rows", c.index.min().date(), "->", c.index.max().date(),
                  "last close", round(float(c.iloc[-1]), 4), "adj/close first", round(float(a.iloc[0] / c.iloc[0]), 5),
                  "divs", {str(k.date()): float(v) for k, v in dv[dv > 0].items()} if dv is not None else None)
            try:
                print("PROBE", t, "info", yf.Ticker(t).fast_info.get("currency"), (yf.Ticker(t).info or {}).get("longName"))
            except Exception as e:
                print("PROBE", t, "info failed", e)
    else:
        main()
