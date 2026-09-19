package com.lifelab.collector

import android.appwidget.AppWidgetManager
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 小组件的配置页：勾选这个实例上要放哪几个记录类型。
 *
 * 两条入口，都是「每个实例各配一份」：
 * 1. **首次拖到桌面**：`widget_record_info.xml` 的 `android:configure` 会先拉起这里，
 *    此时按返回键 = `RESULT_CANCELED` = **这个小组件不落地**（`android:configure` 的标准约定）。
 * 2. **点已有组件的头部那行**（见 [RecordWidgetProvider.build]）：这时组件已经在了，
 *    结果是什么都不影响它存在，只是改配置。
 */
class WidgetConfigActivity : AppCompatActivity() {

    private var widgetId = AppWidgetManager.INVALID_APPWIDGET_ID
    private lateinit var settings: Settings
    private lateinit var hint: TextView
    private lateinit var action: Button
    private lateinit var list: LinearLayout
    private lateinit var save: Button

    /** 已从后端拿到的类型清单；也是「有没有载入成功」的标志。 */
    private var types: List<ApiClient.EventType> = emptyList()
    private var loading = false

    /** 每个勾选框对应的类型 key，顺序与界面一致 —— 保存时就按这个顺序写。 */
    private val boxes = mutableListOf<Pair<CheckBox, String>>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        widgetId = intent.getIntExtra(
            AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID,
        )
        // 先把结果定成「取消」：用户一路按返回键离开时就不会留下一个空小组件。
        // 点「保存」时才改写成 RESULT_OK。
        setResult(
            RESULT_CANCELED,
            Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId),
        )
        // 没有实例 id 就无从配置（正常路径下系统一定会带）。
        if (widgetId == AppWidgetManager.INVALID_APPWIDGET_ID) {
            finish()
            return
        }

        setContentView(R.layout.activity_widget_config)
        settings = Settings(this)
        hint = findViewById(R.id.configHint)
        action = findViewById(R.id.configAction)
        list = findViewById(R.id.configList)
        save = findViewById(R.id.configSave)

        save.isEnabled = false
        save.setOnClickListener { saveAndClose() }
        // 取消就是什么都不做地关掉 —— RESULT_CANCELED 已经在 onCreate 里定好了。
        findViewById<Button>(R.id.configCancel).setOnClickListener { finish() }
    }

    override fun onResume() {
        super.onResume()
        // 放在 onResume 而不是 onCreate：从 MainActivity（去填服务器地址）回来时会重试一次。
        if (types.isEmpty() && !loading) load()
    }

    private fun load() {
        if (!settings.configured) {
            // 没配服务器就读不到类型清单，先把用户送去主界面，别空白着干瞪眼。
            showAction(R.string.widget_config_open_app) {
                startActivity(Intent(this, MainActivity::class.java))
            }
            hint.setText(R.string.widget_config_need_setup)
            return
        }

        loading = true
        hint.setText(R.string.widget_config_loading)
        action.visibility = View.GONE
        list.removeAllViews()
        boxes.clear()

        // 这一页在系统「添加小组件」的流程里，onResume 之前不能 finish()，用 lifecycleScope
        // 让请求跟着 Activity 走：用户中途退出就直接取消，不会对着已销毁的 View 写。
        lifecycleScope.launch {
            val fetched = withContext(Dispatchers.IO) {
                ApiClient.fetchEventTypes(settings.baseUrl, settings.token)
            }
            loading = false
            // 没有 loading 期间被销毁的额外处理：lifecycleScope 在 onDestroy 时自动取消，
            // 协程根本走不到这里。
            if (fetched == null) {
                hint.setText(R.string.widget_config_failed)
                showAction(R.string.widget_config_retry) { load() }
            } else {
                types = fetched
                render(fetched)
            }
        }
    }

    /**
     * 画出勾选框。
     *
     * **顺序 = 桌面上的顺序**：配过的话把选中的按用户存的顺序排前面（勾选顺序就是当初的
     * 选择顺序），没勾的按后端顺序跟在后面。新加的类型自然排在末尾，不会被塞到中间。
     */
    private fun render(fetched: List<ApiClient.EventType>) {
        val picked = settings.widgetTypes(widgetId)
        val ordered =
            if (picked == null) {
                fetched
            } else {
                val byKey = fetched.associateBy { it.key }
                picked.mapNotNull { byKey[it] } + fetched.filter { it.key !in picked }
            }
        // 没配过 = 默认全选（等于「按后端顺序全都放上去」），配过就按存的勾。
        val checked = (picked ?: fetched.map { it.key }).toSet()

        action.visibility = View.GONE
        ordered.forEach { type ->
            val box = CheckBox(this)
            box.text = type.label
            box.isChecked = type.key in checked
            box.setOnCheckedChangeListener { _, _ -> updateHint() }
            list.addView(box)
            boxes += box to type.key
        }
        updateHint()
        save.isEnabled = true
    }

    private fun updateHint() {
        val none = boxes.none { it.first.isChecked }
        hint.setText(
            if (none) R.string.widget_config_hint_empty else R.string.widget_config_hint
        )
    }

    private fun showAction(textRes: Int, onClick: () -> Unit) {
        save.isEnabled = false
        action.setText(textRes)
        action.visibility = View.VISIBLE
        action.setOnClickListener { onClick() }
    }

    private fun saveAndClose() {
        settings.setWidgetTypes(widgetId, boxes.filter { it.first.isChecked }.map { it.second })
        // 让桌面立刻按新选择重画（会联网，refresh 内部自己切线程）。
        RecordWidgetProvider.refresh(this, widgetId)
        setResult(RESULT_OK, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId))
        finish()
    }
}
