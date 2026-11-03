"""Download real weekly prices for every holding on the Cockroach Fund tree, in rand.

For each ticker in data.js this writes data/prices/<TICKER>.csv with columns date,close:
Friday closes, adjusted for dividends and splits, converted to South African rand at
that week's exchange rate. Branches can then be compared directly with the fund line,
which is also in rand.

Run from the repo root (needs internet):
  pip install yfinance pandas
  python scripts/fetch_prices.py
  python scripts/build_data.py

The GitHub workflow in .github/workflows/prices.yml does both every week.
"""
import json
import os
import re
import sys

import pandas as pd
import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "prices")
START = "2020-09-01"

# Trading currency of each ticker. "GBp" and "ZAc" are pence and cents (divided by 100).
CURRENCY = {
    "BRK-B": "USD", "BTI": "USD", "GLD": "USD", "WPM": "USD", "FRMO": "USD", "EEM": "USD",
    "TPL": "USD", "DIS": "USD", "EL": "USD", "EMLC": "USD", "XLE": "USD", "JOE": "USD",
    "CMSG": "USD", "IB01.L": "USD", "IWDA.L": "USD",
    "YCA.L": "GBp", "LSEG.L": "GBp",
    "EXO.AS": "EUR", "DSFIR.AS": "EUR", "RMS.PA": "EUR",
    "NESN.SW": "CHF", "WED.V": "CAD",
    "2561.T": "JPY", "7974.T": "JPY",
    "VAL.JO": "ZAc",
}
# Holdings that are a currency themselves: their rand price is the exchange rate.
FX_HOLDING = {"JPY=X": "JPYZAR=X", "USDZAR=X": "USDZAR=X"}


def tickers_on_page():
    with open(os.path.join(ROOT, "data.js"), encoding="utf-8") as fh:
        txt = fh.read()
    data = json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S).group(1))
    return sorted({a["tk"] for a in data["assets"] if a.get("tk")})


def daily_close(symbol):
    df = yf.download(symbol, start=START, auto_adjust=True, progress=False, threads=False)
    if df is None or df.empty:
        return None
    close = df["Close"]
    if isinstance(close, pd.DataFrame):  # newer yfinance returns one column per ticker
        close = close.iloc[:, 0]
    close = close.dropna()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    return close if len(close) else None


def weekly(series):
    return series.resample("W-FRI").last().dropna()


def main():
    os.makedirs(OUT, exist_ok=True)
    fx_cache = {}

    def fx(cur):
        if cur == "ZAR":
            return None
        if cur not in fx_cache:
            fx_cache[cur] = daily_close(f"{cur}ZAR=X")
        return fx_cache[cur]

    done, failed = [], []
    for tk in tickers_on_page():
        try:
            if tk in FX_HOLDING:
                rand = daily_close(FX_HOLDING[tk])
            else:
                cur = CURRENCY.get(tk)
                if cur is None:
                    print(f"  {tk}: currency unknown, add it to CURRENCY in this script")
                    failed.append(tk)
                    continue
                px = daily_close(tk)
                if px is None:
                    raise ValueError("no prices returned")
                if cur in ("GBp", "ZAc"):
                    px = px / 100.0
                    cur = {"GBp": "GBP", "ZAc": "ZAR"}[cur]
                rate = fx(cur)
                if cur != "ZAR":
                    if rate is None:
                        raise ValueError(f"no {cur}ZAR exchange rate")
                    rate = rate.reindex(px.index.union(rate.index)).ffill().reindex(px.index)
                    px = (px * rate).dropna()
                rand = px
            if rand is None or rand.empty:
                raise ValueError("no prices returned")
            w = weekly(rand)
            path = os.path.join(OUT, f"{tk}.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("date,close\n")
                for d, v in w.items():
                    fh.write(f"{d:%Y-%m-%d},{float(v):.6g}\n")
            done.append(tk)
            print(f"  {tk}: {len(w)} weeks, {w.index[0]:%Y-%m-%d} to {w.index[-1]:%Y-%m-%d}")
        except Exception as e:  # keep going; report at the end
            failed.append(tk)
            print(f"  {tk}: FAILED ({e})")

    print(f"\nPrices in rand for {len(done)} tickers.")
    if failed:
        print("No prices for: " + ", ".join(failed))
    if not done:
        sys.exit(1)


if __name__ == "__main__":
    main()
