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

## Sources that require a Mongolian connection

`python local.py audit` (also `cabinet`, `eiti`, `ebarimt`) resumes a local crawl,
uploads a checkpoint to the private data repository every 30-minute run, and
removes the local working copy only after a complete dated release has been
published and its asset sizes verified. Failed or interrupted crawls keep their
local data and partial release. Use `--keep-local` to retain the verified copy.
Only one runner per source can hold the local process lock.

On Windows install the requirements to enable native certificate-chain validation
with truststore; certificate verification remains enabled. EITI year selection is
checked against the returned page so a fallback year cannot silently be relabelled.
Exhausted failed items remain incomplete and cannot be finalized as a full release.

## Tender

The public archive at [opendata.mn dataset 193](https://opendata.mn/database/29/193)
provides an independently downloadable Tender invitation extract. Run
`python run.py tender_archive` or select `tender_archive` in Actions. Metadata
currently advertises 56,290 rows for 2024–2025 and a June 2026 archive update.
The crawler verifies the row count, unique invitation IDs and stable metadata;
its manifest records the actual years, date range, source, publisher and license
(CC BY-NC-ND 4.0). It downloads no attachments. This archive is published under
its own release name and does not establish completeness of the live Tender
portal or 2026 data.

The current website has returned a Cloudflare verification page to ordinary HTTP
and unmodified Chrome sessions during the 2026-10-24 check. No data has been
downloaded through that blocked connection. The older documented API domain
`opendata.tender.gov.mn` does not currently resolve from this machine.

If the public invitation list opens in your normal browser, `browser_export.js`
can be pasted into that page's DevTools Console. Click its export button and choose
a JSONL file. It follows exposed public procurement links, including plans,
invitations, results, contracts and implementation pages, and exposed year and
pagination links. It also saves JSON from recognised public procurement API
URLs actually requested by the displayed page and follows explicit API next links.
Requests are spaced two seconds apart. Public text, tables and attachment
references are saved; attachments, authentication headers and cookies are never
exported. A security challenge stops the export. Every 50 records the partial
file is committed so an interrupted tab does not lose the whole collection.
The manifest records observed and missing sections. Client-only pagination,
hidden filters and unknown totals require further reconciliation. This exporter
does not establish nationwide completeness, including companies and performance.

`python import_tender_export.py tender-public-export.jsonl --publish` imports that
file and uses the private Releases pipeline. Browser exports remain `wip-tender`
until full-coverage reconciliation. Use `--allow-partial` to recover a committed
checkpoint that has no final manifest. The separate `tender` crawler
supports the documented authorised API, with `TENDER_API_TOKEN` (GitHub secret)
and an optional `TENDER_API_BASE` (Actions variable pointing to an official HTTPS
endpoint). Its parser is tested against the documentation, not a successful live
API response. Issued credentials may be scoped to one organisation.
