package com.lifelab.collector

import android.appwidget.AppWidgetManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * 小组件按钮的落点：记一条，然后回报。
 *
 * 记完立刻用响应里的计数重画小组件 —— 「点一下就看到今天第几条」是这件事的全部意义，
 * 不能等到 30 分钟的周期刷新。失败也重画一次：头部会变成「连不上」，错误就不是隐形的。
 */
class RecordActionReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_RECORD) return
        val type = intent.getStringExtra(EXTRA_TYPE) ?: return
        val label = intent.getStringExtra(EXTRA_LABEL) ?: type

        // 请求在 goAsync 的窗口里发（约 10 秒），见 ApiClient.quickClient 的超时设置
        val pending = goAsync()
        scope.launch {
            var stats: ApiClient.RecordStats? = null
            try {
                val settings = Settings(context)
                val outcome =
                    if (!settings.configured) {
                        ApiClient.RecordOutcome(false, "还没配置服务器地址与 token", null)
                    } else {
                        ApiClient.recordEvent(settings.baseUrl, settings.token, type)
                    }
                stats = outcome.stats
                toast(context, outcome, label)
            } finally {
                // 用响应里带回的计数重画（拿不到就让 render 自己再问一次后端）；
                // 刷新失败无所谓 —— 半小时后还会刷，点下一次也会刷。
                val manager = AppWidgetManager.getInstance(context)
                RecordWidgetProvider.widgetIds(context).forEach { id ->
                    runCatching { RecordWidgetProvider.render(context, manager, id, stats) }
                }
                pending.finish()
            }
        }
    }

    private fun toast(context: Context, outcome: ApiClient.RecordOutcome, label: String) {
        val stats = outcome.stats
        val text =
            if (outcome.ok) {
                val base = context.getString(R.string.toast_recorded, label)
                if (stats == null) base
                else base + " · " + context.getString(R.string.widget_today_count, stats.todayCount)
            } else {
                context.getString(R.string.toast_record_failed, outcome.message)
            }
        Handler(Looper.getMainLooper()).post {
            Toast.makeText(context.applicationContext, text, Toast.LENGTH_SHORT).show()
        }
    }

    companion object {
        const val ACTION_RECORD = "com.lifelab.collector.RECORD"
        const val EXTRA_TYPE = "type"
        const val EXTRA_LABEL = "label"

        private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }
}
