"""
The one contract every collector writes to.

Kerala eTenders, CPPP/NHAI, KIIFB, RBDCK and the news watcher all produce
TenderRecord. The app never learns that these sources exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    """
    The pipeline. Records advance; they are never re-created.

    A tender seen in ANNOUNCED six weeks ago surfaces again in the user's feed
    the moment it reaches AWARDED. That re-surfacing is the product.
    """
    ANNOUNCED = "announced"      # news / KIIFB sanction, no tender yet
    TENDERED = "tendered"        # NIT published, bids open
    BIDS_OPENED = "bids_opened"  # financial bids opened, L1 visible
    AWARDED = "awarded"          # AOC published, winner confirmed
    IN_PROGRESS = "in_progress"  # work order issued
    COMPLETED = "completed"
    CANCELLED = "cancelled"      # retendered or scrapped — happens often


class WorkType(str, Enum):
    ROAD = "road"
    BRIDGE = "bridge"
    ROB = "rob"                  # rail over bridge
    RUB = "rub"
    FLYOVER = "flyover"
    BYPASS = "bypass"
    SERVICE_ROAD = "service_road"
    CULVERT = "culvert"
    OTHER = "other"


@dataclass
class TenderRecord:
    # identity
    tender_id: str
    source: str
    authority: str
    work_title: str
    source_url: str

    # classification — filled by the extraction pass
    work_type: WorkType = WorkType.OTHER
    road_name: str | None = None          # "NH66", "SH-1", "MC Road"
    stretch: str | None = None            # "Ramanattukara - Valanchery"
    chainage_from: float | None = None
    chainage_to: float | None = None
    districts: list[str] = field(default_factory=list)
    lat: float | None = None
    lng: float | None = None

    # money
    estimated_cost: int | None = None     # PAC, rupees
    emd: int | None = None

    # dates
    bid_due_date: datetime | None = None
    bid_open_date: datetime | None = None

    # outcome
    status: Status = Status.TENDERED
    winner_name: str | None = None
    winner_value: int | None = None
    awarded_on: datetime | None = None

    # Precomputed in extract.py, never on the phone. See note 2 there.
    display_value: str | None = None
    display_variance: str | None = None

    # bookkeeping
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    confidence: float = 1.0               # < 0.7 routes to manual review

    @property
    def variance_pct(self) -> float | None:
        """
        Percent above (+) or below (-) the estimate. In Kerala road work,
        deep negative variance is routine and a large positive one usually
        means a retender after a failed first round. Both are signals worth
        surfacing, so this is a first-class field in the UI, not a footnote.
        """
        if not self.estimated_cost or not self.winner_value:
            return None
        return round(
            (self.winner_value - self.estimated_cost)
            / self.estimated_cost * 100, 2
        )

    @property
    def is_nh(self) -> bool:
        return bool(self.road_name and self.road_name.upper().startswith("NH"))
