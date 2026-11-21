# CloudDisk

[English](README.md) | **简体中文**

CloudDisk 是一个面向个人和小团队的自托管云盘文档管理系统。它既能安全保存文件，也能通过文件名搜索、正文搜索和自然语言语义搜索快速找到文档。

项目采用 Python 微服务架构：

- **Gateway**：FastAPI HTTP API、JWT 认证、文件管理、前端静态资源、搜索编排。
- **VFS / Storage**：虚拟文件系统、MD5 内容寻址本地存储、引用计数、软删除。
- **Search Service**：gRPC 服务，使用 jieba + SQLite FTS5 + BM25。
- **Semantic Service**：gRPC 服务，使用 sentence-transformers + FAISS，并提供离线哈希降级。

项目自带纯 HTML/CSS/JavaScript 前端，所有页面都调用真实后端 API。

> 本文档只介绍项目用途和使用方法。
> 完整需求见 [`PROJECT_REQUIREMENTS.md`](PROJECT_REQUIREMENTS.md)；
> 代码结构、目录职责和完整调用链见 [`docs/CODE_GUIDE.md`](docs/CODE_GUIDE.md)。

---

## 项目功能

- 注册、登录、JWT 鉴权。
- 创建文件夹、文件夹树、重命名、移动、递归软删除。
- 流式上传和下载。
- 文件内容按 MD5 寻址：相同内容只保存一份物理文件。
- 逻辑文件与物理内容分离，使用引用计数管理清理。
- 文件名搜索，支持中文/英文分词。
- 正文搜索（TXT、HTML、Markdown；可选 PDF/DOCX/PPTX/XLSX）。
- 文档向量化与语义搜索。
- 查找与某个文件相似的文件。
- 查看每个文件的关键词/语义索引状态，失败可重试，支持全量重建。
- 降级能力：语义服务不可用时关键词搜索仍可用；搜索服务不可用时文件管理仍可用。

默认支持 TXT、HTML、Markdown 等文本类文件的正文提取；无法提取正文的文件仍可按文件名搜索。

---

## 系统架构

```text
浏览器
  │ HTTP + JWT
  ▼
Gateway Service (FastAPI, :8000)
  ├── VFS / Storage（同进程，独立代码模块）
  │     ├── SQLite（演示默认）/ MySQL 8（可选）
  │     └── data/objects/ab/cd/<md5>
  ├── gRPC ──► Search Service (:50051)
  │              jieba + SQLite FTS5 + BM25
  └── gRPC ──► Semantic Service (:50052)
                 sentence-transformers + FAISS
```

Search / Semantic 服务不访问业务数据库。Gateway 从索引服务拿到候选 `file_id` 后，会再查数据库确认文件属于当前用户且未删除，最终结果才返回给浏览器。

---

## 环境要求

- Linux / WSL（推荐 Ubuntu）
- Miniconda 或 Anaconda
- 通过项目提供的 Conda 环境使用 Python 3.11

演示不需要 MySQL，也不需要 Docker。默认配置使用项目内的 SQLite 文件；MySQL 8 仅作为可选的长期部署方案。

---

## 快速开始

### 1. 创建 Conda 环境

```bash
cd CloudDisk
conda env create -f environment.yml
conda activate DSC5003
```

如果机器没有 Conda，可以使用脚本安装 Miniforge 并创建环境（不会使用 `base` 运行项目）：

```bash
bash scripts/setup_conda_env.sh
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate DSC5003
```

### 2. 配置

```bash
cp .env.example .env
```

默认 `.env` 使用 SQLite：

```ini
DATABASE_URL=sqlite:///data/clouddisk.db
AUTO_CREATE_TABLES=true
```

如果项目里已经有 `.env`，保留它，不要用示例文件覆盖。

在对他人开放服务前，请修改 `JWT_SECRET`。

### 3. 初始化数据库和演示账号

```bash
python sql/seed.py --username demo --password demo123456
```

该命令会创建 SQLite 数据库、全部数据表、演示用户和用户根目录。

### 4. 启动服务

```bash
python scripts/run_services.py
```

启动内容：

| 服务 | 默认地址 |
|---|---|
| Search gRPC | `127.0.0.1:50051` |
| Semantic gRPC | `127.0.0.1:50052` |
| Gateway + 前端 | `http://127.0.0.1:8000/` |
| Swagger API 文档 | `http://127.0.0.1:8000/api/docs` |

按 `Ctrl+C` 停止全部服务。

也可以在三个终端分别启动：

```bash
python -m services.search.server
python -m services.semantic.server
uvicorn services.gateway.main:app --host 127.0.0.1 --port 8000
```

### 5. 生成演示文件（可选）

```bash
python scripts/generate_test_data.py --count 8
```

脚本会上传一组中文示例文档，并额外上传一个相同内容、不同文件名的文件，用于演示 MD5 去重。

### 6. 打开页面

访问 <http://127.0.0.1:8000/>，使用以下账号登录：

```text
用户名：demo
密码：demo123456
```

---

## 使用方法

- **新建文件夹**：在文件页点击“新建文件夹”。
- **上传**：点击“上传文件”，或把文件拖到上传区域。
- **下载**：在文件行点击“下载”。
- **重命名 / 移动 / 删除**：使用文件行操作；文件夹支持递归删除。
- **关键词搜索**：选择“全部”“文件名”或“正文”，输入关键词。
- **语义搜索**：选择“语义搜索”，用自然语言描述要找的内容。
- **相似文件**：在文件或搜索结果中点击“相似”。
- **索引状态**：打开“索引状态”，查看关键词/语义状态，重试失败文件或重建索引。
- **API 文档**：访问 `/api/docs` 查看并可交互调用接口。

前端是纯单页应用，所有数据都来自真实后端，没有静态假数据。

---

## 语义模型

语义服务使用 sentence-transformers + FAISS。为了避免启动时意外下载大模型，`SEMANTIC_EMBEDDER=auto` **只使用已缓存的模型**；没有缓存时立即降级为离线哈希向量，API 和前端会标记 `degraded`。

需要真正的神经语义模型时，先显式下载：

```bash
conda run -n DSC5003 python scripts/download_semantic_model.py
```

默认多语言模型约 471 MB。也可以使用更小的中文模型：

```bash
conda run -n DSC5003 python scripts/download_semantic_model.py \
    --model BAAI/bge-small-zh-v1.5
```

如果更换模型，需要在 `.env` 设置 `SEMANTIC_MODEL_NAME`，并在页面或 `scripts/rebuild_indexes.py` 中重建语义索引。

Hugging Face 连接不稳定时可以尝试：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

模型权重保存在 `data/models/`，不会提交到 Git。

---

## 配置说明

复制 `.env.example` 为 `.env`；所有配置也可以直接通过环境变量提供。

| 配置 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///data/clouddisk.db` | 演示数据库；也支持 MySQL URL |
| `JWT_SECRET` | 示例占位符 | JWT 签名密钥，必须修改 |
| `HTTP_HOST` / `HTTP_PORT` | `127.0.0.1:8000` | Gateway 监听地址 |
| `SEARCH_SERVICE_HOST/PORT` | `127.0.0.1:50051` | 内部搜索 gRPC |
| `SEMANTIC_SERVICE_HOST/PORT` | `127.0.0.1:50052` | 内部语义 gRPC |
| `SEMANTIC_EMBEDDER` | `auto` | 已缓存神经模型或离线哈希 |
| `MAX_UPLOAD_BYTES` | `536870912` | 单文件上传上限（512 MiB） |

`data/` 保存 SQLite 数据库、MD5 对象、搜索索引、FAISS 索引和模型缓存，已被 Git 忽略。

### 可选：MySQL 8

修改 `.env`：

```ini
DATABASE_URL=mysql+pymysql://clouddisk:clouddisk@127.0.0.1:3306/clouddisk?charset=utf8mb4
```

然后初始化：

```bash
sudo mysql < sql/create_database.sql
mysql -u clouddisk -p clouddisk < sql/schema.sql
python sql/seed.py --username demo --password demo123456
```

应用代码、搜索服务和语义服务都不需要修改。

### 部署到服务器

快速 HTTP 演示：

1. 在 `.env` 设置 `HTTP_HOST=0.0.0.0`、`HTTP_PORT=8000`；
2. 在服务器防火墙/安全组放行 TCP `8000`；
3. 浏览器访问 `http://服务器IP:8000/`。

如果希望通过 80/443 和域名访问，建议用 Nginx 反向代理到 `127.0.0.1:8000`，并保持 `50051/50052` 只监听本机，不要对外开放。

---

## 测试

```bash
conda activate DSC5003
pytest -q
```

测试会在临时端口启动真实的 Search/Semantic gRPC 服务，并驱动真实 FastAPI 应用；数据库使用临时 SQLite，语义测试使用离线哈希 Embedding，因此不需要 MySQL，也不需要下载模型。

覆盖范围包括：认证与用户隔离、VFS、MD5 去重与引用计数、关键词/语义搜索、索引状态与重建、数据库约束、服务降级、索引损坏恢复和完整端到端流程。

---

## 项目结构

| 目录 | 内容 |
|---|---|
| `services/gateway/` | HTTP API、认证、路由、索引编排、错误处理 |
| `services/vfs_storage/` | 虚拟文件系统、MD5 对象存储、文本提取 |
| `services/search/` | jieba + SQLite FTS5 引擎及 gRPC 服务/客户端 |
| `services/semantic/` | 切块、Embedding、FAISS 索引及 gRPC 服务/客户端 |
| `shared/` | 配置、SQLAlchemy 模型、JWT/密码工具、公共类型 |
| `frontend/` | 纯 HTML/CSS/JavaScript 单页应用 |
| `protos/` | `clouddisk.proto` |
| `sql/` | SQLite/MySQL 初始化、可选 MySQL DDL、种子脚本 |
| `scripts/` | 环境安装、服务启动、演示数据、索引维护 |
| `tests/` | pytest 测试 |
| `data/` | 运行时数据库、对象、索引、模型缓存（Git 忽略） |

更详细的模块职责和调用链见 [`docs/CODE_GUIDE.md`](docs/CODE_GUIDE.md)，数据库 ER 图见 [`docs/ER.md`](docs/ER.md)。

---

## 文档

- [代码结构与调用链](docs/CODE_GUIDE.md)
- [数据库 ER 图](docs/ER.md)
- [项目需求](PROJECT_REQUIREMENTS.md)
- [可选 MySQL schema](sql/schema.sql)

---

## 说明

- 演示默认使用 SQLite；MySQL 8 为可选的长期部署方案。
- Search/Semantic 的 gRPC 端口应仅监听 `127.0.0.1`，除非你有意将服务部署到隔离网络。
- 核心配置来自 `.env`/环境变量，不硬编码本机路径。
