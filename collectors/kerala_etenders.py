"""
Kerala eTenders collector — Award of Contract (AOC) and live tender lists.

Target: https://etenders.kerala.gov.in  (NIC eProcurement / nicgep)

The NIC eProcurement stack is session-driven: almost every list page is reached
by POSTing a `component` / `page` pair against a session cookie obtained from
the landing page. Direct GETs to deep URLs bounce you to the session-expired
page. That is why this uses Playwright rather than plain requests — the session
handshake, the pagination postbacks and the occasional captcha interstitial are
all far cheaper to ride out in a real browser context.

SELECTORS LIVE IN portal_map.py — NIC reskins these portals periodically and
every breakage I have seen is a selector change, not a logic change. Do not put
selectors in this file.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterable

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from ..schema import TenderRecord, Status, WorkType
from ..store import Store
from ..extract import extract_fields
from .portal_map import KERALA_ETENDERS as MAP

log = logging.getLogger(__name__)

BASE = "https://etenders.kerala.gov.in"

# Organisation chains that carry road / bridge / ROB work in Kerala.
# Kept broad on purpose: PWD splits roads and bridges across wings, KRFB and
# RBDCK tender separately, and LSGD carries a surprising volume of small
# bridge work that nobody else is watching.
TARGET_ORGS = [
    "Public Works Department",
    "PWD Roads",
    "PWD Bridges",
    "PWD National Highways",
    "Kerala Road Fund Board",
    "Roads and Bridges Development Corporation of Kerala",
    "Local Self Government Department",
    "Kerala State Transport Project",
    "Irrigation Department",
]

# Title-level prefilter. Cheap, runs before the LLM pass, keeps extraction cost
# proportional to signal. Deliberately loose — a false positive costs one
# extraction call, a false negative costs a missed lead.
RELEVANCE = re.compile(
    r"\b(road|roads|bridge|bridges|culvert|flyover|"
    r"rob|rub|over[\s-]?bridge|overbridge|underpass|bypass|"
    r"bt|bm|bc|metalling|tarring|black[\s-]?top|"
    r"reach|chainage|km\b|widening|re[\s-]?surfacing|"
    r"nh[\s-]?\d+|sh[\s-]?\d+|highway)\b",
    re.IGNORECASE,
)


@dataclass
class RawRow:
    """One row scraped off a list page, before extraction."""
    tender_id: str
    title: str
    organisation: str
    detail_url: str
    list_kind: str                  # "aoc" | "live" | "opened"
    scraped_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    # AOC list pages carry the winner inline, which is the whole point of
    # polling them — no detail fetch needed for the headline fact.
    winner_name: str | None = None
    winner_value: str | None = None
    awarded_on: str | None = None

    def fingerprint(self) -> str:
        """Detects in-place edits. NIC amends rows without changing the ID."""
        blob = "|".join(
            str(x) for x in
            (self.tender_id, self.title, self.winner_name,
             self.winner_value, self.awarded_on)
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


class KeralaETenders:
    """
    Poll cadence is tiered — see poll_tiers in config. Summary:

      aoc      every 15 min   winners land here; this is the money feed
      opened   every 30 min   L1 visible before formal award, days earlier
      live     every  6 hr    new tenders; they do not appear minute-to-minute
      archive  weekly         backfill + catch anything the deltas dropped

    Polling the AOC list at 15 min against ~9 organisations is roughly 900
    page loads a day. That is well within anything a public portal would
    consider abusive, but keep the jitter and the single worker — do not be
    tempted to parallelise across orgs.
    """

    def __init__(self, store: Store, headless: bool = True):
        self.store = store
        self.headless = headless

    async def run(self, kinds: Iterable[str] = ("aoc", "opened", "live")) -> int:
        written = 0
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            ctx = await browser.new_context(
                user_agent=MAP["user_agent"],
                viewport={"width": 1440, "height": 900},
                locale="en-IN",
            )
            page = await ctx.new_page()
            try:
                await self._establish_session(page)
                for kind in kinds:
                    for org in TARGET_ORGS:
                        try:
                            rows = await self._scrape_list(page, kind, org)
                        except PWTimeout:
                            log.warning("timeout on %s/%s, skipping", kind, org)
                            continue
                        written += await self._ingest(rows)
                        await asyncio.sleep(MAP["inter_request_delay"])
            finally:
                await ctx.close()
                await browser.close()
        return written

    async def _establish_session(self, page: Page) -> None:
        """Landing page sets JSESSIONID; everything downstream needs it."""
        await page.goto(BASE, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_selector(MAP["session_ready"], timeout=20_000)

    async def _scrape_list(
        self, page: Page, kind: str, org: str
    ) -> list[RawRow]:
        nav = MAP["lists"][kind]
        await page.goto(
            f"{BASE}{nav['path']}", wait_until="domcontentloaded", timeout=45_000
        )
        if nav.get("org_filter"):
            await page.select_option(nav["org_filter"], label=org)
            await page.click(nav["submit"])
            await page.wait_for_load_state("networkidle", timeout=30_000)

        rows: list[RawRow] = []
        page_no = 1
        while True:
            await page.wait_for_selector(nav["table"], timeout=20_000)
            cells = await page.eval_on_selector_all(
                nav["row"], MAP["row_extractor_js"]
            )
            for c in cells:
                title = (c.get("title") or "").strip()
                if not title or not RELEVANCE.search(title):
                    continue
                rows.append(RawRow(
                    tender_id=(c.get("tender_id") or "").strip(),
                    title=title,
                    organisation=org,
                    detail_url=c.get("href") or "",
                    list_kind=kind,
                    winner_name=(c.get("winner") or None),
                    winner_value=(c.get("value") or None),
                    awarded_on=(c.get("awarded_on") or None),
                ))

            nxt = await page.query_selector(nav["next_page"])
            if not nxt or page_no >= MAP["max_pages"]:
                break
            await nxt.click()
            await page.wait_for_load_state("networkidle", timeout=30_000)
            page_no += 1

        log.info("%s/%s -> %d relevant rows", kind, org, len(rows))
        return rows

    async def _ingest(self, rows: list[RawRow]) -> int:
        """
        Dedupe, then extract only what is new or changed.

        The fingerprint check is what keeps the LLM bill flat. Steady state on
        this portal is a few dozen genuinely new rows a day against a few
        thousand seen; without this you would re-extract the whole board every
        fifteen minutes.
        """
        written = 0
        for row in rows:
            fp = row.fingerprint()
            if self.store.seen(row.tender_id, fp):
                continue

            fields = await extract_fields(
                title=row.title,
                organisation=row.organisation,
                source_url=row.detail_url,
                hint_winner=row.winner_name,
                hint_value=row.winner_value,
            )

            rec = TenderRecord(
                tender_id=row.tender_id,
                source="kerala_etenders",
                authority=row.organisation,
                work_title=row.title,
                source_url=row.detail_url,
                status=self._status_for(row),
                winner_name=row.winner_name,
                winner_value=_money(row.winner_value),
                awarded_on=_date(row.awarded_on),
                first_seen=row.scraped_at,
                last_seen=row.scraped_at,
                **fields,
            )
            self.store.upsert(rec, fingerprint=fp)
            written += 1
        return written

    @staticmethod
    def _status_for(row: RawRow) -> Status:
        if row.list_kind == "aoc" or row.winner_name:
            return Status.AWARDED
        if row.list_kind == "opened":
            return Status.BIDS_OPENED
        return Status.TENDERED


_MONEY = re.compile(r"[\d,]+(?:\.\d+)?")


def _money(raw: str | None) -> int | None:
    """
    NIC writes amounts as '1,23,45,678.00' and occasionally as 'Rs. 12.34 Cr'.
    Indian grouping breaks naive parsers, and the Cr/Lakh suffix silently
    produces numbers off by seven orders of magnitude if you ignore it.
    """
    if not raw:
        return None
    m = _MONEY.search(raw)
    if not m:
        return None
    n = float(m.group(0).replace(",", ""))
    low = raw.lower()
    if "cr" in low:
        n *= 10_000_000
    elif "lakh" in low or "lac" in low:
        n *= 100_000
    return int(round(n))


def _date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%d-%b-%Y %I:%M %p", "%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    log.debug("unparsed date: %r", raw)
    return None
