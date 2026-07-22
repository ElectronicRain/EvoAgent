# EvoAgent 知识库 API

本文档描述 `knowledgeSet` 分支的多源知识库、文档处理、向量检索与 RAG 问答接口。交互式 OpenAPI 文档在后端启动后访问 `http://127.0.0.1:8000/docs`。

## 1. 总体链路

```text
文件 / 网页 / 数据库 / 第三方 API
  → 文本与结构提取
  → Unicode 规范化、布局去噪、重复行与文档去重
  → 结构感知的父子分块（父块约 1600 字，子块约 480 字，重叠约 80 字）
  → Qwen/Qwen3-VL-Embedding-8B
  → SQLite FTS5 + float32 向量表

用户问题
  → 查询规范化与 LLM 多查询改写
  → Dense / BM25 并行召回
  → RRF 融合候选
  → BAAI/bge-reranker-v2-m3 重排序
  → 文档去重与来源多样性控制
  → 父块上下文扩展和字符预算裁剪
  → 配置的大模型生成带 [资料 N] 引用的答案
```

父子分块使细粒度子块负责准确召回、较完整父块负责生成上下文，避免固定大块遗漏关键信息或小块失去语义。SQLite 中保存归一化 float32 BLOB、模型、维度、内容哈希和来源元数据；同时保留 FTS5。该策略不依赖 Windows 原生向量扩展，适合单机桌面软件、可离线迁移和审计。查询采用 Dense + BM25 + RRF + Rerank，降低单一向量相似度对专业术语、公式和中文专名的漏召回风险。

## 2. 模型配置

### `GET /api/knowledge/config`

返回当前 embedding、rerank、生成模型和检索参数。永远不会返回 API Key 密文或明文；`has_api_key` 只表示是否已经配置。

### `PUT /api/knowledge/config`

```json
{
  "embedding_base_url": "https://api.siliconflow.cn/v1/embeddings",
  "embedding_model": "Qwen/Qwen3-VL-Embedding-8B",
  "rerank_base_url": "https://api.siliconflow.cn/v1/rerank",
  "rerank_model": "BAAI/bge-reranker-v2-m3",
  "api_key": "${SILICONFLOW_API_KEY}",
  "llm_endpoint_id": "可选的模型端点 ID",
  "embedding_batch_size": 16,
  "candidate_k": 30,
  "top_k": 6,
  "context_char_budget": 12000
}
```

- `api_key` 不传：保留原密钥；传空字符串：清除本地密钥。
- 密钥由 Fernet 加密后保存到 SQLite，加密密钥位于本机数据目录，不进入 Git。
- 也可通过环境变量 `EVO_SILICONFLOW_API_KEY` 提供密钥。
- 没有配置 SiliconFlow 密钥时，为保证桌面演示可用，会采用确定性的本地 hash embedding 和词项 rerank；响应 `trace.embedding_provider` 会明确标记 `local-hash-fallback`。正式评测应配置 SiliconFlow。
- `llm_endpoint_id` 对应 `/api/model-endpoints` 中已配置的 OpenAI 兼容生成模型。未配置时返回可读的离线证据摘要。

### `POST /api/knowledge/config/test`

执行一次最小 embedding 和 rerank 请求，返回运行模式、向量维度和重排序结果。没有密钥时状态为 `degraded`（本地降级模式），成功连接 SiliconFlow 时为 `healthy`。

## 3. 知识库与文件

### `POST /api/knowledge-bases`

```json
{"name":"流体网格知识库","discipline":"计算流体力学","description":"教材、论文和标准"}
```

### `GET /api/knowledge-bases`

列出知识库。

### `POST /api/knowledge-bases/{knowledge_base_id}/documents/upload`

`multipart/form-data`，字段名 `file`，最大 25MB。支持 PDF、DOCX、PPTX、TXT、Markdown、CSV、JSON 和 HTML。PDF 保留页码，PPTX 保留幻灯片页码，DOCX 优先保留标题和表格结构。

```bash
curl -F "file=@paper.pdf" http://127.0.0.1:8000/api/knowledge-bases/{id}/documents/upload
```

### `POST /api/knowledge-bases/{knowledge_base_id}/documents/text`

```json
{"title":"网格评价规范","content":"正文……","source":"实验室规范 V2"}
```

### `GET /api/knowledge-bases/{knowledge_base_id}/documents`

列出文档及其状态、哈希、字符数、清洗统计元数据。

## 4. 外部数据源

数据源的请求头、数据库连接地址、参数和请求体整体加密保存；列表接口只返回脱敏 URI 和配置字段名称。

### 网页

`POST /api/knowledge-bases/{knowledge_base_id}/sources/web`

```json
{
  "name": "学科规范网页",
  "url": "https://example.edu/standards",
  "max_pages": 10,
  "same_domain": true,
  "sync_now": true
}
```

抓取仅允许公网 HTTP/HTTPS 地址，不跟随重定向，默认只在同域内遍历，最多 20 页，单页最大 5MB；会移除脚本、样式、导航、页眉页脚等噪声。

### 数据库

`POST /api/knowledge-bases/{knowledge_base_id}/sources/database`

```json
{
  "name": "论文元数据库",
  "connection_url": "sqlite:///D:/datasets/papers.db",
  "query": "SELECT title, abstract, doi FROM papers WHERE year >= :year",
  "params": {"year": 2020},
  "row_limit": 5000,
  "sync_now": true
}
```

- 内置可直接使用 SQLite。
- PostgreSQL、MySQL 等连接由 SQLAlchemy 处理，但运行环境需要安装对应驱动。
- 只接受以 `SELECT` 或 `WITH` 开头的只读查询，最多返回 20,000 行。

### API / 第三方数据

`POST /api/knowledge-bases/{knowledge_base_id}/sources/api`

```json
{
  "name": "第三方论文 API",
  "url": "https://api.example.org/v1/papers",
  "method": "GET",
  "headers": {"Authorization": "Bearer ${THIRD_PARTY_TOKEN}"},
  "params": {"discipline": "CFD"},
  "response_path": "data.items",
  "sync_now": true
}
```

只允许 GET/POST，最大响应 10MB；`response_path` 使用点路径并支持数组数字索引，例如 `data.items.0`。

### 数据源管理

- `GET /api/knowledge-bases/{knowledge_base_id}/sources`：脱敏列出数据源。
- `POST /api/knowledge-sources/{source_id}/sync`：重新同步。
- `GET /api/knowledge-ingestion-jobs/{job_id}`：查询同步阶段、进度、文档/片段/重复数和错误。
- `POST /api/knowledge-bases/{knowledge_base_id}/reindex`：模型切换后重新生成全部子块向量。

## 5. 检索与生成

### `POST /api/knowledge/query`

```json
{
  "query": "二维结构化网格出现负雅可比时意味着什么？",
  "knowledge_base_ids": ["kb-id"],
  "top_k": 6,
  "candidate_k": 30,
  "generate_answer": true
}
```

响应：

```json
{
  "answer": "……[资料 1]",
  "query": "原问题",
  "rewritten_queries": ["原问题", "优化后的检索语句"],
  "chunks": [
    {
      "id": "chunk-id",
      "title": "网格评价规范",
      "content": "命中的子块",
      "context": "用于生成的父块",
      "citation": "网格评价规范，第 3 页，来源：……",
      "score": 0.91,
      "dense_score": 0.82,
      "lexical_score": 3.7,
      "metadata": {"page": 3, "locator": "第 3 页"}
    }
  ],
  "citations": [{"number":1,"document_id":"doc-id","title":"网格评价规范","score":0.91}],
  "trace": {
    "embedding_provider": "siliconflow",
    "embedding_model": "Qwen/Qwen3-VL-Embedding-8B",
    "dense_candidates": 30,
    "lexical_candidates": 18,
    "fused_candidates": 30,
    "reranked": 30,
    "rerank_model": "BAAI/bge-reranker-v2-m3",
    "context_chars": 8660
  }
}
```

`generate_answer=false` 时仍完成查询改写、混合检索和 rerank，只把 `answer` 返回为空。旧接口 `POST /api/knowledge/search` 保持兼容，直接返回片段数组。

## 6. 错误与安全约定

- `400`：文件/数据源/查询配置无效。
- `404`：知识库、数据源或导入任务不存在。
- `413`：上传文件超过限制。
- `422`：请求字段校验失败。
- `502`：embedding、rerank 或生成模型上游失败。
- 数据源同步本身失败时会保留失败状态和脱敏错误，便于在软件内检查后重试。
- API Key 不出现在列表、详情、审计详情和测试快照中。
- 网页和第三方 API 拒绝 localhost、回环、内网、链路本地及保留地址，以降低 SSRF 风险。

## 7. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check backend/app backend/tests
cd frontend
npm run build
```

测试覆盖清洗去重、结构化父子分块、PPTX 解析、文档重复检测、SQLite 数据源同步、外部数据源脱敏注册、SiliconFlow embedding/rerank 请求契约、完整入库和 RAG 查询，以及全部原有业务接口。
