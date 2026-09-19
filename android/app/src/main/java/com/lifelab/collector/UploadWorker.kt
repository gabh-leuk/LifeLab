package com.lifelab.collector

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import java.time.LocalDate
import java.time.format.DateTimeFormatter

/**
 * 后台定时上传：每天多次尝试，把「今天」的分小时使用时长整日替换上报。
 * 无权限或未配置时静默跳过（不打扰用户）。
 */
class UploadWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val settings = Settings(applicationContext)
        if (!settings.configured) return Result.success()
        if (!UsageCollector.hasPermission(applicationContext)) return Result.success()

        val collection = UsageCollector.collectToday(applicationContext)
        val date = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE)
        val sessions = ApiClient.uploadSessions(
            settings.baseUrl, settings.token, date, collection.sessions,
        )
        val hourly = ApiClient.uploadUsageHourly(
            settings.baseUrl, settings.token, date, collection.hourly,
        )
        settings.lastUploadAt = java.time.LocalDateTime.now().toString()
        settings.lastUploadResult = "会话：${sessions.message}；分小时：${hourly.message}"
        return if (sessions.ok && hourly.ok) Result.success() else Result.retry()
    }
}
