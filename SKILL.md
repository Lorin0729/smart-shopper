---
name: smart-shopper
description: 电商降价监控与购买决策助手。当用户想盯某件商品降价、查颜色/尺码库存、设置目标价与补货提醒、计算店铺券/跨店满减/会员折扣的最优叠加、多商品凑单、或预测"什么时候买最便宜"时使用。支持优衣库（中国站，完整颜色×尺码矩阵）、京东、淘宝/天猫与任意通用站点。
---

# Smart Shopper 使用说明（给 AI Agent）

这是一个本地优先的命令行工具，所有数据存在本机 SQLite（默认 `shopper.db`），不上传服务器。
本文件是给智能体的操作手册：按下面的命令直接执行即可，不要自己重写抓取逻辑。

## 0. 环境准备

在**仓库根目录**执行（Windows PowerShell / macOS / Linux 通用）：

```bash
python -m pip install -r requirements.txt      # 只需要 requests
python main.py demo                            # 离线自检：不联网跑通全部分析链路
python main.py init                            # 可选：生成 config.json（不加也能跑默认值）
```

- Python ≥ 3.10。
- 可选增强：`pip install -e ".[chart]"` 出 PNG 图表；`pip install playwright && playwright install chromium` 启用渲染兜底。
- 配置优先级：`SMART_SHOPPER_CONFIG` 环境变量（JSON 字符串）> `--config` 指定文件 > `./config.json` > 内置默认值。

## 1. 最常用的四步

```bash
# ① 添加商品：链接可带推广参数；--size 支持 "M"、"黑色"、"黑色 M"、"160/84A/M"
python main.py add "https://www.uniqlo.cn/product-detail.html?productCode=u0000000066172" \
  --name 摇粒绒高领上衣 --category 大衣 --target 599 --size "乳白色 M"

# ② 抓一轮：价格、库存、尺码变化，命中目标价/补货/降价就推送
python main.py check

# ③ 看尺码矩阵（--refresh 先联网刷新，会打印每个颜色×尺码的价格与库存）
python main.py sizes 1 --refresh

# ④ 分析：历史统计 + 降价概率 + 购买窗口 + 建议
python main.py analyze 1
```

- `add` 成功后返回 `平台:商品ID`（如 `uniqlo:u0000000066172`），后续命令可用序号 `1` 或这个 ID。
- `check` 是全量流程：遍历所有商品 → 写库 → 判断提醒 → 推送。单个商品失败不影响其他商品。
- 想长期挂着：`python main.py check --watch --interval 3600`。

## 2. 命令速查

| 目的 | 命令 |
|---|---|
| 添加/修改/删除 | `add <链接> [--target 500] [--size M] [--category 羽绒服] [--launch 2026-09-15]` / `edit 1 --target 450` / `rm 1` |
| 列表 | `list` |
| 抓取一轮 / 持续 | `check` / `check --watch --interval 3600` |
| 尺码库存 | `sizes 1 --refresh` |
| 历史与导出 | `history 1 --days 30 --csv price_log.csv` |
| 分析预测 | `analyze 1` |
| 图表 / 报告 | `chart 1 --format svg\|html\|png` / `report --out report.html` |
| 本地面板 | `serve --port 8777` → http://127.0.0.1:8777 |
| 单品叠券 | `deal --price 899 --platform jd --coupons my_coupons.json` |
| 购物车凑单 | `cart cart.json` |
| 已跟踪商品组合 | `bundle --filler-max 150` / `bundle --file bundle.json` |
| 补历史价 | `seed 1`（慢慢买）/ `seed 1 --csv history.csv`（表头 `datetime,price`） |
| 备份 / 迁移 | `export --with-prices` / `import-list wishlist.json --overwrite` |
| 健康度 / 通知自检 | `status` / `test-notify` |
| 尺码换算 | `fit --kind top --height 178 --chest 100` / `fit --kind shoes --cn 260` |
| 多币种展示 | 任意命令加 `--currency USD` |

## 3. 平台注意事项

### 优衣库（主力数据源）

支持：`productCode=u...` 商品页链接、`h.uniqlo.cn` 移动端链接、`u.uniqlo.cn` 短链（`add` 时联网展开一次）、路径带编号的链接，或直接给 `u` + 13 位数字的编号。

- 抓取按优先级走 7 层：自定义接口 → 官方商品 JSON → 详情接口 → 摘要 JSON → 商品页 HTML（JSON-LD/内嵌 JSON/可见价格）→ 移动端页 → Playwright 渲染。
- 库存来自官方 `POST /p/stock/stock/query/zh_CN`（`type=DETAIL`），一次拿到全部 SKU 的 `skuStocks`。
- 商品级价格取**全部 SKU 最低现价**（含售罄配色），所以 `list`/`check` 记录的价格可能低于你关注的那个颜色；能否买请看 `sizes`。
- 会员价字段已预留，但官方接口目前不返回金额。
- 403/429：403 不重试直接降级到页面；429/5xx/超时会按 `Retry-After` 或退避重试。仍失败时把命令行输出的「已尝试 N 种方式 + 建议」如实转述给用户。
- 可调配置（`config.json` 的 `uniqlo` 块）：`cache_ttl_seconds`（默认 300）、`request_timeout`、`retry_times`、`retry_backoff_seconds`、`cookie`、`headers`、`stock_api`、`mobile_page`、`playwright_fallback`。被限流时可调大 TTL 与 `retry_backoff_seconds`，或填浏览器 Cookie。

### 京东

`add "https://item.jd.com/100012043978.html"`；库存按 `jd.area`（默认北京 `1_72_2799_0`）查询，不同区域不同。

### 淘宝/天猫

实验性，需要 `taobao.cookie`（从浏览器复制登录 Cookie）；无 Cookie 时多数商品抓不到价格，应提示用户而不是反复重试。

### 通用站点

在 `config.json` 的 `generic.sites` 里按域名配置 `price_regex` / `stock_regex` / `stockout_regex` / `name_regex`，然后 `add <url> --platform generic`。

## 4. 提醒与去重

- 提醒类型：`drop`（降价）、`low`（历史新低）、`target`（穿越目标价）、`restock`（商品补货）、`variant`（关注尺码补货/售罄）、`stockout`、`monitor`（连续抓取失败）。
- 同类提醒有冷却期，配置在 `alert_cooldown_hours`（默认 drop 12h / low 24h / target 0 / restock 6h / variant 6h / stockout 12h / monitor 24h）。
- 通知渠道在 `notify` 块配置：Server酱、钉钉、企业微信、Telegram、Bark、ntfy、PushDeer、邮件 SMTP、通用 Webhook；`test-notify` 可自检。
- 没有配置任何通知渠道时，`check` 只写库并打印 `[记录]`，不会丢数据。

## 5. Agent 行为准则

1. **先读后改**：改代码前先读 `README.md` 与对应模块；项目风格是标准库优先、中文 CLI 输出、单测不触网。
2. **不伪造数据**：抓取失败就展示原始 `[失败]` 信息与建议，绝不编造价格/库存；库存未知时不要当成有货。
3. **不擅自清库**：不要删除或覆盖用户的 `shopper.db`、`config.json`；需要重置时先征得同意。
4. **避免频繁请求**：同一商品短时间重复查询优先用缓存/`sizes`（默认 TTL 300s），不要写循环猛刷，容易触发 403/429。
5. **改完必测**：`python -m unittest discover -s tests` 必须全绿；新增解析逻辑请用 `tests/fixtures/` 下的本地样本，不要依赖真实网络。
6. **不提交隐私文件**：`shopper.db`、`config.json`（含 Cookie/密钥）、导出清单都不要提交到 git。
7. **输出结论**：执行完给用户一句话结论（当前价 / 目标价差距 / 建议动作），再附关键明细，不要把整屏日志直接倒给用户。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| `[失败] ... 已尝试 N 种方式` | 看逐条原因：404 多半编号错误；403 是反爬，稍后重试或填 Cookie；空响应可能是接口变更 |
| 优衣库显示「已尝试 6 种方式 / Playwright 未安装」 | `pip install playwright && playwright install chromium` 后重试 |
| 价格与实际页面不一致 | 优衣库取全 SKU 最低价；以 `sizes` 里对应颜色/尺码的价格为准 |
| 提醒没收到 | `test-notify` 自检渠道；`status` 看连续失败次数 |
| 想换数据库/配置 | `--db path.db` / `--config config.json` / 环境变量 `SMART_SHOPPER_CONFIG` |
