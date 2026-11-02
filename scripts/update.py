#!/usr/bin/env python3
"""Weekly SAM.gov FedRAMP / cloud mention tracker.

Streams SAM.gov's public Contract Opportunities bulk CSVs (no API key needed),
keeps notices posted on/after START_DATE whose title or description mention
FedRAMP, FISMA, DoD impact levels, StateRAMP/GovRAMP, or cloud/SaaS, classifies
the language, merges into data/notices.csv, and rebuilds docs/data.json only
when something significant changed.

Usage:
  python scripts/update.py             # weekly: current file + this FY and last FY archives
  python scripts/update.py --backfill  # every archive year since START_DATE
  python scripts/update.py --force     # write outputs even if nothing significant changed
"""
import argparse
import csv
import datetime as dt
import html
import io
import json
import os
import re
import sys
import urllib.request

START_DATE = "2025-03-24"  # FedRAMP 20x announced
BASE = "https://sam.gov/api/prod/fileextractservices/v1/api/download/Contract%20Opportunities"
CURRENT_URL = f"{BASE}/datagov/ContractOpportunitiesFullCSV.csv?privacy=Public"
ARCHIVE_URL = BASE + "/Archived%20Data/FY{fy}_archived_opportunities.csv?privacy=Public"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTICES_CSV = os.path.join(ROOT, "data", "notices.csv")
DATA_JSON = os.path.join(ROOT, "docs", "data.json")
ALERT_MD = os.path.join(ROOT, "alert.md")  # not committed; consumed by the workflow

csv.field_size_limit(sys.maxsize)

PREFILTER = re.compile(
    r"fed\s?ramp|\bfisma\b|cloud|\bsaas\b|\bpaas\b|\biaas\b|software[- ]as[- ]a[- ]service"
    r"|impact level|\bIL[- ]?[2456]\b|state\s?ramp|gov\s?ramp|tx-?ramp", re.I)

LVL = r"(high|moderate|medium|low|li-?saas|tailored)"
RX = {
    "fedramp_20x": re.compile(r"fed\s?ramp\W{0,3}20\s?x|20x\W{0,3}fed\s?ramp", re.I),
    "fedramp_level": re.compile(
        r"fed\s?ramp\W{1,3}(?:\w+\W+){0,4}?" + LVL + r"\b|\b" + LVL + r"\W+(?:\w+\W+){0,3}?fed\s?ramp", re.I),
    "fedramp_any": re.compile(r"fed\s?ramp|federal risk and authorization management", re.I),
    "fisma_level": re.compile(
        r"fisma\W+(?:\w+\W+){0,3}?(high|moderate|low)\b|\b(high|moderate|low)\W+(?:impact\W+)?fisma"
        r"|fips\W*199\W+(?:\w+\W+){0,4}?(high|moderate|low)\b", re.I),
    "fisma_any": re.compile(r"\bfisma\b", re.I),
    "dod_il": re.compile(r"impact level\W*([2456])\b|\bIL\W?([2456])\b", re.I),
    "stateramp": re.compile(r"state\s?ramp|gov\s?ramp|tx-?ramp", re.I),
    "cloud_real": re.compile(
        r"cloud[- ](based|hosted|service|computing|solution|platform|environment|hosting|storage|infrastructure"
        r"|native|provider|subscription|first|smart|migration|offering|deployment|application|software|security)"
        r"|(?:in|to|into) the cloud|\bsaas\b|\bpaas\b|\biaas\b|software[- ]as[- ]a[- ]service|gov\s?cloud"
        r"|\bazure\b|\baws\b|google cloud|oracle cloud|commercial cloud", re.I),
}
STATUS = re.compile(
    r"fed\s?ramp\W{0,3}(?:\w+\W+){0,2}?(authori[sz]ed|certified|compliant|compliance|approved|ready"
    r"|equivalen\w*|accredited|in[- ]process)", re.I)
SNIP = re.compile(r"fed\s?ramp|fisma|impact level|\bIL\W?[2456]\b|state\s?ramp|gov\s?ramp|cloud|saas"
                  r"|software as a service", re.I)
DFARS = re.compile(r"252\.239-70(09|10)")

BUCKETS = ["FedRAMP 20x", "FedRAMP + level", "FedRAMP (no level)", "DoD IL only",
           "FISMA level only", "StateRAMP/GovRAMP only", "FISMA (no level) only", "Cloud/SaaS, no framework"]
SIGNIFICANT = set(BUCKETS) - {"Cloud/SaaS, no framework"}
SOLICITATION_TYPES = {"Solicitation", "Combined Synopsis/Solicitation", "Presolicitation"}
IT_NAICS = ("5415", "518210", "513210", "511210", "519290")

FIELDS = ["NoticeId", "Bucket", "FedRAMPLevels", "FedRAMPStatusWords", "FISMALevels", "DoDIL", "IT",
          "DFARSCloudClause", "PostedDate", "Type", "ResponseDeadLine", "Agency", "SubTier", "Office",
          "Title", "SolNumber", "NAICS", "PSC", "SetAside", "Link", "OpportunityKey", "Snippet", "FirstSeen"]


def clean(t):
    t = html.unescape(t or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def classify(row):
    text = clean((row.get("Title") or "") + " . " + (row.get("Description") or ""))
    f = {k: bool(p.search(text)) for k, p in RX.items()}
    if f["fedramp_20x"]:
        bucket = "FedRAMP 20x"
    elif f["fedramp_level"]:
        bucket = "FedRAMP + level"
    elif f["fedramp_any"]:
        bucket = "FedRAMP (no level)"
    elif f["dod_il"]:
        bucket = "DoD IL only"
    elif f["fisma_level"]:
        bucket = "FISMA level only"
    elif f["stateramp"]:
        bucket = "StateRAMP/GovRAMP only"
    elif f["fisma_any"]:
        bucket = "FISMA (no level) only"
    elif f["cloud_real"]:
        bucket = "Cloud/SaaS, no framework"
    else:
        return None  # noise: point cloud, cloud cover, St. Cloud...

    def first(m):
        return next(g for g in m.groups() if g).lower()

    levels = {first(m) for m in RX["fedramp_level"].finditer(text)}
    levels = {"moderate" if x == "medium" else "li-saas" if "saas" in x else x for x in levels}
    status = set()
    for m in STATUS.finditer(text):
        s = m.group(1).lower()
        status.add("equivalent" if s.startswith("equivalen") else "authorized" if s.startswith("authori")
                   else "compliant" if s.startswith("complian") else s)
    fisma = {first(m) for m in RX["fisma_level"].finditer(text)}
    il = {"IL" + first(m) for m in RX["dod_il"].finditer(text)}
    m = SNIP.search(text)
    snippet = text[max(0, m.start() - 140):m.start() + 220] if m else ""
    naics, psc = row.get("NaicsCode") or "", (row.get("ClassificationCode") or "").upper()
    sol = (row.get("Sol#") or "").strip()
    agency = row.get("Department/Ind.Agency") or ""
    return {
        "NoticeId": row["NoticeId"], "Bucket": bucket,
        "FedRAMPLevels": ";".join(sorted(levels)), "FedRAMPStatusWords": ";".join(sorted(status)),
        "FISMALevels": ";".join(sorted(fisma)), "DoDIL": ";".join(sorted(il)),
        "IT": "Y" if naics.startswith(IT_NAICS) or psc.startswith(("D", "7")) else "N",
        "DFARSCloudClause": "Y" if DFARS.search(row.get("Description") or "") else "N",
        "PostedDate": (row.get("PostedDate") or "")[:10], "Type": row.get("Type") or "",
        "ResponseDeadLine": (row.get("ResponseDeadLine") or "")[:10], "Agency": agency,
        "SubTier": row.get("Sub-Tier") or "", "Office": row.get("Office") or "",
        "Title": row.get("Title") or "", "SolNumber": sol, "NAICS": naics, "PSC": psc,
        "SetAside": row.get("SetAside") or "",
        "Link": f"https://sam.gov/opp/{row['NoticeId']}/view",
        "OpportunityKey": (sol.upper() or row["NoticeId"]) + "|" + agency,
        "Snippet": snippet,
    }


def stream(url, label):
    req = urllib.request.Request(url, headers={"User-Agent": "sam-fedramp-tracker (github.com/khanssen)"})
    kept = seen = 0
    with urllib.request.urlopen(req, timeout=600) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{label}: HTTP {resp.status}")
        reader = csv.DictReader(io.TextIOWrapper(resp, encoding="cp1252", errors="replace", newline=""))
        if "NoticeId" not in (reader.fieldnames or []):
            raise RuntimeError(f"{label}: unexpected header {reader.fieldnames[:5] if reader.fieldnames else None}")
        for row in reader:
            seen += 1
            if (row.get("PostedDate") or "")[:10] < START_DATE:
                continue
            if not PREFILTER.search((row.get("Title") or "") + " " + (row.get("Description") or "")):
                continue
            rec = classify(row)
            if rec:
                kept += 1
                yield rec
    print(f"{label}: scanned {seen:,} rows, kept {kept:,}", file=sys.stderr)


def fiscal_year(d):
    return d.year + 1 if d.month >= 10 else d.year


def load_existing():
    if not os.path.exists(NOTICES_CSV):
        return {}
    with open(NOTICES_CSV, newline="", encoding="utf-8") as fh:
        return {r["NoticeId"]: r for r in csv.DictReader(fh)}


def sig(records):
    return {(r["NoticeId"], r["Bucket"]) for r in records.values() if r["Bucket"] in SIGNIFICANT}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    today = dt.date.today()
    fy_now = fiscal_year(today)
    first_fy = fiscal_year(dt.date.fromisoformat(START_DATE)) if args.backfill else fy_now - 1
    sources = [(ARCHIVE_URL.format(fy=fy), f"FY{fy} archive") for fy in range(first_fy, fy_now + 1)]
    sources.append((CURRENT_URL, "current"))  # last, so current versions win

    old = load_existing()
    new = dict(old)
    for url, label in sources:
        for rec in stream(url, label):
            rec["FirstSeen"] = old.get(rec["NoticeId"], {}).get("FirstSeen") or today.isoformat()
            new[rec["NoticeId"]] = rec

    added_sig = sig(new) - sig(old)
    added_any = set(new) - set(old)
    print(f"total {len(new):,} notices; {len(added_any):,} new; {len(added_sig):,} new/changed significant",
          file=sys.stderr)

    changed = bool(added_sig) or args.force or not old
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")
    if not changed:
        print("No significant changes; leaving outputs untouched.", file=sys.stderr)
        return

    rows = sorted(new.values(), key=lambda r: (r["PostedDate"], r["NoticeId"]))
    os.makedirs(os.path.dirname(NOTICES_CSV), exist_ok=True)
    with open(NOTICES_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    keys = ["NoticeId", "Bucket", "FedRAMPLevels", "FedRAMPStatusWords", "FISMALevels", "DoDIL", "IT",
            "DFARSCloudClause", "PostedDate", "Type", "ResponseDeadLine", "Agency", "SubTier", "Title",
            "SolNumber", "OpportunityKey", "FirstSeen", "Snippet"]
    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "startDate": START_DATE,
        "buckets": BUCKETS,
        "fields": keys,
        "rows": [[r.get(k, "") for k in keys] for r in rows],
    }
    with open(DATA_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), ensure_ascii=False)

    # Alert only for the interesting things, and never on the first (backfill) run.
    if old:
        alerts = [new[nid] for nid, b in added_sig
                  if b == "FedRAMP 20x" or (new[nid]["Type"] in SOLICITATION_TYPES)]
        if alerts:
            alerts.sort(key=lambda r: (r["Bucket"] != "FedRAMP 20x", r["PostedDate"]))
            lines = [f"New FedRAMP / IL / FISMA language in {len(alerts)} SAM.gov notice(s) this week.\n",
                     "| Bucket | Posted | Type | Agency | Title | Due |", "|---|---|---|---|---|---|"]
            for r in alerts:
                title = r["Title"].replace("|", "/")[:90]
                lines.append(f"| {r['Bucket']} | {r['PostedDate']} | {r['Type']} | {r['Agency']} | "
                             f"[{title}]({r['Link']}) | {r['ResponseDeadLine'] or '—'} |")
            with open(ALERT_MD, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            print(f"alert: {len(alerts)} notice(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
