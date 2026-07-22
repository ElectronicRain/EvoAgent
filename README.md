# EvoAgent

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)](https://vuejs.org/)
[![Tauri](https://img.shields.io/badge/Tauri-2-24C8DB?logo=tauri&logoColor=white)](https://tauri.app/)
[![SQLite](https://img.shields.io/badge/SQLite-local-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)

EvoAgent 是一套面向高校教学科研的 Windows 多智能体协作平台，采用 Vue 3、Python/FastAPI、SQLite 和 Tauri 2 从零构建。

当前内置“教育学科研证据链”示范场景，可替换成医学、法学、经济学、计算机等学科包。

项目仓库：[github.com/ElectronicRain/EvoAgent](https://github.com/ElectronicRain/EvoAgent)

## 快速开始

### 普通用户

1. 从项目的 [Releases](https://github.com/ElectronicRain/EvoAgent/releases) 下载最新的 `EvoAgent_*_x64-setup.exe`。
2. 双击安装并启动 EvoAgent。
3. 进入“扩展与模型”，添加 OpenAI Chat Completions 兼容接口并测试连通性。
4. 在“Agent 工厂”创建或配置 Agent，然后通过对话或工作流运行任务。

如果尚未发布 Release，可按“开发运行”和“测试与构建”章节从源码运行。未配置 API Key 时会使用离线演示模型。

### 开发者

```powershell
git clone https://github.com/ElectronicRain/EvoAgent.git
cd EvoAgent
./scripts/setup.ps1
./scripts/dev.ps1
```

浏览器访问 `http://127.0.0.1:5173`，API 文档位于 `http://127.0.0.1:8000/docs`。

## 已实现能力

- Agent 工厂：Agent 模板、现有 Agent 设置编辑、独立模型、工具、Skills、知识库、权限和版本谱系。
- Agent 联动：Agent 可通过 `call_agent` 互相调用，具备深度限制和循环检测。
- Agent 对话：持久化多轮会话、刷新恢复运行状态、上下文续聊、流式执行事件与历史步骤回放。
- 联网研究：根据任务自动分流；论文、文献、综述等学术任务使用 Google Scholar 学术检索入口与 Crossref 元数据，学校、企业、产品、新闻等调查任务使用普通网页与权威官网检索。搜索型 Agent 无论输入何种主题都会强制联网，并通过四组多角度检索词、主题相关性过滤、正文/摘要抓取、多轮综合与质量审校尽量全面地完成任务。
- 来源展示：学术结果展示原文与 Google Scholar 精确题名入口；普通网页只展示网页搜索及原文入口。所有来源均显示可信度等级、评分与理由。
- 来源复核：网页可在软件内预览，并可对每条来源执行“确认采用”或“排除”，复核结果持久化到 SQLite。
- 成果交付：研究任务自动生成 `.md` 文件，保存到工作区，并以排版后的 Markdown、表格和公式在对话右侧直接渲染。
- AI 文档课堂：当前 Agent 使用已绑定模型或最近启用的模型接口动态生成口语化教学脚本；可选择讲解章节，只圈画模型识别的重点词，并在独立板书区逐字书写公式推导。支持硅基流动 CosyVoice2 神经语音、8 种云端中文音色、自然/生动/严谨语气、Windows 本地语音降级、暂停、清除、用户接管画笔、公式点选提问及边看文档边追问。
- 可视化工作流：Agent 侧栏指针拖放与双击添加、节点自由移动、50%–180% 缩放和适应画布、端口拖线、箭头、循环检测、节点/连线删除、属性配置和运行轨迹。
- 本地电脑工具：工作区文件列表、读取、写入、搜索和受控 PowerShell。
- 安全治理：风险分级、策略匹配、人工审批、关键风险二次阻止和审计日志。
- 审批策略：内置“稳健默认”“严格只读”“演示自动化”，支持自定义 JSON 规则。
- 自定义模型 API：OpenAI Chat Completions 兼容 Endpoint、独立模型名、请求头、附加参数和连通性测试。
- 密钥保护：API Key 加密落盘，列表接口与界面不回传明文。
- 知识库：PDF/DOCX/TXT/MD/CSV 导入、自动切分、SQLite FTS5、中文模糊召回和引用定位。
- 扩展中心：MCP Streamable HTTP / stdio、插件清单和 `SKILL.md` 同步。
- 内置扩展：Office 文档解析、Citation Guard、成果导出、工作区 MCP、知识库 MCP，以及五项科研 Skills。
- 受控进化：围绕 Agent 目标任务自动联网检索改进方法，按来源可信度整理并封装为候选版本专属 `SKILL.md`，同步优化系统提示词和目标任务提示词；支持自定义基准用例、实时过程、基线/候选逐用例评测、Markdown 进化成果、人工批准和旧版本保留。
- Windows 桌面端：Tauri 自动启动 Python sidecar，提供 NSIS 和 MSI 安装包。

## 安装包

构建后的安装包位于：

```text
frontend/src-tauri/target/release/bundle/nsis/EvoAgent_0.1.0_x64-setup.exe
frontend/src-tauri/target/release/bundle/msi/EvoAgent_0.1.0_x64_en-US.msi
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

- 在“Agent 工厂”选择 Agent 后点击“打开对话”，支持连续追问。
- 点击 Agent 卡片右下角的设置按钮，可修改已有 Agent 的模型、提示词、工具、Skills、知识库和审批策略。
- 右侧“运行与交付”包含“步骤 / 网页 / 文档”三个页签，实时显示模型事件、检索词、候选网址、抓取正文摘要和排版后的 Markdown 成果。
- “网页”页签可在 EvoAgent 内预览所选来源，查看可信度理由，并确认采用或排除；若目标网站禁止 iframe 嵌入，可使用原文链接在系统浏览器打开。
- “文档”页签可放大为课堂模式：先选择需要讲解的部分，再选择云端或 Windows 音色及授课风格，点击“开始讲解”。Agent 会用自己的话串联知识、仅圈画重点词并逐步书写公式；点击公式仍可让当前 Agent 单独解释。云端神经语音会使用已配置的硅基流动 API Key 并产生相应模型用量。
- 页面刷新或重新打开软件时，当前用户消息、运行状态和已写入的执行步骤会从 SQLite 恢复；运行完成后自动补回 Agent 回复与文档。
- 重新打开历史会话时，单击任意 Agent 回复即可回放该轮步骤。
- 在“协作工作流”中，从左侧 Agent 工厂拖入画布（也可双击居中添加）；从节点右侧端口拖到目标节点左侧端口即可连接。画板支持工具栏缩放、Ctrl+滚轮缩放和一键适应。
- Agent 节点可使用卡片删除按钮、工具栏或 Delete 键删除；连线可先单击选中后使用工具栏/Delete 键删除，也可双击直接删除。
- 点击“开始运行”会先自动保存，并实时显示当前节点、节点耗时、长任务等待时间和最终结果；无需等待整个工作流结束才看到状态。

无 API Key 时系统自动使用离线演示模型，界面和完整工作流仍可运行，但不能替代真实模型的专业推理。

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
