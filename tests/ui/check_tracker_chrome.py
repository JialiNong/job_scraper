#!/usr/bin/env python3
"""
UI chrome check — layout and overlay contracts, not MongoDB.

A different layer from tests/e2e (those click through the app and assert
database writes). This script opens phone + desktop viewports and checks
that Filters, job details, Log Job selects, and sibling pages stay on
screen and close the way a person expects.

Does not read or write the real Tracker database. Starts a throwaway
mongod + server (same isolation as E2E).

Usage:
  ./scripts/check_ui.sh
  python3 tests/ui/check_tracker_chrome.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))

from harness import (  # noqa: E402
    PASSWORD,
    USERNAME,
    Mongo,
    Server,
    find_mongod,
    launch_browser,
    seed,
)

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright
except ImportError:
    PlaywrightTimeout = Exception  # type: ignore[misc, assignment]
    sync_playwright = None

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 800}
PHONE_SELECT_PAD = "2px 26px 0px 8px"
PHONE_CTRL_H = 30


def js_metrics(page):
    return page.evaluate(
        """() => {
      const pop = document.getElementById('filterPop');
      const fScrim = document.getElementById('filterScrim');
      const pScrim = document.getElementById('panelScrim');
      const body = document.querySelector('.card.expanded .card-body');
      const bar = document.querySelector('.batch-fetch-bar.select-mode');
      const tl = document.querySelector('.tl-modal-backdrop');
      const box = (el) => el && !el.hidden ? el.getBoundingClientRect().toJSON() : null;
      const inView = (b) => {
        if (!b) return false;
        return b.left >= -1 && b.right <= innerWidth + 1
            && b.top >= -1 && b.bottom <= innerHeight + 1 && b.width > 0 && b.height > 0;
      };
      const popBox = box(pop);
      const bodyBox = body ? body.getBoundingClientRect().toJSON() : null;
      const selectStyle = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const cs = getComputedStyle(el);
        return {
          h: Math.round(el.getBoundingClientRect().height),
          pad: cs.padding,
          appearance: cs.appearance || cs.webkitAppearance,
          fontSize: cs.fontSize,
        };
      };
      return {
        vw: innerWidth, vh: innerHeight,
        scrollW: document.documentElement.scrollWidth,
        clientW: document.documentElement.clientWidth,
        lock: document.documentElement.classList.contains('ui-lock'),
        overflowX: getComputedStyle(document.documentElement).overflowX,
        popHidden: !pop || pop.hidden,
        popPos: pop ? getComputedStyle(pop).position : null,
        popBox, popInView: inView(popBox),
        filterScrimHidden: !fScrim || fScrim.hidden,
        panelScrimHidden: !pScrim || pScrim.hidden,
        panelScrimZ: pScrim ? getComputedStyle(pScrim).zIndex : null,
        expanded: !!document.querySelector('.card.expanded'),
        bodyBox, bodyInView: inView(bodyBox),
        bodyTop: body ? getComputedStyle(body).top : null,
        bodyZ: body ? getComputedStyle(body).zIndex : null,
        filterBtn: (document.getElementById('filterBtn') || {}).textContent || '',
        customDateHidden: !document.getElementById('customDateGroup')
          || document.getElementById('customDateGroup').classList.contains('hidden-filter'),
        customScoreHidden: !document.getElementById('customScoreGroup')
          || document.getElementById('customScoreGroup').classList.contains('hidden-filter'),
        batchVisible: !!(bar && getComputedStyle(bar).display !== 'none'),
        batchInView: inView(bar ? bar.getBoundingClientRect().toJSON() : null),
        tlVisible: !!tl,
        tlZ: tl ? getComputedStyle(tl).zIndex : null,
        jobStatus: selectStyle('#jobStatus'),
        recommendedBy: selectStyle('#recommendedBy'),
        timeoutsSelect: selectStyle('.toolbar select'),
        sideSelect: selectStyle('.status-side-select'),
        headerScore: [...document.querySelectorAll(
          '.card.expanded .card-header .score-badge, .card.expanded .card-header .score-ring'
        )].filter(el => getComputedStyle(el).display !== 'none').length,
        localScore: document.querySelectorAll('.card.expanded .ai-local .score-ring').length,
        listRingBeforeTitle: (() => {
          const row = document.querySelector('.job-grid .card-row');
          if (!row) return false;
          const ring = row.querySelector(':scope > .score-ring');
          const title = row.querySelector('.row-title');
          return !!(ring && title && (ring.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING));
        })(),
        statusOutline: (() => {
          const el = document.querySelector('.status-pill');
          if (!el) return null;
          const cs = getComputedStyle(el);
          return { bg: cs.backgroundColor, bw: cs.borderTopWidth };
        })(),
        countryInBody: document.querySelectorAll('.card.expanded .card-body .country-pill').length,
        panelOrder: [...document.querySelectorAll(
          '.card.expanded .card-split-match > .company-history, .card.expanded .card-split-match > .progress-panel, .card.expanded .card-split-match > .recommend-reason, .card.expanded .card-split-match > .ai-local, .card.expanded .card-split-match > .job-description'
        )].map(el => el.className.split(/\\s+/)[0]),
        summaryClamp: (() => {
          const el = document.querySelector('.card.expanded .ai-local-lead .card-summary');
          if (!el) return null;
          const cs = getComputedStyle(el);
          return { clamp: cs.webkitLineClamp, overflow: cs.overflow };
        })(),
      };
    }"""
    )


class Runner:
    def __init__(self, out: Path):
        self.out = out
        self.failed = []
        self.passed = []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed.append(name)
            print(f"  ok   {name}")
        else:
            self.failed.append((name, detail))
            print(f"  FAIL {name}  {detail}")

    def shot(self, page, name):
        path = self.out / name
        page.screenshot(path=str(path), full_page=False)
        return path


def login_state(browser, base):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(base + "/login")
    page.fill("#username", USERNAME)
    page.fill("#password", PASSWORD)
    page.click(".btn-login")
    page.wait_for_url(base + "/")
    state = ctx.storage_state()
    ctx.close()
    return state


def new_page(browser, state, *, phone):
    kwargs = {"storage_state": state, "viewport": PHONE if phone else DESKTOP}
    if phone:
        kwargs.update(is_mobile=True, has_touch=True)
    ctx = browser.new_context(**kwargs)
    ctx.set_default_timeout(12_000)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    return ctx, page, errors


def wait_cards(page):
    page.locator(".card-row").first.wait_for(state="visible")
    page.wait_for_timeout(200)


def cards_face(page):
    return page.evaluate(
        """() => {
          const vis = (el) => {
            if (!el) return false;
            const cs = getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden') return false;
            const r = el.getBoundingClientRect();
            return r.width > 1 && r.height > 1;
          };
          const card = document.querySelector('.job-grid.layout-cards .card');
          if (!card) return { hasCard: false };
          const title = card.querySelector('.card-row .row-title') || card.querySelector('.card-title');
          const apply = card.querySelector('.card-actions-row, a.btn-apply');
          const extra = card.querySelector('.progress-panel, .reason-box, .card-meta');
          const body = card.querySelector('.card-body');
          const closer = card.querySelector('.panel-close');
          const row = card.querySelector('.card-row');
          return {
            hasCard: true,
            expanded: card.classList.contains('expanded'),
            rowRing: vis(card.querySelector('.card-row .score-ring')),
            headerRing: vis(card.querySelector('.card-header .score-ring')),
            title: vis(title),
            titleSize: title ? parseFloat(getComputedStyle(title).fontSize) : 0,
            company: vis(card.querySelector('.row-company, .card-company')),
            loc: vis(card.querySelector('.row-loc')),
            locInSub: vis(card.querySelector('.card-row .row-sub .row-loc')),
            locInMeta: vis(card.querySelector('.card-row .row-meta .row-loc')),
            apply: vis(apply),
            extra: vis(extra),
            rowVisible: vis(row),
            drawer: body ? getComputedStyle(body).position === 'fixed' : false,
            closer: vis(closer),
            bodyTop: body ? getComputedStyle(body).top : null,
          };
        }"""
    )


def check_got_interview_chip(page, run: Runner, prefix: str):
    chip = page.locator('.stat-chip[data-status="got_interview"]')
    run.check(f"{prefix}: Got interview chip is visible", chip.is_visible())
    chip.click()
    page.wait_for_timeout(150)
    run.check(f"{prefix}: Got interview chip activates on tap",
              "active" in (chip.get_attribute("class") or ""),
              f"class={chip.get_attribute('class')}")
    page.locator('.stat-chip[data-status="active"]').click()
    wait_cards(page)


def nav_targets(page):
    return page.evaluate(
        """() => {
          const map = {};
          document.querySelectorAll('.nav a').forEach(a => {
            map[a.getAttribute('href')] = a.getAttribute('target') || '';
          });
          return map;
        }"""
    )


def open_filters(page):
    page.click("#filterBtn")
    page.wait_for_timeout(200)


def tap_scrim_top(page, scrim_id):
    loc = page.locator(f"#{scrim_id}")
    loc.wait_for(state="visible")
    loc.click(position={"x": 16, "y": 12}, force=True)
    page.wait_for_timeout(200)


def status_outlined(info):
    if not info:
        return False
    bg = (info.get("bg") or "").replace(" ", "")
    try:
        bw = float(str(info.get("bw") or "0").replace("px", "") or 0)
    except ValueError:
        bw = 0
    transparent = bg in ("transparent", "rgba(0,0,0,0)", "rgb(0,0,0,0)")
    return transparent and bw >= 1


def summary_unclamped(info):
    if not info:
        return False
    clamp = str(info.get("clamp") or "").strip().lower()
    return clamp in ("none", "unset", "")


def phone_select_ok(style):
    if not style:
        return False, "missing"
    reasons = []
    if style["h"] != PHONE_CTRL_H:
        reasons.append(f"height {style['h']}px")
    if style["pad"] != PHONE_SELECT_PAD:
        reasons.append(f"padding {style['pad']}")
    if style["appearance"] not in ("none",):
        reasons.append(f"appearance {style['appearance']}")
    if style["fontSize"] != "16px":
        reasons.append(f"font {style['fontSize']}")
    return (not reasons, ", ".join(reasons))


def run_phone(page, base, run: Runner):
    page.goto(base + "/")
    wait_cards(page)
    nav = nav_targets(page)
    run.check("phone tracker nav: sibling pages open in a new tab",
              nav.get("/manual-apply") == "_blank" and nav.get("/timeouts") == "_blank"
              and nav.get("/unmatched") == "_blank" and nav.get("/") == "",
              f"nav={nav}")

    open_filters(page)
    m = js_metrics(page)
    run.shot(page, "phone-filters.png")
    run.check("phone filters: sheet in viewport", m["popInView"] and m["popPos"] == "fixed",
              f"pos={m['popPos']} in={m['popInView']} box={m['popBox']}")
    run.check("phone filters: no horizontal page overflow",
              m["scrollW"] <= m["clientW"] + 2,
              f"scrollW={m['scrollW']} clientW={m['clientW']}")
    run.check("phone filters: scrim + scroll lock",
              m["lock"] and not m["filterScrimHidden"],
              f"lock={m['lock']} scrimHidden={m['filterScrimHidden']}")

    page.select_option("#sourceFilter", "linkedin")
    page.wait_for_timeout(100)
    m = js_metrics(page)
    run.check("phone filters: count label", m["filterBtn"].strip() == "Filters (1)",
              f"text={m['filterBtn']!r}")

    page.select_option("#timePreset", "custom")
    page.wait_for_timeout(150)
    m = js_metrics(page)
    run.check("phone filters: custom date stays in viewport",
              (not m["customDateHidden"]) and m["popInView"],
              f"dateHidden={m['customDateHidden']} in={m['popInView']}")

    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    m = js_metrics(page)
    run.check("phone filters: Escape closes", m["popHidden"] and not m["lock"],
              f"hidden={m['popHidden']} lock={m['lock']}")

    open_filters(page)
    tap_scrim_top(page, "filterScrim")
    m = js_metrics(page)
    run.check("phone filters: tap scrim closes", m["popHidden"] and not m["lock"],
              f"hidden={m['popHidden']} lock={m['lock']}")

    m = js_metrics(page)
    run.shot(page, "phone-list.png")
    run.check("phone list: score ring before title", m["listRingBeforeTitle"])
    run.check("phone list: status pill is outlined",
              status_outlined(m.get("statusOutline")),
              f"outline={m.get('statusOutline')}")
    check_got_interview_chip(page, run, "phone list")

    page.click(".card-row")
    page.wait_for_timeout(350)
    m = js_metrics(page)
    run.shot(page, "phone-details.png")
    run.check("phone details: sheet in viewport", m["expanded"] and m["bodyInView"],
              f"expanded={m['expanded']} in={m['bodyInView']}")
    run.check("phone details: scrim + scroll lock",
              m["lock"] and not m["panelScrimHidden"],
              f"lock={m['lock']} scrimHidden={m['panelScrimHidden']}")
    run.check("phone details: mask band above the sheet",
              m["bodyTop"] not in (None, "0px", "auto"),
              f"top={m['bodyTop']}")
    run.check("phone details: no country pill under the title",
              m["countryInBody"] == 0, f"count={m['countryInBody']}")
    run.check("phone details: score ring lives in AI match, not the title row",
              m["headerScore"] == 0 and m["localScore"] == 1,
              f"header={m['headerScore']} local={m['localScore']}")
    run.check("phone details: AI summary is not ellipsis-clamped",
              summary_unclamped(m.get("summaryClamp")),
              f"clamp={m.get('summaryClamp')}")
    order = m.get("panelOrder") or []
    run.check("phone details: AI match before original description",
              "ai-local" in order and "job-description" in order
              and order.index("ai-local") < order.index("job-description"),
              f"order={order}")
    page.locator(".card.expanded .ai-local").scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    run.shot(page, "phone-details-ai.png")

    tap_scrim_top(page, "panelScrim")
    m = js_metrics(page)
    run.check("phone details: tap scrim closes", (not m["expanded"]) and not m["lock"],
              f"expanded={m['expanded']} lock={m['lock']}")

    page.click("#selectModeBtn")
    page.wait_for_timeout(200)
    m = js_metrics(page)
    run.shot(page, "phone-batch.png")
    run.check("phone batch bar: on screen", m["batchVisible"] and m["batchInView"],
              f"visible={m['batchVisible']} in={m['batchInView']}")
    page.click("#selectModeBtn")

    page.goto(base + "/manual-apply")
    page.locator("#jobStatus").wait_for(state="visible")
    nav = nav_targets(page)
    run.check("phone Log Job nav: sibling pages stay in this tab",
              nav.get("/timeouts") == "" and nav.get("/unmatched") == "" and nav.get("/") == "",
              f"nav={nav}")
    m = js_metrics(page)
    run.shot(page, "phone-log-job.png")
    ok, why = phone_select_ok(m["jobStatus"])
    run.check("phone Log Job: Status select recipe", ok, why)
    ok, why = phone_select_ok(m["recommendedBy"])
    run.check("phone Log Job: Recommended by select recipe", ok, why)

    page.goto(base + "/unmatched")
    wait_cards(page)
    nav = nav_targets(page)
    run.check("phone unmatched nav: sibling pages stay in this tab",
              nav.get("/manual-apply") == "" and nav.get("/timeouts") == "" and nav.get("/") == "",
              f"nav={nav}")
    m = js_metrics(page)
    run.shot(page, "phone-unmatched-list.png")
    run.check("phone Unmatched list: score ring before title", m["listRingBeforeTitle"])
    open_filters(page)
    page.select_option("#scorePreset", "custom")
    page.wait_for_timeout(150)
    m = js_metrics(page)
    run.shot(page, "phone-unmatched-filters.png")
    run.check("phone Unmatched filters: sheet in viewport",
              m["popInView"] and m["popPos"] == "fixed" and not m["customScoreHidden"],
              f"in={m['popInView']} pos={m['popPos']} scoreHidden={m['customScoreHidden']}")
    run.check("phone Unmatched filters: lock + scrim",
              m["lock"] and not m["filterScrimHidden"],
              f"lock={m['lock']} scrimHidden={m['filterScrimHidden']}")
    tap_scrim_top(page, "filterScrim")

    page.goto(base + "/timeouts")
    page.locator(".toolbar select").first.wait_for(state="visible")
    nav = nav_targets(page)
    run.check("phone timeouts nav: sibling pages stay in this tab",
              nav.get("/manual-apply") == "" and nav.get("/unmatched") == "" and nav.get("/") == "",
              f"nav={nav}")
    m = js_metrics(page)
    run.shot(page, "phone-timeouts.png")
    ok, why = phone_select_ok(m["timeoutsSelect"])
    run.check("phone Timeouts: toolbar select recipe", ok, why)


def run_desktop(page, base, run: Runner):
    page.goto(base + "/")
    wait_cards(page)
    nav = nav_targets(page)
    run.check("desktop tracker nav: sibling pages open in a new tab",
              nav.get("/manual-apply") == "_blank" and nav.get("/timeouts") == "_blank"
              and nav.get("/unmatched") == "_blank" and nav.get("/") == "",
              f"nav={nav}")

    open_filters(page)
    m = js_metrics(page)
    run.shot(page, "desktop-filters.png")
    run.check("desktop filters: dropdown (not a sheet)",
              m["popPos"] == "absolute" and m["popInView"] and m["filterScrimHidden"] and not m["lock"],
              f"pos={m['popPos']} scrimHidden={m['filterScrimHidden']} lock={m['lock']}")

    page.locator(".brand").click()
    page.wait_for_timeout(150)
    m = js_metrics(page)
    run.check("desktop filters: click outside closes", m["popHidden"],
              f"hidden={m['popHidden']}")

    m = js_metrics(page)
    run.shot(page, "desktop-list.png")
    run.check("desktop list: score ring before title", m["listRingBeforeTitle"])
    run.check("desktop list: status pill is outlined",
              status_outlined(m.get("statusOutline")),
              f"outline={m.get('statusOutline')}")
    check_got_interview_chip(page, run, "desktop list")

    page.click(".card-row")
    page.wait_for_timeout(350)
    m = js_metrics(page)
    run.shot(page, "desktop-details.png")
    run.check("desktop details: side drawer, no mask, page unlocked",
              m["expanded"] and m["bodyTop"] == "0px" and m["panelScrimHidden"] and not m["lock"],
              f"top={m['bodyTop']} scrimHidden={m['panelScrimHidden']} lock={m['lock']}")
    run.check("desktop details: score ring lives in AI match, not the title row",
              m["headerScore"] == 0 and m["localScore"] == 1,
              f"header={m['headerScore']} local={m['localScore']}")
    run.check("desktop details: AI summary is not ellipsis-clamped",
              summary_unclamped(m.get("summaryClamp")),
              f"clamp={m.get('summaryClamp')}")
    order = m.get("panelOrder") or []
    run.check("desktop details: progress before AI, description last",
              "progress-panel" in order and "ai-local" in order and "job-description" in order
              and order.index("progress-panel") < order.index("ai-local") < order.index("job-description"),
              f"order={order}")
    page.locator(".card.expanded .ai-local").scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    run.shot(page, "desktop-details-ai.png")

    page.get_by_role("button", name="+ Applied").click()
    page.locator(".tl-modal-backdrop").wait_for(state="visible")
    m = js_metrics(page)
    run.shot(page, "desktop-timeline-modal.png")
    try:
        tl_z, body_z = int(m["tlZ"] or 0), int(m["bodyZ"] or 0)
    except ValueError:
        tl_z, body_z = 0, 0
    run.check("desktop timeline modal sits above the drawer",
              m["tlVisible"] and tl_z > body_z,
              f"tlZ={m['tlZ']} bodyZ={m['bodyZ']}")
    page.click(".tl-modal .tl-cancel")

    closer = page.locator(".card.expanded .panel-close")
    if closer.count():
        closer.click()
    page.wait_for_timeout(200)
    page.get_by_role("button", name="Cards").click()
    page.wait_for_timeout(250)
    run.shot(page, "desktop-cards.png")
    face = cards_face(page)
    run.check("desktop cards: collapsed to score, title, company, location",
              face.get("hasCard") and face.get("rowRing") and face.get("title")
              and face.get("company") and face.get("locInSub") and not face.get("locInMeta")
              and not face.get("expanded") and not face.get("apply"),
              f"face={face}")
    run.check("desktop cards: title reads as a headline",
              face.get("titleSize", 0) >= 22,
              f"size={face.get('titleSize')}")
    before_h = page.evaluate(
        "() => document.querySelector('.job-grid.layout-cards .card').getBoundingClientRect().height"
    )
    page.locator(".job-grid.layout-cards .card-row").first.click()
    page.wait_for_timeout(250)
    run.shot(page, "desktop-cards-expanded.png")
    face = cards_face(page)
    after_h = page.evaluate(
        "() => document.querySelector('.job-grid.layout-cards .card.expanded').getBoundingClientRect().height"
    )
    run.check("desktop cards: click opens a side sheet",
              face.get("expanded") and face.get("drawer") and face.get("bodyTop") == "0px"
              and face.get("closer") and face.get("rowVisible"),
              f"face={face}")
    run.check("desktop cards: board card stays short",
              face.get("drawer") and after_h < before_h + 48,
              f"before={before_h} after={after_h}")
    m = js_metrics(page)
    run.check("desktop cards: sheet score lives in AI match, not the title row",
              m["headerScore"] == 0 and m["localScore"] == 1,
              f"header={m['headerScore']} local={m['localScore']}")
    page.locator(".job-grid.layout-cards .card.expanded .panel-close").click()
    page.wait_for_timeout(200)
    face = cards_face(page)
    run.check("desktop cards: Close returns to the board",
              not face.get("expanded") and face.get("rowVisible") and not face.get("apply"),
              f"face={face}")

    tracker_url = page.url
    with page.expect_popup() as pop_info:
        page.locator('.nav a[href="/unmatched"]').click()
    um = pop_info.value
    um.wait_for_url("**/unmatched")
    wait_cards(um)
    run.check("desktop tracker nav: Unmatched opens a new tab",
              "/unmatched" in um.url and page.url == tracker_url,
              f"tracker={page.url} unmatched={um.url}")
    nav = nav_targets(um)
    run.check("desktop unmatched nav: sibling pages stay in this tab",
              nav.get("/manual-apply") == "" and nav.get("/timeouts") == "" and nav.get("/") == "",
              f"nav={nav}")
    um.get_by_role("button", name="Cards").click()
    um.wait_for_timeout(250)
    run.shot(um, "desktop-unmatched-cards.png")
    face = cards_face(um)
    run.check("desktop unmatched cards: collapsed to score, title, company, location",
              face.get("hasCard") and face.get("rowRing") and face.get("title")
              and face.get("company") and face.get("locInSub") and not face.get("locInMeta")
              and not face.get("expanded")
              and not face.get("extra"),
              f"face={face}")
    before_h = um.evaluate(
        "() => document.querySelector('.job-grid.layout-cards .card').getBoundingClientRect().height"
    )
    um.locator(".job-grid.layout-cards .card-row").first.click()
    um.wait_for_timeout(250)
    run.shot(um, "desktop-unmatched-cards-expanded.png")
    face = cards_face(um)
    after_h = um.evaluate(
        "() => document.querySelector('.job-grid.layout-cards .card.expanded').getBoundingClientRect().height"
    )
    run.check("desktop unmatched cards: click opens a side sheet",
              face.get("expanded") and face.get("drawer") and face.get("bodyTop") == "0px"
              and face.get("closer") and face.get("rowVisible"),
              f"face={face}")
    run.check("desktop unmatched cards: board card stays short",
              face.get("drawer") and after_h < before_h + 48,
              f"before={before_h} after={after_h}")
    um.locator(".job-grid.layout-cards .card.expanded .panel-close").click()
    um.wait_for_timeout(200)
    face = cards_face(um)
    run.check("desktop unmatched cards: Close returns to the board",
              not face.get("expanded") and face.get("rowVisible"),
              f"face={face}")


def main():
    if sync_playwright is None:
        print("FAIL: playwright is not installed")
        return 1
    if find_mongod() is None:
        print("FAIL: mongod is not installed")
        return 1

    out = Path(os.environ.get("UI_CHECK_OUT") or os.path.join(tempfile.gettempdir(), "job-ui-chrome"))
    out.mkdir(parents=True, exist_ok=True)
    run = Runner(out)
    print(f"UI chrome check  screenshots → {out}")

    mongo = Mongo()
    try:
        seed(mongo.db)
        server = Server(mongo.uri)
        try:
            with sync_playwright() as pw:
                browser = launch_browser(pw)
                state = login_state(browser, server.base)

                print("phone")
                ctx, page, err = new_page(browser, state, phone=True)
                try:
                    run_phone(page, server.base, run)
                    run.check("phone: no JS errors", not err, "; ".join(err))
                except PlaywrightTimeout as exc:
                    run.check("phone: run finished", False, str(exc).split("\n")[0])
                finally:
                    ctx.close()

                print("desktop")
                ctx, page, err = new_page(browser, state, phone=False)
                try:
                    run_desktop(page, server.base, run)
                    run.check("desktop: no JS errors", not err, "; ".join(err))
                except PlaywrightTimeout as exc:
                    run.check("desktop: run finished", False, str(exc).split("\n")[0])
                finally:
                    ctx.close()
                browser.close()
        finally:
            server.stop()
    finally:
        mongo.stop()

    print()
    print(f"{len(run.passed)} passed, {len(run.failed)} failed")
    if run.failed:
        print("failures:")
        for name, detail in run.failed:
            print(f"  - {name}: {detail}")
        (out / "failures.json").write_text(json.dumps(run.failed, indent=2))
        return 1
    print("UI chrome check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
