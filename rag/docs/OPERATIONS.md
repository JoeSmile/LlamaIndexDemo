# Operations Guide

本文档面向运维与值班同学，覆盖部署、运行、排障、恢复与升级策略。

---

## 1. Runtime Requirements

- Python 3.10+（建议 3.11）
- 可访问 DashScope API
- 本地可写目录用于：
  - `RAG_CHROMA_PATH`（向量库）
  - `RAG_CACHE_PATH`（缓存）
  - `RAG_LOG_PATH`（日志）

依赖安装：

```bash
pip install -r requirements.txt
```

---

## 2. Environment Configuration

最低必需：

```env
DASHSCOPE_API_KEY=...
```

生产建议：

```env
RAG_INGEST_ROOT=/data/docs
RAG_CHROMA_PATH=/data/rag/chroma
RAG_CACHE_PATH=/data/rag/file_hash_cache.json
RAG_LOG_PATH=/data/rag/rag_process.log
RAG_MAX_FILE_MB=100
RAG_RETRY_TIMES=3
RAG_BATCH_SIZE=8
RAG_RESOLVE_SYMLINKS=0
```

说明：

- `RAG_INGEST_ROOT` 建议固定，减少路径漂移导致重复索引。
- `RAG_BATCH_SIZE` 不是越大越好，需结合 embedding 与 DB 写入延迟调优。

---

## 3. Standard Operating Procedures

### 3.1 全量入库

```bash
python llama.py ingest /data/docs
```

### 3.2 增量入库

```bash
python llama.py ingest /data/docs/changed_subtree
```

### 3.3 单次问答

```bash
python llama.py query "项目里怎么做去重？"
```

### 3.4 对话调试

```bash
python llama.py chat
```

---

## 4. What to Monitor

建议收集以下运行指标（可从日志解析）：

- 文件扫描总数 `files=...`
- 跳过统计：
  - `skip (unchanged)`
  - `skip (duplicate content)`
  - `skip (suffix/unsupported/size)`
- 失败统计：
  - `read fail ... retry x/y`
  - `gave up: ...`
- 每文件 chunk 数量 `chunks: ... n=...`
- 内存 `RSS MB: ...`

告警建议：

- 连续 `read fail` 超阈值
- `gave up` 比例超过阈值
- RSS 超过安全上限并持续增长

---

## 5. Log Management

默认日志在 `RAG_LOG_PATH`，建议：

- 使用 logrotate（按大小/天轮转）
- 关键字段做结构化提取（文件名、source_key、重试次数）
- 失败日志单独索引（便于回放与补偿）

---

## 6. Failure Scenarios & Playbooks

## 6.1 API Key 缺失

现象：

- 启动即退出，日志报 `DASHSCOPE_API_KEY` 未配置

处理：

1. 检查 `.env` 或系统环境变量
2. 重启任务

---

## 6.2 DashScope 网络/限流错误

现象：

- `read fail ... retry ...` 或 embedding 相关异常增多

处理：

1. 观察是否瞬时抖动（重试后恢复）
2. 若持续失败，降低并发压力（减小 `RAG_BATCH_SIZE`）
3. 排查出口网络与 API 配额

---

## 6.3 Chroma 删除失败

现象：

- `delete by source_key failed: ...`

影响：

- 旧向量可能残留，召回噪声上升

处理：

1. 检查 Chroma 可用性与磁盘空间
2. 确认 metadata 中 `source_key` 存在
3. 必要时离线重建索引（见 8.2）

---

## 6.4 缓存损坏或异常格式

现象：

- `Unknown cache format, starting fresh mapping`

处理：

1. 备份当前缓存文件
2. 允许系统重建映射，或人工修复后再运行
3. 复查磁盘稳定性与写权限

---

## 6.5 入库后结果异常（召回不准）

排查路径：

1. 验证文档是否被扫描到（日志 `files=...`）
2. 验证是否被过滤（suffix/size/unsupported）
3. 验证是否被 dedup 跳过（duplicate content）
4. 检查 chunk 数量是否异常（过大/过小）
5. 检查 `RAG_INGEST_ROOT` 是否漂移

---

## 7. Capacity Planning

容量因素：

- 文档规模（文件数、总字节）
- 文档类型（PDF/Office 解析成本更高）
- chunk 粒度（越细越多向量）
- embedding API 延迟与吞吐

建议：

- 在预生产跑一次全量基准，记录：
  - 每千文件耗时
  - 平均 chunk/file
  - 峰值 RSS
  - 失败率

---

## 8. Backup & Recovery

### 8.1 备份策略

定期备份：

- `RAG_CHROMA_PATH`
- `RAG_CACHE_PATH`
- （可选）日志归档

建议至少日备份一次。

### 8.2 一键重建索引（灾难恢复）

当数据不一致或库损坏时：

1. 停服务
2. 备份并清理旧 Chroma 目录
3. 备份并清理缓存文件
4. 重新执行全量 `ingest`

优点：状态最干净。缺点：耗时较长。

---

## 9. Change Management

每次发布前建议 checklist：

- `python -m py_compile` 通过
- 小样本 ingest 回归通过（覆盖各文件类型）
- `query` 与 `chat` 基础可用
- 缓存迁移路径验证（旧缓存 -> v2）
- 关键日志关键字可观测

发布后观察窗口：

- 前 30 分钟重点看失败率和内存曲线

---

## 10. Security Checklist

- `.env` 不进版本库
- API Key 使用最小权限与定期轮换
- Chroma / cache / logs 目录设置最小读写权限
- 避免把敏感原文直接写进日志

---

## 11. Troubleshooting Commands

快速检查语法：

```bash
python -m py_compile llama.py rag/*.py
```

查看帮助：

```bash
python llama.py
```

最小链路验证：

```bash
python llama.py ingest ./some_small_dir
python llama.py query "test question"
```

---

## 12. Operational Best Practices

- 固定 `RAG_INGEST_ROOT`，不要在多种 cwd 下随意 ingest
- 先小批量验证，再跑全量
- 优先处理失败文件重试，再进行下一批全量
- 定期抽样 QA，验证召回质量没有明显回退
