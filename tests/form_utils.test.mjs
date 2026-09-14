import test from "node:test";
import assert from "node:assert/strict";
import { formatBirthDateInput as format } from "../app/static/form-utils.mjs";

test("numeric keyboard input becomes a complete date", () => {
  let value = "";
  for (const digit of "15051990") value = format(value + digit).text;
  assert.equal(value, "15.05.1990");
});
test("clipboard dates and ISO browser autofill keep the intended day", () => {
  assert.equal(format("15/05/1990").text, "15.05.1990");
  assert.deepEqual(format("1990-05-15"), { text: "15.05.1990", caret: 10 });
});
test("editing the day does not jump the caret to the year", () => {
  assert.deepEqual(format("16.05.1990", 2), { text: "16.05.1990", caret: 2 });
  assert.deepEqual(format("1.05.1990", 1), { text: "1.05.1990", caret: 1 });
});
test("clear, partial input and pasted noise remain editable", () => {
  assert.deepEqual(format(""), { text: "", caret: 0 });
  assert.equal(format("15").text, "15");
  assert.equal(format("150").text, "15.0");
  assert.equal(format("15a05b1990").text, "15.05.1990");
});
test("formatting does not silently correct an impossible date", () => {
  assert.equal(format("31021990").text, "31.02.1990");
});
