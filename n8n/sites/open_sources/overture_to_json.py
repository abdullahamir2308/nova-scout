"""Flatten extract_overture.py's parquet files into the JSON candidates.js reads.

    python n8n/sites/open_sources/overture_to_json.py OUT_DIR overture.json

One row per place: id, name, category, first website, address (locality,
region, ISO country, freeform), operating status and the sources' licences.
The emails and phones columns are deliberately not carried forward: no contact
ever comes from a directory (Workflow 3b).
"""
import json
import sys


def main(parts_dir, out):
    import duckdb
    rows = duckdb.connect().execute("""
      SELECT id, name, cat, websites[1] AS website, addresses[1].locality AS locality, addresses[1].region AS region,
             addresses[1].country AS country, addresses[1].freeform AS freeform, operating_status, src
        FROM read_parquet('%s/part*.parquet')""" % parts_dir.replace("\\", "/")).fetchall()
    cols = ["id", "name", "cat", "website", "locality", "region", "country", "freeform", "operating_status", "src"]
    data = [dict(zip(cols, r)) for r in rows]
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    print(len(data), "places ->", out)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
