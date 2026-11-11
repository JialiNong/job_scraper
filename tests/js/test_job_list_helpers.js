#!/usr/bin/env node
/* Load job_list.js in a tiny DOM stub and assert shared helpers.
   Run: node tests/js/test_job_list_helpers.js
*/
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "../..");
const src = fs.readFileSync(path.join(ROOT, "static/js/shared/job_list.js"), "utf8");

const media = { matches: false, addEventListener() {} };
const document = {
  querySelectorAll: () => [],
  querySelector: () => null,
  getElementById: () => null,
  addEventListener() {},
  createElement: () => ({
    style: {},
    classList: { toggle() {}, add() {}, remove() {} },
    setAttribute() {},
    addEventListener() {},
    appendChild() {},
  }),
  body: { appendChild() {} },
};
const sandbox = {
  window: { matchMedia: () => media },
  document,
  localStorage: { getItem: () => null, setItem() {} },
  location: { search: "", hostname: "test" },
  history: { replaceState() {} },
  URL,
  Date,
  Number,
  String,
  Boolean,
  Array,
  Math,
  Set,
  Map,
  JSON,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const {
  escHtml,
  formatDate,
  formatGroupDate,
  formatApplicantsLabel,
  scoreCls,
  scoreRingHtml,
  groupJobsByDate,
  jobChannel,
  postedPillHtml,
} = sandbox;

function eq(actual, expected, label) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new Error(`${label}: got ${a}, expected ${e}`);
  }
}

eq(escHtml('<a href="x">'), "&lt;a href=&quot;x&quot;&gt;", "escHtml");

eq(formatDate(new Date(2026, 10, 11)), "2026-11-11", "formatDate");
eq(formatGroupDate("1999-01-01"), "1999-01-01", "formatGroupDate past");
const today = formatDate(new Date());
eq(formatGroupDate(today), `Today · ${today}`, "formatGroupDate today");

eq(formatApplicantsLabel("Be among the first 25 applicants"), "first 25", "applicants first-n");
eq(formatApplicantsLabel("Over 200 applicants"), "200+ applicants", "applicants over");
eq(formatApplicantsLabel("47 applicants"), "47 applicants", "applicants n");
eq(formatApplicantsLabel("Be an early applicant"), "early applicant", "applicants early");
eq(formatApplicantsLabel(""), "", "applicants empty");

eq(scoreCls(null), "score-pending", "score pending");
eq(scoreCls(8), "score-hi", "score hi");
eq(scoreCls(6), "score-mid", "score mid");
eq(scoreCls(4), "score-lo", "score lo");

const ringJob = scoreRingHtml({ match_score: 8 });
if (!ringJob.includes("score-ring") || !ringJob.includes("from Run AI on this site")) {
  throw new Error("scoreRingHtml(job) missing shared markup");
}
const ringNum = scoreRingHtml(4.0);
if (!ringNum.includes("score-ring") || !ringNum.includes("Match score 4 of 10")) {
  throw new Error("scoreRingHtml(number) missing shared markup");
}

const groups = groupJobsByDate([
  { title: "A", matched_at: "2026-11-11 10:00" },
  { title: "B", matched_at: "2026-11-11 11:00" },
  { title: "C", matched_at: "2026-11-10 09:00" },
]);
eq(groups.length, 2, "group count");
eq(groups[0][1].length + groups[1][1].length, 3, "group sizes");

eq(jobChannel({ source: "linkedin", origin: "pipeline" }).label, "Scraper", "channel scraper");
eq(jobChannel({ recommended_by: "cursor" }).label, "Cursor", "channel agent");
eq(jobChannel({ source: "manual", origin: "manual" }).label, "Log job", "channel log");
eq(jobChannel({ source: "ats" }).label, "ATS", "channel ats");

const pill = postedPillHtml({ posted_at: "2026-11-11" });
if (!pill.includes("Posted") || !pill.includes("meta-pill")) {
  throw new Error("postedPillHtml missing pill markup");
}

console.log("ok job_list helpers");
