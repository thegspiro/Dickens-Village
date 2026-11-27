# Catalog build scripts

These one-off scripts turned Department 56's 2025 Dickens' Village PDF checklists
("Lighted Items" and "Accessories") into `data/catalog/dickens_village_2025.csv`.
The app does not need them at runtime. They need `pdfplumber` (`pip install pdfplumber`).

```sh
cd scripts/catalog
python dump_lines.py Dickens_Village_2025-lighted-items.pdf 485 /tmp/lighted.json
python dump_lines.py Dickens_Village_2025-accessories.pdf 500 /tmp/acc.json
python build_records.py /tmp/lighted.json /tmp/acc.json /tmp/records.json
python normalize.py /tmp/records.json ../../data/catalog/dickens_village_2025.csv \
    ../../data/catalog/dickens_village_2025_source_issues.txt
```

`dump_lines.py` reads each PDF line and sorts its words into columns by x position.
The second argument is the x position that separates the US and Canadian price columns,
which differs between the two PDFs. `build_records.py` joins wrapped lines and groups set
pieces under their set. `normalize.py` splits names from notes, tags series, applies the
`OVERRIDES` for lines the parser cannot read, and writes problems in the source (impossible
years, duplicate item numbers) to the issues file instead of guessing a correction.

The General Village Accessories (2021) list was built the same way, with options for its
layout and its own profile in `normalize.py`:

```sh
python dump_lines.py General_Village_Accessories_2021.pdf 500 /tmp/general.json \
    --retired-x 370 --skip '^General Village Accessories$'
python build_records.py --out /tmp/general-records.json general=/tmp/general.json
python normalize.py /tmp/general-records.json ../../data/catalog/general_village_accessories_2021.csv \
    ../../data/catalog/general_village_accessories_2021_source_issues.txt --profile general-2021
```

`--retired-x` moves the boundary of the "Year Retired" column (this PDF prints retired years
a few points further left). `--skip` drops page titles, and `--section` (not needed here)
records section headings so a profile can set the series from them. The `general-2021`
profile also drops pieces listed twice with the same years and prices, and reports US and
Canadian prices that can't both be right. The default profile, `dickens-2025`, still
reproduces `dickens_village_2025.csv` byte for byte.

The Holy Land lists use `--section` so each piece gets the series of the heading it sits under:

```sh
python dump_lines.py Holy_Land.pdf 500 /tmp/hl.json --retired-x 370 --section '^Holy Land™ Collection'
python dump_lines.py Holy_Land_acc.pdf 500 /tmp/ha.json --retired-x 370 --section '^Holy Land™ Collection'
python build_records.py --out /tmp/holy-records.json holy-land-lighted=/tmp/hl.json holy-land-accessories=/tmp/ha.json
python normalize.py /tmp/holy-records.json ../../data/catalog/holy_land.csv \
    ../../data/catalog/holy_land_source_issues.txt --profile holy-land
```

A different year's PDF may lay out differently, so check the output against the PDF before
committing it. Every row keeps its `source_page` and original `source_text` for that purpose.
