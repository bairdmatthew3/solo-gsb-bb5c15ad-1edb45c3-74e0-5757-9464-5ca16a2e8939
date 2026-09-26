# Intranet CIDR Allocator

内网 CIDR 批量分配 API。基于 **Python + FastAPI + SQLite**，地址计算仅使用标准库
`ipaddress`。

地址池由**一个父网段**和**若干保留子网**（永不分配）构成。预演接口接收 1～8 个
前缀长度请求，在剩余空间内搜索全部可行的对齐子网组合，并按以下顺序择优：

1. **最小化分配后剩余空闲 CIDR 的数量**（尽量降低地址碎片）；
2. 并列时取**按请求顺序排列的网络地址序列字典序最小**的方案。

预演返回池版本与分配方案；提交接口仅当**池版本未变化且方案仍全部可用**时才原子
落库（版本号 +1），否则返回 `409 Conflict`，不会产生任何部分分配。

## 快速开始

单条命令完成构建、依赖安装、数据库初始化并启动服务：

```bash
docker compose up --build
```

启动后：

| 地址 | 说明 |
| --- | --- |
| `http://localhost:8000` | 服务入口信息 |
| `http://localhost:8000/docs` | Swagger 交互式 API 文档 |
| `http://localhost:8000/api/v1` | API 前缀 |
| `http://localhost:8000/health` | 健康检查 |

服务监听端口 **8000**（宿主机映射同样为 8000，可在 `docker-compose.yml` 的
`ports` 中调整）。

## 配置

通过环境变量配置（`docker-compose.yml` 的 `environment` 段）：

| 变量 | 默认值（Compose） | 说明 |
| --- | --- | --- |
| `POOL_PARENT` | `10.0.0.0/8` | 地址池父网段（必须为网络地址，IPv4/IPv6 均可） |
| `POOL_RESERVED` | `10.0.0.0/16,10.1.0.0/16` | 逗号分隔的保留子网，须位于父网段内且互不重叠；可置空 |
| `DB_PATH` | `/data/pool.db` | SQLite 数据库文件路径（Compose 中挂载命名卷持久化） |

注意：数据库会记录池配置的指纹。若修改 `POOL_PARENT`/`POOL_RESERVED` 后复用旧
数据库文件，服务将拒绝启动；请更换 `DB_PATH`（或删除旧卷）后再启动。

## API 一览

### `GET /api/v1/pool`

查看池状态：版本、父网段、保留子网、已分配与空闲 CIDR。

### `POST /api/v1/allocations/preview`

预演批量分配。请求体为 1～8 个前缀长度：

```bash
curl -s -X POST http://localhost:8000/api/v1/allocations/preview \
  -H 'Content-Type: application/json' \
  -d '{"prefix_lengths": [24, 25, 25]}'
```

响应（`200`）：

```json
{
  "version": 1,
  "allocations": ["10.2.0.0/24", "10.2.1.0/25", "10.2.1.128/25"],
  "remaining_free_cidrs": ["10.2.2.0/23", "..."]
}
```

预演不落库、不改变版本。剩余空间不足时返回 `409`。

### `POST /api/v1/allocations/commit`

提交预演得到的方案（原样回传 `version` 与 `allocations`）：

```bash
curl -s -X POST http://localhost:8000/api/v1/allocations/commit \
  -H 'Content-Type: application/json' \
  -d '{"version": 1, "allocations": ["10.2.0.0/24", "10.2.1.0/25", "10.2.1.128/25"]}'
```

- 成功：`201`，返回新的池版本（原版本 +1）与已落库的分配；
- 冲突：`409` —— 版本已变化，或方案中任一 CIDR 不再可用（越出空闲空间、
  互相重叠等）。冲突时**不会写入任何部分分配**；
- 参数非法：`422`（如 CIDR 格式错误、主机位非零、批量大小越界）。

### 冲突示例

```bash
# 同一版本重复提交，第二次必然 409
curl -s -X POST .../commit -d '{"version": 1, "allocations": ["10.2.0.0/24"]}'  # 201
curl -s -X POST .../commit -d '{"version": 1, "allocations": ["10.2.0.0/24"]}'  # 409
```

## 分配策略说明

设空闲块为若干 CIDR。对每个请求前缀 `p`，若从前缀为 `q` 的空闲块中切分
（`q ≤ p`），剩余空闲 CIDR 数的变化恒为 `p - q - 1`（精确匹配 `q = p` 时为
`-1`），与块内位置无关。规划器据此：

1. 在“块前缀多重集合”上做带记忆化的穷举搜索，求出分配后剩余空闲 CIDR 数的
   最小值；
2. 按请求顺序贪心确定每个请求的具体位置：逐个尝试地址最低的空闲块，凡仍能
   达到最优剩余数量的即选中——从而得到字典序最小的网络地址序列。

## 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 运行测试（含与暴力穷举对拍的规划器正确性测试）
pytest

# 本地启动（数据库默认为 ./pool.db，可用环境变量覆盖）
POOL_PARENT=10.0.0.0/16 POOL_RESERVED=10.0.0.0/24 \
  uvicorn app.main:app --reload --port 8000
```

## 项目结构

```
app/
  config.py    # 环境变量配置加载与校验（父网段 / 保留子网 / DB 路径）
  planner.py   # 分配规划器：穷举 + 最优（最小剩余 CIDR 数、字典序最小）
  db.py        # SQLite 存储：版本管理、BEGIN IMMEDIATE 原子提交
  schemas.py   # Pydantic 请求/响应模型
  main.py      # FastAPI 路由
tests/
  test_planner.py  # 规划器单测 + 与暴力穷举的差分对拍
  test_api.py      # API 端到端：预演/提交/版本冲突/原子性
Dockerfile
docker-compose.yml
requirements.txt      # 运行时依赖（随镜像构建安装）
requirements-dev.txt  # 测试依赖
```
