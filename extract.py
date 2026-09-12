"""
Extraction: messy government tender text -> clean TenderRecord fields.

Four decisions here are made for the app's sake, not the pipeline's.

1. NEVER GUESS A NUMBER. If the estimate is not in the source text, the field
   is None. A wrong figure in a feed people use to chase contracts is worse
   than a blank one, because a blank prompts a tap through to the source and a
   wrong number does not. Confidence below REVIEW_FLOOR routes to review and
   the app shows the raw title instead of the pretty parse.

2. DISPLAY STRINGS ARE COMPUTED HERE, NOT ON THE PHONE. "Rs 1,284 Cr" ships
   pre-rendered. The list view must never call a formatter mid-scroll — that
   is the single biggest cause of the jitter we are trying to avoid, and it
   also means Indian crore/lakh formatting is decided in one place rather than
   reimplemented in Flutter and in the PWA.

3. DISTRICTS ARE CANONICALISED. "TVM", "Trivandrum" and "Thiruvananthapuram"
   are one filter value or the district filter is useless.

4. CHEAP MODEL FIRST, ESCALATE ON DOUBT. Haiku handles the ~95% of rows that
   are formulaic. Anything it is unsure about goes to Sonnet. Running Sonnet
   on everything costs roughly 8x for no visible gain.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import httpx

from .schema import WorkType

log = logging.getLogger(__name__)

API = "https://api.anthropic.com/v1/messages"
FAST_MODEL = "claude-haiku-4-5-20251001"
DEEP_MODEL = "claude-sonnet-5"
REVIEW_FLOOR = 0.70
BATCH_SIZE = 25

DISTRICTS = [
    "Thiruvananthapuram", "Kollam", "Pathanamthitta", "Alappuzha",
    "Kottayam", "Idukki", "Ernakulam", "Thrissur", "Palakkad",
    "Malappuram", "Kozhikode", "Wayanad", "Kannur", "Kasaragod",
]

# Every spelling I have seen on Kerala tender documents, mapped home.
DISTRICT_ALIASES = {
    "tvm": "Thiruvananthapuram", "trivandrum": "Thiruvananthapuram",
    "tvpm": "Thiruvananthapuram", "quilon": "Kollam",
    "alleppey": "Alappuzha", "aleppey": "Alappuzha",
    "cochin": "Ernakulam", "kochi": "Ernakulam", "ekm": "Ernakulam",
    "trichur": "Thrissur", "tcr": "Thrissur",
    "palghat": "Palakkad", "pkd": "Palakkad",
    "calicut": "Kozhikode", "kkd": "Kozhikode",
    "cannanore": "Kannur", "knr": "Kannur",
    "kasargod": "Kasaragod", "kasargode": "Kasaragod",
    "manjeri": "Malappuram", "mlp": "Malappuram",
}

SYSTEM = f"""You extract structured data from Kerala public-works tender notices.

Return ONLY a JSON array. No prose, no markdown fences. One object per input
item, in the same order, each with the input's "ref" echoed back.

Fields per object:
  ref            echo the input ref exactly
  work_type      one of: road, bridge, rob, rub, flyover, bypass,
                 service_road, culvert, other
  road_name      "NH66", "NH544", "SH-1", "MC Road", "Kollam bypass",
                 or null. Normalise "N.H.66"/"NH 66"/"NH-66" to "NH66".
  stretch        "Ramanattukara - Valanchery" style, or null
  chainage_from  number in km, or null
  chainage_to    number in km, or null
  districts      array of Kerala districts. Full official names only,
                 from: {", ".join(DISTRICTS)}
                 Infer from place names when the district is not stated
                 (Valanchery -> Malappuram). Empty array if genuinely unclear.
  estimated_cost integer rupees, or null
  emd            integer rupees, or null
  winner_name    contractor name, or null
  winner_value   integer rupees, or null
  confidence     0.0-1.0, your own read on the whole object

CRITICAL RULES

Never infer a monetary figure that is not written in the source. If the text
does not state an amount, the field is null. Do not estimate from project
scale, do not carry a number over from another item.

Amounts use Indian conventions. "1,23,45,678" is 12345678. "Rs. 12.5 Cr" is
125000000. "45 Lakh" is 4500000. Getting this wrong by a factor of 100 is the
most damaging error you can make here.

Titles mix English, Malayalam and transliterated Malayalam. Place names are
often transliterated inconsistently. Extract the place as written, but map it
to the correct district.

PWD abbreviations: BM/BC/BT are surfacing works (work_type road). "Reach"
introduces a stretch. "ROB" is rail over bridge, "RUB" is rail under bridge.
A "bypass" is work_type bypass even when the title also says road.

Set confidence below 0.7 whenever the title is truncated, the work is bundled
across several unrelated locations, or you are unsure about a money figure.
Low confidence is useful to us. A confident wrong answer is not."""


def _client() -> httpx.AsyncClient:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return httpx.AsyncClient(
        timeout=90,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )


class _Cache:
    """
    Content-hash cache. Re-running a collector after a crash, or backfilling,
    must not re-bill extraction for rows already parsed.
    """

    def __init__(self, path: str = ".extract_cache.jsonl"):
        self.path = path
        self.mem: dict[str, dict] = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        self.mem[row["k"]] = row["v"]
                    except (json.JSONDecodeError, KeyError):
                        continue

    def get(self, k: str) -> dict | None:
        return self.mem.get(k)

    def put(self, k: str, v: dict) -> None:
        self.mem[k] = v
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"k": k, "v": v}, ensure_ascii=False) + "\n")


_cache = _Cache()


def _key(title: str, org: str, winner: str | None, value: str | None) -> str:
    return hashlib.sha256(
        f"{title}|{org}|{winner}|{value}".encode()
    ).hexdigest()[:20]


async def _call(client: httpx.AsyncClient, model: str, payload: str) -> list[dict]:
    r = await client.post(API, json={
        "model": model,
        "max_tokens": 4000,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": payload}],
    })
    r.raise_for_status()
    text = "".join(
        b.get("text", "") for b in r.json().get("content", [])
        if b.get("type") == "text"
    ).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        log.error("unparseable extraction response from %s", model)
        return []
    return out if isinstance(out, list) else [out]


async def extract_batch(items: list[dict[str, Any]]) -> dict[str, dict]:
    """
    items: [{ref, title, organisation, hint_winner, hint_value}, ...]
    returns: {ref: fields}

    Two passes. Everything goes to Haiku; anything that comes back below the
    review floor is re-run on Sonnet. In practice this escalates a handful of
    rows a day.
    """
    pending, results = [], {}

    for it in items:
        k = _key(it["title"], it.get("organisation", ""),
                 it.get("hint_winner"), it.get("hint_value"))
        hit = _cache.get(k)
        if hit is not None:
            results[it["ref"]] = hit
        else:
            pending.append((k, it))

    if not pending:
        return results

    async with _client() as client:
        for i in range(0, len(pending), BATCH_SIZE):
            chunk = pending[i:i + BATCH_SIZE]
            payload = json.dumps(
                [{"ref": it["ref"], "title": it["title"],
                  "organisation": it.get("organisation"),
                  "stated_winner": it.get("hint_winner"),
                  "stated_value": it.get("hint_value")}
                 for _, it in chunk],
                ensure_ascii=False,
            )

            parsed = await _call(client, FAST_MODEL, payload)
            by_ref = {p.get("ref"): p for p in parsed if isinstance(p, dict)}

            weak = [
                it for _, it in chunk
                if float(by_ref.get(it["ref"], {}).get("confidence", 0))
                < REVIEW_FLOOR
            ]
            if weak:
                log.info("escalating %d rows to %s", len(weak), DEEP_MODEL)
                deep = await _call(client, DEEP_MODEL, json.dumps(
                    [{"ref": it["ref"], "title": it["title"],
                      "organisation": it.get("organisation"),
                      "stated_winner": it.get("hint_winner"),
                      "stated_value": it.get("hint_value")}
                     for it in weak], ensure_ascii=False))
                for p in deep:
                    if isinstance(p, dict) and p.get("ref"):
                        by_ref[p["ref"]] = p

            for k, it in chunk:
                fields = _normalise(by_ref.get(it["ref"], {}))
                _cache.put(k, fields)
                results[it["ref"]] = fields

    return results


async def extract_fields(
    title: str,
    organisation: str = "",
    source_url: str = "",
    hint_winner: str | None = None,
    hint_value: str | None = None,
) -> dict:
    """Single-item convenience wrapper. Prefer extract_batch in collectors."""
    out = await extract_batch([{
        "ref": "x", "title": title, "organisation": organisation,
        "hint_winner": hint_winner, "hint_value": hint_value,
    }])
    return out.get("x", _normalise({}))


def _normalise(raw: dict) -> dict:
    """Coerce model output into schema types and precompute display strings."""
    def num(v):
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def fnum(v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    try:
        wt = WorkType(str(raw.get("work_type", "other")).lower())
    except ValueError:
        wt = WorkType.OTHER

    districts = []
    for d in (raw.get("districts") or []):
        s = str(d).strip()
        canon = DISTRICT_ALIASES.get(s.lower())
        if canon:
            districts.append(canon)
        elif s in DISTRICTS:
            districts.append(s)
    districts = list(dict.fromkeys(districts))

    road = raw.get("road_name")
    if road:
        road = re.sub(r"^N\.?\s*H\.?[\s-]*", "NH", str(road).strip(),
                      flags=re.IGNORECASE)
        road = re.sub(r"^S\.?\s*H\.?[\s-]*", "SH-", road, flags=re.IGNORECASE)

    est = num(raw.get("estimated_cost"))
    won = num(raw.get("winner_value"))
    conf = fnum(raw.get("confidence"))

    return {
        "work_type": wt,
        "road_name": road,
        "stretch": (raw.get("stretch") or None),
        "chainage_from": fnum(raw.get("chainage_from")),
        "chainage_to": fnum(raw.get("chainage_to")),
        "districts": districts,
        "estimated_cost": est,
        "emd": num(raw.get("emd")),
        "confidence": conf if conf is not None else 0.5,
        # Precomputed for the client. See note 2 at the top of this file.
        "display_value": inr(won or est),
        "display_variance": variance_label(est, won),
    }


def inr(n: int | None) -> str | None:
    """
    Indian-convention money for display. Crore above 1Cr, lakh above 1L.
    Rounded to one decimal because nobody scanning a feed needs the paise,
    and a shorter string keeps the card from wrapping on a narrow phone.
    """
    if n is None:
        return None
    if n >= 10_000_000:
        return f"Rs {n / 10_000_000:,.1f} Cr".replace(".0 Cr", " Cr")
    if n >= 100_000:
        return f"Rs {n / 100_000:,.1f} L".replace(".0 L", " L")
    return f"Rs {n:,}"


def variance_label(est: int | None, won: int | None) -> str | None:
    if not est or not won:
        return None
    pct = (won - est) / est * 100
    return f"{pct:+.1f}% vs estimate"
