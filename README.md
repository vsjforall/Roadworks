# kerala-infra

Backend that watches Kerala road and bridge tenders and reports winners.

## Where the files go

```
kerala-infra/
  kerala_infra/
    __init__.py
    schema.py                    the record every source normalises to
    store.py                     SQLite, status pipeline, dedupe
    extract.py                   Claude API pass, title -> fields
    collectors/
      __init__.py
      portal_map.py              ALL selectors live here
      kerala_etenders.py         source 1 of 5
  requirements.txt
```

## First run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
export ANTHROPIC_API_KEY=sk-ant-...
python -m kerala_infra.run --once --headed
```

Run it `--headed` the first time. The selectors in `portal_map.py` are a
reconstruction, not a verified read of the live portal, so expect the first
run to find zero rows. When it does, the browser window is open in front of
you: right-click the tender table, inspect, and correct the strings in
`portal_map.py`. Nothing else needs editing.

Budget half a day for this. It is the only genuinely fiddly part of the build,
and it is a one-time cost per portal.

## Still missing before this is an app

- `run.py` — scheduler and CLI entry point
- `api.py` — the read API the phone talks to
- 4 more collectors: CPPP/NHAI, KIIFB, RBDCK, news watcher
- alert delivery (WhatsApp Business API or Telegram)
- the client itself

## Operating notes

Keep the single worker and the 2.5s delay in `portal_map.py`. These are public
portals run on public money; the collector should be invisible in their logs.

Never scrape behind a DSC login. Bid documents that require a bidder account
are out of scope, permanently.
