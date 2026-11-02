# FedRAMP in SAM.gov

A weekly tracker of how federal contract notices on [SAM.gov](https://sam.gov) describe FedRAMP, FISMA, DoD impact levels and cloud requirements. It covers notices posted since FedRAMP 20x was announced (2025-03-24).

**Live page:** https://khanssen.github.io/sam-fedramp-tracker/

## How it works

1. Every Monday morning, a GitHub Actions job ([`.github/workflows/weekly.yml`](.github/workflows/weekly.yml)) runs [`scripts/update.py`](scripts/update.py).
2. The script streams SAM.gov's public Contract Opportunities bulk CSVs: the current file plus this fiscal year's and last fiscal year's archives. No API key is needed.
3. It keeps notices posted on or after the start date whose **title or description** mention FedRAMP, FISMA, DoD IL, StateRAMP/GovRAMP or cloud/SaaS. Each one is labeled with the most specific language it uses.
4. **Update on condition:** outputs are rewritten and committed only when there's a new or changed FedRAMP, IL or FISMA notice. New cloud/SaaS-only notices alone don't trigger an update; they're picked up at the next real one.
5. **Alerts:** a new FedRAMP 20x mention, or new FedRAMP/IL/FISMA language in a solicitation, combined synopsis or presolicitation, opens a GitHub issue. If you watch the repo, that arrives as an email.

## Data

- [`data/notices.csv`](data/notices.csv): one row per notice, with category, FedRAMP levels and status words, DoD IL, FISMA levels, IT flag, DFARS cloud-clause flag, dates, agency, SAM link and a text snippet.
- [`docs/data.json`](docs/data.json): the same data in a compact form for the page.

Contracting officer contact details are deliberately not stored.

## Limits

- Only title and description text is searched, not attachments, so counts are a floor.
- Matching is keyword-based. Spot-check rows with the `Snippet` column or the SAM link.
- Amendments are grouped into opportunities by solicitation number and agency.

## Running locally

```bash
python scripts/update.py             # weekly mode
python scripts/update.py --backfill  # re-scan all archive years since the start date
python scripts/update.py --force     # rewrite outputs even if nothing changed
```

You can also run the workflow manually from the Actions tab (**Run workflow**).

## License

- **Code** (scripts, page, workflow): [MIT](LICENSE).
- **Data** (`data/`, `docs/data.json`): the underlying notices are U.S. government works from SAM.gov and are in the public domain. The classifications added here are dedicated to the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

Not affiliated with GSA, FedRAMP or SAM.gov.
