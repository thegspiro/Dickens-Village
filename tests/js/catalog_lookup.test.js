// Run with: node --test tests/js/catalog_lookup.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const lookup = require("../../app/static/catalog-lookup.js");

const piece = {
  id: 7, item_number: "56.65005", name: "Fezziwig's Warehouse", details: 'Part of "Christmas Carol Cottage"',
  series: null, year_issued: 1986, year_retired: 1995, is_current: false, srp_usd: "25.00", srp_cad: null,
  category: "building", parent_name: "Christmas Carol Cottage",
};

test("describe shows number, name, years and set", () => {
  assert.equal(lookup.describe(piece), "56.65005 · Fezziwig's Warehouse · 1986–1995 · part of “Christmas Carol Cottage”");
});

test("formatYears handles current and unknown years", () => {
  assert.equal(lookup.formatYears({ year_issued: 2025, is_current: true }), "2025–Current");
  assert.equal(lookup.formatYears({ year_issued: null, year_retired: null, is_current: false }), "?–?");
});

test("fieldValues maps catalog fields onto form field ids", () => {
  assert.deepEqual(lookup.fieldValues(piece), {
    name: "Fezziwig's Warehouse", item_number: "56.65005", category: "building",
    year_introduced: "1986", year_retired: "1995",
  });
});

test("fieldValues leaves series and category alone when the catalog has none", () => {
  const values = lookup.fieldValues({ ...piece, category: null, series: null });
  assert.equal("category" in values, false);
  assert.equal("series" in values, false);
});

test("fieldValues clears retired year for pieces still in production", () => {
  const values = lookup.fieldValues({ ...piece, year_retired: null, is_current: true, series: "A Christmas Carol" });
  assert.equal(values.year_retired, "");
  assert.equal(values.series, "A Christmas Carol");
});

test("missing item number becomes empty string", () => {
  assert.equal(lookup.fieldValues({ ...piece, item_number: null }).item_number, "");
});

test("detailLine names the list and drops a repeated set name", () => {
  assert.equal(lookup.detailLine(piece), "Lighted list");
  assert.equal(lookup.detailLine({ ...piece, details: 'Part of "Christmas Carol Cottage" Limited' }),
    'Lighted list · Part of "Christmas Carol Cottage" Limited');
  assert.equal(lookup.detailLine({ list: "accessories", details: "set of 2", parent_name: null }),
    "Accessories list · set of 2");
});
