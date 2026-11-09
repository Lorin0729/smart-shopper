---
name: smart-shopper
description: 购物购买决策助手。用户给出商品编号/链接、问「怎么买最划算」「有 88VIP 怎么买」「没有会员怎么买」「怎么凑单」「我穿什么码」「值得等吗」「往年什么时候降价」时，一条命令给出：当前可购价、无会员/有会员两套叠券方案、凑单建议、按身高体重腰围推荐的尺码、以及往年同类商品的降价窗口。支持京东、淘宝/天猫、优衣库与通用站点。
---

# Smart Shopper — 购买方案助手

先定位本文件所在目录，记作 `<skill_dir>`。

- 若 `<skill_dir>` 下存在 `app\` 子目录：命令在 `<skill_dir>\app` 下执行。
- 否则（直接克隆仓库）：在 `<skill_dir>` 下执行。

不要写死绝对路径。统一使用 `python main.py plan ...`。

## 安装（首次使用）

如果本地还没有这个 skill：

```bash
# Qoder 国内版（.qoder-cn）
git clone https://github.com/Lorin0729/smart-shopper.git "$HOME/.qoder-cn/skills/smart-shopper"
python -m pip install -r "$HOME/.qoder-cn/skills/smart-shopper/requirements.txt"

# Qoder 国际版（.qoder）
git clone https://github.com/Lorin0729/smart-shopper.git "$HOME/.qoder/skills/smart-shopper"
python -m pip install -r "$HOME/.qoder/skills/smart-shopper/requirements.txt"
```

- Qoder 国内版：`$HOME/.qoder-cn/skills/smart-shopper`
- Qoder 国际版：`$HOME/.qoder/skills/smart-shopper`
- Qoder 项目级：`<项目>/.qoder/skills/smart-shopper` 或 `<项目>/.qoder-cn/skills/smart-shopper`
- QoderWork：`$HOME/.qoderwork/skills/smart-shopper`
- 其他 Agent：克隆到任意目录，按本文件执行即可

## 操作红线（必须遵守）

- **不代提交订单、不代付款**；购物车默认只读，改动前必须得到明确授权。
- **不读取、不转述订单页的姓名、电话、地址**等个人信息。
- **不把 Cookie 发到聊天或外部服务**；只能写入本地 `config.json`。
- 涉及「买凑单品再退货」的方案，**先确认券的退回规则**，再给净价；不要先给结论再补风险。
- 抓取失败就如实说明，不编造价格、库存或优惠。

## 什么时候用

用户说类似：

- 「478897 怎么买最划算？」
- 「这个链接现在买还是等双11？」
- 「我有 88VIP，怎么买最便宜？没有 88VIP 呢？」
- 「帮我凑个单，怎么买最划算？」
- 「身高 178 体重 70 腰围 82，我穿什么码？」
- 「去年这种羽绒服什么时候降价？」

## 对话流程（最多问一轮）

### 1. 先判断商品标识

- **URL**：直接用，支持京东 / 淘宝天猫 / 优衣库 / 通用站点。
- **`platform:pid` 或库里序号**：直接用。
- **裸数字（如 478897）**：无法判断平台。**只问一句**：
  > 「478897 是京东还是淘宝的编号？直接发商品链接更快。」
  拿到平台后加 `--platform jd` 或 `--platform taobao`。
- 如果用户给的是品牌货号（不是平台商品 ID），说明「不同平台编号不通用，发商品链接我才能查到实时价」。

### 2. 一次性补齐购买信息

拿得到商品后，**合并成一条消息**问用户（不要连问好几轮）：

> 为了算准，回我四件事：① 有 88VIP / PLUS 会员吗？② 手上有哪些券（面额 + 门槛 + 有效期）？③ 买几件、预算上限、能否接受「凑单后再退货」？④ 要推荐尺码的话，给我身高/体重/腰围（裤装必须腰围）。

用户已经说过的信息不要重复问。**不要默认买 2 件**，也不要默认接受囤货/退货。拿到后执行：

```bash
python main.py plan "<商品链接>" \
  --vip \
  --coupons my_coupons.json \
  --height 178 --weight 70 --waist 82 \
  --coupon-valid-to 2026-11-12 --deadline 2026-11-12
```

- 用户催「一句话」时加 `--brief`：只输出 3 行（结论 + 实付 + 时效）。
- 断网/登录墙/抓取失败时，用 Qoder 内置浏览器**只读**打开商品页，读出价格后：
  ```bash
  python main.py plan "<商品链接>" --price 399 --coupons my_coupons.json
  ```
  输出会标注「手动价（未验证）」。
- 没有券：不加 `--coupons`，工具按 0 券计算，只会额外给一条标注「估算」的会员折扣。
- 要演示券：加 `--demo`（输出会标注「演示券」）。

### 3. 按固定模板转述

```
【结论】买 / 等 / 关注 — 一句话理由

商品：测试羽绒服  [taobao]  ¥899（有货）· 实时抓取
尺码建议：身高 178 / 体重 70 → 推荐 L（中国 175/92A / 美码 42-44）
  商品尺码「乳白色 / L」：有货，剩 6 件，¥899

购买方案（你的券）：
  无会员：¥849（店铺券 满500减50（结算页实测））
  有 88VIP：¥806（88VIP 95折（估算） + 店铺券 满500减50（结算页实测））  ← 你的方案
  凑单：再凑 ¥101 可多减 ¥50（跨店满减，净多花 ¥51）

价格时机：
  往年同类（羽绒服）：2~3 月季末清仓约 6.5 折；双11 首个深折扣
  本商品：1 次采样，样本不足；先跑几天 check 再给概率
数据时间：2026-11-09 21:30
时效：券至 2026-11-12 · 活动至 2026-11-12 · 价保 30天
```

一句话模式（`--brief`）固定 3 行：

```
【结论】买 — 正处于双11窗口
实付：无会员 ¥899 / 有会员 ¥854（无券）
时效：数据 2026-11-09 11:00 · 券/活动截止未提供
```

规则：

- 第一行必须是**动作**，不是数据。
- **没传 `--coupons` 时不得出现任何满减金额**；只能给原价 + 一条标注「估算」的会员折扣。
- 每条优惠必须带来源：`结算页实测` / `估算`；没来源的券不进入计算。
- 「无会员」「有会员」两套都要给；只有一套时才省略另一套。
- 商品行必须带价格来源：`实时抓取` / `手动价（未验证）` / `上次记录（可能滞后）` / `抓取失败`。
- 售罄 SKU 不得进入可购价。
- 明细不刷屏，用户追问再展开；用户催「一句话」直接用 `--brief`。

## 价格口径红线（必须遵守）

**所有面向用户的价格必须来自「有货」的 SKU。**

- 商品级接口价可能来自已售罄的便宜配色（实测 ¥699 的三色全码无货，真正能买的乳白色是 ¥799）。
- `plan` 已按有货 SKU 计算；输出里出现「价差说明」时，把那一行一起转述。
- 历史最低价只用于判断位置，不代表现在能买到。

## 往年降价规律怎么回答

- `plan` 的「往年同类」来自类目模型（羽绒服 / 大衣 / 夏装 / 鞋 / 通用），**是基于类目的规律，不是这款商品去年的真实价格**。
- 本商品的真实历史只能来自本地采样（`check` 积累）或 `seed` 补历史：
  ```bash
  python main.py seed 1          # 从慢慢买补历史价
  python main.py analyze 1       # 详细历史 + 概率 + 窗口
  ```
- 没有历史时明确说「本商品样本不足」，不要把类目规律说成这款商品的去年价格。

## 尺码规则

- 上衣：身高 + 胸围（有胸围最准）；没有胸围时用 BMI 做一档修正。体重只是辅助。
- 裤装：腰围为主；90cm 以上或跨界建议选大一码并核对尺码表。
- 鞋：身高体重推不出鞋码，直接问用户平时穿的码。
- 用户没给身体数据时，不要硬猜，输出「未提供身高/体重/腰围」并提示补三项。
- 身体数据只用于本次计算，不写入数据库。

## 凑单退货测算（return）

用户问「买袜子凑满减再退掉，实际花多少」时，**先问清券的退回规则**，再用：

```bash
python main.py return --order order.json --return 486885 --rule uniqlo
```

`order.json` 格式：

```json
{
  "items": [
    {"sku": "489482", "name": "茄克", "price": 399, "qty": 2},
    {"sku": "486885", "name": "袜子", "price": 20, "qty": 1}
  ],
  "coupon": {"name": "满800减80", "amount": 80, "threshold": 800},
  "paid": 738,
  "rule": "full_clawback"
}
```

- `--rule uniqlo` 等同 `full_clawback`；也可用 `proportional` / `unknown`。
- 规则未知时**不要猜**：工具会同时列出「按比例退券」和「整张券扣回」两种情景，并提示以退货页预计退款为准。
- 输出包含：退货项已付、预计退款、保留商品实际成本、券扣回方式、风险提示。
- 结论优先一句：「不要为优惠买不需要的东西再退；直接买需要的商品通常更干净。」

## 商品标识与平台

| 形式 | 用法 |
|---|---|
| `https://item.jd.com/100012043978.html` | 直接 `<链接>` |
| `https://item.taobao.com/item.htm?id=679876543210` | 直接 `<链接>`（淘宝需配置 `taobao.cookie`） |
| `uniqlo:u0000000066172` / 优衣库链接 | 直接 `<链接或 platform:pid>` |
| 裸数字 `478897` | 必须加 `--platform jd` 或 `--platform taobao` |
| 品牌货号（非平台 ID） | 查不到实时价，让用户发商品链接 |

## 失败与降级

- 京东旧价格接口（p.3.cn）在部分网络会 404 或证书校验失败，工具会自动改用移动端商品页 `item.m.jd.com` 的内嵌 JSON 取价格与库存；两者都失败才报错。
- 淘宝未配置 Cookie 时会明确提示（无 Cookie 基本取不到价格）；不要让用户反复重试。若 Qoder 内置浏览器已登录，可用**浏览器只读**打开商品页读价格/券，再 `plan <链接> --price <价格> --coupons ...`，并标注「浏览器实测/手动价」。
- 优衣库 403 会降级到页面解析；429/5xx 自动退避重试。仍失败就说明被限流，建议稍后再试或填 `uniqlo.cookie`。
- 任何平台失败都会打印 `[失败] ...`；若有上次记录，会标注「以下使用上次记录的数据，结论可能滞后」，照实转述，**不要编造价格**。
- 永远**不要读取订单页的姓名/电话/地址**，也不要代提交订单或代付款。

## 追问时才用的命令

```bash
python main.py sizes 1 --refresh          # 完整颜色×尺码明细
python main.py analyze 1                  # 详细历史 / 概率 / 购买窗口
python main.py plan "<链接>" --brief       # 一句话：结论 + 实付 + 时效
python main.py plan "<链接>" --price 399   # 手动价（未验证），断网/登录墙时用
python main.py deal --price 899 --platform jd --coupons my_coupons.json
python main.py deal --price 899 --platform jd --demo   # 演示券（会标注估算）
python main.py return --order order.json --return 486885 --rule uniqlo
python main.py cart cart.json             # 多商品跨店凑单
python main.py bundle --file bundle.json  # 已跟踪商品组合购买
python main.py check --watch --interval 3600
python main.py serve --port 8777
```

券文件支持顶层数组或 `{"coupons": [...]}`；券种 `full_off` / `every_full_off` / `percent` / `fixed` / `points`。建议每条券带 `source`（`checkout_verified` / `calculated` / `estimated`）和 `valid_to`；没有来源的券不得当作真实优惠。

## 环境

- 依赖：`python -m pip install -r requirements.txt`（只有 requests）。
- 配置：代码根目录的 `config.json`（默认数据库 `shopper.db`；也可 `--config` 或 `SMART_SHOPPER_CONFIG` 覆盖）。
- 不要删除/覆盖用户的 `shopper.db`；不要把含 Cookie/密钥的 `config.json` 提交进 git。
