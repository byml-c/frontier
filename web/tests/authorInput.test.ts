import assert from "node:assert/strict";
import test from "node:test";
import { authorsText, parseAuthors } from "../src/components/authorInput.ts";

test("author input accepts comma-separated names and preserves their order", () => {
  assert.deepEqual(parseAuthors(" Ada, Bob， Chen\nDana, , "), [
    { name: "Ada" }, { name: "Bob" }, { name: "Chen" }, { name: "Dana" },
  ]);
  assert.equal(authorsText([{ name: " Ada " }, { name: "Bob" }]), "Ada, Bob");
  assert.deepEqual(parseAuthors(" , \n"), []);
});

test("reordering and adding authors keeps existing identity and affiliation details", () => {
  const authors = [
    { id: "ada", name: "Ada", affiliation: "Lab A", evidence: "First page" },
    { id: "bob", name: "Bob", affiliation: "Lab B", evidence: "Verified" },
  ];
  assert.deepEqual(parseAuthors("Bob, Ada, Chen", authors), [authors[1], authors[0], { name: "Chen" }]);
  assert.deepEqual(parseAuthors("Bob", authors), [authors[1]]);
  assert.equal(authors.length, 2);
});

test("authors with the same name retain separate identities", () => {
  const authors = [{ id: "first", name: "Alex" }, { id: "second", name: "Alex" }];
  assert.deepEqual(parseAuthors("Alex, Alex", authors), authors);
});
