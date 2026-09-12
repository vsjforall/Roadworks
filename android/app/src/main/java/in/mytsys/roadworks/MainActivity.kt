package `in`.mytsys.roadworks

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.net.http.SslError
import android.os.Bundle
import android.view.View
import android.webkit.*
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

/**
 * Hosts the feed.
 *
 * Every failure path below renders a screen naming the URL and the reason.
 * A white screen tells you nothing, which is exactly where this app was.
 * If something breaks, the phone should say what.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout
    private var errored = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)

        web = WebView(this).apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                databaseEnabled = true
                cacheMode = WebSettings.LOAD_DEFAULT
                loadWithOverviewMode = true
                useWideViewPort = true
                builtInZoomControls = false
                displayZoomControls = false
                setSupportZoom(false)
            }
            overScrollMode = View.OVER_SCROLL_NEVER
            isVerticalScrollBarEnabled = false

            webViewClient = object : WebViewClient() {

                override fun onPageStarted(v: WebView?, url: String?, f: Bitmap?) {
                    errored = false
                }

                override fun onPageFinished(v: WebView?, url: String?) {
                    refresh.isRefreshing = false
                }

                // Render immediately rather than waiting for onPageFinished.
                // On DNS and connection failures that callback may never run,
                // which is what produced the blank screen.
                override fun onReceivedError(
                    v: WebView, req: WebResourceRequest, err: WebResourceError
                ) {
                    if (!req.isForMainFrame) return
                    errored = true
                    fail("Can't reach the server",
                         "${err.description} (code ${err.errorCode})",
                         req.url.toString())
                }

                // A 404 or 500 returns a page, so onReceivedError never fires.
                override fun onReceivedHttpError(
                    v: WebView, req: WebResourceRequest, res: WebResourceResponse
                ) {
                    if (!req.isForMainFrame) return
                    errored = true
                    fail("Server returned an error",
                         "HTTP ${res.statusCode}", req.url.toString())
                }

                // WebView blocks bad certificates silently. Expired or
                // mismatched certs are a very common white-screen cause and
                // the default behaviour gives no clue at all.
                override fun onReceivedSslError(
                    v: WebView, handler: SslErrorHandler, err: SslError
                ) {
                    handler.cancel()
                    errored = true
                    val why = when (err.primaryError) {
                        SslError.SSL_EXPIRED -> "certificate expired"
                        SslError.SSL_IDMISMATCH -> "certificate is for a different domain"
                        SslError.SSL_UNTRUSTED -> "certificate not trusted"
                        SslError.SSL_NOTYETVALID -> "certificate not yet valid"
                        SslError.SSL_DATE_INVALID -> "certificate date invalid"
                        else -> "SSL error ${err.primaryError}"
                    }
                    fail("HTTPS problem", why, err.url ?: BuildConfig.FEED_URL)
                }

                override fun shouldOverrideUrlLoading(
                    v: WebView, req: WebResourceRequest
                ): Boolean = false
            }

            // Surfaces JS errors in logcat. Without this a broken script is
            // invisible from the device.
            webChromeClient = object : WebChromeClient() {
                override fun onConsoleMessage(m: ConsoleMessage): Boolean {
                    android.util.Log.d("Roadworks",
                        "${m.messageLevel()}: ${m.message()} @${m.sourceId()}:${m.lineNumber()}")
                    return true
                }
            }
        }

        refresh = SwipeRefreshLayout(this).apply {
            addView(web)
            setOnRefreshListener { web.loadUrl(BuildConfig.FEED_URL) }
            setOnChildScrollUpCallback { _, _ -> web.scrollY > 0 }
        }

        setContentView(refresh)
        web.loadUrl(BuildConfig.FEED_URL)

        onBackPressedDispatcher.addCallback(
            this, object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (!errored && web.canGoBack()) web.goBack() else finish()
                }
            })
    }

    private fun fail(title: String, detail: String, url: String) {
        refresh.isRefreshing = false
        val html = """
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <div style="font-family:system-ui,sans-serif;padding:22vh 28px;
                        color:#6b6862;line-height:1.5">
              <div style="font-size:18px;font-weight:600;color:#1a1917;
                          margin-bottom:10px">${esc(title)}</div>
              <div style="margin-bottom:18px">${esc(detail)}</div>
              <div style="font-size:12px;color:#9a968e;word-break:break-all;
                          padding-top:14px;border-top:1px solid #e6e3dd">${esc(url)}</div>
              <div style="font-size:12px;color:#9a968e;margin-top:10px">
                Pull down to retry.</div>
            </div>""".trimIndent()
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
    }

    private fun esc(s: String) = s
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    override fun onSaveInstanceState(out: Bundle) {
        super.onSaveInstanceState(out); web.saveState(out)
    }

    override fun onDestroy() {
        web.destroy(); super.onDestroy()
    }
}
