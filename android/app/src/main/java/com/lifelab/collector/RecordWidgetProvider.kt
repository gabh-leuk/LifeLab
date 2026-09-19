package com.lifelab.collector

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.RemoteViews
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** 一格按钮：要么是某个记录类型（点一下记它），要么是「更多」（点开网页端）。 */
private data class Slot(val label: String, val pending: PendingIntent)

/**
 * 桌面小组件：顶部一行「今日 N 条 · 连续 M 天」，下面是要记的类型按钮。
 *
 * 按钮不写死在 XML 里，而是按 GET /ingest/event-types 的清单用 RemoteViews.addView
 * 拼出来 —— 类型可以在网页端增删/归档，小组件跟着变。
 *
 * **按钮数由小组件实际占的格数决定**（见 [visibleRows]），不是写死的常量：
 * 2×2 只放得下 4 个、2×3 放得下 8 个，多出来的进「更多…」。
 * 否则 2×2 会画出宿主裁得下、用户看不全的按钮行。
 *
 * **要显示哪几个由用户自己勾**（每个实例各一份，存在 [Settings] 里）：顶部那行
 * 「⚙ 今日 N 条」是入口，点它进 [WidgetConfigActivity]；首次拖到桌面时也会自动弹。
 */
class RecordWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        // 刷新要联网，而 onUpdate 跑在广播接收线程（主线程）上。goAsync() 把这条广播
        // 的寿命延长到协程结束，否则请求还没回来系统就可能回收进程。
        val pending = goAsync()
        scope.launch {
            try {
                // 一个实例刷不出来不该拖垮其余，也不该把异常抛到线程的默认处理器上
                ids.forEach { runCatching { render(context, manager, it) } }
            } finally {
                pending.finish()
            }
        }
    }

    /** 用户拖拽改了尺寸：行数可能变了，重画这一个实例。 */
    override fun onAppWidgetOptionsChanged(
        context: Context,
        manager: AppWidgetManager,
        id: Int,
        newOptions: Bundle?,
    ) {
        refresh(context, id)
    }

    /** 实例被删掉：连同它那份「选中的类型」一起清掉，别在 prefs 里留垃圾。 */
    override fun onDeleted(context: Context, ids: IntArray) {
        val settings = Settings(context)
        ids.forEach { settings.clearWidgetTypes(it) }
    }

    companion object {
        // 小组件几何（dp）。**这两个数是「内容对得上格子」的唯一来源**：
        // 按钮高度在 widget_record_button.xml 里，改那边要同步改这里。
        private const val TOOLBAR_DP = 38 // 容器上下内边距 + 头部一行 + 头部与按钮的间距
        private const val BUTTON_ROW_DP = 34 // 一行按钮（含行间距）
        private const val MAX_ROWS = 4 // 再高就不好按了，再多也要留「更多」

        private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

        /** 本机上所有该小组件实例的 id。 */
        fun widgetIds(context: Context): IntArray =
            AppWidgetManager.getInstance(context).getAppWidgetIds(
                ComponentName(context, RecordWidgetProvider::class.java)
            )

        /** 后台重画一个实例。会联网，调用方不用管线程。 */
        fun refresh(context: Context, id: Int) {
            scope.launch {
                runCatching { render(context, AppWidgetManager.getInstance(context), id) }
            }
        }

        /**
         * 重画一个实例。会联网，必须在后台线程调。
         *
         * [knownStats] 是调用方已经拿到手的计数（刚记完一条时）。给了它就不再问一次
         * 后端 —— 否则「记成功了但这次 GET 失败」会把头部显示成「连不上」，白白泄气。
         */
        fun render(
            context: Context,
            manager: AppWidgetManager,
            id: Int,
            knownStats: ApiClient.RecordStats? = null,
        ) {
            val settings = Settings(context)
            val views =
                if (!settings.configured) {
                    unconfigured(context)
                } else {
                    build(
                        context,
                        manager,
                        id,
                        knownStats
                            ?: ApiClient.fetchRecordStats(settings.baseUrl, settings.token),
                        ApiClient.fetchEventTypes(settings.baseUrl, settings.token),
                    )
                }
            manager.updateAppWidget(id, views)
        }

        /**
         * 本实例竖向放得下几行。
         *
         * `OPTION_APPWIDGET_MIN_HEIGHT` 是宿主报的「这个格子有多高（dp）」—— 唯一能
         * 知道用户把小组件拖成多大的途径。个别桌面不报（返回 0），那时按最大画：
         * 宁多勿缺，宿主本来就会裁掉多余部分。
         */
        private fun visibleRows(manager: AppWidgetManager, id: Int): Int {
            val h = manager.getAppWidgetOptions(id)
                .getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT)
            if (h <= 0) return MAX_ROWS
            return ((h - TOOLBAR_DP) / BUTTON_ROW_DP).coerceIn(1, MAX_ROWS)
        }

        private fun build(
            context: Context,
            manager: AppWidgetManager,
            id: Int,
            stats: ApiClient.RecordStats?,
            types: List<ApiClient.EventType>?,
        ): RemoteViews {
            val views = RemoteViews(context.packageName, R.layout.widget_record)
            views.setTextViewText(R.id.widget_header, headerText(context, stats))
            // 按钮占满了整个小组件，只有头部这行是空的 —— 它就是「进配置页」的落点。
            views.setOnClickPendingIntent(R.id.widget_header, configIntent(context, id))
            views.removeAllViews(R.id.widget_buttons)

            // 类型拉不到 = 后端连不上。这时给一个进网页端的出口，别留个空壳子。
            if (types == null) {
                addRow(views, context, listOf(webSlot(context)))
                return views
            }

            val picked = Settings(context).widgetTypes(id)
            val chosen =
                if (picked == null) {
                    types // 没配过：后端顺序，顺序就是内置在前、自定义按 sort_order
                } else {
                    // 配过：按用户勾选顺序取交集。归档/删掉的类型 key 还在 prefs 里，
                    // 这里静默落掉，后面的自然顶上（不清 prefs：用户取消归档后应该复原）
                    val byKey = types.associateBy { it.key }
                    picked.mapNotNull { byKey[it] }
                }

            // 用户明确一个都没勾：给一行提示，而不是静默回退成「全都显示」——
            // 否则他明明取消勾选了，按钮却又全冒出来。
            if (picked != null && chosen.isEmpty()) {
                addRow(views, context, listOf(configSlot(context, id), webSlot(context)))
                return views
            }

            // 两列，所以容量 = 行数 × 2。放不下时最后一格换成「更多」，而不是静默丢掉几个。
            val capacity = visibleRows(manager, id) * 2
            val slots =
                if (chosen.size <= capacity) {
                    chosen.map { typeSlot(context, it) }
                } else {
                    chosen.take(capacity - 1).map { typeSlot(context, it) } + webSlot(context)
                }
            slots.chunked(2).forEach { addRow(views, context, it) }
            return views
        }

        private fun unconfigured(context: Context): RemoteViews {
            val views = RemoteViews(context.packageName, R.layout.widget_record)
            views.setTextViewText(R.id.widget_header, context.getString(R.string.widget_unconfigured))
            views.removeAllViews(R.id.widget_buttons)
            addRow(
                views,
                context,
                listOf(
                    Slot(
                        context.getString(R.string.widget_open_settings),
                        PendingIntent.getActivity(
                            context,
                            1,
                            Intent(context, MainActivity::class.java),
                            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                        ),
                    )
                ),
            )
            return views
        }

        private fun addRow(views: RemoteViews, context: Context, slots: List<Slot>) {
            val row = RemoteViews(context.packageName, R.layout.widget_record_row)
            // 槽位有两个，单数时右边留空 —— 靠 widget_record_row.xml 的 weight 撑住左半宽度。
            slots.forEachIndexed { i, slot ->
                val button = RemoteViews(context.packageName, R.layout.widget_record_button)
                button.setTextViewText(R.id.widget_button, slot.label)
                button.setOnClickPendingIntent(R.id.widget_button, slot.pending)
                row.addView(if (i == 0) R.id.widget_row_left else R.id.widget_row_right, button)
            }
            views.addView(R.id.widget_buttons, row)
        }

        private fun typeSlot(context: Context, type: ApiClient.EventType) = Slot(
            type.label,
            PendingIntent.getBroadcast(
                context,
                // 每个类型一个 requestCode，否则 FLAG_UPDATE_CURRENT 会互相覆盖 extras
                type.key.hashCode(),
                Intent(context, RecordActionReceiver::class.java)
                    .setAction(RecordActionReceiver.ACTION_RECORD)
                    .putExtra(RecordActionReceiver.EXTRA_TYPE, type.key)
                    .putExtra(RecordActionReceiver.EXTRA_LABEL, type.label),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            ),
        )

        /** 「未选类型」那格的落点：还是回配置页。 */
        private fun configSlot(context: Context, id: Int) =
            Slot(context.getString(R.string.widget_configure), configIntent(context, id))

        /** 打开配置页并带上本实例 id。requestCode 用 id，不同实例各有各的 PendingIntent。 */
        private fun configIntent(context: Context, id: Int) = PendingIntent.getActivity(
            context,
            id,
            Intent(context, WidgetConfigActivity::class.java)
                .putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        private fun webSlot(context: Context) = Slot(
            context.getString(R.string.widget_more),
            PendingIntent.getActivity(
                context,
                0,
                Intent(context, WebViewActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            ),
        )

        private fun headerText(context: Context, stats: ApiClient.RecordStats?): String {
            val body =
                if (stats == null) {
                    context.getString(R.string.widget_offline)
                } else {
                    val today = context.getString(R.string.widget_today_count, stats.todayCount)
                    // 连续 1 天不值得报（网页端同口径）
                    if (stats.streakDays > 1) {
                        "$today · ${context.getString(R.string.widget_streak, stats.streakDays)}"
                    } else {
                        today
                    }
                }
            // ⚙ 放最前面：头部只有一行，窄的时候会被省略号吃掉尾巴，
            // 放末尾就等于「窄的时候就看不见入口了」。
            return "⚙ $body"
        }
    }
}
