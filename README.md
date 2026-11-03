# trading-infra

独立的 Python 公共行情基础设施：集中维护交易所网络地址、只读行情客户端、协议解析和数据单位转换。支持 Python 3.10+。

本包不读取应用配置或私钥，不实现签名、账户查询或交易下单。交易所的注册信息只表示公共行情能力；由使用方决定启用哪些交易所和市场。

## 安装

```bash
git clone https://github.com/dmechai/trading-infra.git trading_infra
cd trading_infra
python3 -m pip install .
# 异步 DEX 行情和 CCXT 行情客户端
python3 -m pip install '.[async]'
```

基础包仅使用标准库；异步功能通过可选依赖安装。开发时可以使用 `pip install -e '.[async]'`。

## 模块与能力

| 模块 | 功能 |
| --- | --- |
| `venues` | Hyperliquid、Entropy、Lighter、RiseX、Arcus、Binance、Bitget 的公共地址与网络信息 |
| `transport` | 带显式超时的同步公共 JSON GET 请求 |
| `adapters.risex` | 市场、精度、资金费字段、盘口；主网与测试网独立 |
| `adapters.arcus` | 市场、精度、费率与资金费字段、最优买卖价 |
| `adapters.entropy` | Entropy 市场目录请求及解析 |
| `adapters.hyperliquid` | 行情上下文解析与 SDK L2 调用兼容 |
| `hip3` | HIP-3 DEX 索引、资产编号和数量精度 |
| `adapters.ccxt` | 匿名 CCXT 客户端工厂、超时与代理配置 |
| `market_data` | 复用 CCXT 连接读取 USDT 永续盘口、成交量和资金费率 |
| `dex` | Hyperliquid / Lighter 异步报价、会话、短期缓存与批量扫描 |
| `funding` / `symbols` | 资金费周期、原生合约名称解析 |
| `models` | 含网络、合约身份和时间信息的报价快照 |

各适配器保留协议所需的字段和精度，返回值未全部统一为一种类型。RiseX / Arcus 的部分字段使用 `Decimal`。`market_data` 的报价同时包含 `received_at` 和可用时的 `source_timestamp`。

## 使用

显式选择交易所和网络，不使用其他网络替代缺失的测试网：

```python
from trading_infra.venues import api_url
from trading_infra.adapters.risex import RiseXPublicClient

url = api_url("hyperliquid", "testnet")
client = RiseXPublicClient("testnet", timeout=8)
markets = client.list_markets()
```

异步 DEX 行情需要安装 `async` 可选依赖。使用结束后关闭会话：

```python
import asyncio
from trading_infra import dex

async def main():
    try:
        rows = await dex.scan_quotes(["BTC"], exchanges=("hyperliquid", "lighter"))
        print(rows)
    finally:
        await dex.close()

asyncio.run(main())
```

CCXT 公共连接也需要显式释放：

```python
from trading_infra.market_data import CexMarketData

async def read_cex_quote():
    client = CexMarketData()
    try:
        return await client.quote("binance", "BTC")
    finally:
        await client.close()
```

每个进程维护自己的会话与缓存；本包不运行独立行情服务。异步 DEX 模块的共享会话用于单个事件循环，在停止循环前调用 `close()`。

## 增加交易所

1. 在 `venues.py` 登记官方地址、明确网络和已实现的公共行情能力。
2. 在 `adapters/` 实现公共协议。同步请求可以复用 `PublicJSONClient`；保留原生 symbol、合约乘数、报价币、结算币、资金费周期与时间单位。
3. 用固定响应测试网络隔离、精度、缺失字段和盘口异常。不要将不同合约仅按 base 合并，也不要把未知数据当成已知零。
4. 若需加入异步报价扫描，使用 `dex.register_quote_adapter(venue, async_quote)`，再在 `scan_quotes(..., exchanges=(...))` 中显式选择。

公共行情接入与账户、签名、订单执行的接入分别维护。注册一个适配器不会自动启用任何交易行为。

## 测试与构建

测试独立于任何使用方项目，使用固定响应与模拟客户端，不需要凭据或交易所连接：

```bash
python3 -m pip install -e '.[async]'
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --wheel-dir dist .
```

Docker 消费方可以把此仓库作为独立命名构建上下文，在自己的 Dockerfile 中安装本包：

```dockerfile
COPY --from=trading_infra / /opt/trading_infra
RUN python -m pip install --no-deps /opt/trading_infra
```

构建时提供 `--build-context trading_infra=/path/to/trading_infra`。使用 `--no-deps` 时，调用方负责安装需要的可选依赖。
