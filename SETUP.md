# Getting the app onto a phone

Three stages. Do them in order.

## 1. Put the backend on a server

The app is a window onto your server, so the server has to exist first and be
reachable over HTTPS. Any small VPS works, or Railway / Render if you would
rather not manage a box.

```bash
git clone <your-repo> && cd kerala-infra
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m kerala_infra.run seed        # sample data so you see it working
python -m kerala_infra.run serve       # http://localhost:8000
```

Open `http://localhost:8000` in a browser. You should see the feed with seven
Kerala records. If you do, the whole backend works.

Then put it behind a domain with HTTPS — Caddy does this in two lines, or use
Cloudflare Tunnel. **HTTPS is not optional**: the Android app sets
`usesCleartextTraffic="false"` and will refuse a plain http:// address.

## 2. Build the APK

You do not need Android Studio.

1. Push this whole folder to a **private** GitHub repo.
2. Edit one line first — in `android/app/build.gradle`, change
   `FEED_URL` to your domain.
3. On GitHub, open the **Actions** tab, pick **Build APK**, click
   **Run workflow**.
4. Wait about four minutes. Open the finished run, scroll to
   **Artifacts**, download `roadworks-apk`.

Inside the zip is `app-release.apk`. That is the file you send people.

## 3. Install it

Send the .apk over WhatsApp or Drive. On opening it, Android warns about
installing from an unknown source and offers a settings toggle — that is
normal for any app not from the Play Store, and it is a one-time allow per
sender app.

The APK is debug-signed. That is fine for sideloading among friends. It is
**not** enough for the Play Store later, and because the signing key is
generated fresh on each build machine, a future rebuild may not install as an
update over an old one. When you have real users, generate a keystore once,
store it as a GitHub secret, and switch the signing config. Until then this
avoids a secret you cannot afford to lose.

## What still isn't built

- Four of five collectors: CPPP/NHAI, KIIFB, RBDCK, news watcher
- Push notifications (a WebView cannot do these; needs FCM or a Telegram bot)
- The selector verification pass in `collectors/portal_map.py`

Until that last one is done the app shows seed data only. That is the next
real task and it needs a browser pointed at the live portal, which is
something only you can do.
