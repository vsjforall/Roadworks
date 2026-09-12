"""
Entry point.

  python -m kerala_infra.run seed      load sample data, see the app working
  python -m kerala_infra.run serve     start the API + PWA on :8000
  python -m kerala_infra.run collect   one collection pass (needs selectors)
  python -m kerala_infra.run watch     continuous, on the tiered schedule
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from .store import Store
from .schema import TenderRecord, Status, WorkType

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("kerala_infra")

# Tiered cadence. The three clocks in this data are genuinely different and
# polling them all at 15 minutes wastes requests without gaining anything.
TIERS = {"aoc": 15 * 60, "opened": 30 * 60, "live": 6 * 3600}


def seed(db: str) -> None:
    """
    Realistic sample records so the app is usable before the scrapers work.
    Shaped exactly like real extractor output, so swapping in live data
    changes nothing downstream.
    """
    s = Store(db)
    now = datetime.now(timezone.utc)
    rows = [
        ("NHAI/KL/66/P3-2026", "NHAI PIU Kozhikode",
         "Six-laning of NH66 from Ramanattukara to Valanchery, package 3",
         WorkType.ROAD, "NH66", "Ramanattukara - Valanchery",
         ["Malappuram", "Kozhikode"], 13_40_00_00_000, 12_84_00_00_000,
         "Megha Engineering and Infrastructure", Status.AWARDED, 12),
        ("RBDCK/ROB/2026/17", "RBDCK",
         "Construction of rail over bridge at Chandera level crossing",
         WorkType.ROB, None, None, ["Kasaragod"],
         45_82_00_000, 46_78_00_000,
         "Cherian Varkey Construction", Status.AWARDED, 41),
        ("PWD/BR/KNR/2026/204", "PWD Bridges",
         "Reconstruction of approach road to Thalassery-Mahe bridge",
         WorkType.BRIDGE, "SH-38", "Thalassery - Mahe", ["Kannur"],
         20_60_00_000, 18_21_00_000,
         "Uralungal Labour Contract Cooperative Society",
         Status.BIDS_OPENED, 180),
        ("PWD/RD/KTM/2026/881", "PWD Roads",
         "BM and BC works on MC Road, reach km 44 to km 52",
         WorkType.ROAD, "MC Road", "km 44 - km 52", ["Kottayam"],
         8_12_00_000, 7_40_00_000, "KMC Constructions",
         Status.AWARDED, 128),
        ("NHAI/KL/66/SR-11", "NHAI PIU Alappuzha",
         "Service road and storm water drain at NH66 Kollam bypass junction",
         WorkType.SERVICE_ROAD, "NH66", "Kollam bypass", ["Kollam"],
         22_60_00_000, None, None, Status.BIDS_OPENED, 300),
        ("PWD/RD/TVM/2026/1032", "PWD Roads",
         "Widening and strengthening of Kazhakkoottam - Kovalam bypass reach 2",
         WorkType.BYPASS, "NH66", "Kazhakkoottam - Kovalam",
         ["Thiruvananthapuram"], 96_40_00_000, 91_12_00_000,
         "RDS Projects", Status.AWARDED, 420),
        ("LSGD/TSR/BR/2026/55", "Local Self Government Department",
         "Construction of culvert and approach at Chalakudy puzha crossing",
         WorkType.CULVERT, None, None, ["Thrissur"],
         1_86_00_000, 1_74_00_000, "Sreekrishna Builders",
         Status.AWARDED, 900),
    ]

    from .extract import inr, variance_label
    for tid, auth, title, wt, road, stretch, dist, est, won, win, st, mins \
            in rows:
        seen = now - timedelta(minutes=mins)
        rec = TenderRecord(
            tender_id=tid, source="seed", authority=auth, work_title=title,
            source_url="https://etenders.kerala.gov.in/", work_type=wt,
            road_name=road, stretch=stretch, districts=dist,
            estimated_cost=est, status=st, winner_name=win,
            winner_value=won,
            awarded_on=seen if st == Status.AWARDED else None,
            first_seen=seen, last_seen=seen, confidence=0.94,
            display_value=inr(won or est),
            display_variance=variance_label(est, won),
        )
        s.upsert(rec, fingerprint=f"seed-{tid}")
    log.info("seeded %d records into %s", len(rows), db)


async def collect(db: str, kinds) -> None:
    from .collectors.kerala_etenders import KeralaETenders
    n = await KeralaETenders(Store(db)).run(kinds=kinds)
    log.info("wrote %d new or changed records", n)
    if n == 0:
        log.warning(
            "Zero rows. Almost certainly the selectors in "
            "collectors/portal_map.py need correcting against the live site. "
            "Re-run with --headed and inspect the tender table."
        )


async def watch(db: str) -> None:
    last = {k: 0.0 for k in TIERS}
    loop = asyncio.get_event_loop()
    while True:
        due = [k for k, gap in TIERS.items() if loop.time() - last[k] >= gap]
        if due:
            await collect(db, due)
            for k in due:
                last[k] = loop.time()
        await asyncio.sleep(60)


def main() -> None:
    p = argparse.ArgumentParser(prog="kerala_infra")
    p.add_argument("cmd", choices=["seed", "serve", "collect", "watch"])
    p.add_argument("--db", default="kerala_infra.db")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--headed", action="store_true")
    a = p.parse_args()

    if a.cmd == "seed":
        seed(a.db)
    elif a.cmd == "serve":
        import uvicorn
        import os
        os.environ["KI_DB"] = a.db
        uvicorn.run("kerala_infra.api:app", host="0.0.0.0", port=a.port)
    elif a.cmd == "collect":
        asyncio.run(collect(a.db, ("aoc", "opened", "live")))
    elif a.cmd == "watch":
        asyncio.run(watch(a.db))


if __name__ == "__main__":
    main()
