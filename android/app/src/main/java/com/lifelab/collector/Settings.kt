package com.lifelab.collector

import android.content.Context

/** 采集器配置：服务器地址与设备 token（创建后写死在本机，不随仓库分发）。 */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("lifelab", Context.MODE_PRIVATE)

    var baseUrl: String
        get() = prefs.getString("base_url", "") ?: ""
        set(value) = prefs.edit().putString("base_url", value.trim().trimEnd('/')).apply()

    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(value) = prefs.edit().putString("token", value.trim()).apply()

    var lastUploadAt: String
        get() = prefs.getString("last_upload_at", "") ?: ""
        set(value) = prefs.edit().putString("last_upload_at", value).apply()

    var lastUploadResult: String
        get() = prefs.getString("last_upload_result", "") ?: ""
        set(value) = prefs.edit().putString("last_upload_result", value).apply()

    val configured: Boolean
        get() = baseUrl.isNotEmpty() && token.isNotEmpty()

    // ── 桌面小组件：每个实例各存一份「要记哪些类型」────────────────
    //
    // 实例 id 由 AppWidgetManager 分配，随实例生灭，所以键里带上它。
    // 存的是逗号分隔的 key（`LEARNING_START` / `custom_xxx`，本身不含逗号）——
    // 不能用 StringSet：它不保序，而用户勾选的顺序就是按钮顺序。

    /** 本实例选中的类型 key。**null 表示没配过**（调用方按后端顺序取），空列表是「一个都没选」。 */
    fun widgetTypes(id: Int): List<String>? =
        prefs.getString("widget_types_$id", null)?.split(',')?.filter { it.isNotEmpty() }

    fun setWidgetTypes(id: Int, keys: List<String>) =
        prefs.edit().putString("widget_types_$id", keys.joinToString(",")).apply()

    /** 实例被删掉了，别在 prefs 里留垃圾。 */
    fun clearWidgetTypes(id: Int) =
        prefs.edit().remove("widget_types_$id").apply()
}
