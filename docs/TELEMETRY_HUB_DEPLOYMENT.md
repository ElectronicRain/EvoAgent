# EvoAgent V2.1.4 中央管理与离线日志同步

V2.1.4 的桌面客户端会先把脱敏事件写入本机 SQLite `telemetry_events` 表。中央服务不可用时，事件状态保持为 `pending` 或 `failed`；网络恢复后客户端通过设备令牌批量补传，中央服务以事件 UUID 去重。

## 部署中央服务

中央服务复用同一 FastAPI 后端，但必须部署在持续在线、启用 HTTPS 且使用独立数据库的服务器上。服务器环境变量至少包括：

```env
EVO_DEBUG=false
EVO_DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST/DATABASE
EVO_TELEMETRY_ENABLED=true
EVO_TELEMETRY_HUB_MODE=true
EVO_TELEMETRY_HUB_ADMIN_KEY=使用密码管理器生成的高强度随机值
```

正式部署还应在反向代理或网关中配置速率限制、请求体大小限制、TLS、数据库备份和访问日志。`/api/telemetry-hub/devices/register` 是设备登记入口；`/api/telemetry-hub/events/batch` 只接受登记后签发的 Device Token。

## 配置普通客户端

普通客户端只需要知道中央服务的 HTTPS 地址：

```env
EVO_TELEMETRY_HUB_URL=https://your-evoagent-hub.example.com
EVO_TELEMETRY_HUB_MODE=false
EVO_TELEMETRY_HUB_ADMIN_KEY=
```

普通客户端不应获得中央管理密钥。设备首次同步时会自动登记并将 Device Token 保存在 `%LOCALAPPDATA%\EvoAgent\telemetry_identity.json`。

## 配置管理员设备

管理员设备除中央地址外，还需要单独保存中央管理密钥：

```env
EVO_TELEMETRY_HUB_URL=https://your-evoagent-hub.example.com
EVO_TELEMETRY_HUB_ADMIN_KEY=与服务器一致的管理密钥
```

管理员登录本机后，本地后端先验证 `user_accounts.role = 'admin'`，再代理查询中央服务。密钥不会发送到前端，也不会随普通安装包分发。

## 数据边界

遥测清洗器默认剔除密码、Token、Authorization、API Key、密钥、提示词、输入输出正文、文档正文以及本地绝对路径。管理员面板只展示账户标识、设备、版本、功能事件、耗时、结果和脱敏错误信息。

管理员电脑关机不会影响中央服务接收其他客户端事件。完全离线的客户端仍会保留本地事件，但只有在其重新联网后中央面板才能获得这些补传记录。
