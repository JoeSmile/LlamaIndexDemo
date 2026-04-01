# Architecture Guide

本文档说明当前 RAG 系统的架构边界、模块职责、核心数据结构与关键流程。

---

## 1. System Overview

该项目是一个离线/半离线文档入库 + 在线问答系统：

- **Ingest path**: 多格式文件 -> 切片 -> 向量化 -> 写入 Chroma
- **Query path**: 用户问题 -> 检索 Chroma -> LLM 生成回答

技术栈：

- LLM / Embedding: DashScope (`QWEN_TURBO`, `text-embedding-v4`)
- Framework: LlamaIndex
- Vector DB: Chroma (persistent mode)

---

## 2. High-Level Module Map

入口层：

- `llama.py`：极薄入口，只调用 `rag.cli.main()`
- `rag/cli.py`：解析 `ingest/query/chat` 命令并分发

核心层：

- `rag/ingest.py`：扫描、去重、重试、切片、写入向量库
- `rag/query.py`：问答与聊天接口（基于向量检索）

基础设施层：

- `rag/config.py`：环境变量、常量、日志初始化
- `rag/models.py`：LLM/Embedding 单例初始化
- `rag/store.py`：Chroma 客户端与删除方法
- `rag/file_handlers.py`：文件格式 reader + splitter 配置
- `rag/paths.py`：路径归一化、`source_key` 与 `doc_id`
- `rag/cache.py`：缓存读写、迁移、版本控制
- `rag/utils.py`：哈希、内存日志、统计函数

---

## 3. Dependency Flow

依赖方向（从上到下）：

1. `cli` 依赖 `ingest/query`
2. `ingest/query` 依赖 `models/store/config` 与工具模块
3. `cache/paths/utils/file_handlers` 依赖 `config`（日志/常量）
4. `models/store` 依赖外部 SDK（LlamaIndex/Chroma）

设计原则：

- **单向依赖**：业务层不被基础设施层反向引用
- **避免环依赖**：`paths/cache/utils` 仅提供纯工具能力
- **按需初始化**：入口导入核心模块，降低耦合

---

## 4. Core Data Model

### 4.1 `source_key`

定义：文档在逻辑知识库中的稳定定位键。

- 在 `ingest_root` 下：使用相对路径（统一 `/`）
- 在 `ingest_root` 外：`__external__/<path_hash>`

目标：

- 避免绝对路径泄露
- 降低路径变动导致重复索引风险

### 4.2 `content_sha256`

定义：文件二进制内容哈希。

目标：

- 内容级去重（同内容仅索引一次）
- 低成本变化检测

### 4.3 `doc_id`

定义：`sha256(source_key + "|" + content_sha256)`。

当 reader 产出多段 `Document` 时，派生 `doc_id_p{i}` 防冲突。

### 4.4 Cache v2

存储结构（`RAG_CACHE_PATH`）：

```json
{
  "_format": 2,
  "ingest_root": "/path/to/root",
  "sources": {
    "docs/a.md": "sha256...",
    "docs/b.pdf": "sha256..."
  }
}
```

---

## 5. Ingest Pipeline (Detailed)

`rag.ingest.batch_process` 主流程：

1. 校验输入路径非空
2. 递归扫描候选文件（忽略 `IGNORE_DIR`）
3. 解析 `ingest_root`（优先环境变量）
4. 加载缓存（必要时从旧格式迁移到 v2）
5. 构建 `indexed_hashes = set(sources.values())`
6. 逐文件调用 `process_single_file`

`process_single_file` 子流程：

1. 路径归一化 + 后缀识别
2. 大小限制判断（`RAG_MAX_FILE_MB`）
3. 后缀黑名单与格式支持校验
4. 计算 `file_hash`
5. 执行去重判定：
   - 未变化：跳过
   - 内容已存在：跳过嵌入，仅更新映射
   - 旧内容变更：先删旧向量再入库
6. 读取文档（失败重试）
7. 注入 metadata 与稳定 id
8. 执行 `IngestionPipeline`（split + embed）
9. 批量 `aadd` 写入 Chroma
10. 更新缓存并原子落盘

---

## 6. Query Pipeline

`rag.query` 中：

- `query_answer`: 创建 `VectorStoreIndex` + `query_engine`，执行 `aquery`
- `chat_loop`: 创建 `chat_engine`，循环 `achat`

说明：

- 当前是“启动即创建引擎”策略，适合 CLI 模式
- 若迁移到 API 服务，可考虑长生命周期 engine 复用

---

## 7. Consistency & Idempotency

### 7.1 幂等性

同一输入重复执行 `ingest`：

- 若内容未变化，不会重复写入向量（由 `cached == file_hash` 保证）
- 若不同路径同内容，只索引一次（由 `indexed_hashes` 保证）

### 7.2 一致性

- 缓存写入使用 `tmp + os.replace`，避免部分写入
- 内容变更时先清理旧 `source_key` 向量，再写新内容

### 7.3 边界情况

- 若 Chroma 删除失败，系统记录 warning，继续流程
- 若缓存与实际库状态偏离，可能出现少量脏映射，后续全量重建可修复

---

## 8. Performance Characteristics

主要成本：

- 文件解析（PDF/Office）
- 文本切分（尤其 semantic split）
- embedding RPC 调用
- 向量写入 I/O

当前优化点：

- 内容去重显著降低重复 embedding 成本
- 批量写入（`BATCH_SIZE`）减少写放大
- 大文件阈值可控（`RAG_MAX_FILE_MB`）

可进一步优化：

- 并发文件处理（worker pool）
- 分级重试（网络错误与解析错误分离）
- 缓存热启动与增量索引窗口

---

## 9. Security & Privacy Notes

- 不在 metadata 里暴露绝对路径（通过 `source_key` 规避）
- 仅使用 DashScope key，主动移除 `OPENAI_API_KEY`
- 生产建议：
  - `.env` 不入库
  - `RAG_CACHE_PATH` 与 `RAG_CHROMA_PATH` 加访问控制
  - 日志脱敏（若将来写入更多业务字段）

---

## 10. Extension Points

### 10.1 新文件类型

在 `rag/file_handlers.py` 增加后缀映射：

- `reader`
- `split` transformations

### 10.2 新向量库

替换 `rag/store.py`，并调整 `ingest.py` 的删除与写入调用。

### 10.3 新模型

在 `rag/models.py` 替换 embedding / llm 实现，接口尽量保持不变。

### 10.4 服务化

新增 `api/` 层（FastAPI 等），复用 `rag.ingest` 与 `rag.query` 作为领域逻辑。

---

## 11. Suggested Future Refactors

- 引入 typed domain objects（例如 `SourceRecord`, `IngestResult`）
- 引入结构化日志（JSON log）
- 为 `paths/cache/dedup` 增加单元测试
- 将 ingest 结果导出为统计报告（成功/跳过/失败）
