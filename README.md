# MediaLens

LLM 辅助的智能媒体刮削重命名工具。集成 TMDB 精确匹配 + LLM 模糊匹配双引擎，支持单文件、蓝光原盘（BDMV/ISO）和电视剧集的自动识别与整理。

## 功能特性

- **双引擎匹配**：TMDB 精确匹配 + LLM（OpenAI/Anthropic/Ollama）模糊匹配兜底
- **多格式支持**：单文件 (.mkv/.mp4)、蓝光原盘 (BDMV)、ISO 镜像、电视剧集
- **RAG 缓存**：基于嵌入向量的语义缓存，重复文件秒级匹配
- **批量处理**：断点续传，支持大批量文件的自动处理
- **Web UI**：FastAPI 提供 REST API + 静态前端 SPA
- **安全模式**：默认 dry-run，确认无误后再执行实际文件操作

## Docker 部署

### 前置条件

- Docker Engine 24+ 和 Docker Compose v2+
- TMDB API Key（[申请地址](https://www.themoviedb.org/settings/api)）
- （可选）LLM API Key（OpenAI / Anthropic / Ollama）

### 快速启动

```bash
# 1. 进入项目目录
cd medialens

# 2. 创建配置文件
cp .env.docker .env

# 3. 编辑 .env，填入 TMDB API Key
# vim .env

# 4. 创建持久化数据目录
mkdir -p data config

# 5. 构建并启动
docker compose up -d

# 6. 查看日志
docker compose logs -f

# 7. 打开浏览器访问
# http://localhost:8000
```

### 构建镜像

```bash
# 从源码构建
docker compose build

# 或使用 docker build 单独构建
docker build -t medialens:latest .
```

### 环境变量配置

创建 `.env` 文件，参考 [.env.docker](.env.docker) 模板：

```env
# TMDB API（必填）
TMDB_API_KEY=your_tmdb_api_key_here
TMDB_LANGUAGE=zh-CN

# LLM API（可选）
LLM_PROVIDER=openai
LLM_API_KEY=your_api_key_here
LLM_MODEL=gpt-4o-mini

# 匹配阈值
TMDB_CONFIDENCE_THRESHOLD=80
LLM_CONFIDENCE_THRESHOLD=60

# 文件管理
RENAME_DRY_RUN=true
ORGANIZE_MODE=hardlink

# 数据库路径（容器内路径）
DB_PATH=/app/data/medialens.db

# 默认媒体扫描目录（对应卷映射中的 /media）
DATA_DIR=/media
```

### 卷映射说明

| 主机路径 | 容器路径 | 用途 |
|---------|---------|------|
| `./data` | `/app/data` | SQLite 数据库和缓存数据持久化 |
| `./config` | `/app/config` | 用户配置文件（config.yaml / .env） |
| `/媒体库路径` | `/media` | 媒体文件目录（在 .env 中配置 DATA_DIR） |

### 停止和卸载

```bash
# 停止容器（保留数据）
docker compose down

# 停止容器并删除数据卷
docker compose down -v

# 查看运行状态
docker compose ps
```

## 极空间 Z4Pro NAS 部署说明

### 路径说明

极空间 Z4Pro 使用 ZFS 存储池，典型路径格式为：

```
/tmp/zfsv3/<存储池>/<用户ID>/data/电影
/tmp/zfsv3/<存储池>/<用户ID>/data/qb/downloads
/tmp/zfsv3/<存储池>/<用户ID>/data/电视剧
```

修改 [docker-compose.yml](docker-compose.yml) 中的卷映射以匹配实际路径。

### 与 tinyMediaManager 共存

如果已在极空间上运行 TMM，建议：
- 使用相同 `network_mode: bridge`（已在 compose 中配置）
- 映射相同的媒体目录路径
- 端口不冲突（TMM 使用 4399，MediaLens 使用 8000）

### SSH 部署步骤

```bash
# 通过 SSH 连接到极空间（端口和IP以实际为准）
ssh -p <SSH端口> <用户名>@<极空间IP>

# 拉取代码
git clone https://github.com/gg480/medialens.git
cd medialens

# 创建配置文件和数据目录
cp .env.docker .env
mkdir -p data config

# 编辑 .env 填入 TMDB API Key
# vim .env

# 启动（通过 GitHub Actions 构建的镜像，无需本地编译）
docker compose up -d
```

### 日志查看

```bash
# 实时日志
docker compose logs -f

# 最近 100 行
docker compose logs --tail=100

# 查看健康检查状态
docker inspect --format='{{json .State.Health}}' medialens
```

## 回滚方案

### 数据库回滚

SQLite 数据库文件位于 `./data/medialens.db`，建议定期备份：

```bash
# 手动备份
cp data/medialens.db data/medialens.db.bak.$(date +%Y%m%d)

# 回滚
cp data/medialens.db.bak.20250601 data/medialens.db
docker compose restart
```

### 版本回退

```bash
# 使用特定版本的镜像
docker compose down
# 修改 docker-compose.yml 中的 build/image 标签
docker compose up -d
```

## 开发指南

### 本地运行（非 Docker）

```bash
# 安装依赖
pip install -e .

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 TMDB API Key

# 启动开发服务器
uvicorn medialens.api.server:app --reload --port 8000
```

### 测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_api.py -v
```

## 技术栈

- **后端**: Python 3.11+, FastAPI, Uvicorn
- **数据库**: SQLite (WAL 模式)
- **匹配引擎**: TMDB API + LiteLLM (多 LLM 供应商)
- **文件解析**: guessit + 自定义 BDMV/ISO 解析器
- **前端**: 原生 HTML/CSS/JS SPA
