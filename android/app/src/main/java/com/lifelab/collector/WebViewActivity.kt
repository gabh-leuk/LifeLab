package com.lifelab.collector

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity

/**
 * 把 Web 端内置进来 —— 从桌面小组件或主界面一点就到，不用再开浏览器。
 *
 * 前端是 React SPA，与 API 同源（Tailscale Funnel 的 HTTPS 根地址），所以这里只是
 * 加载 [Settings.baseUrl]，不需要内置任何浏览器内核，也没有 CORS 问题。
 *
 * 唯一的关键是 [android.webkit.WebSettings.setDomStorageEnabled]：登录令牌存在
 * localStorage（frontend/src/lib/auth.ts），不开它每进一次都要重新登录。
 */
class WebViewActivity : AppCompatActivity() {

    private lateinit var webView: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_webview)

        val url = Settings(this).baseUrl
        if (url.isEmpty()) {
            Toast.makeText(this, R.string.widget_unconfigured, Toast.LENGTH_LONG).show()
            finish()
            return
        }

        webView = findViewById(R.id.webView)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            setSupportZoom(false)
        }
        webView.webViewClient = object : WebViewClient() {
            // 站内的 http(s) 一律留在应用内；别的 scheme（tel:/mailto:…）交回系统
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest,
            ): Boolean {
                val uri: Uri = request.url
                if (uri.scheme == "http" || uri.scheme == "https") return false
                startActivity(Intent(Intent.ACTION_VIEW, uri))
                return true
            }
        }

        // 返回键先退网页历史，退到底再退出活动。用 OnBackPressedDispatcher 而不是
        // 重写 onBackPressed()：后者在 API 33+ 已废弃。
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (webView.canGoBack()) {
                        webView.goBack()
                    } else {
                        isEnabled = false
                        onBackPressedDispatcher.onBackPressed()
                    }
                }
            },
        )

        // 旋转屏幕时活动不重建（见 AndroidManifest 的 configChanges），所以只需首次加载；
        // 进程被回收后重建才走 restoreState。
        if (savedInstanceState == null) webView.loadUrl(url) else webView.restoreState(savedInstanceState)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }
}
