// Run with: node --test tests/js/offline_model.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import * as m from "../../app/static/offline/model.mjs";

const F = ["name", "storage_tub", "condition_grade", "notes"];
const serverRow = (over) => ({ id: 7, version: 1, client_uuid: null, name: "Mill", storage_tub: "1",
  condition_grade: "good", notes: null, ...over });

function synced() {
  return m.mergeSnapshot([], [serverRow()], F, "", null).put[0];
}

test("snapshot creates records keyed by server id", () => {
  const rec = synced();
  assert.equal(rec.key, "s-7");
  assert.deepEqual(rec.local, { name: "Mill", storage_tub: "1", condition_grade: "good", notes: null });
  assert.equal(m.isPending(rec), false);
});

test("editing records the starting value once and only for changed fields", () => {
  let rec = m.editRecord(synced(), { storage_tub: "2", name: "Mill" }, F);
  rec = m.editRecord(rec, { storage_tub: "3" }, F);
  assert.deepEqual(rec.dirty, { storage_tub: "1" });
  assert.deepEqual(m.patchPayload(rec), { base: { storage_tub: "1" }, changes: { storage_tub: "3" } });
  assert.equal(m.isPending(rec), true);
});

test("a newer snapshot updates untouched fields but keeps unsent edits and their base", () => {
  const rec = m.editRecord(synced(), { storage_tub: "2" }, F);
  const { put } = m.mergeSnapshot([rec], [serverRow({ version: 2, condition_grade: "fair", storage_tub: "5" })],
    F, "", null);
  assert.equal(put[0].local.condition_grade, "fair");
  assert.equal(put[0].local.storage_tub, "2");
  assert.deepEqual(put[0].dirty, { storage_tub: "1" }); // still the value the edit started from
});

test("afterPatch clears only fields not edited again in the meantime", () => {
  let rec = m.editRecord(synced(), { storage_tub: "2", notes: "chip" }, F);
  const sent = m.patchPayload(rec).changes;
  rec = m.editRecord(rec, { notes: "two chips" }, F);
  rec = m.afterPatch(rec, sent, serverRow({ version: 2, storage_tub: "2", notes: "chip" }), F);
  assert.deepEqual(rec.dirty, { notes: null });
  assert.equal(rec.local.notes, "two chips");
  assert.equal(rec.server.version, 2);
});

test("conflict resolution: theirs drops the edit, mine rebases it", () => {
  let rec = m.editRecord(synced(), { storage_tub: "2", notes: "x" }, F);
  rec = m.afterConflict(rec, [
    { field: "storage_tub", base: "1", mine: "2", theirs: "3" },
    { field: "notes", base: null, mine: "x", theirs: "y" },
  ], serverRow({ version: 2, storage_tub: "3", notes: "y" }), F);
  assert.equal(m.needsAttention(rec), true);
  rec = m.resolveConflict(rec, { storage_tub: "theirs", notes: "mine" });
  assert.equal(rec.local.storage_tub, "3");
  assert.equal(rec.local.notes, "x");
  assert.deepEqual(rec.dirty, { notes: "y" });
  assert.equal(rec.conflict, null);
});

test("new records are created, then follow the server", () => {
  let rec = m.newLocalRecord("", "11111111-1111-4111-8111-111111111111", { name: "Carolers", storage_tub: "2" }, F);
  assert.equal(rec.key, "c-11111111-1111-4111-8111-111111111111");
  assert.equal(m.isPending(rec), true);
  const sent = { ...rec.local };
  rec = m.editRecord(rec, { notes: "added while uploading" }, F);
  rec = m.afterCreate(rec, sent, serverRow({ id: 9, name: "Carolers", storage_tub: "2", condition_grade: null,
    client_uuid: rec.client_uuid }), F);
  assert.equal(rec.id, 9);
  assert.equal(rec.key, "c-11111111-1111-4111-8111-111111111111"); // key stays stable for the UI
  assert.deepEqual(rec.dirty, { notes: null });
  assert.equal(rec.local.notes, "added while uploading");
});

test("snapshot matches a created record by client_uuid when the create reply was lost", () => {
  const uuid = "22222222-2222-4222-8222-222222222222";
  const rec = m.newLocalRecord("", uuid, { name: "Carolers" }, F);
  const { put, remove } = m.mergeSnapshot([rec], [serverRow({ id: 10, name: "Carolers", client_uuid: uuid })],
    F, "", null);
  assert.equal(put.length, 1);
  assert.equal(put[0].key, rec.key);
  assert.equal(put[0].id, 10);
  assert.equal(m.isPending(put[0]), false);
  assert.deepEqual(remove, []);
});

test("records deleted on the server are removed, or flagged if they have unsent edits", () => {
  const clean = synced();
  const edited = { ...m.editRecord(synced(), { notes: "x" }, F), key: "s-8", id: 8 };
  const unsent = m.newLocalRecord("", "33333333-3333-4333-8333-333333333333", { name: "New" }, F);
  const { put, remove } = m.mergeSnapshot([clean, edited, unsent], [], F, "", null);
  assert.deepEqual(remove, ["s-7"]);
  assert.equal(put.length, 1);
  assert.match(put[0].error.message, /deleted on another device/);
});

test("catalog search ranks item numbers first and accepts digits only", () => {
  const entries = [
    { id: 1, item_number: "56.65005", name: "Christmas Carol Cottage" },
    { id: 2, item_number: "56.65005", name: "Fezziwig's Warehouse", parent_name: "Christmas Carol Cottage" },
    { id: 3, item_number: "56.58539", name: "The Big Prize Turkey" },
    { id: 4, item_number: "6015650", name: "Lord Lilliput's Cottage" },
  ];
  assert.deepEqual(m.searchCatalog(entries, "65005").map((e) => e.id), [1, 2]);
  assert.deepEqual(m.searchCatalog(entries, "5665005").map((e) => e.id), [1, 2]);
  assert.deepEqual(m.searchCatalog(entries, "cottage").map((e) => e.id), [1, 4]);
  assert.deepEqual(m.searchCatalog(entries, "x"), []);
});

test("catalogFill mirrors the website lookup", () => {
  assert.deepEqual(m.catalogFill({ id: 4, item_number: "6015650", name: "Lord Lilliput's Cottage",
    category: "building", series: null, year_issued: 2025, year_retired: null }),
  { catalog_id: 4, name: "Lord Lilliput's Cottage", item_number: "6015650", category: "building",
    year_introduced: 2025 });
});
