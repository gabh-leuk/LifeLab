package com.lifelab.collector

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.temporal.ChronoUnit

/** 一条 (本地小时, 应用) 的使用时长。hour 为 0..23（采集器本地时区）。 */
data class UsageRow(
    val pkg: String,
    val label: String,
    val hour: Int,
    val seconds: Int,
    val switches: Int,
)

/** 一条精确前台会话（真实测量），供服务端自动生成行为时间段。 */
data class SessionRow(
    val pkg: String,
    val label: String,
    val startMs: Long,
    val endMs: Long,
)

/** 一次采集结果：分小时时长 + 精确会话。 */
data class Collection(
    val hourly: List<UsageRow>,
    val sessions: List<SessionRow>,
)

/** 采集过程中的原始区间（未解析 label / 未过滤）。 */
private data class RawSession(val pkg: String, val start: Long, val end: Long)

/**
 * 读取手机本地「今天」的前台使用数据。
 *
 * - 时长：用 UsageEvents 重建每段「某应用在前台」的区间，再按本地小时边界切分累加。
 *   （不用 queryUsageStats(INTERVAL_HOURLY)：其小时桶对不齐本地整点。）
 * - 会话：同一批区间原样保留（精确起止），服务端据此自动生成行为时间段。
 * - 次数：每个「进入前台」事件计一次（API 29+ ACTIVITY_RESUMED，之前 MOVE_TO_FOREGROUND）。
 * - 停止计入：切到别的应用、PAUSED、或息屏/锁屏（SCREEN_NON_INTERACTIVE / KEYGUARD_SHOWN）。
 * - 过滤：单 (小时,应用) < 10 秒视为噪声；自身；无启动入口的系统包。
 */
object UsageCollector {

    /** 单 (小时,应用) 的秒数下限：太小的是噪声，也会撑爆上报条数上限。 */
    private const val MIN_HOUR_SECONDS = 10

    /** 单条会话的秒数下限（低于此视为切换噪声）。 */
    private const val MIN_SESSION_MS = 5_000L

    /** 与服务端 UsageHourlyIngestRequest.entries 上限一致（超出会被 422）。 */
    private const val MAX_ENTRIES = 500

    /** 会话条数上限（服务端上限 3000，留余量）。 */
    private const val MAX_SESSIONS = 2800

    @Suppress("DEPRECATION")
    fun hasPermission(context: Context): Boolean {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            appOps.unsafeCheckOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(),
                context.packageName,
            )
        } else {
            appOps.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(),
                context.packageName,
            )
        }
        return mode == AppOpsManager.MODE_ALLOWED
    }

    fun collectToday(context: Context): Collection {
        val zone = ZoneId.systemDefault()
        val today = LocalDate.now(zone)
        val start = today.atStartOfDay(zone).toInstant().toEpochMilli()
        val end = today.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
        return collect(context, start, end)
    }

    @Suppress("DEPRECATION")
    fun collect(context: Context, startMs: Long, endMs: Long): Collection {
        val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val pm = context.packageManager
        val zone = ZoneId.systemDefault()

        val resumedType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            UsageEvents.Event.ACTIVITY_RESUMED
        } else {
            UsageEvents.Event.MOVE_TO_FOREGROUND
        }
        val pausedType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            UsageEvents.Event.ACTIVITY_PAUSED
        } else {
            UsageEvents.Event.MOVE_TO_BACKGROUND
        }

        val seconds = HashMap<Int, HashMap<String, Long>>() // hour -> pkg -> sec
        val switches = HashMap<Int, HashMap<String, Int>>() // hour -> pkg -> 进入前台次数
        val rawSessions = ArrayList<RawSession>()

        val events = usm.queryEvents(startMs, endMs)
        val ev = UsageEvents.Event()
        var curPkg: String? = null
        var curStart = 0L
        while (events.hasNextEvent()) {
            events.getNextEvent(ev)
            val type = ev.eventType
            // 息屏/锁屏事件常无 packageName，必须先于「空包跳过」处理
            if (type == UsageEvents.Event.SCREEN_NON_INTERACTIVE ||
                type == UsageEvents.Event.KEYGUARD_SHOWN
            ) {
                val cp = curPkg
                if (cp != null) {
                    addInterval(seconds, rawSessions, cp, curStart, ev.timeStamp, zone)
                    curPkg = null
                }
                continue
            }
            val pkg = ev.packageName ?: continue
            when (type) {
                resumedType -> {
                    addSwitch(switches, hourOf(ev.timeStamp, zone), pkg)
                    if (curPkg == null) {
                        curPkg = pkg
                        curStart = ev.timeStamp
                    } else if (curPkg != pkg) {
                        addInterval(seconds, rawSessions, curPkg, curStart, ev.timeStamp, zone)
                        curPkg = pkg
                        curStart = ev.timeStamp
                    }
                    // 同一应用连续 RESUME（切换内部 Activity）：不重置计时，避免漏算
                }
                pausedType -> {
                    if (curPkg == pkg) {
                        addInterval(seconds, rawSessions, pkg, curStart, ev.timeStamp, zone)
                        curPkg = null
                    }
                }
            }
        }
        // 窗口结束时仍在前台：区间延到窗口末尾
        val tail = curPkg
        if (tail != null) addInterval(seconds, rawSessions, tail, curStart, endMs, zone)

        val labelCache = HashMap<String, String?>()
        val rows = ArrayList<UsageRow>()
        for ((hour, apps) in seconds) {
            for ((pkg, sec) in apps) {
                if (sec < MIN_HOUR_SECONDS) continue
                if (pkg == context.packageName) continue
                if (!labelCache.containsKey(pkg)) labelCache[pkg] = appLabel(pm, pkg)
                val label = labelCache[pkg] ?: continue
                rows.add(
                    UsageRow(
                        pkg = pkg,
                        label = label,
                        hour = hour,
                        seconds = sec.toInt().coerceAtMost(3600),
                        switches = switches[hour]?.get(pkg) ?: 0,
                    )
                )
            }
        }

        val sessions = ArrayList<SessionRow>()
        for (rs in rawSessions) {
            if (rs.end - rs.start < MIN_SESSION_MS) continue
            if (rs.pkg == context.packageName) continue
            if (!labelCache.containsKey(rs.pkg)) labelCache[rs.pkg] = appLabel(pm, rs.pkg)
            val label = labelCache[rs.pkg] ?: continue
            sessions.add(SessionRow(rs.pkg, label, rs.start, rs.end))
        }

        val cappedRows = if (rows.size <= MAX_ENTRIES) {
            rows
        } else {
            rows.sortedByDescending { it.seconds }.take(MAX_ENTRIES)
        }
        val cappedSessions = if (sessions.size <= MAX_SESSIONS) {
            sessions
        } else {
            sessions.sortedByDescending { it.endMs - it.startMs }.take(MAX_SESSIONS)
        }
        return Collection(
            hourly = cappedRows.sortedWith(compareBy({ it.hour }, { -it.seconds })),
            sessions = cappedSessions.sortedBy { it.startMs },
        )
    }

    private fun hourOf(ms: Long, zone: ZoneId): Int =
        Instant.ofEpochMilli(ms).atZone(zone).hour

    private fun addSwitch(switches: HashMap<Int, HashMap<String, Int>>, hour: Int, pkg: String) {
        val m = switches.getOrPut(hour) { HashMap() }
        m[pkg] = (m[pkg] ?: 0) + 1
    }

    /** 把 [startMs, endMs) 按本地整点切分累加时长，同时保留为一条精确会话。 */
    private fun addInterval(
        seconds: HashMap<Int, HashMap<String, Long>>,
        sessions: MutableList<RawSession>,
        pkg: String,
        startMs: Long,
        endMs: Long,
        zone: ZoneId,
    ) {
        if (endMs > startMs) sessions.add(RawSession(pkg, startMs, endMs))
        var a = startMs
        while (a < endMs) {
            val zdt = Instant.ofEpochMilli(a).atZone(zone)
            val nextHour = zdt.truncatedTo(ChronoUnit.HOURS).plusHours(1).toInstant().toEpochMilli()
            val b = minOf(nextHour, endMs)
            val sec = (b - a) / 1000L
            if (sec > 0) {
                val m = seconds.getOrPut(zdt.hour) { HashMap() }
                m[pkg] = (m[pkg] ?: 0L) + sec
            }
            a = b
        }
    }

    private fun appLabel(pm: PackageManager, pkg: String): String? {
        return try {
            if (pm.getLaunchIntentForPackage(pkg) == null) return null // 无启动入口的系统包
            val info = pm.getApplicationInfo(pkg, 0)
            pm.getApplicationLabel(info).toString()
        } catch (_: PackageManager.NameNotFoundException) {
            pkg // 有启动入口但取不到元信息：回退包名，宁可不美化也别丢数据
        }
    }
}
