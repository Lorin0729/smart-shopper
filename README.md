# Smart Shopper — 降价监控 · 优惠叠加最优解 · 购买时机预测

一个本地优先（local-first）的网购省钱工具：

- **优衣库 / 京东 / 淘宝 降价监控**：定时抓取价格，降价、历史新低、补货立即提醒
- **目标价提醒**：`add --target 500`，降到心理价位自动叫你，不用天天盯
- **优惠叠加计算器**：店铺券、跨店满减、会员折扣、红包、京豆/淘金币……穷举所有合法叠加组合，给出到手最低价与 TOP N 方案
- **购买时机预测**：基于历史降价节奏 + 平台大促日历（618 / 双11 / 双12 / 年货节…）估计未来 30 天降价概率与窗口
- **季节类目模型**：回答“羽绒服发售多久后买最划算”这类问题——9 月上新定价最硬，双11 出现首个深折扣，次年 2 月中~3 月底季末清仓约 6.5 折是全年最低窗口
- **库存感知推荐**：结合库存状态（现货/无货/紧张/剩余件数）给出「立即购买 / 尽快入手 / 建议等待 / 关注补货」决策
- **终端走势图 + CSV 导出**：`▁▂▄█▅` 一眼看出当前价在历史中的位置
- **多渠道通知**：Server酱（微信）/ 钉钉 / 企业微信 / Telegram / 通用 Webhook
- **云端免费监控**：一条 GitHub Actions workflow，无需自己开电脑（见下文）

## 快速开始

```bash
pip install -r requirements.txt

# 离线演示（无需任何配置，模拟一件羽绒服的完整分析链路）
python main.py demo

# 一键生成配置文件
python main.py init

# 添加跟踪商品（--target 设置目标价，可选）
python main.py add "https://item.jd.com/100012043978.html" --category 羽绒服 --launch 2026-09-15 --target 500

# 立即抓取一轮（降价/新低/达标/补货会打印并可推送）
python main.py check

# 持续监控，每 3600 秒一轮
python main.py check --watch --interval 3600

# 查看分析报告：历史统计 + 走势图 + 降价预测 + 购买窗口 + 建议
python main.py list
python main.py analyze 1

# 价格历史与导出
python main.py history 1 --days 30 --csv price_log.csv

# 优惠叠加：¥899 的商品叠大促券（内置演示券）
python main.py deal --price 899 --platform jd

# 使用自己的券配置
python main.py deal --price 899 --platform taobao --coupons my_coupons.json

# 购物车跨店凑单优化
python main.py cart cart.json
```

`my_coupons.json` 示例：

```json
[
  {"rid": "shop1", "name": "店铺券 满300减50", "kind": "full_off", "value": 50, "threshold": 300, "stack_group": "store"},
  {"rid": "cross", "name": "跨店每满300减50", "kind": "every_full_off", "value": 50, "threshold": 300, "step": 300},
  {"rid": "vip", "name": "88VIP 95折", "kind": "percent", "value": 0.95},
  {"rid": "red", "name": "红包", "kind": "fixed", "value": 18},
  {"rid": "bean", "name": "淘金币", "kind": "points", "value": 10, "max_off": 0.01}
]
```

`cart.json` 示例：

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

## 分析报告长什么样

```
==============================================================
商品分析：加厚羽绒夹克   [jd]
==============================================================
当前价 ¥819 | 最低 ¥819 | 最高 ¥899 | 均价 ¥871.5 | 采样 4 次
价格分位 0% | 有效降价 3 次 | 平均间隔 17.5 天 | 平均降幅 4%
库存：有货 现货（剩 23 件）
--------------------------------------------------------------
◆ 降价预测（未来 30 天）：概率 25%
  预计窗口：10-31 ~ 11-11
  依据：30 天内有大促「双11」（峰值 11-11）……
--------------------------------------------------------------
◆ 购买时机（类目：羽绒服）
  1. [羽绒服季末清仓] 2027-02-10 ~ 2027-03-28  预期 6.5 折
  2. [发售 90~150 天折扣深水期] 2026-12-14 ~ 2027-01-29  预期 7.2 折
  3. [双11] 2026-10-20 ~ 2026-11-13  预期 7.5 折 ← 当前正处于该窗口
  ...
--------------------------------------------------------------
◆ 购买建议：▶ 立即购买（决策分 85/100）
  - 当前价 ¥819 已是历史最低附近
  - 正处于「双11」窗口
```

## 目录结构

```
smart-shopper/
├── main.py                # CLI 入口（add/list/rm/check/analyze/deal/cart/history/init/demo）
├── core/
│   ├── models.py          # Product / PricePoint / InventorySnapshot
│   ├── storage.py         # SQLite 存储（商品、价格、库存历史，自动迁移）
│   └── sparkline.py       # 终端价格走势图
├── monitors/
│   ├── uniqlo.py          # 优衣库官网（JSON-LD / 内嵌 JSON 解析，接口可配置）
│   ├── jd.py              # 京东（p.3.cn 价格 + c0.3.cn 库存）
│   └── taobao.py          # 淘宝/天猫（自备 Cookie，实验性）
├── optimizer/deals.py     # 叠券穷举引擎 + 跨店凑单优化
├── predictor/
│   ├── forecast.py        # 历史统计、大促日历、降价概率、购买建议
│   └── season.py          # 季节类目模型（羽绒服/大衣/夏装/球鞋…）
├── notifier/              # 控制台 + Server酱/钉钉/企业微信/Telegram/Webhook
├── .github/workflows/
│   ├── ci.yml             # 三版本 Python 跑测试
│   └── scheduled-check.yml# 云端每 6 小时定时监控
└── tests/                 # 单元测试（python -m unittest discover -s tests）
```

## 配置

执行 `python main.py init` 生成 `config.json`（可选）：

```json
{
  "db": "shopper.db",
  "check_interval_seconds": 3600,
  "jd": { "area": "1_72_2799_0" },
  "uniqlo": { "api_template": "" },
  "taobao": { "cookie": "" },
  "notify": {
    "serverchan_key": "",
    "dingtalk_webhook": "",
    "wecom_webhook": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "webhook_url": ""
  }
}
```

- `jd.area`：库存查询的收货区域码（`1_72_2799_0` 为北京），不同区域库存可能不同
- `notify.*`：通知渠道可同时启用多个；Server酱推微信，钉钉/企业微信填群机器人 Webhook，Telegram 填 Bot Token + Chat ID
- 配置也可用环境变量 `SMART_SHOPPER_CONFIG`（JSON 字符串）注入，优先级高于文件——GitHub Actions 就是这样读 Secrets 的

## 部署到 GitHub Actions（免费云端监控）

不想挂着电脑？把监控搬到 GitHub Actions，每 6 小时自动跑一轮并推送通知：

1. 在本仓库 **Settings → Secrets and variables → Actions** 新建 secret，名称 `SMART_SHOPPER_CONFIG`，值为完整 config.json 内容（含通知渠道配置）
2. 本地 `python main.py add ...` 添加商品后，强制提交数据库：`git add -f shopper.db && git commit && git push`（私有仓库无隐私顾虑；公开仓库请注意商品列表本身不敏感，Cookie 等只在 Secret 中）
3. 完成。仓库自带 `.github/workflows/scheduled-check.yml`，会定时运行 `python main.py check`，通过 `actions/cache` 在多轮之间延续价格历史，命中目标价/降价/补货时推送到你的微信或钉钉

也可在 Actions 页面手动触发（workflow_dispatch）立即检查一轮。

## 接口适配说明

- **京东**：价格 `p.3.cn`、库存 `c0.3.cn` 是社区长期使用的公开端点，无需登录
- **优衣库**：官网为前端渲染，默认走商品页 JSON-LD/内嵌 JSON 解析；若官网改版导致解析失败，可在 `uniqlo.api_template` 配置你抓包得到的 JSON 接口模板（`{}` 占位 productCode）
- **淘宝/天猫**：接口有签名与风控，须自备浏览器 Cookie（实验性，失效需更新）

抓取均为低频轮询（默认 1 小时一轮）。请遵守各平台服务条款，勿高频请求，数据仅供个人购物决策参考。

## 预测方法

| 信号 | 说明 |
|---|---|
| 历史降价节奏 | 平均降价间隔临近 → 概率 +30% |
| 大促日历 | 30 天内有 618/双11 等大促 → 概率 +35% |
| 价格分位 | 当前价处历史低位 → 降低再降预期 |
| 季节模型 | 类目清仓月 + 发后天数（羽绒类 90~150 天） |
| 库存 | 无货→关注补货；低价 + 库存紧张→尽快入手 |

决策融合为 0-100 分，映射到「立即购买 / 尽快入手 / 建议等待 / 关注补货」。

## Roadmap

- [ ] 慢慢买等第三方历史价数据源接入（新商品冷启动即有历史）
- [ ] PNG 走势图导出（matplotlib 可选依赖）
- [ ] SKU 级库存与尺码监控
- [ ] Playwright 渲染兜底，提升优衣库/淘宝解析健壮性
- [ ] 多商品组合“一起买最划算”建议（共用跨店满减档位）

## 免责声明

本项目仅供学习交流与个人购物参考，不构成任何消费建议；使用时请遵守目标平台的服务条款与 robots 协议，因使用本项目产生的任何问题由使用者自行承担。

## License

MIT
