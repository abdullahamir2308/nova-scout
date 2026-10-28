"""Extract research-site-shaped places WITH A WEBSITE from Overture Maps' places theme.

    pip install duckdb
    python n8n/sites/open_sources/extract_overture.py RELEASE OUT_DIR      # e.g. 2026-10-21.0

Source 2 of the research-site niche (migration 025). Overture Maps Foundation
publishes its places as GeoParquet on a public S3 bucket, no key. Licences, per
its attribution page: CDLA Permissive 2.0 for most providers (Meta, Microsoft,
...), Apache 2.0 for Foursquare (with its NOTICE), CC0 for AllThePlaces -- each
row keeps its sources' dataset:licence pairs (`src`), and site_lead_sources.
overture_licences stores them per lead. The data is processed locally: DuckDB
reads only the columns asked for, one file at a time (~0.7 GB each, 16 files,
~3 minutes each), and a finished file is never read again, so a crash resumes.

A row is kept when it has a website AND either its name says it is a research
site (in the included countries' languages) or its category is one research
sites carry. The emails/phones columns are extracted for completeness but NEVER
used as a contact: a site's address comes only from the site's own pages
(Workflow 3b), fetched later by Contact Lookup.
"""
import os
import re
import sys
import time
import urllib.request

NAME_RE = (r"(?i)(clinical research|clinical trial|clinical stud|research (center|centre|site|institute|associates|group|clinic|unit|partners|network)"
           r"|medical research|site management|investigaci[oó]n(es)? cl[ií]nica|centro de investigaci|pesquisa cl[ií]nica|centro de pesquisa"
           r"|instituto de pesquisa|badania kliniczne|badań klinicznych|klinische forschung|studienzentrum|studien ?zentrum|klinische studien"
           r"|recherche clinique|ricerca clinica|klinik araştırma|klinik arastirma|onderzoekscentrum|klinisch onderzoek|kliinilise|klinikai kutat"
           r"|klinick[eéý] (studie|hodnocen|výzkum)|cercetare clinic|cercetări clinice|ensayos cl[ií]nicos|estudios cl[ií]nicos|trial (center|centre|site))")
CATEGORIES = ("medical_research_institute", "research_institute", "medical_service_organization", "b2b_clinical_lab")


def main(release, out_dir):
    import duckdb
    os.makedirs(out_dir, exist_ok=True)
    listing = urllib.request.urlopen(
        "https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/%s/theme=places/" % release).read().decode()
    keys = re.findall(r"<Key>([^<]+\.parquet)</Key>", listing)
    if not keys:
        sys.exit("no places files for release %s" % release)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2'; SET memory_limit='2GB'; SET threads=2;")
    for i, k in enumerate(keys):
        dest = os.path.join(out_dir, "part%02d.parquet" % i)
        if os.path.exists(dest):
            continue
        t = time.time()
        con.execute("""
          COPY (
            SELECT id, names.primary AS name, taxonomy.primary AS cat, taxonomy.alternates AS cat_alt, basic_category,
                   websites, emails, phones, addresses, confidence, operating_status,
                   [s.dataset || ':' || coalesce(s.license,'') FOR s IN sources] AS src,
                   bbox.xmin AS lon, bbox.ymin AS lat
            FROM read_parquet('s3://overturemaps-us-west-2/%s')
            WHERE websites IS NOT NULL AND len(websites) > 0
              AND (regexp_matches(names.primary, '%s') OR taxonomy.primary IN %s)
          ) TO '%s' (FORMAT parquet)""" % (k, NAME_RE.replace("'", "''"), CATEGORIES, (dest + ".tmp").replace("\\", "/")))
        os.replace(dest + ".tmp", dest)
        n = con.execute("select count(*) from read_parquet('%s')" % dest.replace("\\", "/")).fetchone()[0]
        print("%d/%d %d rows %ds" % (i + 1, len(keys), n, time.time() - t), flush=True)
    print("done")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
