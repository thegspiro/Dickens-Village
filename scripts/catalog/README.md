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

A different year's PDF may lay out differently, so check the output against the PDF before
committing it. Every row keeps its `source_page` and original `source_text` for that purpose.
