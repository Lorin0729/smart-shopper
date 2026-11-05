# Smart Shopper — 降价监控 · 优惠叠加最优解 · 购买时机预测

一个本地优先（local-first）的网购省钱工具，零服务器成本即可 7×24 监控。

**监控与预警**

- 优衣库 / 京东 / 淘宝·天猫 价格与库存定时抓取，降价、历史新低、补货即时推送
- **通用站点监控**：拼多多、唯品会、天猫超市、品牌官网……一条正则即可接入
- 目标价提醒：`--target 500`，只在价格「穿越」目标价时提醒一次，不重复轰炸
- 尺码/SKU 级库存：`--size M`，只关心你穿的码，M 码补货单独通知
- **提醒冷却期**：按类型分别设置（降价 12h / 新低 24h / 补货 6h），避免反复轰炸
- **监控健康度**：连续抓取失败自动告警（`status` 可查），避免“监控悄悄失效却以为在盯”
- 多渠道通知：Server酱（微信）/ 钉钉 / 企业微信 / Telegram / **Bark / ntfy / PushDeer** / 邮件 SMTP / 通用 Webhook，`test-notify` 一键自检
- 云端免费监控：GitHub Actions 定时跑，产物 HTML 报告自动上传为 Artifact

**查看与分享**

- 终端报告、`report` 自包含 HTML 报告、`chart` SVG/PNG 图表
- **本地 Web 面板**：`serve` 启动零依赖只读面板，支持搜索、排序、价格曲线、尺码库存
- **清单备份与家庭共享**：`export` / `import-list`，可带价格历史，随时迁移或分享
- **多币种显示**：`--currency USD` 或配置默认币种，价格仍以 CNY 入库、仅展示换算

**省钱计算**

- 优惠叠加计算器：店铺券、跨店满减、会员折扣、红包、京豆/淘金币……穷举所有合法叠加组合，给出到手最低价与 TOP N 方案
- 多商品组合凑单：一起下单能吃到几档满减？再凑 ¥120 换 ¥50 值不值？分开买 vs 一起买差多少？

**购买时机预测**

- 历史统计：最低/最高/均价、价格分位、降价间隔与平均降幅、终端走势图
- 降价概率：历史降价节奏 + 平台大促日历（618 / 双11 / 双12 / 年货节…）融合估计未来 30 天
- **趋势外推**：对近 90 天价格做线性回归，给出 30 天后预测价与区间（数据不足时自动不出结论）
- 季节类目模型：回答“羽绒服发售多久后买最划算”——9 月上新定价最硬，双11 首个深折扣，次年 2 月中~3 月底季末清仓约 6.5 折是全年最低窗口
- 库存感知推荐：结合库存状态与趋势方向给出「立即购买 / 尽快入手 / 建议等待 / 关注补货」

**可视化与冷启动**

- 终端走势图 `▁▂▄█▅`、SVG/HTML/PNG 图表导出
- 自包含 HTML 报告：价格曲线 + 统计 + 预测 + 季节窗口，一个文件可直接分享
- 第三方历史价冷启动（慢慢买）或 CSV 导入，新商品添加即拥有历史数据

## 一键开始

```bash
# 方式一：直接运行（无需安装）
pip install -r requirements.txt
python main.py demo

# 方式二：安装成命令行工具
pip install -e .
smart-shopper demo
# 可选增强
pip install -e ".[chart]"    # PNG 图表（matplotlib）
pip install -e ".[render]" && playwright install chromium   # 渲染兜底
```

## 常用命令

```bash
# 1. 生成配置（可选，默认值即可跑）
python main.py init

# 2. 添加商品：目标价 + 关注尺码
python main.py add "https://item.jd.com/100012043978.html" \
  --category 羽绒服 --launch 2026-09-15 --target 500 --size M
python main.py add "https://www.uniqlo.cn/product-detail.html?productCode=u0000000064781" \
  --name 摇粒绒外套 --category 大衣 --target 199

# 3. 补历史价（让预测立刻可用），或用 --csv 导入自己的数据
python main.py seed 1
python main.py seed 1 --csv history.csv      # 表头 datetime,price

# 4. 一条命令出购买方案 / 监控
python main.py plan "https://item.jd.com/100012043978.html" --vip --height 178 --weight 70
python main.py plan 478897 --platform jd --vip --waist 82   # 裸编号需指定平台
python main.py advise u0000000066172          # 已跟踪商品：买/等/关注 + 可购价 + 关注尺码
python main.py check                          # 抓一轮
python main.py check --watch --interval 3600  # 持续监控

# 5. 看报告
python main.py list
python main.py analyze 1                      # 终端详细分析
python main.py report --out report.html       # HTML 报告（可分享）
python main.py chart 1 --format png           # 走势图：svg/html/png
python main.py history 1 --days 30 --csv price_log.csv
python main.py serve                          # 本地 Web 面板 http://127.0.0.1:8777

# 6. 省钱计算
python main.py deal --price 899 --platform jd           # 单品叠券 TOP3
python main.py cart cart.json                           # 购物车跨店凑单
python main.py bundle --filler-max 150                  # 已跟踪商品组合购买
python main.py bundle --file bundle.json                # 自定义组合

# 7. 维护与分享
python main.py edit 1 --target 450 --size L             # 改目标价/尺码
python main.py sizes 1 --refresh                        # 刷新尺码库存
python main.py status                                   # 监控健康度（抓取失败排查）
python main.py export --with-prices                     # 导出清单（含价格历史）
python main.py import-list wishlist.json --overwrite    # 导入并合并
python main.py test-notify                              # 通知渠道自检
python main.py rm 1

# 8. 尺码助手与多币种
python main.py fit --kind top --alpha L                 # 字母码 ↔ 中/美/欧/日
python main.py fit --kind top --height 178 --chest 100  # 按身材推荐
python main.py fit --kind shoes --cn 260                # 鞋码换算
python main.py --currency USD analyze 1                 # 用美元展示价格
```

`my_coupons.json`（`deal --coupons` / `bundle --coupons`）：

```json
[
  {"rid": "shop1", "name": "店铺券 满300减50", "kind": "full_off", "value": 50, "threshold": 300, "stack_group": "store"},
  {"rid": "cross", "name": "跨店每满300减50", "kind": "every_full_off", "value": 50, "threshold": 300, "step": 300},
  {"rid": "vip", "name": "88VIP 95折", "kind": "percent", "value": 0.95},
  {"rid": "red", "name": "红包", "kind": "fixed", "value": 18},
  {"rid": "bean", "name": "淘金币", "kind": "points", "value": 10, "max_off": 0.01}
]
```

`cart.json` / `bundle.json`（含商品与店铺）：

```json
{
  "items": [
    {"name": "羽绒服", "price": 800, "store": "店A"},
    {"name": "围巾", "price": 100, "store": "店B"}
  ],
  "coupons": [
    {"rid": "a1", "name": "店A券 满600减80", "kind": "full_off", "value": 80, "threshold": 600, "store": "店A"},
    {"rid": "cross", "name": "跨店每满300减50", "kind": "every_full_off", "value": 50, "threshold": 300, "step": 300}
  ]
}
```

## 一条命令算购买方案（plan）

```bash
# 链接最稳：京东 / 淘宝天猫 / 优衣库 / 通用站点
python main.py plan "https://item.jd.com/100012043978.html" --vip --height 178 --weight 70

# 裸数字编号必须指定平台，避免误判
python main.py plan 478897 --platform jd --vip --height 178 --weight 70 --waist 82

# 有券：券文件支持顶层数组或 {"coupons": [...]}
python main.py plan "https://item.taobao.com/item.htm?id=679876543210" \
  --coupons my_coupons.json --vip --height 178 --weight 70
```

输出固定包含：

- **结论**：买 / 等 / 关注
- **商品与可购价**：只取有货 SKU 的最低价（售罄配色不参与）
- **尺码建议**：按身高/体重/腰围推荐，并匹配商品实际 SKU 的库存与价格（上衣用身高+胸围/体重修正，裤装用腰围）
- **购买方案**：无会员 / 有 88VIP（或 PLUS）两套叠券，外加「再凑多少钱能多减多少」
- **价格时机**：往年同类商品的类目规律 + 本商品真实采样（样本不足时明确说明）

注意：

- 裸数字 `478897` 无法判断是京东还是淘宝，必须加 `--platform jd` 或 `--platform taobao`；品牌货号（非平台商品 ID）请直接发商品链接。
- `--vip` 只是标记「这套会员方案是我的」，工具仍会同时算无会员/有会员两套。
- 没有 `--coupons` 时使用演示券，会标注「未计入你的个人券」。
- 淘宝需要 `config.json` 的 `taobao.cookie`；京东价格接口在部分公司网络/代理下可能因证书校验失败，工具会如实报错，不会编造价格。

## 输出示例

```
商品分析：加厚羽绒夹克   [jd]
目标价 ¥500（当前 ¥779，还差 ¥129）
当前价 ¥779 | 最低 ¥759 | 最高 ¥899 | 均价 ¥837 | 采样 6 次
价格分位 17% | 有效降价 3 次 | 平均间隔 17.5 天 | 平均降幅 6%
走势 █▇█▄▁▂  （¥899 → ¥779）
库存：有货 现货（剩 23 件）
尺码库存（关注 M）：L:有货  M:有货  S:无货
--------------------------------------------------------------
◆ 降价预测（未来 30 天）：概率 35%
  预计窗口：10-31 ~ 11-11
◆ 购买时机（类目：羽绒服）
  1. [羽绒服季末清仓] 2027-02-10 ~ 2027-03-28  预期 6.5 折
  2. [发售 90~150 天折扣深水期] 2026-12-14 ~ 2027-02-12  预期 7.2 折
  3. [双11] 2026-10-20 ~ 2026-11-13  预期 7.5 折 ← 当前正处于该窗口
◆ 购买建议：▶ 建议等待（决策分 52/100）
```

组合凑单：

```
组合下单到手 ¥531.43（省 ¥247.57）
分开买最优合计 ¥550.24，组合额外省 ¥18.81
已触发：「跨店满减 每300减50」×2 档（共减 ¥100）
· 再凑 ¥121.00 可多触发一档，多减 ¥50（净亏 ¥71.00，不建议硬凑）
```

## 本地 Web 面板

```bash
python main.py serve                 # 默认 http://127.0.0.1:8777
python main.py serve --port 9000     # 自定义端口
```

零第三方依赖（标准库 HTTP 服务），只读、不会触发抓取：

- 卡片式列表，支持按决策分 / 距历史低价 / 当前价 / 降价概率排序
- 关键词搜索（商品名、类目、平台）
- 每个商品一条 SVG 价格曲线（含目标价虚线与最低点标注）
- 展示尺码库存、30 天降价概率、购买窗口与建议理由

接口：`GET /api/summary`（全部商品 JSON）、`GET /api/product/<id>`、`GET /api/health`。
默认只监听 `127.0.0.1`，不会暴露到局域网。

## 目录结构

```
smart-shopper/
├── main.py                # CLI：add/edit/list/rm/check/analyze/history/seed/
│                          #      chart/report/deal/cart/bundle/sizes/fit/serve/
│                          #      export/import-list/status/test-notify/init/demo
├── pyproject.toml         # 可 pip 安装（console script: smart-shopper）
├── core/
│   ├── models.py          # Product / PricePoint / InventorySnapshot / VariantSnapshot / HealthState
│   ├── storage.py         # SQLite（商品/价格/库存/尺码/提醒冷却/健康度，自动迁移旧库）
│   ├── sparkline.py       # 终端价格走势图
│   ├── currency.py        # 多币种展示换算
│   ├── variants.py        # 关注尺码匹配（颜色 + 尺码宽松匹配）
│   └── report.py          # SVG 走势图 + 自包含 HTML 报告 + PNG（可选）
├── datasource/            # 第三方历史价（慢慢买）+ CSV 导入
├── monitors/
│   ├── uniqlo.py          # 优衣库：候选接口链 / 限流重试 / TTL 缓存 / 库存富集
│   ├── uniqlo_parse.py    # 优衣库纯解析层（链接识别 / SKU 矩阵 / JSON-LD / 内嵌 JSON）
│   ├── jd.py              # 京东：p.3.cn 价格 + c0.3.cn 库存 + SKU 尺码
│   ├── taobao.py          # 淘宝/天猫：自备 Cookie（实验性）
│   ├── generic.py         # 通用站点：配置正则即可接入任意电商页
│   └── renderer.py        # Playwright 渲染兜底（可选依赖）
├── optimizer/
│   ├── deals.py           # 叠券穷举引擎
│   └── bundle.py          # 多商品组合凑单规划
├── predictor/
│   ├── forecast.py        # 历史统计、大促日历、降价概率、趋势回归、购买建议
│   ├── season.py          # 季节类目模型
│   └── sizing.py          # 尺码换算助手
├── webui/dashboard.py     # 本地只读 Web 面板（标准库）
├── notifier/              # Server酱/钉钉/企微/Telegram/Bark/ntfy/PushDeer/邮件/Webhook
├── .github/workflows/
│   ├── ci.yml             # 三版本 Python 跑测试
│   └── scheduled-check.yml# 云端定时监控 + 报告 Artifact
└── tests/                 # 114 个单元测试
```

## 配置

执行 `python main.py init` 生成 `config.json`（可选）：

```json
{
  "db": "shopper.db",
  "check_interval_seconds": 3600,
  "health_alert_after": 3,
  "currency": { "code": "CNY", "rates": { "USD": 0.14, "EUR": 0.128, "JPY": 20.6 } },
  "alert_cooldown_hours": {
    "drop": 12, "low": 24, "target": 0, "restock": 6,
    "variant": 6, "stockout": 12, "monitor": 24
  },
  "jd": { "area": "1_72_2799_0" },
  "uniqlo": {
    "api_template": "", "api_templates": [],
    "use_builtin_apis": true, "stock_api": true, "mobile_page": true,
    "cache_ttl_seconds": 300, "request_timeout": 15,
    "retry_times": 1, "retry_backoff_seconds": 1.5,
    "cookie": "", "playwright_fallback": true
  },
  "taobao": { "cookie": "", "playwright_fallback": true },
  "generic": {
    "sites": {
      "example.com": {
        "price_regex": "\"price\"\\s*:\\s*\"?([0-9.]+)",
        "stock_regex": "有货|立即购买",
        "stockout_regex": "售罄|已抢光",
        "name_regex": "<title>(.+?)</title>"
      }
    }
  },
  "notify": {
    "serverchan_key": "",
    "dingtalk_webhook": "",
    "wecom_webhook": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "bark_url": "",
    "ntfy_topic": "",
    "ntfy_token": "",
    "pushdeer_key": "",
    "webhook_url": "",
    "email": {
      "smtp_host": "smtp.qq.com", "smtp_port": 465,
      "username": "", "password": "", "from": "", "to": ""
    }
  }
}
```

- `jd.area`：库存查询收货区域码（`1_72_2799_0` 为北京），不同区域库存不同
- `alert_cooldown_hours`：同类提醒的冷却小时数，可只填想改的项；填 `0` 表示每次都提醒
- `health_alert_after`：连续抓取失败多少次后推送「监控异常」
- `currency`：默认展示币种与汇率（相对 1 CNY），也可用 `--currency` 临时覆盖
- `notify.*`：多渠道可同时启用；邮件用 SMTP，465 端口走 SSL，587 走 STARTTLS
- `playwright_fallback`：解析失败时是否用浏览器渲染兜底（需先安装 playwright）
- 配置也可用环境变量 `SMART_SHOPPER_CONFIG`（JSON 字符串）注入，优先级高于文件——GitHub Actions 就是这样读 Secrets 的

## 优衣库监控说明（中国站）

优衣库是中国站主力数据源：价格、颜色 × 尺码库存、划线价、折扣率都能抓，并且带多级降级与限流处理。

最简用法：直接给货号，一条命令出结论（可购最低价、关注尺码、叠券、该不该等）：

```bash
python main.py advise u0000000066172
```

### 支持的链接形式

```bash
# 商品页链接（带推广参数也能识别）
python main.py add "https://www.uniqlo.cn/product-detail.html?productCode=u0000000066172&utm_source=xxx" --size "乳白色 M"

# 移动端 / 分享链接
python main.py add "https://h.uniqlo.cn/?type=pc#/product?pid=u0000000066172"

# 路径里带编号的链接
python main.py add "https://www.uniqlo.cn/zh_CN/u0000000066172.html"

# 直接填商品编号
python main.py add "u0000000066172"
```

- `productCode`、`pid` 参数、链接中任意位置的 `u` + 13 位数字都能识别；
- `u.uniqlo.cn` 短链会在 `add` 阶段联网展开一次（普通链接不会产生额外请求）；
- 识别失败会明确提示支持的形式，不会静默失败。

### 抓取链路

按优先级逐级尝试，单层失败只记录原因、不影响后续降级：

1. `uniqlo.api_template` / `api_templates`（自定义接口）
2. 官方静态商品 JSON：完整颜色 × 尺码矩阵、价格、划线价
3. 官方详情接口 JSON：价格兜底
4. 官方摘要 JSON：商品级价格/库存兜底
5. 商品页 HTML：JSON-LD → 内嵌 JSON → 页面可见价格
6. 移动端页面
7. Playwright 渲染（需安装 playwright）

库存单独调用官方 `POST /stock/stock/query/zh_CN`（`type=DETAIL`），一次请求拿到所有 SKU 的 `skuStocks` 映射。价格取全部 SKU 的最低现价，因此某个便宜颜色售罄时价格历史不会被推高；该颜色是否可买看尺码表。

### 配置项（`config.uniqlo`）

| 配置 | 默认 | 说明 |
|---|---|---|
| `api_template` | `""` | 自定义 JSON 接口，支持 `{code}` 或 `{}` 占位 |
| `api_templates` | `[]` | 额外候选接口，按顺序尝试 |
| `use_builtin_apis` | `true` | 是否使用内置静态 JSON / 详情接口 |
| `stock_api` | `true` | 是否调用库存接口富集 SKU 库存 |
| `mobile_page` | `true` | 是否尝试移动端页面兜底 |
| `cache_ttl_seconds` | `300` | 同一商品重复 check 的缓存时间；`0` 表示不缓存 |
| `request_timeout` | `15` | 单次请求超时秒数 |
| `retry_times` | `1` | 429/5xx/超时的重试次数（403 不重试） |
| `retry_backoff_seconds` | `1.5` | 重试退避基数，会优先遵守 `Retry-After` |
| `cookie` | `""` | 被风控时可填浏览器 Cookie |
| `playwright_fallback` | `true` | 解析失败时用浏览器渲染兜底 |

`fetch` 与 `variants` 共享同一份缓存：一次 `check` 只发 1 次商品请求 + 1 次库存请求。

### 尺码库存与提醒

```bash
python main.py check
python main.py sizes uniqlo:u0000000066172          # 查看颜色 × 尺码矩阵
python main.py sizes uniqlo:u0000000066172 --refresh # 立即重新抓取
```

`--size` 支持三种写法：

- `M`：所有颜色的 M；
- `黑色`：该颜色所有尺码；
- `乳白色 M` / `乳白色 / M`：精确到颜色 + 尺码。

关注的 SKU 由「有货 → 无货」或「无货 → 有货」都会提醒（按 `alert_cooldown_hours.variant` 去重，默认 6 小时）；不写 `--size` 时，任一尺码补货都会提醒。

### 常见问题

- **403 / 429 被限流**：`check` 间隔调到 3600 秒以上；在 `uniqlo.cookie` 填浏览器 Cookie；短期可用 `--interval` 拉长；项目会自动读取 `Retry-After` 并退避重试一次。
- **返回 404**：商品下架，或官网接口/静态 JSON 路径改版。可先用浏览器打开商品页确认编号，再升级项目或配置 `uniqlo.api_template`。
- **价格有了但库存未知**：库存接口失败时不会伪造「有货」，`sizes` 会显示「库存未知」，也不会误触发补货提醒。
- **短链打不开**：先在浏览器打开 `u.uniqlo.cn` 短链，复制跳转后的完整地址再 `add`。

## 通用站点监控（拼多多 / 唯品会 / 天猫超市 / 任意站点）

没有专门适配的平台，用 `--platform generic` + 配置正则即可：

```bash
python main.py add "https://mobile.yangkeduo.com/goods.html?goods_id=123" \
  --platform generic --name 拼多多羽绒服 --category 羽绒服 --target 299
```

然后在 `config.json` 的 `generic.sites` 里按域名后缀配置（`*` 为兜底规则）：

```json
{
  "generic": {
    "sites": {
      "mobile.yangkeduo.com": {
        "price_regex": "\"minPrice\"\\s*:\\s*([0-9.]+)",
        "stock_regex": "立即购买|加入购物车",
        "stockout_regex": "已售罄|已抢光",
        "name_regex": "<title>(.+?)</title>",
        "cookie": "",
        "playwright": true,
        "encoding": "utf-8"
      }
    }
  }
}
```

- `price_regex` 取第 1 个捕获组（无捕获组则取整个匹配），支持 `¥1,299.00` 这类带符号/逗号的写法
- `stock_regex` 命中视为有货，`stockout_regex` 命中视为无货；都没命中则库存记为未知
- `playwright: true`：该站点请求后用浏览器渲染再解析（应对前端渲染或风控）
- 查找价格的正则可先用浏览器「查看源代码」或 `curl` 得到 HTML，再定位价格字段

## 尺码助手

```bash
python main.py fit --kind top --table                    # 男装上衣完整对照表
python main.py fit --kind top --alpha L                 # L 对应中/美/欧/日码
python main.py fit --kind top --height 178 --chest 100  # 按身材推荐
python main.py fit --kind top --gender women --chest 86
python main.py fit --kind shoes --cn 260                # 中国 260mm → 欧 42 / 美 8.5 / 英 8
python main.py fit --kind shoes --gender women --eu 38
```

数据是常见成衣尺码表的近似整理（优衣库、京东等品牌通用性较高）。不同版型（修身/宽松）会有偏差，
下单前请以商品页尺码表为准；数值落在交界处时会提示「建议选大一码」。

## 多币种显示

```bash
python main.py --currency USD list
python main.py --currency JPY analyze 1
```

或在 `config.json` 里设默认币种：`"currency": { "code": "USD", "rates": { "USD": 0.14 } }`。

价格始终以人民币入库，只在展示层换算，因此历史数据不会因汇率变化而失真；
汇率是本地静态近似值（可自行覆盖），不联网、不用于交易。Web 面板会跟随同一配置自动换算坐标轴与数值。

## 监控健康度

```bash
python main.py status
```

- 每轮抓取都会记录成功/失败；连续失败达到 `health_alert_after`（默认 3 次）自动推送「监控异常」
- `status` 展示每件商品的连续失败次数、最近成功时间与最近错误
- 常见失败原因：Cookie 失效、商品下架、接口改版、网络/代理受限
- 连续失败告警同样受冷却期约束（默认 24 小时），不会每轮重复刷屏

## 提醒冷却与去重

降价的细节抖动很容易造成重复提醒，本项目对每类提醒按商品单独记录时间戳：

| 类型 | 默认冷却 | 说明 |
|---|---|---|
| `drop` 降价 | 12 小时 | 同一商品 12 小时内只提醒一次降价 |
| `low` 历史新低 | 24 小时 | 反复触及最低价不会重复推送 |
| `target` 目标价 | 0（仅穿越时） | 从高于目标价跌到目标价才提醒，之后不重复 |
| `restock` 补货 | 6 小时 | 商品整体补货 |
| `variant` 尺码补货 | 6 小时 | 关注尺码补货 |

改冷却时长只需调整 `config.alert_cooldown_hours`，例如全部改成 3 小时：

```json
{ "alert_cooldown_hours": 3 }
```

## 备份与家庭共享

```bash
python main.py export --out wishlist.json               # 只导出商品清单与目标价
python main.py export --with-prices --out backup.json   # 连同价格历史一起备份

# 在另一台电脑 / 家人设备上
python main.py import-list wishlist.json                # 合并：已存在则跳过
python main.py import-list wishlist.json --overwrite    # 覆盖本地字段
```

导入会保留各自的本地价格历史，并按时间戳合并去重，适合“我挑好清单，家人按同样目标价一起盯”。

## 部署到 GitHub Actions（免费云端监控）

1. 仓库 **Settings → Secrets and variables → Actions** 新建 secret：名称 `SMART_SHOPPER_CONFIG`，值为完整 config.json（含通知渠道配置）
2. 本地 `python main.py add ...` 添加商品后强制提交数据库：`git add -f shopper.db && git commit && git push`
3. 完成。`.github/workflows/scheduled-check.yml` 每 6 小时运行 `check`，用 `actions/cache` 延续价格历史，并把 `report.html` 作为 Artifact 上传，可直接下载查看

也可在 Actions 页面手动触发（workflow_dispatch）立即检查一轮。

## 接口适配说明

- **京东**：价格 `p.3.cn`、库存 `c0.3.cn` 是社区长期使用的公开端点，无需登录；尺码 SKU 从商品页 `colorSize` 解析后逐个查库存（默认最多 12 个）
- **优衣库**：默认解析商品页 JSON-LD/内嵌 JSON；若官网改版导致失败，可配置 `uniqlo.api_template`（`{}` 占位 productCode），或安装 Playwright 启用渲染兜底
- **淘宝/天猫**：接口有签名与风控，须自备浏览器 Cookie（实验性，失效需更新）
- **慢慢买**：`seed` 使用其非官方历史价端点，结构可能调整；失败时请用 `--csv` 导入

抓取均为低频轮询（默认 1 小时一轮）。请遵守各平台服务条款，勿高频请求，数据仅供个人购物决策参考。

## 预测方法

| 信号 | 说明 |
|---|---|
| 历史降价节奏 | 平均降价间隔临近 → 概率 +30% |
| 大促日历 | 30 天内有 618/双11 等大促 → 概率 +35% |
| 价格分位 | 当前价处历史低位 → 降低再降预期 |
| 季节模型 | 类目清仓月 + 发后天数（羽绒类 90~150 天） |
| 趋势外推 | 近 90 天线性回归：下行且 30 天预测跌幅 ≥8% → 建议再等；上行且处低位 → 建议入手 |
| 库存 | 无货→关注补货；低价 + 库存紧张→尽快入手 |

决策融合为 0-100 分，映射到「立即购买 / 尽快入手 / 建议等待 / 关注补货」。目标价提醒采用「穿越」判定，同一目标价只提醒一次。
趋势外推在样本少于 4 个或跨度不足 5 天时自动不出结论，避免给出假精度；区间至少 ±1%。

## 测试

```bash
python -m unittest discover -s tests -v
```

114 个单元测试，覆盖：叠券数学与互斥组、组合凑单、季节窗口、降价预测与建议、趋势回归、走势图/报告渲染、CSV 导入、SQLite 迁移、尺码存储、提醒冷却、通用站点正则解析、Web 面板数据、清单导入导出、币种换算、尺码助手、监控健康度、优衣库链接识别/SKU 矩阵解析/库存合并/缓存/限流重试/多级降级、advise 可购价口径（售罄配色不参与决策）、plan 会员/凑单/尺码推荐（身高体重腰围 → 尺码）。

## Roadmap

- [x] 通用站点价格源（拼多多 / 唯品会 / 天猫超市 / 任意站点正则接入）
- [x] 尺码/SKU 级库存与关注尺码补货提醒
- [x] 尺码换算助手（优衣库/JD/欧码对照）
- [x] 价格提醒冷却期与去重
- [x] 本地只读 Web 面板
- [x] 购物清单导出/导入（家庭共享与备份）
- [x] 移动端推送渠道（Bark / ntfy / PushDeer）
- [x] 预测模型升级（趋势回归 + 置信区间）
- [x] 多币种显示
- [x] 监控健康度（连续抓取失败告警）
- [ ] 拼多多/唯品会 原生 API 适配（需处理签名与风控）
- [ ] 多用户协同（云端共享同一份监控数据）
- [ ] 到达提醒（在途物流/到货通知）

## 免责声明

本项目仅供学习交流与个人购物参考，不构成任何消费建议；使用时请遵守目标平台的服务条款与 robots 协议，因使用本项目产生的任何问题由使用者自行承担。

## License

MIT
