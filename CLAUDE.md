# LifeTrack - 个人时间追踪与 AI 复盘助手

## 项目概要

手机优先的 PWA 网页应用，用于记录日常活动（几点到几点做了什么），通过 DeepSeek API 分析行为模式并给出迭代优化建议。

GitHub: https://github.com/3057hub/lifetrack

## 技术栈

- **后端**: Python 3.10+, FastAPI, SQLAlchemy (SQLite), httpx
- **前端**: 单个 HTML 文件 (`static/index.html`), Alpine.js CDN, 纯 CSS, PWA
- **AI**: DeepSeek API (`deepseek-chat`), `response_format: json_object`
- **部署**: 阿里云 2核2G, Nginx 反代 `/api` → uvicorn:8000

## 项目结构

```
main.py              # 所有后端代码（模型 + 路由 + DeepSeek 集成）
static/index.html    # 单文件 Alpine.js 前端（4 个视图）
static/manifest.json # PWA manifest
static/sw.js         # Service Worker
static/icons/        # SVG 占位图标
lifetrack.conf       # Nginx 配置
requirements.txt     # fastapi, uvicorn, sqlalchemy, httpx
```

## 数据库（SQLite，自动创建）

- `activities`: id, start_time, end_time, description, tags, created_at
- `reports`: id, period_start, period_end, summary_text, memory_abstract, user_feedback
- `goals`: id, content, is_active, created_at

## API 端点

- `POST /api/auth/verify` — 密码验证
- `POST/GET /api/activities` `GET /api/activities/active` `PUT/DELETE /api/activities/{id}` — 活动 CRUD
- `POST /api/reports/generate` `GET /api/reports/latest` `GET /api/reports` `PUT /api/reports/{id}/feedback` — 报告
- `GET/POST /api/goals` `PUT /api/goals/{id}/activate|deactivate` — 目标

## 前端视图（Alpine.js 切换）

1. **记录** — 计时器 + 手动补录
2. **历史** — 按日期分组时间轴，编辑/删除
3. **报告** — 生成 AI 分析，按章节分卡片展示，反馈按钮
4. **目标** — 添加/停用/启用目标

## 开发启动

```bash
cd d:/AI/AI-Coach
set DEEPSEEK_API_KEY=你的密钥
python -m uvicorn main:app --host 0.0.0.0 --port 8002
```

## 当前状态

阶段 1 和阶段 2 均已完成，所有功能可用。项目已推送到 GitHub。
代理端口: 65532（本地）
