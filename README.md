# EvoAgent

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)](https://vuejs.org/)
[![Tauri](https://img.shields.io/badge/Tauri-2-24C8DB?logo=tauri&logoColor=white)](https://tauri.app/)
[![SQLite](https://img.shields.io/badge/SQLite-local-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Version](https://img.shields.io/badge/version-0.4.1-1769c2)](#windows-桌面端)

EvoAgent 是一套面向高校教学科研的 Windows 多智能体协作平台，采用 Vue 3、Python/FastAPI、SQLite 和 Tauri 2 构建。当前版本已覆盖 Agent 创建与进化、全链路 RAG、多 Agent 工作流、在线模型与图片生成、安全审批、用户记忆和成果数据库交付。

当前内置“教育学科研证据链”示范场景，可替换成医学、法学、经济学、计算机等学科包。

项目仓库：[github.com/ElectronicRain/EvoAgent](https://github.com/ElectronicRain/EvoAgent)

## 快速开始

### 普通用户

1. 从项目的 [Releases](https://github.com/ElectronicRain/EvoAgent/releases) 下载最新的 `EvoAgent_*_x64-setup.exe`。
2. 双击安装并启动 EvoAgent。
3. 进入“扩展与模型”，添加 OpenAI Chat Completions 兼容接口并测试连通性。
4. 按需添加图片生成接口，并在“Agent 工厂”创建或配置 Agent。
5. 直接与 Agent 对话，或进入“协作工作流”手动/智能编排完整任务。

如果尚未发布 Release，可按“开发运行”和“测试与构建”章节从源码运行。生产 Agent 必须使用已经启用的在线模型接口；未配置可用接口时系统会明确报错，不会用离线内容冒充真实推理结果。

### 开发者

```powershell
git clone https://github.com/ElectronicRain/EvoAgent.git
cd EvoAgent
./scripts/setup.ps1
./scripts/dev.ps1
```

浏览器访问 `http://127.0.0.1:5173`，API 文档位于 `http://127.0.0.1:8000/docs`。

## 已实现能力

- Agent 工厂：按“启用 / 候选 / 已归档”和自定义分组管理 Agent；支持模板、新建 Agent、完整设置编辑、独立模型、工具、Skills、MCP、知识库、RAG、安全策略和版本谱系。
- Agent 联动：Agent 可通过 `call_agent` 互相调用，具备深度限制和循环检测。
- Agent 对话：可同时打开多个浮动对话窗口，支持全屏、最小化堆叠和跨页面悬浮；关闭、最小化或切换页面不会终止后台任务。支持持久化多轮会话、刷新恢复、上下文续聊、流式执行事件和历史步骤回放。
- 全链路 RAG：包括检索（Retrieval）、增强（Augmentation）和生成（Generation）；支持问题独立化、查询扩展、向量与全文混合召回、结果融合、Rerank、父子块上下文组装、引用生成、生成质量校验和修复。
- 联网研究：根据任务自动分流；论文、文献、综述等学术任务使用 Google Scholar 学术检索入口与 Crossref 元数据，学校、企业、产品、新闻等调查任务使用普通网页与权威官网检索。工作流只从用户原始意图和已确认条件提取研究主题、年份与文献目标，上游节点文本不会污染检索范围；像“网格质量”这样存在跨领域歧义的主题会在运行前确认数值计算、3D 视觉或两者对比，检索端再按研究对象强制过滤同名噪声并优先近年论文。文献数量是优先目标，可尽量达到或超过；取得部分真实来源时会披露实际数量并继续，只有 0 条真实来源才会在模型生成前停止，始终禁止用占位或虚构文献补数。
- 联网访问中心：Agent 对话和工作流都会按标签集中展示当前访问的检索页、Crossref API、候选论文和原文页，并标注正在访问、已读取、失败或等待验证。Google Scholar 触发反爬时，用户可在单一隔离 WebView 窗口中手动通过机器人验证，再让当前检索继续。只短暂同步反爬所需的非登录 Cookie，不写入数据库。
- 来源展示：学术结果展示原文与 Google Scholar 精确题名入口；普通网页只展示网页搜索及原文入口。所有来源均显示可信度等级、评分与理由。
- 来源复核：网页可在软件内预览，并可对每条来源执行“确认采用”或“排除”，复核结果持久化到 SQLite。
- 成果交付：Agent 和工作流产出、运行轨迹及文档元数据写入 SQLite；结果区会安全渲染 Markdown、表格、代码、公式和图片，每份工作流成果及整次运行都可下载为排版规范的 `.docx` Word 文档。
- AI 文档课堂：当前 Agent 使用已绑定模型或最近启用的模型接口动态生成口语化教学脚本；可选择讲解章节，只圈画模型识别的重点词，并在独立板书区逐字书写公式推导。支持硅基流动 CosyVoice2 神经语音、8 种云端中文音色、自然/生动/严谨语气、Windows 本地语音降级、暂停、清除、用户接管画笔、公式点选提问及边看文档边追问。
- 可视化工作流：全量展示 Agent、知识库和专业节点，支持画布拖动、节点拖放、端口连线、条件分支、变量槽、模板、安全函数、合并、工具、成果节点、缩放、适应画布、全屏及左右/底部面板收起。
- 智能编排专家：每个画板拥有独立、可恢复的专家会话，新建画板不会继承上一画板的对话或任务。首次编排先在本地识别任务类型与缺失约束，按综述、研究、开发、数据分析、规划、写作等场景动态提问；确认后再调用一次编排模型，减少无效模型消耗。专家可生成工作流、分支、变量、知识库节点以及配置完整的新 Agent；没有合适现有 Agent 时会按任务自动组建专用 Agent 团队，新 Agent 自动绑定现有在线文本/图片模型，并继承全部本地工具、已启用 Skills 与 MCP 服务。生成方案通过资源和可执行链路校验后才会应用并自动保存，用户仍可手工修改节点、连线和参数。
- 工作流运行：保存、智能编排实体化和正式运行共享同一套可执行性门禁，提前检查 Agent 状态与在线模型、知识库、工具及变量引用；失效、归档、离线或来自旧画板的 Agent 绑定会在节点和工具栏明确标记，并可零模型调用自动改绑同谱系在线版本，或创建继承全部工具、Skills、MCP 与安全能力的新在线 Agent。本地 API 禁止读取浏览器旧缓存，新增资源应用后立即可见。运行时实时显示每个节点及 Agent 内部 RAG、联网、证据压缩、模型、工具和成果事件；支持循环次数、停止条件、运行中暂停、中断、人工引导和每轮成果文档。模型接口负责成本安全的瞬时故障重试，节点默认不整体重跑；已经可能被服务接收的读超时不会自动重发，避免重复计费。Agent 节点可按规划、检索、撰写、审核、均衡或完整能力设置独立工具策略与上下文预算；重复工具结果自动复用，超长结果自动压缩，达到预算后基于已有证据收敛。长文节点可独立设置输出 Token 上限，文献综述会在交付前校验是否存在真实来源、完整章节、参考文献和截断状态；来源数量低于优先目标时以警告完成交付，0 来源等关键问题仍会阻止伪成功。
- 工作流 RAG 去重：知识库节点负责一次检索并沿连线传递可追溯证据，检索、撰写和评审 Agent 默认不重复执行内部 RAG；节点可按需切换为 Agent 自身 RAG，并可控制是否启用查询改写，从而减少在线模型、Embedding 与 Rerank 的重复调用。
- 工作流恢复：节点状态、执行轨迹、运行参数和成果持久化；切换页面后自动恢复当前运行、追赶遗漏事件并继续显示后台进度。
- 本地电脑工具：工作区文件列表、读取、写入、搜索和受控 PowerShell。
- 学科知识库：支持多文件或整个文件夹递归导入，保留目录层级，并完成清洗、去重、父子分块、向量化和检索问答。
- 安全治理：可为每次 Agent/工作流运行选择只读、应用工作区、指定路径或完全访问，以及继承、人工审批、自动执行或拒绝策略；风险操作会在“运行详情”中等待批准/拒绝，关键风险仍受硬拦截和审计。
- 审批策略：内置“稳健默认”“严格只读”“演示自动化”，支持自定义 JSON 规则。
- 自定义模型 API：支持 OpenAI Chat Completions 兼容的文本和图片模型 Endpoint、独立模型名、请求头、附加参数、用途路由和连通性测试；硅基流动自动优先使用 `/v1` 与流式响应，避免长生成被固定读超时误杀；文本接口连通性测试只读取模型列表，不再产生生成费用。Agent 只使用现有在线接口执行任务。
- 密钥保护：API Key 加密落盘，列表接口与界面不回传明文。
- 知识库：PDF/DOCX/PPTX 等文档、网页、数据库和第三方 API 接入；点击卡片打开独立管理窗口，可查看并增删改查文档、正文、分块、向量和数据源；多对多知识库分组与全库/分组/单库检索；清洗去重、结构化父子切分、SiliconFlow Embedding、SQLite 向量 + FTS5 混合检索、Rerank 与带引用生成。
- 扩展中心：MCP Streamable HTTP / stdio、插件清单和 `SKILL.md` 同步，每个 Agent 均可配置 exec、MCP 和 Skills 能力。
- 内置扩展：Office 文档解析、Citation Guard、成果导出、工作区 MCP、知识库 MCP、科研 Skills，以及用于数学推导和交互图表的 JSXGraph Skill。
- 受控进化：围绕 Agent 目标任务自动联网检索改进方法，按来源可信度整理并封装为候选版本专属 `SKILL.md`，同步优化系统提示词和目标任务提示词；支持自定义基准用例、实时过程、基线/候选逐用例评测、Markdown 进化成果、人工批准和旧版本保留。
- 用户与感知：本地用户注册/登录、每日/每周/每月 Token 用量、使用明细、对话记忆、用户画像以及全局 AI 回复风格预设和自定义。
- Windows 桌面端：Tauri 自动启动 Python sidecar，提供 NSIS 和 MSI 安装包。

## 安装包

构建后的安装包位于：

```text
frontend/.tmp/tauri-target-0.4.1/release/bundle/nsis/EvoAgent_0.4.1_x64-setup.exe
frontend/.tmp/tauri-target-0.4.1/release/bundle/msi/EvoAgent_0.4.1_x64_en-US.msi
```

安装后的持久化目录：

```text
%LOCALAPPDATA%\EvoAgent\
├── evoagent.db
├── .secret.key
├── workspace\
├── skills\
└── plugins\
```

## 开发运行

环境要求：Python 3.11+、Node.js 18+、pnpm、Rust stable、WebView2。

```powershell
./scripts/setup.ps1
./scripts/dev.ps1
```

浏览器开发地址：`http://127.0.0.1:5173`<br>
API 文档：`http://127.0.0.1:8000/docs`

知识库完整建立过程、系统架构和工作流程见 [`docs/KNOWLEDGE_SYSTEM_TECHNICAL_GUIDE.md`](docs/KNOWLEDGE_SYSTEM_TECHNICAL_GUIDE.md)，接口和 RAG 存储/检索参数见 [`docs/KNOWLEDGE_API.md`](docs/KNOWLEDGE_API.md)。

桌面开发：

```powershell
cd frontend
pnpm desktop:dev
```

## 项目结构

```text
EvoAgent/
├── backend/                 # FastAPI、Agent 引擎、工作流、工具与 SQLite 模型
│   ├── app/
│   └── tests/
├── frontend/                # Vue 3 前端与 Tauri 2 Windows 桌面壳
│   ├── src/
│   └── src-tauri/
├── scripts/                 # 环境初始化、开发启动和 Windows 打包脚本
├── docs/                    # 设计与比赛需求对应文档
├── data/                    # 本地开发数据目录；数据库和用户成果不会提交
├── pyproject.toml
└── README.md
```

## 测试与构建

```powershell
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check backend
cd frontend
pnpm build
cd src-tauri
cargo check
```

生成 Windows 安装包：

```powershell
./scripts/build_desktop.ps1
```

## 模型 API 配置

在“扩展与模型 → 模型 API → 添加模型接口”中填写：

- Base URL，例如 `https://example.com/v1`
- API Key
- 默认模型名
- 可选的自定义请求头
- 可选的附加请求参数
- 请求超时

保存后先执行连通性测试，再在 Agent 工厂中将 Agent 绑定到该 Endpoint。

## Agent 对话与工作流画板

- 在“Agent 工厂”选择 Agent 后点击“打开对话”，对话以浮动窗口打开；可并发运行多个 Agent，并在全屏、普通窗口和最小化堆栈之间切换。
- 点击 Agent 卡片右下角的设置按钮，可修改已有 Agent 的模型、提示词、工具、Skills、知识库和审批策略。
- 右侧“运行与交付”包含“步骤 / 网页 / RAG / 文档”四个页签，实时显示模型事件、检索词、候选网址、抓取正文摘要和排版后的 Markdown 成果。
- “网页”页签可在 EvoAgent 内预览所选来源，查看可信度理由，并确认采用或排除；若目标网站禁止 iframe 嵌入，可使用原文链接在系统浏览器打开。
- “文档”页签可放大为课堂模式：先选择需要讲解的部分，再选择云端或 Windows 音色及授课风格，点击“开始讲解”。Agent 会用自己的话串联知识、仅圈画重点词并逐步书写公式；点击公式仍可让当前 Agent 单独解释。云端神经语音会使用已配置的硅基流动 API Key 并产生相应模型用量。
- 页面刷新或重新打开软件时，当前用户消息、运行状态和已写入的执行步骤会从 SQLite 恢复；运行完成后自动补回 Agent 回复与文档。关闭对话窗口或切换页面不会取消已经提交的任务。
- 重新打开历史会话时，单击任意 Agent 回复即可回放该轮步骤。
- 在“协作工作流”中，从左侧资源栏拖入 Agent、知识库或专业节点（也可双击居中添加）；从节点右侧端口拖到目标节点左侧端口即可连接。左键拖动空白画布可平移，工具栏支持缩放、适应画布和全屏。
- Agent 节点可使用卡片删除按钮、工具栏或 Delete 键删除；连线可先单击选中后使用工具栏/Delete 键删除，也可双击直接删除。
- “工作流智能编排专家”可根据目标自动创建 Agent、知识库节点、条件分支、变量和完整连线；应用方案后仍可继续手动编辑。
- Agent 节点的“节点工具策略”会按职责限制工具轮数与调用次数；“节点 RAG 策略”用于选择 Agent 自身知识库或只复用上游证据。默认自动策略会让提纲节点直接规划、检索节点只检索和综合一次、撰写/审核节点复用上游结果，避免重复消耗模型额度。
- 点击“开始运行”前，可在“本次运行安全策略”中选择访问范围和审批方式。运行时会实时显示当前节点、节点耗时、Agent 内部步骤和最终结果，无需等待整个工作流结束才看到状态。
- 点击“开始运行”后会先检查任务是否明确；若语言、研究对象、范围、数量、时效、交付形式或验收标准等关键要求缺失，会先弹出场景化确认窗口，补全后才启动工作流。已经写明的要求不会重复询问。
- “访问网站”页签会保留本次运行的站点状态；点击“打开联网访问中心”可切换所有站点。若出现“等待机器人验证”，在统一访问窗口中处理后点击“我已通过验证，继续”；也可选择跳过并使用 Crossref 等备用源。
- 工作流完成后，在“最终成果”中阅读渲染后的 Markdown；右上角可下载整次运行的 Word 文档，“本次产出文档”列表也支持逐份预览和下载。
- Word 导出会自动展开历史成果中的 JSON 结果封装，将转义换行和内层 Markdown 恢复为真正的标题、正文、列表和表格；旧成果无需重新执行即可重新下载。
- 需要人工审批的工具操作会暂停在对应节点，并出现在“运行详情”中；用户可直接批准并继续或拒绝。切换到其他模块再返回时，系统会自动重连、恢复运行状态并补齐事件。

Agent 和工作流不会降级为离线演示模型。运行前必须在“扩展与模型”中至少启用一个健康的在线文本模型接口；需要生成图片时还需启用图片模型接口。

## Windows 桌面端

当前桌面版本为 **v0.4.1**。源码构建后，主程序和安装包位于 `frontend/.tmp/tauri-target-0.4.1/release/`，本地客户端安装目标位于 `frontend/src-tauri/target/release/`。正式版将用户数据独立保存在 `%LOCALAPPDATA%\EvoAgent\`，升级或替换程序不会覆盖数据库、密钥、工作区、Skills 和插件。

v0.4.1 将精确短语规则扩展到全部学术检索源：Google Scholar、Crossref 和联网访问面板统一只使用完整引号短语，结束引号后不再附加 `solver evaluation`、`discretization error` 等扩展词。

v0.4.0 优化学术检索精度：Crossref 会优先只使用检索式中明确加引号的主题，不再把面向搜索引擎的扩展词误当成题名或摘要关键词；相同精确主题会自动去重，工作流与 Agent 的联网访问面板也会展示各检索源实际使用的查询词。

v0.3.25 修复工作流终稿交付链：产出节点会自动跟随画布实际末端节点，评审与修订 Agent 会同时接收检索证据、正文和评审意见；失败或未通过质量校验的运行不能再伪装成“最终成果”导出，嵌套运行结果也会在 Word 导出前递归解包为可读 Markdown。

若使用本地源码版快捷方式，推荐目标为：

```text
frontend/src-tauri/target/release/evoagent-desktop.exe
```

## 审批规则格式

```json
[
  {
    "name": "只读自动执行",
    "when": { "risk_levels": ["low"] },
    "decision": "auto"
  },
  {
    "name": "写入人工确认",
    "when": { "tools": ["write_file"], "risk_levels": ["medium"] },
    "decision": "ask"
  },
  {
    "name": "关键风险拒绝",
    "when": { "risk_levels": ["critical"] },
    "decision": "deny"
  }
]
```

匹配条件支持 `tools`、`risk_levels` 和 `agent_ids`，支持 `*` 通配。规则按数组顺序匹配。

## 安全边界

- 工具只能访问已授权工作区，路径穿越会被拒绝。
- 删除、磁盘、关机、注册表和账户管理命令命中关键风险规则后直接阻止。
- 审批只对单次请求生效，不会扩大 Agent 的长期权限。
- Agent 进化不能直接覆盖生产版本或修改权限。
- 专业结论必须由真实用户复核；界面统一标识“AI 生成内容”。
- 来源可信度基于 DOI、出版类型、引文元数据、摘要与域名等可追溯性信号，不代表论文结论必然正确。

更多比赛对应关系见 `docs/COMPETITION_ALIGNMENT.md`。

## Git 协作

仓库采用 `main` 作为稳定分支。功能开发建议使用独立分支并通过 Pull Request 合并：

```powershell
git switch main
git pull --ff-only
git switch -c feat/workflow-improvements

# 修改并测试后
git add .
git commit -m "feat: improve workflow execution"
git push -u origin feat/workflow-improvements
```

然后在 GitHub 页面创建 Pull Request。更多分支、提交、测试和 PR 约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 数据与密钥安全

- `.env`、SQLite 数据库、`.secret.key`、用户工作区、模型 API Key 和构建产物均已被 `.gitignore` 排除。
- 不要使用 `git add -f` 强制提交这些文件。
- 提交前建议执行 `git status` 和 `git diff --cached`，确认暂存区不包含私密数据。
