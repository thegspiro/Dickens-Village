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

test("discardChanges reverts to the server copy, or drops never-sent records", () => {
  let rec = m.editRecord(synced(), { storage_tub: "9" }, F);
  rec.error = { message: "deleted elsewhere" };
  const back = m.discardChanges(rec, F);
  assert.equal(back.local.storage_tub, "1");
  assert.deepEqual(back.dirty, {});
  assert.equal(back.error, null);
  assert.equal(m.isPending(back), false);
  const unsent = m.newLocalRecord("", "44444444-4444-4444-8444-444444444444", { name: "x" }, F);
  assert.equal(m.discardChanges(unsent, F), null);
});

const newRec = (values) => m.newLocalRecord("", "11111111-1111-4111-8111-111111111111",
  { name: "Mill", storage_tub: "1", condition_grade: null, notes: null, ...values }, F);

test("a fresh create keeps only edits made while the request was out", () => {
  const sent = { ...newRec().local };
  const rec = m.editRecord(newRec(), { notes: "typed during upload" }, F);
  const out = m.afterCreate(rec, sent, serverRow({ id: 9, notes: null }), F, true);
  assert.equal(out.id, 9);
  assert.deepEqual(out.dirty, { notes: null });
  assert.equal(out.local.notes, "typed during upload");
});

test("a retried create keeps edits made after the first attempt's reply was lost", () => {
  // First attempt stored tub "1"; the person then changed it to "4" and the retry sent that.
  let rec = m.markCreateSent(newRec(), newRec().local);
  rec = m.editRecord(rec, { storage_tub: "4" }, F);
  const sent = { ...rec.local };
  rec = m.markCreateSent(rec, sent);
  const out = m.afterCreate(rec, sent, serverRow({ id: 9, storage_tub: "1", condition_grade: null }), F, false);
  assert.equal(out.creates, null);
  assert.deepEqual(out.dirty, { storage_tub: "1" }); // still to send, based on what the server holds
  assert.equal(out.local.storage_tub, "4");
  assert.deepEqual(m.patchPayload(out), { base: { storage_tub: "1" }, changes: { storage_tub: "4" } });
});

test("a retried create doesn't resend values the server only reformatted", () => {
  const F2 = ["name", "price_paid", "year_introduced"];
  const rec = m.newLocalRecord("", "11111111-1111-4111-8111-111111111111",
    { name: "Mill  House", price_paid: "12", year_introduced: "1990" }, F2);
  const out = m.afterCreate(rec, { ...rec.local },
    { id: 9, version: 1, name: "Mill House", price_paid: "12.00", year_introduced: 1990 }, F2, false);
  assert.deepEqual(out.dirty, {});
  assert.deepEqual(out.local, { name: "Mill House", price_paid: "12.00", year_introduced: 1990 });
});

test("a snapshot that shows a lost create keeps the phone's later edits", () => {
  const rec = m.editRecord(m.markCreateSent(newRec(), newRec().local), { storage_tub: "4" }, F);
  const { put } = m.mergeSnapshot([rec], [serverRow({ id: 9, client_uuid: rec.client_uuid, storage_tub: "1",
    condition_grade: null })], F, "", null);
  assert.equal(put[0].key, rec.key);
  assert.equal(put[0].id, 9);
  assert.deepEqual(put[0].dirty, { storage_tub: "1" });
  assert.equal(put[0].local.storage_tub, "4");
});

test("a 409 caused by this phone's own lost write is re-based, not shown as a conflict", () => {
  let rec = m.editRecord(synced(), { storage_tub: "2" }, F);
  rec = m.markSent(rec, m.patchPayload(rec).changes); // applied as version 2, reply lost
  rec = m.editRecord(rec, { storage_tub: "3" }, F);    // edited again before the retry
  const payload = m.patchPayload(rec);
  assert.deepEqual(payload, { base: { storage_tub: "1" }, changes: { storage_tub: "3" } });
  rec = m.markSent(rec, payload.changes);
  rec = m.afterConflict(rec, [{ field: "storage_tub", base: "1", mine: "3", theirs: "2" }],
    serverRow({ version: 2, storage_tub: "2" }), F);
  assert.equal(m.needsAttention(rec), false);
  assert.equal(rec.inflight, null);
  assert.deepEqual(m.patchPayload(rec), { base: { storage_tub: "2" }, changes: { storage_tub: "3" } });
});

test("own-write detection survives a snapshot arriving before the retry", () => {
  let rec = m.editRecord(synced(), { storage_tub: "2" }, F);
  rec = m.markSent(rec, m.patchPayload(rec).changes);
  rec = m.mergeSnapshot([rec], [serverRow({ version: 2, storage_tub: "2" })], F, "", null).put[0];
  rec = m.editRecord(rec, { storage_tub: "3" }, F);
  rec = m.markSent(rec, m.patchPayload(rec).changes);
  assert.equal(rec.inflight.version, 1);
  rec = m.afterConflict(rec, [{ field: "storage_tub", base: "1", mine: "3", theirs: "2" }],
    serverRow({ version: 2, storage_tub: "2" }), F);
  assert.equal(m.needsAttention(rec), false);
});

test("a change from another device is still a conflict", () => {
  let rec = m.editRecord(synced(), { storage_tub: "2" }, F);
  rec = m.markSent(rec, m.patchPayload(rec).changes);
  // two writes happened (version 3): not provably this phone's
  let out = m.afterConflict(rec, [{ field: "storage_tub", base: "1", mine: "2", theirs: "2x" }],
    serverRow({ version: 3, storage_tub: "2x" }), F);
  assert.equal(m.needsAttention(out), true);
  // one write, but with a value this phone never sent
  out = m.afterConflict(rec, [{ field: "storage_tub", base: "1", mine: "2", theirs: "9" }],
    serverRow({ version: 2, storage_tub: "9" }), F);
  assert.deepEqual(out.conflict.map((c) => c.theirs), ["9"]);
  // no patch was recorded as sent at all
  out = m.afterConflict(m.editRecord(synced(), { storage_tub: "2" }, F),
    [{ field: "storage_tub", base: "1", mine: "2", theirs: "2" }], serverRow({ version: 2, storage_tub: "2" }), F);
  assert.equal(m.needsAttention(out), true);
});

test("a successful patch or a discard clears the sent-patch record", () => {
  let rec = m.editRecord(synced(), { storage_tub: "2" }, F);
  const sent = m.patchPayload(rec).changes;
  rec = m.markSent(rec, sent);
  assert.equal(m.afterPatch(rec, sent, serverRow({ version: 2, storage_tub: "2" }), F).inflight, null);
  assert.equal(m.discardChanges(rec, F).inflight, null);
});

test("orphaned keeps the record (including a photo's blob) and flags it", () => {
  const blob = { size: 3 }; // stands in for a Blob
  const photo = { key: "p-1", itemKey: "c-gone", blob, error: null };
  const out = m.orphaned(photo);
  assert.equal(out.blob, blob);
  assert.match(out.error.message, /deleted on another device/);
  assert.equal(photo.error, null);
});

test("equivalent treats canonical forms alike but not real changes", () => {
  assert.equal(m.equivalent("12", "12.00"), true);
  assert.equal(m.equivalent(1990, "1990"), true);
  assert.equal(m.equivalent(" a  b ", "a b"), true);
  assert.equal(m.equivalent("", null), true);
  assert.equal(m.equivalent("12", "13"), false);
  assert.equal(m.equivalent("0", null), false);
  assert.equal(m.equivalent(false, null), false);
  assert.equal(m.equivalent("Tub 1", "Tub 2"), false);
});

test("after a lost create, a change made on another device is kept, not overwritten", () => {
  const rec = m.markCreateSent(newRec(), newRec().local); // the phone sent tub "1" and changed nothing since
  const server = serverRow({ id: 9, storage_tub: "6", condition_grade: null }); // the website then set tub 6
  assert.deepEqual(m.afterCreate(rec, rec.local, server, F, false).dirty, {});
  const snap = m.mergeSnapshot([rec], [{ ...server, client_uuid: rec.client_uuid }], F, "", null).put[0];
  assert.deepEqual(snap.dirty, {});
  assert.equal(snap.local.storage_tub, "6");
});

test("after a lost create, a field changed on both sides goes to the server as a conflict", () => {
  const rec = m.editRecord(m.markCreateSent(newRec(), newRec().local), { storage_tub: "4" }, F);
  const out = m.afterCreate(rec, rec.local, serverRow({ id: 9, storage_tub: "6", condition_grade: null }), F, false);
  // based on what the phone sent, so the server sees tub changed there too and reports it
  assert.deepEqual(m.patchPayload(out), { base: { storage_tub: "1" }, changes: { storage_tub: "4" } });
});
