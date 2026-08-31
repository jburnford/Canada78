#!/usr/bin/env python3
"""Indian Trust Fund ledgers (Return B / Return C) -> entries + balance series (KG_BUILD_PLAN step 7).

    python3 build/parse_return_b.py [--tol 0.02]

The trust-fund returns are a two-sided ledger printed as one table: each line
carries a Debit entry on the left and an *unrelated* Credit entry on the right,
sharing the line only because the printer set them side by side. Reading a line
as one record fuses two transactions, so every line is split at the CR boundary
into up to two entries before anything else happens.

The `tabstmt*` result directories are not one table. They hold the land-sales
statements, the "TO WHOM PAID" payment lists, census returns and much else, so
rows are gated by their **governing `headers` block** the way the census and
agstat column maps are — 74,586 extracted rows, of which only the trust-fund
families below are ledger lines. Without the gate the land-sales table's
county acreages parse as money and enter the fund totals.

Four printed shapes carry the same ledger across the eras:

    ledger_dr_cr_5   DR Capital | DR Interest | CR desc | CR Capital | CR Interest
    ledger_dr_cr_4   DR Capital | DR Interest |          CR Capital | CR Interest
    ledger_split_3   Capital | Interest, but three values: dr amt, cr desc, cr amt
    ledger_debit_2   Debit | Credit — the later summary form, one side per line

The ledger is self-validating, which is why this step needs no external truth:
for a closed account the debits and the credits must come to the same figure,
and the return prints its own totals besides. `trustfund_checks.csv` reports
every account against both tests, so a parse error shows up as an imbalance
rather than as a plausible wrong number.

Outputs: registries/annotations/trustfund_entries.parquet   one row per Dr/Cr entry
         registries/annotations/trustfund_balances.parquet  balance series per account-year
         registries/annotations/trustfund_checks.csv        per-account balance test
         registries/crosswalks/trustfund_accounts.csv       account names seen, for band linking
         registries/crosswalks/trustfund_reextract.csv      page ranges whose accounts do not balance
"""
import argparse
import collections
import csv
import glob
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))

RESULTS = "eval/results/tabstmt*_qwen38_medium/out_*.jsonl"

# "1,017 25" is the printer's dollars-and-cents; "398.40" and a bare "12,052"
# both occur. Anything else is left null and counted, never guessed at.
_MONEY_SPACE = re.compile(r"^\(?\$?\s*([\d,]+)[  ]+(\d{2})\)?$")
_MONEY_DOT = re.compile(r"^\(?\$?\s*([\d,]+)\.(\d{2})\)?$")
_MONEY_INT = re.compile(r"^\(?\$?\s*([\d,]+)\)?$")
_BLANK = {"", "...", "..", ".", "-", "--", "—", "–", "nil", "none"}

BALANCE_RE = re.compile(r"balance", re.I)
YEAR_RE = re.compile(r"(18\d\d|19\d\d)")


def parse_money(v):
    """Printed amount -> float, or None when the cell is blank or unreadable."""
    if v is None:
        return None
    s = str(v).strip().replace(" ", " ")
    if s.lower() in _BLANK:
        return None
    for rx, cents in ((_MONEY_SPACE, True), (_MONEY_DOT, True), (_MONEY_INT, False)):
        m = rx.match(s)
        if m:
            whole = m.group(1).replace(",", "")
            if not whole.isdigit():
                return None
            val = float(whole) + (float(m.group(2)) / 100 if cents else 0.0)
            return -val if s.startswith("(") else val
    return None


def classify(cols, title):
    """Which ledger shape a `headers` block introduces, or None for other tables."""
    c = [str(x or "").lower() for x in cols]
    joined = " | ".join(c)
    t = str(title or "").lower()
    has_cap = "capital" in joined
    has_int = "interest" in joined
    if len(c) == 5 and has_cap and has_int and any(x.startswith(("cr", "cr.")) for x in c):
        return "ledger_dr_cr_5"
    if len(c) == 4 and has_cap and has_int and any(x.startswith(("cr", "cr.")) for x in c):
        return "ledger_dr_cr_4"
    if len(c) == 2 and has_cap and has_int:
        return "ledger_split_3"
    if len(c) == 2 and ("debit" in joined or "dr" in c[0]) and ("credit" in joined or "cr" in c[1]):
        # Only the trust-fund returns, not the agency cash statements.
        if "trust" in t or "return b" in t or "return c" in t or not t or t == "none":
            return "ledger_debit_2"
    return None


def split_row(shape, label, values):
    """One printed line -> its Debit entry and its Credit entry.

    This is the column-split at the CR boundary. `values` is positional against
    the governing headers, so each shape says where the credit side starts.
    """
    v = list(values or [])

    def at(i):
        return v[i] if i < len(v) else None

    if shape == "ledger_dr_cr_5":
        dr = (label, parse_money(at(0)), parse_money(at(1)))
        cr = (at(2), parse_money(at(3)), parse_money(at(4)))
    elif shape == "ledger_dr_cr_4":
        dr = (label, parse_money(at(0)), parse_money(at(1)))
        cr = (None, parse_money(at(2)), parse_money(at(3)))
    elif shape == "ledger_split_3":
        dr = (label, parse_money(at(0)), None)
        cr = (at(1), parse_money(at(2)), None)
    elif shape == "ledger_debit_2":
        # One description, and whichever side carries the figure.
        dr = (label, parse_money(at(0)), None)
        cr = (label, parse_money(at(1)), None)
    else:
        return []

    out = []
    for side, (desc, cap, inter) in (("Dr", dr), ("Cr", cr)):
        if cap is None and inter is None and not (desc and str(desc).strip()):
            continue
        if cap is None and inter is None:
            continue  # a description with no figure carries no transaction
        out.append((side, None if desc is None else str(desc).strip(), cap, inter))
    return out


CARRIED = re.compile(r"(carried|brought)\s+(for|down)", re.I)


def load():
    """Every trust-fund ledger line, gated by its governing headers block.

    Accounts are bounded by their own printed `total` row, not by the `section`
    label. The extractor leaves `section` null on continuation rows, so a
    carried-forward section name runs on into the next band's account: rows
    from p.717 were being added to Batchewana's p.683 account, which balances
    exactly on its own. The total row is the printer's own statement that the
    account is closed, and it doubles as the figure to check the entries
    against. "Carried forward" / "brought down" totals are page-break
    subtotals, not account ends, and must not close a block.
    """
    entries, accounts = [], []
    block, block_no = [], 0

    def effective_shape(declared, rows, total_row):
        """The column layout the rows actually use, which the header may misstate.

        Under one "Debit | Credit" header on p.802 of 1893, the Gibson account
        prints four columns (Dr Capital, Dr Interest, Cr Capital, Cr Interest)
        and Texas Lake two. Read by the header, Gibson does not balance; read by
        its own row width it balances to the cent. So the width of the widest
        row in the account decides, and the balance test stays an independent
        check on the result.
        """
        widths = [len(r.get("values") or []) for r in rows]
        if total_row is not None:
            widths.append(len(total_row.get("values") or []))
        w = max(widths) if widths else 0
        if w >= 5:
            return "ledger_dr_cr_5"
        if w == 4:
            return "ledger_dr_cr_4"
        if w == 3:
            return "ledger_split_3" if declared == "ledger_split_3" else "ledger_dr_cr_4"
        return declared

    def close(year, chunk, shape, title, total_row):
        nonlocal block, block_no
        if not block:
            return
        block_no += 1
        acct_id = f"{year}:{chunk}:{block_no:04d}"
        names = [b["section"] for b in block if b.get("section")]
        name = collections.Counter(names).most_common(1)[0][0] if names else None
        eff = effective_shape(shape, block, total_row)
        v = list((total_row or {}).get("values") or [])

        def tv(i):
            return parse_money(v[i]) if i < len(v) else None
        if eff == "ledger_dr_cr_5":
            t = dict(t_dr_capital=tv(0), t_dr_interest=tv(1),
                     t_cr_capital=tv(3), t_cr_interest=tv(4))
        elif eff == "ledger_dr_cr_4":
            t = dict(t_dr_capital=tv(0), t_dr_interest=tv(1),
                     t_cr_capital=tv(2), t_cr_interest=tv(3))
        else:
            t = dict(t_dr_capital=tv(0), t_dr_interest=None,
                     t_cr_capital=tv(1), t_cr_interest=None)
        n = 0
        for b in block:
            for seq, (side, desc, cap, inter) in enumerate(
                    split_row(eff, b["label"], b["values"])):
                entries.append(dict(account_id=acct_id, account=name or "(unnamed)",
                                    year=year, chunk=chunk, row_idx=b["row_idx"],
                                    page=b["page"], section=b["section"],
                                    shape=eff, declared_shape=shape, seq=seq,
                                    side=side, description=desc, capital=cap,
                                    interest=inter, confidence=b["confidence"]))
                n += 1
        accounts.append(dict(account_id=acct_id, year=year, chunk=chunk,
                             shape=eff, declared_shape=shape,
                             title=str(title or "")[:120],
                             account=name or "(unnamed)", n_entries=n,
                             page=min([b["page"] for b in block if b["page"]] or [None])
                             if any(b["page"] for b in block) else None,
                             has_total=total_row is not None, **t))
        block = []

    for path in sorted(glob.glob(str(ROOT / RESULTS))):
        year = int(re.search(r"tabstmt(\d{4})", path).group(1))
        chunk = re.search(r"(out_\d+\.jsonl)$", path).group(1)
        shape, title, section = None, None, None
        for i, line in enumerate(open(path)):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict):
                continue
            rt = r.get("row_type")
            if rt == "headers":
                close(year, chunk, shape, title, None)
                title = r.get("title")
                shape = classify(r.get("columns") or [], title)
                section = None
                continue
            if shape is None or rt not in ("row", "total"):
                continue
            new_section = (str(r.get("section")).strip()
                           if r.get("section") is not None else None)
            if new_section and section and new_section != section:
                # A named change of account is a boundary even when the return
                # printed no total, which happens where a statement runs over a
                # page break. Only a *stated* new name closes the block: a null
                # section means the row continues the current account, and
                # treating null as a change was what merged p.717 into
                # Batchewana's p.683 account.
                close(year, chunk, shape, title, None)
            if new_section:
                section = new_section
            if rt == "total":
                if CARRIED.search(str(r.get("label") or "")):
                    continue  # page-break subtotal, the account runs on
                close(year, chunk, shape, title, r)
                section = None
                continue
            block.append(dict(row_idx=i, page=r.get("page"), section=section,
                              label=r.get("label"), values=r.get("values"),
                              confidence=r.get("confidence")))
        close(year, chunk, shape, title, None)
    return pd.DataFrame(entries), pd.DataFrame(accounts)


def balance_series(e):
    """Opening and closing balances per account-year, from the balance lines.

    "To Balance on 30th June, 1889" and "By Balance on 30th June, 1888" are the
    two ends of the account: the credit-side balance is what the fund held when
    the year opened, the debit-side one what it held when the year closed.
    """
    b = e[e.description.notna() & e.description.str.contains(BALANCE_RE, na=False)].copy()
    b["stated_year"] = [int(m.group(1)) if (m := YEAR_RE.search(d)) else None
                        for d in b.description]
    b["amount"] = b.capital.where(b.capital.notna(), b.interest)
    b = b[b.amount.notna()]
    b["kind"] = ["closing" if s == "Dr" else "opening" for s in b.side]
    return b[["account_id", "year", "account", "kind", "side", "stated_year",
              "amount", "capital", "interest", "page", "chunk", "shape",
              "description"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=0.02,
                    help="dollars of slack when comparing two sides")
    args = ap.parse_args()

    e, acc = load()
    print(f"ledger entries: {len(e)} from {e.account_id.nunique()} "
          f"accounts, {e.year.nunique()} volumes {e.year.min()}-{e.year.max()}")
    print("by shape:")
    print(e.groupby("shape").size().to_string())

    # ---- balance test, per account -------------------------------------
    # Two independent tests. The internal one is Dr == Cr, the printer's own
    # convention for a closed account. The external one is our summed entries
    # against the total the return itself prints, which catches a dropped or
    # doubled line that leaves both sides equally wrong. Interest is only
    # compared where the shape has an interest column and something was
    # printed in it, otherwise 0 == 0 would pass vacuously.
    rows = []
    by_acct = {a.account_id: a for a in acc.itertuples()}
    for acct_id, d in e.groupby("account_id"):
        a = by_acct[acct_id]
        dr, cr = d[d.side == "Dr"], d[d.side == "Cr"]
        drc, crc = dr.capital.sum(), cr.capital.sum()
        dri, cri = dr.interest.sum(), cr.interest.sum()
        has_interest = d.interest.notna().any()
        cap_ok = abs(drc - crc) <= args.tol
        int_ok = (abs(dri - cri) <= args.tol) if has_interest else None

        def near(x, y):
            return x is not None and pd.notna(x) and abs(x - y) <= args.tol
        vs_total = None
        if a.has_total and pd.notna(a.t_dr_capital):
            vs_total = near(a.t_dr_capital, drc) and near(a.t_cr_capital, crc)

        if len(dr) == 0 or len(cr) == 0:
            status = "one_sided"
        elif cap_ok and (int_ok is not False):
            status = "balanced"
        elif cap_ok or int_ok:
            status = "capital_only" if cap_ok else "interest_only"
        else:
            status = "unbalanced"
        rows.append(dict(account_id=acct_id, year=a.year, account=a.account,
                         shape=a.shape, page=a.page, n_dr=len(dr), n_cr=len(cr),
                         dr_capital=round(drc, 2), cr_capital=round(crc, 2),
                         cap_diff=round(drc - crc, 2),
                         dr_interest=round(dri, 2), cr_interest=round(cri, 2),
                         int_diff=round(dri - cri, 2),
                         has_total=a.has_total, matches_printed_total=vs_total,
                         status=status))
    chk = pd.DataFrame(rows)
    print(f"\nbalance test over {len(chk)} accounts:")
    for k, v in chk.status.value_counts().items():
        print(f"  {k:14} {v:6}  {v/len(chk):6.1%}")
    two_sided = chk[chk.status != "one_sided"]
    if len(two_sided):
        ok = (two_sided.status == "balanced").sum()
        print(f"  of the {len(two_sided)} two-sided accounts, {ok} balance "
              f"({ok/len(two_sided):.1%})")
    tt = chk[chk.matches_printed_total.notna()]
    if len(tt):
        print(f"  against the printed total: {int(tt.matches_printed_total.astype(bool).sum())}"
              f"/{len(tt)} ({tt.matches_printed_total.astype(bool).mean():.1%})")

    # The series inherits its account's verdict. A balance drawn from an
    # account that does not balance is not trustworthy, so it is carried with
    # the verdict attached rather than dropped or silently published.
    bal = balance_series(e)
    status = dict(zip(chk.account_id, chk.status))
    bal["balance_status"] = bal.account_id.map(status)
    trusted = bal[bal.balance_status == "balanced"]
    print(f"\nbalance series: {len(bal)} balance lines, "
          f"{bal.account.nunique()} accounts, {bal.year.min()}-{bal.year.max()}")
    print(f"  from accounts that balance: {len(trusted)} lines, "
          f"{trusted.account.nunique()} accounts ({len(trusted)/len(bal):.1%})")

    e.to_parquet(ROOT / "registries/annotations/trustfund_entries.parquet", index=False)
    bal.to_parquet(ROOT / "registries/annotations/trustfund_balances.parquet", index=False)
    chk.sort_values(["status", "year", "account"]).to_csv(
        ROOT / "registries/annotations/trustfund_checks.csv", index=False)

    # Targeted re-extraction list: the plan's "failures -> Qwen on the halves".
    # Page ranges, not whole volumes, so a re-run is cheap.
    fail = chk[chk.status.isin(["unbalanced", "interest_only"])]
    if len(fail):
        rex = (fail.groupby(["year", "shape"])
               .agg(accounts=("account_id", "nunique"),
                    first_page=("page", "min"), last_page=("page", "max"),
                    worst_diff=("cap_diff", lambda s: round(s.abs().max(), 2)))
               .reset_index().sort_values(["year", "shape"]))
        rex.to_csv(ROOT / "registries/crosswalks/trustfund_reextract.csv", index=False)
        print(f"re-extraction targets: {len(rex)} (year, shape) blocks covering "
              f"{fail.account_id.nunique()} accounts -> trustfund_reextract.csv")

    acc = (e.groupby("account")
           .agg(years=("year", lambda s: f"{s.min()}-{s.max()}"),
                n_years=("year", "nunique"), n_entries=("side", "size"),
                shapes=("shape", lambda s: ",".join(sorted(set(s)))))
           .reset_index().sort_values("n_entries", ascending=False))
    acc.to_csv(ROOT / "registries/crosswalks/trustfund_accounts.csv", index=False)
    print(f"distinct account names: {len(acc)}")

    unparsed = e[(e.capital.isna()) & (e.interest.isna())]
    print(f"entries with no readable figure: {len(unparsed)}")


if __name__ == "__main__":
    main()
