# LifeLab 安卓采集器

两件事：后台采集手机各应用**分小时**使用时长定时上传；在**桌面小组件**上
一键记录事件、在 **App 内置的 WebView** 里看数据、写复盘。

## 1. 准备后端

1. 后端以 Tailscale 模式启动：`uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. 创建设备拿到 token（只显示一次）：
   - Swagger `http://<你的TailscaleIP>:8000/docs` → `POST /devices`
     body: `{"name": "我的手机", "platform": "android"}`
   - 记下返回的 `token`

## 2. 构建 APK

需要 Android Studio（含 Android SDK）：

1. 安装 https://developer.android.com/studio（默认下一步即可）
2. Android Studio → **Open** → 选择本目录 `E:\my_app\android`
   （工程未附带 gradle-wrapper.jar，首次打开时 AS 会提示或自动使用自带的
   Gradle 8.4；若提示 "Gradle wrapper is missing"，点 OK/使用默认即可）
3. 首次打开会自动下载 Gradle 8.4 与依赖（需联网，约几分钟）
4. 菜单 **Build → Build App Bundle(s) / APK(s) → Build APK(s)**
5. 产物：`app/build/outputs/apk/debug/app-debug.apk`

不用 Android Studio 也能出包（需本机有 JDK 17）：

```
cd android
gradle :app:assembleDebug        # 工程没带 wrapper，用系统 gradle 或 AS 自带的
```

手机用数据线连接后，点 AS 顶部 **Run ▶** 可直接安装；也可把 APK 传到手机点击安装
（需在手机上允许"安装未知来源应用"）。

## 3. 手机配置

1. 打开 App → 「授予『使用情况访问』权限」→ 在系统设置页选中 LifeLab 采集
2. 填入：
   - 服务器地址：`http://<你的TailscaleIP>:8000`
   - token：第 1 步拿到的
3. 点「保存配置」（自动安排每 4 小时后台上传）
4. 点「立即上传今日数据」验证——成功会显示上传的小时记录条数

## 4. 桌面小组件（一键记录）

1. 长按桌面空白处 → 小组件 → 找到 **LifeLab 记录** → 拖到桌面
2. **接着会弹配置页**：勾选要放哪几个记录类型 → 「保存」。
   这一步是必经的——在配置页按返回键 = 这个小组件不添加（`android:configure` 的约定）
3. 默认 2×2 格、放 4 个按钮；**拖高会自己多出按钮行**（2×3 正好放 8 个，不空也不裁）
4. 顶部那行「⚙ 今日 N 条 · 连续 M 天」是配置入口，点它随时回来改勾选；
   下面的按钮是一按即记，点完立刻刷新计数
5. 计数每 30 分钟自动刷一次（系统对 `updatePeriodMillis` 的下限）；点按钮会立即刷

按钮清单来自后端的类型清单（`GET /ingest/event-types`），**跟着网页端走**：网页端
「管理类型」里新增的会出现在配置页，归档掉的会从配置页消失（已勾选的不用手动清理，
自动落掉）。**勾选顺序就是桌面上按钮的顺序**，新加的类型排在末尾；顺序想改就取消
勾选再按想要的顺序重勾。放不下的类型收进最后一格「更多…」→ 打开内置网页端。

**没配上服务器地址/token 时**，小组件只显示一行提示和一个「打开设置」按钮，配置页
也会把你送到主界面去填。

## 5. 内置网页端

App 主界面的「打开网页端」按钮，以及小组件的「更多…」，都会在应用内打开
`settings.baseUrl`（落地就是记录页）——不用再开浏览器。

- 用的是系统 WebView，**没有内置任何浏览器内核**；前端与 API 同源（Tailscale
  Funnel），所以也不涉及 CORS
- 登录态存在 WebView 的 localStorage 里，所以刷新/重开不用重新登录
- 返回键先退网页历史，退到底才退出活动

## 6. 验证数据

Web 端用手机浏览器打开 `http://<你的TailscaleIP>:5173`，或电脑上访问：

```
GET http://127.0.0.1:8000/devices/usage-hourly/<今天日期>
```

## 说明与限制

- 上传内容：应用包名、展示名、**本地小时（0-23）**、该小时前台秒数、打开次数（`switches`）
- 时长口径：用 `UsageEvents` 重建「某应用在前台」的区间，按本地整点切段累加；切到别的应用 / PAUSED / 息屏锁屏即停止计时
- 整日替换：同设备同一天重复上传会覆盖（每次带当天**全部小时**），手机丢包/重试安全
- 权限：`PACKAGE_USAGE_STATS` 是特殊权限，只能用户手动授予，不能代码申请
- 后台策略：WorkManager 每 4 小时（Android 可能因省电策略延后，属正常）
- 小组件记录走的是**设备令牌**（`POST /ingest/events`），落库 `source=manual`，
  与网页端「手动记录」同一条通路、同一套校验；未知类型 422
- 小组件的**按钮数**由宿主报的格高算出来（见 `RecordWidgetProvider.visibleRows`），
  几何绑在两个常量上：`TOOLBAR_DP`（头部那行）和 `BUTTON_ROW_DP`（一行按钮），
  后者要与 `widget_record_button.xml` 的上下 padding + 文字 + margin 对得上。
  真机上按钮和格子对不齐，就调这两个数
- 按钮的**宽度**仍写死在 XML 槽位里（RemoteViews 没法设 `layout_weight`），
  所以记录类型的名字别取太长——两列时每格只有半个小组件宽
- 「每个实例一份配置」存在 `SharedPreferences`（`lifelab` / `widget_types_<实例id>`），
  `onDeleted` 时清掉；换桌面/清应用数据会丢，重配一次即可
