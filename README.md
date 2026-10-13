# mn-open-data-crawlers

Crawlers for Mongolian public-sector open data, used for non-commercial public-interest transparency research
(public money, procurement, officials' interests). They run on GitHub Actions; the collected data is stored in a
separate private repository, not here.

| Source | What is collected |
|---|---|
| parliament.mn | members, committees, polls and per-member votes, bills, law archive, draft laws |
| xacxom.iaac.mn | list of officials who filed asset/interest declarations (organisation, position, years) |
| legalinfo.mn | all legal acts: metadata, full text, amendment links |
| shuukh.mn | court decisions on corruption (Criminal Code ch. 22) and administrative tender/state-property disputes |
| opendata.gov.mn | legal-entity register dump, Ministry of Finance barred-supplier and complaint lists, dataset catalogue |
| opendata.burtgel.gov.mn | liquidated and removed legal entities |
| OpenSanctions, ADB | Mongolian PEPs, World Bank / ADB debarments (OpenSanctions data: CC BY-NC 4.0) |
| egazar.gov.mn | non-residential land parcels from the public WFS (no holder names) |
| iaac.mn | anti-corruption authority news |

## Conduct

- Only publicly published data; robots.txt is respected; logins, CAPTCHAs and other access controls are never bypassed.
- Every request carries an honest User-Agent naming this project.
- Requests are rate-limited per site (0.3–3 per second) with backoff on errors; sites run in parallel, never
  many connections to one site.
- No attachments or PDFs are downloaded.
- Site operators: open an issue here to ask for a slower rate or an exclusion and it will be honoured.

## Running

`Actions → crawl → Run workflow` (one source) or `crawl-all`. Tick `sample` for a dry run that stores nothing.
Long crawls save their state and continue in a new run automatically. Requires the secret `DATA_TOKEN`
(fine-grained token, Contents read/write on the data repository).
