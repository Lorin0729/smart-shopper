# AGENTS.md — Smart Shopper

优衣库 / 京东 / 淘宝价格与库存监控 + 优惠叠加最优解 + 购买时机预测。CLI 工具，数据存本地 SQLite，零服务器。

**能力说明与完整命令手册见 [`SKILL.md`](SKILL.md)；用户文档见 [`README.md`](README.md)。**

## 运行

```bash
python -m pip install -r requirements.txt
python main.py demo          # 离线自检，不联网
python main.py init          # 生成 config.json（可选）
python main.py list          # 查看已跟踪商品
```

常用：`advise <货号/链接>` 一条命令出结论；`add <url> --target 500 --size M` → `check` → `sizes 1 --refresh` → `analyze 1`。

## 测试

```bash
python -m unittest discover -s tests
```

要求全绿。解析类测试必须使用 `tests/fixtures/` 的本地样本，不得访问真实网络（网络层通过 `transport` 注入或 `mock.patch` 隔离）。

## 代码结构

- `main.py`：CLI 入口与子命令；`core/`：模型、SQLite、报告、尺码匹配
- `monitors/`：`uniqlo.py`（候选接口链/重试/缓存）、`uniqlo_parse.py`（纯解析）、`jd.py`、`taobao.py`、`generic.py`、`renderer.py`
- `optimizer/`：叠券与凑单；`predictor/`：预测与季节模型；`notifier/`：多渠道推送；`webui/`：本地面板
- `tests/`：105 个单测，`tests/fixtures/uniqlo/` 为优衣库页面/接口样本

## 约定

- Python ≥ 3.10，标准库优先，唯一运行时依赖 `requests`；新增依赖需同步 `requirements.txt` 与 `pyproject.toml`。
- CLI 输出面向中文用户；金额以人民币入库，展示时按 `--currency` 换算。
- 抓取失败时如实输出诊断（已尝试哪些接口、原因、建议），不得伪造价格或库存；库存未知不要当作有货。
- 不要删除/覆盖 `shopper.db`、`config.json`；不要把含 Cookie/密钥的配置、数据库、导出清单提交进 git。
- 修改抓取节奏相关逻辑时保留 TTL 缓存与提醒冷却，避免对目标站点高频请求。
- 用户问「多少钱 / 怎么买 / 值不值得等」时优先用 `advise`；结论价必须基于 `in_stock` 的 SKU，商品级历史价（可能来自售罄配色）只用于趋势，不得作为结论价。
- 优衣库商品级价格 = 全部 SKU 最低现价（含售罄配色），单 SKU 价格与库存以 `sizes` 输出为准。
