package com.lifelab.collector

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** 开机后恢复周期任务（WorkManager 任务本身会持久化，这里只做兜底触发）。 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            WorkScheduler.ensureScheduled(context)
        }
    }
}
