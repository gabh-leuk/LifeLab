package com.lifelab.collector

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** 与后端 ingest 系列端点的通信（协议见 docs/SETUP_ENV.md 第六节）。 */
object ApiClient {

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    // 小组件专用：它在 BroadcastReceiver 的 goAsync() 窗口里发请求，窗口只有约 10 秒，
    // 超了就是 ANR。所以把超时压到 6 秒 —— 连不上就快点失败，小组件显示上一次的数字。
    private val quickClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(6, TimeUnit.SECONDS)
        .build()

    private val JSON = "application/json; charset=utf-8".toMediaType()

    data class Result(val ok: Boolean, val message: String)

    /** 小组件要显示的一个记录按钮。 */
    data class EventType(val key: String, val label: String)

    /** 今天的条数与连续天数 —— 记录之后当场的回报。 */
    data class RecordStats(val todayCount: Int, val streakDays: Int)

    /** 记一条的结果：成功与否、给用户看的那句话、以及拿到手的新计数。 */
    data class RecordOutcome(val ok: Boolean, val message: String, val stats: RecordStats?)

    /** 上报精确前台会话（服务端据此自动生成行为时间段；整日替换，幂等）。 */
    fun uploadSessions(
        baseUrl: String,
        token: String,
        date: String,
        sessions: List<SessionRow>,
    ): Result {
        if (sessions.isEmpty()) return Result(true, "无会话可上传")

        val entries = JSONArray()
        for (s in sessions) {
            entries.put(
                JSONObject().apply {
                    put("app", s.pkg)
                    put("label", s.label)
                    put("start_ms", s.startMs)
                    put("end_ms", s.endMs)
                }
            )
        }
        val body = JSONObject().apply {
            put("date", date)
            put("entries", entries)
        }.toString()

        val url = "$baseUrl/ingest/usage/sessions"
        val request = Request.Builder()
            .url(url)
            .header("X-Device-Token", token)
            .post(body.toRequestBody(JSON))
            .build()

        return try {
            client.newCall(request).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (resp.isSuccessful) {
                    Result(true, "会话上传成功（${sessions.size} 条）：$text")
                } else {
                    Result(false, "HTTP ${resp.code}（$url）：$text")
                }
            }
        } catch (e: Exception) {
            Result(false, "网络错误（$url）：${e.message}")
        }
    }

    fun uploadUsageHourly(
        baseUrl: String,
        token: String,
        date: String,
        rows: List<UsageRow>,
    ): Result {
        if (rows.isEmpty()) return Result(true, "无数据可上传")

        val entries = JSONArray()
        for (row in rows) {
            entries.put(
                JSONObject().apply {
                    put("app", row.pkg)
                    put("label", row.label)
                    put("hour", row.hour)
                    put("seconds", row.seconds)
                    put("switches", row.switches)
                }
            )
        }
        val body = JSONObject().apply {
            put("date", date)
            put("entries", entries)
        }.toString()

        val url = "$baseUrl/ingest/usage/hourly"
        val request = Request.Builder()
            .url(url)
            .header("X-Device-Token", token)
            .post(body.toRequestBody(JSON))
            .build()

        return try {
            client.newCall(request).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (resp.isSuccessful) {
                    Result(true, "上传成功（${rows.size} 条小时记录）：$text")
                } else {
                    Result(false, "HTTP ${resp.code}（$url）：$text")
                }
            }
        } catch (e: Exception) {
            Result(false, "网络错误（$url）：${e.message}")
        }
    }

    // ── 手动记录（安卓主屏小组件）─────────────────────────────
    //
    // 走 ingest 端点而不是 POST /events：手机只持有设备令牌，而 /events 要的是登录
    // 令牌。设备行自带 user_id，服务端据此确定「记给谁」（见 backend/app/routers/ingest.py）。

    /** 记一条手动事件。响应里带回新计数，小组件就地刷新头部，不必再发一次 GET。 */
    fun recordEvent(baseUrl: String, token: String, type: String): RecordOutcome {
        val url = "$baseUrl/ingest/events"
        val body = JSONObject().apply { put("type", type) }.toString()
        val request = Request.Builder()
            .url(url)
            .header("X-Device-Token", token)
            .post(body.toRequestBody(JSON))
            .build()

        return try {
            quickClient.newCall(request).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    RecordOutcome(false, "HTTP ${resp.code}：$text", null)
                } else {
                    val json = JSONObject(text)
                    RecordOutcome(
                        true,
                        "",
                        RecordStats(
                            json.optInt("today_count"),
                            json.optInt("streak_days"),
                        ),
                    )
                }
            }
        } catch (e: Exception) {
            RecordOutcome(false, e.message ?: e.javaClass.simpleName, null)
        }
    }

    /** 今天的条数与连续天数；连不上返回 null（小组件保留上次显示的数字）。 */
    fun fetchRecordStats(baseUrl: String, token: String): RecordStats? =
        fetchJson("$baseUrl/ingest/record-stats", token)?.let {
            RecordStats(it.optInt("today_count"), it.optInt("streak_days"))
        }

    /** 可记录的类型清单（小组件的按钮就来自这里）；连不上返回 null。 */
    fun fetchEventTypes(baseUrl: String, token: String): List<EventType>? {
        val arr = fetchJsonArray("$baseUrl/ingest/event-types", token) ?: return null
        val out = ArrayList<EventType>(arr.length())
        for (i in 0 until arr.length()) {
            val item = arr.optJSONObject(i) ?: continue
            val key = item.optString("key")
            if (key.isEmpty()) continue
            out.add(EventType(key, item.optString("label").ifEmpty { key }))
        }
        return out
    }

    /** 失败的形态一律是 null —— 调用方把它当「这次没刷出来」，而不是「没有类型」。 */
    private fun fetchJson(url: String, token: String): JSONObject? = try {
        get(url, token)?.let { JSONObject(it) }
    } catch (e: JSONException) {
        null
    }

    private fun fetchJsonArray(url: String, token: String): JSONArray? = try {
        get(url, token)?.let { JSONArray(it) }
    } catch (e: JSONException) {
        null
    }

    private fun get(url: String, token: String): String? = try {
        val request = Request.Builder()
            .url(url)
            .header("X-Device-Token", token)
            .get()
            .build()
        quickClient.newCall(request).execute().use { resp ->
            if (resp.isSuccessful) resp.body?.string() else null
        }
    } catch (e: Exception) {
        null
    }
}
