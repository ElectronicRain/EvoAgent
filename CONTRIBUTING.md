# EvoAgent 协作指南

感谢参与 EvoAgent 开发。请通过功能分支和 Pull Request 协作，避免直接在 `main` 上开发。

## 准备环境

```powershell
git clone https://github.com/ElectronicRain/EvoAgent.git
cd EvoAgent
./scripts/setup.ps1
```

## 分支约定

- `feat/<name>`：新功能
- `fix/<name>`：问题修复
- `docs/<name>`：文档
- `refactor/<name>`：重构
- `test/<name>`：测试

示例：

```powershell
git switch main
git pull --ff-only
git switch -c fix/workflow-file-recovery
```

## 提交约定

推荐使用 Conventional Commits：

```text
feat: add evolution skill packaging
fix: recover from missing workspace files
docs: update installation guide
test: cover streamed workflow progress
```

一次提交只处理一个清晰目标。提交前运行：

```powershell
./.venv/Scripts/python.exe -m ruff check backend
./.venv/Scripts/python.exe -m pytest -q
cd frontend
pnpm build
```

## Pull Request

1. 将功能分支推送到 GitHub。
2. 创建 Pull Request，说明问题、方案、测试和界面变化。
3. 确认 CI 通过。
4. 处理审查意见后合并。
5. 合并后删除远端功能分支，并在本地同步 `main`。

## 安全要求

不要提交以下内容：

- API Key、Access Token、密码或 `.env`
- `data/.secret.key`
- SQLite 数据库及 WAL/SHM 文件
- 用户工作区成果和知识库原文
- `.venv`、`node_modules`、Tauri `target`、安装包等构建产物

提交前使用以下命令复核：

```powershell
git status
git diff --cached
```
