package com.lifelab.collector

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

/** 周期上传任务：每 4 小时尝试一次（Android 最短 15 分钟；4h 足够且省电）。 */
object WorkScheduler {
    private const val WORK_NAME = "lifelab_usage_upload"

    fun ensureScheduled(context: Context) {
        val request = PeriodicWorkRequestBuilder<UploadWorker>(4, TimeUnit.HOURS)
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            WORK_NAME,
            ExistingPeriodicWorkPolicy.KEEP,
            request,
        )
    }
}
