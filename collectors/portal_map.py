"""
Every selector, path and timing knob for the portals, in one place.

WHEN A COLLECTOR BREAKS, IT BREAKS HERE. Open the portal in a browser, read the
DOM, fix the string, redeploy. No collector logic should ever need touching.

The values below are my best reconstruction of the NIC eProcurement layout and
WILL need one verification pass against the live site before first run. Start
with `python -m kerala_infra.tools.probe --portal kerala_etenders`, which loads
each list page headed and prints what it finds against what it expects.
"""

KERALA_ETENDERS = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),

    # Be a good citizen. The portal is public infrastructure paid for by the
    # same taxes funding the roads we are tracking.
    "inter_request_delay": 2.5,
    "max_pages": 40,

    "session_ready": "#tendersearchform, .page_title, form[name='sessionForm']",

    "lists": {
        # Winners. Highest-value feed, polled hardest.
        "aoc": {
            "path": "/nicgep/app?page=WebAwardOfContractLists&service=page",
            "org_filter": "select[name='organisationChain']",
            "submit": "input[name='submit'], #submit",
            "table": "table#awardTable, table.list_table",
            "row": "table#awardTable tr.even, table#awardTable tr.odd, "
                   "table.list_table tr.even, table.list_table tr.odd",
            "next_page": "a#loadNext, a[title='Next']",
        },
        # L1 visible here days before the formal award appears in AOC.
        "opened": {
            "path": "/nicgep/app?page=WebTenderStatusLists&service=page",
            "org_filter": "select[name='organisationChain']",
            "submit": "input[name='submit'], #submit",
            "table": "table#tenderStatusTable, table.list_table",
            "row": "table#tenderStatusTable tr.even, "
                   "table#tenderStatusTable tr.odd",
            "next_page": "a#loadNext, a[title='Next']",
        },
        # New tenders.
        "live": {
            "path": "/nicgep/app?page=FrontEndAdvancedSearch&service=page",
            "org_filter": "select[name='organisationChain']",
            "submit": "input[name='submit'], #submit",
            "table": "table#table, table.list_table",
            "row": "table#table tr.even, table#table tr.odd",
            "next_page": "a#loadNext, a[title='Next']",
        },
    },

    # Runs inside the page. Returns plain objects the collector maps to RawRow.
    # Column order shifts between the three list types, so this reads by header
    # text rather than by index — index-based reads are the single most common
    # cause of silent garbage in scrapers like this.
    "row_extractor_js": """
    (rows) => {
      const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
      const table = rows[0] && rows[0].closest('table');
      if (!table) return [];
      const heads = [...table.querySelectorAll('tr')][0];
      const cols = heads
        ? [...heads.children].map(th => norm(th.textContent).toLowerCase())
        : [];
      const at = (tr, ...names) => {
        for (const n of names) {
          const i = cols.findIndex(c => c.includes(n));
          if (i >= 0 && tr.children[i]) return norm(tr.children[i].textContent);
        }
        return '';
      };
      return rows.map(tr => ({
        tender_id:  at(tr, 'tender id', 'tender ref', 'reference'),
        title:      at(tr, 'title', 'work description', 'tender title'),
        winner:     at(tr, 'selected bidder', 'awarded to', 'contractor',
                           'bidder name', 'l1'),
        value:      at(tr, 'contract value', 'awarded value', 'quoted',
                           'tender value'),
        awarded_on: at(tr, 'award date', 'contract date', 'published'),
        href: (tr.querySelector('a[href]') || {}).href || ''
      })).filter(r => r.title);
    }
    """,
}
