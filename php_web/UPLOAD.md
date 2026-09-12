# reports.mytsys.com — what to upload

Upload the **contents** of this folder into the document root for
reports.mytsys.com (usually `public_html/` or a subfolder your domain
points at).

```
index.html
app.js
api.php
sw.js
manifest.json
icon-192.png
icon-512.png
.htaccess
data/feed.json
```

`.htaccess` is a hidden file. Turn on "show hidden files" in cPanel File
Manager or your FTP client, or it will silently not upload.

## Then check, in this order

1. `https://reports.mytsys.com/api.php?action=health`
   Expect `{"ok":true,"records":7,...}`. If `writable` is false, chmod the
   `data` folder to 755.
2. `https://reports.mytsys.com/api.php?filter=nh`
   Expect 3 records.
3. `https://reports.mytsys.com/`
   Expect five filter chips and seven cards.

If the page still looks like your screenshot — no chips, no cards — then
`app.js` did not load. Open the browser console; it will name the file it
could not find.

## Why it looked broken before

`index.html` was asking for `/static/app.js`. That path only exists inside
the Python app, which shared hosting cannot run. On a plain host the file
sits next to the page, so every path in this version is relative. Nothing
was wrong with your server.

## Before the collector goes live

Change `INGEST_SECRET` at the top of `api.php` to a long random string.
Anyone who knows it can write to your feed.

The collector then POSTs to:

```
POST https://reports.mytsys.com/api.php?action=ingest
X-Ingest-Secret: <your secret>
{"items": [ ...records... ]}
```

That is the whole hybrid: Python scrapes wherever you can run it, PHP
serves the phone. Your Android APK needs no change — it already points at
this domain.
