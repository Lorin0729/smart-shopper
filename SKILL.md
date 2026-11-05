---
name: smart-shopper
description: 电商降价监控与购买决策助手。用户给出优衣库货号/链接，或问「货号 xxx 怎么买最划算」「优衣库降价了吗」「值得等吗」「尺码库存还有吗」「怎么凑单/叠券」时，用一条命令给出买/等/关注结论、可购最低价、关注尺码库存、叠券到手价与降价时机。支持优衣库（颜色×尺码完整矩阵）、京东、淘宝/天猫及通用站点。
---

# Smart Shopper — 货号直达

先定位本文件所在目录，记作 `<skill_dir>`。

- 若 `<skill_dir>` 下存在 `app\` 子目录（WorkBuddy 安装形态）：所有命令在 `<skill_dir>\app` 下执行。
- 否则（直接使用仓库）：在 `<skill_dir>` 下执行。

不要写死绝对路径。统一使用 `python main.py <命令>`。

## 默认流程（不要反问）

用户只给货号、商品链接或「这个怎么买最划算」时：

1. 直接执行：

```bash
python main.py advise <货号或链接>
```

2. **按原样转述输出**（模板见下），不要自己重新拼 `add` / `check` / `sizes` / `deal` 五条命令。
3. 如果这次没带 `--coupons`，在结论后追问一句：
   **「你手上有没有店铺券/平台券？报给我能算得更准。」**
4. 只有「用户明显在挑尺码」且库里没设关注尺码时，才问一次尺码；否则不要追问。

`advise` 的行为：裸货号（`u` + 13 位数字）会自动补成商品页链接；商品不在库中会自动加入跟踪；随后刷新一次尺码库存（复用 TTL 缓存），再给出结论。**同一商品不要连续重复执行，会白白触发 403/429。**

## 价格口径红线（必须遵守）

**所有面向用户的价格，必须来自「有货」的 SKU（`stock > 0`）。**

- 商品级历史价可能是**已售罄配色**的最低现价（实测：深橄榄色/铅灰色/驼色全码无货，¥699），用户关注的乳白色 M 实际是 ¥799。
- 因此**禁止**用 `check` / `list` 的商品级价格作为「现在多少钱」「划不划算」的结论价。
- `advise` 已按有货 SKU 计算；如果输出里出现「价差说明」，把那一行一并转述给用户。

## 输出模板（保持字段顺序）

```
【结论】买 / 等 / 关注 — 一句话理由（不超过 25 字）

可购最低价：¥799（乳白色 / M）
你关注的「乳白色 M」：¥799 · 有货（剩 9 件）
目标价 ¥599：还差 ¥200（需再降 25%）
叠券后估算：¥749（跨店满减 300-50 · 未计入你的个人券）
价格位置：历史 0% 分位 · 30 天内再降概率 35%
数据时间：2026-11-05 10:30
```

- 第一行必须是**动作**，不是数据。
- 售罄项不得进入「可购最低价」。
- 明细表只在用户追问时再展开，默认不刷屏。
- 金额用 `¥` + 整数或两位小数，不要出现 `799.0`。

## 边界处理（不许含糊）

| 情况 | 行为 |
|---|---|
| 抓取失败 | 原样转述 `[失败] ... 已尝试 N 种方式` 的原因与建议，**绝不编造价格** |
| 关注尺码售罄 | 明说该尺码无货，同时给出「全码最低可购价」作为替代 |
| 全部尺码售罄 | 只报无货，不报价、不算叠券 |
| 历史 < 3 天 | 明说样本不足、暂不给概率数字，建议先跑几天 `check` |
| 目标价未设 | 跳过「还差多少」，只给可购价与建议 |
| 用了演示券 | 必须带「未计入你的个人券」，并追问用户真实券 |

## 用户追问时才用的命令

```bash
python main.py sizes 1 --refresh          # 完整颜色×尺码明细
python main.py analyze 1                  # 详细历史 / 预测 / 购买窗口
python main.py advise u0000000066172 --coupons my_coupons.json
python main.py deal --price 899 --platform jd --coupons my_coupons.json
python main.py cart cart.json             # 购物车跨店凑单
python main.py bundle --file bundle.json  # 多商品组合购买
python main.py check --watch --interval 3600
python main.py report --out report.html
python main.py serve --port 8777
```

券文件支持两种写法：顶层数组，或 `{"coupons": [...]}`。券种：`full_off` / `every_full_off` / `percent` / `fixed` / `points`。

## 环境与故障

- 依赖只有 `requests`：`python -m pip install -r requirements.txt`。
- 配置在 `app` 根目录的 `config.json`（skill 形态数据库指向 `data/shopper.db`）；也可用 `--config` 或环境变量 `SMART_SHOPPER_CONFIG`。
- 优衣库 403：不重试、自动降级到页面解析；429/5xx/超时：按 `Retry-After` 或退避重试。仍失败时照实说明，可稍后再试、调大 `uniqlo.cache_ttl_seconds` / `retry_backoff_seconds`，或填 `uniqlo.cookie`。
- 优衣库商品页是 SPA，HTML 层只能靠 JSON-LD/内嵌 JSON；需要更强兜底时安装 Playwright。
- 淘宝/天猫需要 `taobao.cookie`；京东库存按 `jd.area` 查询。
- 不要删除/覆盖 `data/shopper.db`；不要把含 Cookie/密钥的 `config.json` 提交进 git。
