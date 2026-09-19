package com.lifelab.collector

import android.content.Intent
import android.os.Bundle
import android.provider.Settings as AndroidSettings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDate
import java.time.format.DateTimeFormatter

class MainActivity : AppCompatActivity() {

    private lateinit var settings: Settings
    private lateinit var statusText: TextView
    private lateinit var resultText: TextView
    private lateinit var baseUrlInput: EditText
    private lateinit var tokenInput: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        settings = Settings(this)
        statusText = findViewById(R.id.statusText)
        resultText = findViewById(R.id.resultText)
        baseUrlInput = findViewById(R.id.baseUrlInput)
        tokenInput = findViewById(R.id.tokenInput)

        baseUrlInput.setText(settings.baseUrl)
        tokenInput.setText(settings.token)

        findViewById<Button>(R.id.permissionButton).setOnClickListener {
            startActivity(Intent(AndroidSettings.ACTION_USAGE_ACCESS_SETTINGS))
        }

        findViewById<Button>(R.id.saveButton).setOnClickListener {
            settings.baseUrl = baseUrlInput.text.toString()
            settings.token = tokenInput.text.toString()
            WorkScheduler.ensureScheduled(this)
            Toast.makeText(this, "已保存，后台每 4 小时自动上传", Toast.LENGTH_SHORT).show()
            refreshStatus()
        }

        findViewById<Button>(R.id.uploadButton).setOnClickListener {
            uploadNow()
        }

        findViewById<Button>(R.id.webButton).setOnClickListener {
            openWeb()
        }
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun refreshStatus() {
        val permission = if (UsageCollector.hasPermission(this)) "已授予" else "未授予"
        val config = if (settings.configured) "已配置" else "未配置"
        statusText.text = "使用情况权限：$permission\n采集配置：$config" +
            (settings.lastUploadAt.takeIf { it.isNotEmpty() }?.let { "\n上次上传：$it" } ?: "")
    }

    private fun openWeb() {
        if (!settings.configured) {
            Toast.makeText(this, "请先保存服务器地址与 token", Toast.LENGTH_SHORT).show()
            return
        }
        startActivity(Intent(this, WebViewActivity::class.java))
    }

    private fun uploadNow() {
        if (!settings.configured) {
            Toast.makeText(this, "请先保存服务器地址与 token", Toast.LENGTH_SHORT).show()
            return
        }
        if (!UsageCollector.hasPermission(this)) {
            Toast.makeText(this, "请先授予使用情况访问权限", Toast.LENGTH_SHORT).show()
            return
        }
        resultText.text = "上传中…"
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                val collection = UsageCollector.collectToday(this@MainActivity)
                val date = LocalDate.now().format(DateTimeFormatter.ISO_LOCAL_DATE)
                val sessions = ApiClient.uploadSessions(
                    settings.baseUrl, settings.token, date, collection.sessions,
                )
                val hourly = ApiClient.uploadUsageHourly(
                    settings.baseUrl, settings.token, date, collection.hourly,
                )
                ApiClient.Result(
                    sessions.ok && hourly.ok,
                    "会话：${sessions.message}；分小时：${hourly.message}",
                )
            }
            settings.lastUploadAt = java.time.LocalDateTime.now().toString()
            settings.lastUploadResult = result.message
            resultText.text = result.message
            refreshStatus()
        }
    }
}
