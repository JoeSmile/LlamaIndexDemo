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

可选配置（建议直接拷贝 `.env.dev.example` / `.env.prod.example`）：

```env
RAG_INGEST_ROOT=/abs/path/to/your/docs_root
RAG_CHROMA_PATH=./qwen_rag_data
RAG_CACHE_PATH=./file_hash_cache.json
RAG_MAX_FILE_MB=0
RAG_RESOLVE_SYMLINKS=0
RAG_RETRY_TIMES=3
RAG_BATCH_SIZE=4
RAG_EMBED_BATCH_SIZE=10
RAG_QUERY_TOP_K=5
RAG_CHAT_TOP_K=5
RAG_QUERY_SHOW_SOURCES=1
RAG_QUERY_MAX_SOURCE_NODES=5
RAG_QUERY_SOURCE_MAX_CHARS=2000
RAG_QUERY_FULL_OUTPUT=0
RAG_XLSX_MAX_SHEETS=20
RAG_XLSX_MAX_ROWS_PER_SHEET=2000
RAG_XLSX_MAX_COLS=50
RAG_XLSX_MAX_CELL_CHARS=300
```

### 1.3 运行命令

```bash
python llama.py ingest <file_or_dir> [more_paths...]
python llama.py query "你的问题"
python llama.py query --full "你的问题（完整来源输出）"
python llama.py chat
python llama.py chat --full
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

## 3.6 多格式读取、版本兼容与切分策略

不同文档类型采用不同 reader + splitter（`rag/file_handlers.py`），并带有兼容与降级逻辑：

- `.txt`: `SentenceSplitter(1024, overlap=200)`
- `.md`: `MarkdownNodeParser + SentenceSplitter`
- `.docx`: `SemanticSplitterNodeParser(embed_model=...)`
- `.pdf`: `SentenceSplitter(1536, overlap=256)`
- `.xlsx`: `TokenTextSplitter(1200, overlap=120)`（已调整为更稳的粒度）
- `.pptx`: `SentenceSplitter(2048, overlap=300)`

兼容细节：

- `CleanText` 在不同 `llama-index` 版本可能不存在，代码会自动降级继续运行
- `DocxReader/PDFReader/ExcelReader/PptxReader` 采用动态探测导入，避免版本差异导致启动失败
- `ChromaVectorStore` 优先走 `aadd`，若版本无异步接口自动回退 `add`

---

## 3.7 xlsx 改造（重点）

近期对 xlsx 做了稳定性改造，核心目标是避免“卡死 / 内存暴涨 / 输出被截断感过强”。

### 3.7.1 Reader 兼容策略

- 优先使用 `ExcelReader`（如果当前环境可用）
- 若不可用，自动使用 fallback（`openpyxl`）逐行读取

### 3.7.2 大表防护（fallback 模式）

通过环境变量限制读取规模：

- `RAG_XLSX_MAX_SHEETS`
- `RAG_XLSX_MAX_ROWS_PER_SHEET`
- `RAG_XLSX_MAX_COLS`
- `RAG_XLSX_MAX_CELL_CHARS`

这样可显著降低单次入库的峰值内存和 embedding 成本。

### 3.7.3 xlsx 文本组织方式

fallback 会把每个 sheet 转成结构化文本：

- 首行标记 `# sheet: <name>`
- 每行拼接为 `col1 | col2 | ...`
- 超限内容会截断并标记

这是一种“检索优先”的工程折中：更稳、更可控，语义精度略低于专用表格语义模型。

这是一个典型“按格式定制 chunk 策略”的实践，能平衡召回质量与成本。

---

## 3.8 异步流程与吞吐控制

入库流程是异步函数串行处理单文件，chunk 级写入采用批次：

- `BATCH_SIZE` 控制单次向量 upsert 数量
- 每个文件失败会重试 `RETRY_TIMES`
- 每轮任务后 `clear_memory()` 输出 RSS

这是“稳态优先”的策略，易于排障；若追求极致吞吐可再做并发 worker 化。

---

## 3.9 Query/Chat 检索与输出策略

Query 与 Chat 支持独立 topK：

- `RAG_QUERY_TOP_K`
- `RAG_CHAT_TOP_K`

来源片段打印控制：

- `RAG_QUERY_SHOW_SOURCES`
- `RAG_QUERY_MAX_SOURCE_NODES`
- `RAG_QUERY_SOURCE_MAX_CHARS`
- `RAG_QUERY_FULL_OUTPUT`

当 `RAG_QUERY_FULL_OUTPUT=1` 或命令行使用 `--full` 时：

- 打印所有 source nodes
- 不截断 source 文本

适用于调试“是否真正召回到 xlsx / docx / pdf 具体片段”。

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

### Q5: 为什么 `xlsx` 显示 unsupported？

通常是当前环境未加载到 `ExcelReader`。本项目已内置 fallback：

- 若 `ExcelReader` 不可用，会自动尝试 `openpyxl` 读取
- 若仍失败，检查 `openpyxl` / `pandas` 是否安装、文件是否损坏

### Q6: 为什么聊天里看起来“输出不完整”？

有两层截断：

1. 展示截断（`RAG_QUERY_SOURCE_MAX_CHARS`）  
2. xlsx fallback 的单元格截断（`RAG_XLSX_MAX_CELL_CHARS`）

可通过 `--full` 或 `RAG_QUERY_FULL_OUTPUT=1` 做完整来源输出调试。

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
