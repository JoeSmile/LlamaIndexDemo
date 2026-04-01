# multipleFiles RAG Pipeline

一个基于 `LlamaIndex + DashScope + Chroma` 的多格式文档入库与问答项目，重点能力是：

- 支持多格式文档读取（`txt/md/docx/pdf/xlsx/pptx`）
- 内容哈希去重（同内容只索引一次）
- 稳定 `source_key`（减少路径变化导致的重复和脏数据）
- 缓存版本化与原子写入（缓存文件更稳健）
- 命令行批量入库、单次问答、持续对话

---

## 1. 快速开始

### 1.1 安装依赖

```bash
pip install -r requirements.txt
```

### 1.2 配置环境变量

在项目根目录 `.env` 中至少配置：

```env
DASHSCOPE_API_KEY=your_key_here
```

可选配置：

```env
RAG_INGEST_ROOT=/abs/path/to/your/docs_root
RAG_CHROMA_PATH=./qwen_rag_data
RAG_CACHE_PATH=./file_hash_cache.json
RAG_MAX_FILE_MB=0
RAG_RESOLVE_SYMLINKS=0
RAG_RETRY_TIMES=3
RAG_BATCH_SIZE=4
```

### 1.3 运行命令

```bash
python llama.py ingest <file_or_dir> [more_paths...]
python llama.py query "你的问题"
python llama.py chat
```

---

## 2. 架构拆分（模块职责）

当前代码已经从单文件拆成包结构，入口保持不变（`llama.py`）：

- `llama.py`: 仅 CLI 入口，调用 `rag.cli.main`
- `rag/cli.py`: 命令分发（`ingest/query/chat`）
- `rag/config.py`: 环境变量、日志、通用配置常量
- `rag/models.py`: DashScope LLM 与 Embedding 模型初始化
- `rag/store.py`: Chroma 持久化客户端与向量删除能力
- `rag/file_handlers.py`: 不同后缀文件的读取器和切分策略
- `rag/paths.py`: 路径归一化、`ingest_root` 推断、`source_key` 与 `doc_id` 生成
- `rag/cache.py`: 缓存加载/迁移/保存（v2 + 原子写）
- `rag/utils.py`: 文件哈希、内存回收日志、哈希计数
- `rag/ingest.py`: 扫描、去重、重试、切片、向量写入主流程
- `rag/query.py`: 查询引擎与聊天引擎

这个拆分方式的核心价值：

- 降低认知复杂度（每个模块单一职责）
- 便于单测（例如单测 `paths.py` 和 `cache.py`）
- 便于后续替换组件（比如换向量库或 Embedding 模型）

---

## 3. 技术知识点（重点）

## 3.1 `source_key` 设计（路径稳定性）

问题背景：直接拿绝对路径做索引身份有风险（改名、迁移、跨机器路径不同）。

当前方案：

- 优先用 `RAG_INGEST_ROOT` 作为文档根
- `source_key = 相对 ingest_root 的路径`（统一 `/`）
- 如果文件不在根下，使用 `__external__/<短哈希>`

收益：

- 同一项目跨机器/跨目录更稳定
- 避免在元数据里暴露绝对路径
- 删除旧向量时可以按 `source_key` 精准过滤

实现位置：`rag/paths.py`

---

## 3.2 去重策略（同内容只索引一次）

核心变量：

- `file_hash = sha256(file_bytes)`
- `sources: dict[source_key, file_hash]`（来自缓存）
- `indexed_hashes: set[file_hash]`（内存集合，O(1) 判断）

判定逻辑（`rag/ingest.py`）：

1. `cached == file_hash`  
   文件内容未变化，跳过。
2. `file_hash in indexed_hashes`  
   该内容已被索引，跳过嵌入，仅更新 `sources[source_key]`。
3. `cached exists and changed`  
   先删掉旧 `source_key` 对应向量，再重新入库。

这个策略能保证“同内容只进库一次”，同时支持“同 source 内容更新”。

---

## 3.3 `doc_id` 与文档身份

`doc_id = sha256(source_key + "|" + content_sha256)`，用于稳定文档级身份。

当 reader 返回多个 `Document`（如多页/多段）时，当前实现会派生：

- `doc_id_p0`, `doc_id_p1`, ...

并尝试写入 `id_` / `doc_id` 属性（兼容不同版本接口）。

意义：

- 降低多段文档 ID 冲突风险
- 为后续增量刷新、审计、追踪来源打基础

---

## 3.4 缓存 v2 与原子写入

缓存文件默认 `file_hash_cache.json`，格式：

```json
{
  "_format": 2,
  "ingest_root": "/abs/path",
  "sources": {
    "docs/a.md": "sha256...",
    "docs/b.pdf": "sha256..."
  }
}
```

关键点：

- 支持旧格式迁移（旧格式是 path->hash）
- 保存时先写临时文件再 `os.replace`，防止写一半导致损坏

实现位置：`rag/cache.py`

---

## 3.5 Chroma 元数据与删除策略

元数据包含：

- `source_key`
- `content_sha256`
- `file_name`
- `ingest_root`
- `doc_id`

删除策略：

- 内容更新时，按 `source_key` 执行 `chroma_collection.delete(where=...)`

这样比按 `file_path` 删除更稳健。

实现位置：`rag/store.py` + `rag/ingest.py`

---

## 3.6 多格式读取与切分策略

不同文档类型采用不同 reader + splitter（`rag/file_handlers.py`）：

- `.txt`: `SentenceSplitter(1024, overlap=200)`
- `.md`: `MarkdownNodeParser + SentenceSplitter`
- `.docx`: `SemanticSplitterNodeParser(embed_model=...)`
- `.pdf`: `SentenceSplitter(1536, overlap=256)`
- `.xlsx`: `TokenTextSplitter(512, overlap=64)`
- `.pptx`: `SentenceSplitter(2048, overlap=300)`

这是一个典型“按格式定制 chunk 策略”的实践，能平衡召回质量与成本。

---

## 3.7 异步流程与吞吐控制

入库流程是异步函数串行处理单文件，chunk 级写入采用批次：

- `BATCH_SIZE` 控制单次向量 upsert 数量
- 每个文件失败会重试 `RETRY_TIMES`
- 每轮任务后 `clear_memory()` 输出 RSS

这是“稳态优先”的策略，易于排障；若追求极致吞吐可再做并发 worker 化。

---

## 4. 端到端流程（ingest）

1. 扫描输入路径（过滤忽略目录）
2. 解析 `ingest_root`
3. 加载缓存并迁移（如有必要）
4. 对每个文件：
   - 后缀/大小过滤
   - 计算 `sha256`
   - 去重判定
   - 读取文档（带重试）
   - 注入元数据与文档 ID
   - 切片 + 嵌入 + 入库
   - 更新缓存

---

## 5. 上线建议（生产实践）

- 固定 `RAG_INGEST_ROOT`，避免路径漂移导致重复索引
- 固定 `RAG_CHROMA_PATH` 到持久卷（容器/云主机重启不丢数据）
- 将日志文件接入集中化日志系统
- 在 CI 中加入：
  - `python -m py_compile`
  - 核心模块单测（`paths/cache/ingest dedup`）
- 对 `file_hash_cache.json` 做周期性备份
- 监控指标建议：
  - 入库文件数/跳过数/失败数
  - 每批次耗时
  - 内存峰值（RSS）

---

## 6. 常见问题（FAQ）

### Q1: 为什么我改了目录后出现重复索引？

大概率是 `RAG_INGEST_ROOT` 未固定，导致 `source_key` 发生变化。  
建议在 `.env` 固定 `RAG_INGEST_ROOT`。

### Q2: 如何限制超大文件不入库？

设置 `RAG_MAX_FILE_MB`（例如 `50`）即可跳过超大文件。

### Q3: 旧缓存能直接用吗？

可以。系统会自动识别旧格式并迁移到 v2。

### Q4: 如何扩展新文件格式？

在 `rag/file_handlers.py` 的 `FILE_HANDLERS` 增加后缀项：

- `reader`
- `split`（transformations 列表）

---

## 7. 后续可继续增强的方向

- 并发入库 worker 池（按 CPU/IO 自适应）
- 文档级增量刷新 API（按 `doc_id`）
- 元数据过滤检索（按 `source_key` 前缀、文档类型）
- 更完善的异常分类与死信重试
- 单测与基准测试完善（延迟、吞吐、召回）

---

## 8. 许可证

按你的项目策略添加（例如 MIT / Apache-2.0 / 私有）。
