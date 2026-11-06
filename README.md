# 兰州理工大学智能校园助手（SmartCampusSystem）

面向在校学生的校园事务助手：**规章制度问答**（RAG）+ **实验室 / 自习室座位预约**（两阶段确认的写操作）。
后端是 FastAPI，Agent 编排用 LangGraph，向量库是 Milvus，业务数据在 MySQL；
前端在 `frontend/`（Vue 3 + TypeScript + Element Plus）。

- 全部接口文档（自动生成）：`http://127.0.0.1:8000/docs`
- 前端说明：`frontend/README.md`（页面、路由、接口对照、两阶段确认）
- 设计文档：`data/RAG 项目六层总图.md`、`data/agent项目结构组成.md`
- 生产必需档的补齐记录：`data/生产必需档补齐需求.md`

---

## 一、快速开始（四步）

### 0. 前置条件

| 依赖 | 说明 |
|---|---|
| Python **3.14+** | 仓库自带 `.venv/`，也可以自建 |
| MySQL **8.x** | 业务库名默认 `smart_campus_system` |
| Milvus **2.x**（可选） | 存知识库向量 + 会话记忆。**不启动也能跑**，只是检索和记忆降级 |
| 本地模型权重（约 4GB） | `models/BAAI/bge-m3`、`models/BAAI/bge-reranker-v2-m3`（不入库，需自行下载） |

> Milvus 未启动时：`/health` 会如实报 `degraded`；问答仍可用（只是查不到校规条款），
> 会话历史返回空列表。想省事可以先不装它。

### 1. 安装依赖

项目用 [uv](https://docs.astral.sh/uv/) 管依赖（仓库里有 `uv.lock`，且 `.venv` 里没有 pip）：

```powershell
# 运行时依赖
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
# 开发/测试额外依赖（pytest、alembic）
uv pip install --python .\.venv\Scripts\python.exe -r requirements-dev.txt
```

没有 uv 的话，先给虚拟环境装回 pip 再用 pip 装：

```powershell
.\.venv\Scripts\python.exe -m ensurepip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### 2. 配置 `.env`

```powershell
Copy-Item .env.example .env     # 然后把下面这些填成真实值
```

最少要填 3 个：

```bash
DATABASE_URL=mysql+pymysql://root:root@127.0.0.1:3306/smart_campus_system?charset=utf8mb4
JWT_SECRET_KEY=<至少 32 位的随机串>
DEEPSEEK_API_KEY=<你的 key>
```

其余（预算、重试、降级、记忆、可观测性）都有默认值，见 `.env.example` 的注释。

### 3. 建表 + 灌测试数据

```powershell
.\.venv\Scripts\python.exe create_table.py                     # 建表（幂等）
.\.venv\Scripts\python.exe scripts\seed_test_data.py           # 5 个账号 + 预约 + 用量
```

账号（**仅本地测试**）：`admin/Admin@123456`、`20210001/Student@123456`（主测试账号）、
`20210002`、`20210003`（禁用，测 403）、`probe01`（冒烟专用）。详见 [scripts/README.md](scripts/README.md)。

### 4. 起服务

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/docs>，或先跑一遍冒烟确认环境没问题：

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test_api.py --skip-chat
```

启动过程有四步（`app/main.py` 的 lifespan），**全部 fail-open**：数据库没起、Milvus 连不上、
模型没配好，服务照样能启动，具体接口按需返回 503 / 降级，而不是整个进程起不来。

### 5. 起前端（可选，但要看到完整界面就这一步）

```powershell
cd frontend
npm install            # 首次；node >= 22.18 或 >= 24.12
npm run dev            # http://localhost:5173
```

前端默认走 vite 代理（`/api`、`/static` → `127.0.0.1:8000`），不用改后端 CORS。
登录页内置了测试账号快速回填：`admin / Admin@123456`、`20210001 / Student@123456`。

生产构建与无依赖预览（构建产物 + `/api` 反向代理）：

```powershell
npm run build
python scripts\serve_dist.py --port 5173
```

详见 [frontend/README.md](frontend/README.md)。

### 6. 或者：Docker 一键起（可选）

不想在本机装 MySQL + Milvus，可以直接拉起整套环境（app + MySQL + etcd + MinIO + Milvus）：

```powershell
docker compose up -d --build                                   # 首次会拉镜像，约 1.5GB
docker compose exec app python scripts/seed_test_data.py        # 灌测试数据
docker compose logs -f app                                      # 看日志
docker compose down                                             # 停（数据在命名卷里）
```

- `.env` 仍然从宿主机读，只有 `DATABASE_URL` / `MILVUS_URI` 被覆盖成容器网络地址；
- `models/` 以只读方式挂进容器（4GB 权重不进镜像）；
- 镜像里 torch 默认装 **CPU 版**（`--build-arg TORCH_INDEX_URL=...` 可换成 CUDA 版）。

---

## 二、目录结构

```
app/
  api/             HTTP 路由（auth / chat / lab / room / reservation / user / admin / admin_reservation / health）
  common/          统一信封、异常处理器、滑动窗口限流
  config.py        pydantic-settings 读取 .env
  database.py      engine / SessionLocal / Base / init_db
  dependencies/    鉴权、agent、限流三类 FastAPI 依赖
  harness/         Agent 编排：模型、中间件（闸门）、决策层、预约子图、写操作确认、预算、失败处理
  memory/          短期记忆（Milvus 会话历史）+ 身份绑定
  observability/   日志落盘、回合汇总/span、按 trace 回放
  rag/             知识库六层：数据 → 切分 → 存储 → 检索 → 评估
  schemas/         通用 ORM 表（users / agent_usage）、HTTP 模型、分页信封、前端组合视图
  services/        业务层：user / laboratory / library（表、模型、服务、异常）
  tool/            Agent 工具：知识库检索、预约（查询 + 写操作）
  utils/           密码、JWT、trace、安全护栏（注入/密钥/PII）
frontend/          前端（Vue 3 + TS + Element Plus），详见 frontend/README.md
  src/api/         一个后端文件对应一个 api 模块
  src/views/       登录 / 首页 / 实验室 / 自习室 / 预约 / 用户 / 个人中心
  src/components/  智能助手对话（含确认卡片）、实验室 / 自习室表单弹窗
  scripts/         无依赖静态预览（serve_dist.py，含 /api 代理）
scripts/           测试数据 + 接口冒烟（见 scripts/README.md）
tests/             业务规则单测（pytest）
docs/
  management_rules/ 知识库源文件（RAG 读这里，**唯一语料来源**）
data/              设计文档 + 检索 / Agent 评估集
  生产必需档补齐需求.md
get_data/          爬虫原始输出（历史遗留，代码不读它）
models/            本地模型权重（不入库）
logs/              运行日志 JSONL（不入库）
```

---

## 三、接口一览（54 个路由）

> 前 19 个是给「助手 / 管理端脚本」用的；后面几组是前端页面需要的读写接口
> （列表、详情、排期、座位视图、我的预约、用户管理）。前端对照表见
> [frontend/README.md](frontend/README.md) 第四节。

**系统 / 认证 / 助手（12 个）**

| 分组 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 系统 | GET | `/` | 服务信息 |
| 系统 | GET | `/health` | 健康检查（真实探测 DB / Milvus / 知识库 / 模型配置） |
| 认证 | POST | `/api/auth/register` | 学生自助注册 |
| 认证 | POST | `/api/auth/login` | 登录拿 JWT |
| 认证 | GET | `/api/auth/me` | 当前用户 |
| 认证 | PUT | `/api/auth/profile` | 改自己的资料 |
| 认证 | PUT | `/api/auth/password` | 改自己的密码 |
| 助手 | POST | `/api/chat` | 发消息（可能返回 `need_confirm`） |
| 助手 | POST | `/api/chat/confirm` | 确认 / 拒绝待提交的写操作 |
| 助手 | GET | `/api/chat/history` | 会话历史 |
| 助手 | GET | `/api/chat/threads` | 我的会话列表 |
| 助手 | DELETE | `/api/chat/thread` | 清空会话 |

**实验室 / 设备（11 个，读=登录即可，写=仅管理员）**

| 分组 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 实验室 | GET | `/api/labs` | 列表（关键词 / 人数 / 状态 / 分页） |
| 实验室 | GET | `/api/labs/{id}` | 详情（关闭的实验室也能看） |
| 实验室 | GET | `/api/labs/{id}/schedule` | 某天排期 + 按整点切分的可预约时段 |
| 实验室 | GET | `/api/labs/{id}/equipments` | 该实验室的设备清单 |
| 实验室 | POST / PUT / DELETE | `/api/labs`、`/api/labs/{id}` | 新增 / 更新 / 删除（管理员） |
| 实验室 | POST | `/api/labs/{id}/equipments` | 批量新增设备（管理员） |
| 设备 | GET | `/api/equipments` | 设备分页（可按实验室 / 关键词 / 状态） |
| 设备 | PUT / DELETE | `/api/equipments/{id}` | 更新 / 删除设备（管理员） |

**自习室 / 座位（9 个）**

| 分组 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 自习室 | GET | `/api/rooms` | 列表（关键词 / 容量 / 状态 / 分页） |
| 自习室 | GET | `/api/rooms/{id}` | 详情 |
| 自习室 | GET | `/api/rooms/{id}/seats` | 座位视图，可带 `start_time/end_time` 查空闲 |
| 自习室 | POST / PUT / DELETE | `/api/rooms`、`/api/rooms/{id}` | 新增 / 更新 / 删除（管理员） |
| 自习室 | POST | `/api/rooms/{id}/seats` | 批量新增座位（管理员，不能超过容量） |
| 座位 | PUT / DELETE | `/api/seats/{id}` | 更新 / 删除座位（管理员） |

**预约（9 + 6 个）**

| 分组 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 我的预约 | GET | `/api/reservations/mine/summary` | 两类业务按状态统计（学生首页） |
| 我的预约 | GET | `/api/reservations/lab/mine`、`/library/mine` | 我的实验室 / 座位预约（多状态筛选） |
| 我的预约 | POST | `/api/reservations/lab`、`/library` | 提交预约（身份取 JWT，body 不含 user_id） |
| 我的预约 | GET | `/api/reservations/lab/{id}`、`/library/{id}` | 详情（非本人 403） |
| 我的预约 | PUT | `/api/reservations/lab/{id}/cancel`、`/library/{id}/cancel` | 取消（学生只能取消自己的） |
| 管理员 | GET | `/api/admin/reservations/lab`、`/library` | 全部预约（可按状态 / 关键词筛） |
| 管理员 | PUT | `/api/admin/reservations/lab/{id}/review`、`/library/{id}/review` | 审核（通过 / 驳回） |
| 管理员 | PUT | `/api/admin/reservations/lab/{id}/cancel`、`/library/{id}/cancel` | 代取消 |

**用户管理（7 个，全部仅管理员）**

| 分组 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 用户 | GET | `/api/users` | 分页列表（关键词 / 角色 / 状态） |
| 用户 | GET | `/api/users/{id}` | 详情 |
| 用户 | POST | `/api/users` | 新增（可指定角色 / 状态） |
| 用户 | PUT | `/api/users/{id}` | 更新（不能把自己降级 / 禁用） |
| 用户 | PUT | `/api/users/{id}/password` | 重置密码（不需要原密码） |
| 用户 | DELETE | `/api/users/{id}` | 删除（有预约记录时 409） |
| 用量 | GET | `/api/admin/usage/{user_id}` | 某个学生今天的 token / 调用用量 |

约定：所有响应都是 `{code, message, data}` 信封，`code` 与 HTTP 状态码一致；
分页统一 `{total, page, page_size, data}`。登录后带 `Authorization: Bearer <token>`。

**写操作是两阶段的**：`POST /api/chat` 只返回 `need_confirm` + `pending_actions`，
学生调 `POST /api/chat/confirm` 后服务端才真正落库；重复确认是幂等的。

`/api/reservations/*` 是**表单直传**入口（学生点「提交预约」就是确认），
身份同样只认 JWT；两条入口最终都落到同一批 service 函数，业务规则只有一份。

---

## 四、测试与自检

| 命令 | 覆盖什么 |
|---|---|
| `python scripts\smoke_test_api.py` | 43 条 HTTP 用例：19 个路由 + 主要错误分支（401/403/404/409/422） |
| `python scripts\smoke_test_api.py --skip-chat` | 同上，跳过 `/api/chat`（不花大模型的钱） |
| `python scripts\smoke_test_web_api.py` | 58 条断言：**前端用到的 26 个接口**（列表 / 详情 / 排期 / 座位视图 / 提交 / 越权 / 审核 / 取消 / 用户增删改），跑完自动清理临时数据 |
| `python -m pytest` | 89 条单测：业务规则 + Agent 异常/降级、重试、回合汇总、replay、中间件与预算计量 |
| `cd frontend; npm run type-check` | 前端 TypeScript 类型检查（vue-tsc） |
| `cd frontend; npm run build` | 前端构建（类型检查 + vite build 到 `dist/`） |
| `python scripts\eval_agent.py` | Agent 端到端评测 20 条（5 类），规则打分 + 基线对比 |
| `python scripts\eval_agent.py --retry-failed 1 --baseline` | 失败的再试一次，并把结果存成基线 |
| `python scripts\eval_agent.py --diff` | 与基线对比，有回归退出码 1（适合接 CI） |
| `python -m app.utils.guard` | 安全护栏自检（PII 掩码 / 注入 / 资料边界） |
| `python -m app.harness.resilience` | 失败处理自检（重试白名单、超时不重试、熔断记账） |
| `python -m app.harness.agent --self-check` | 主模型降级自检（5 条路径） |
| `python -m app.memory.short_term` | 短期记忆自检（需要 Milvus） |
| `python -m app.harness.confirm` | 写操作确认自检（拦、确认、幂等） |

- **pytest** 用的是同一个 MySQL 里的独立库（默认 `smart_campus_system_test`，可用 `TEST_DB_NAME` 改），
  不会碰你的开发数据；会话开始时自动建库建表，结束时拆表，每个用例跑完清空所有表。
- **eval_agent** 的 `rule_qa` 类需要 Milvus + 知识库索引；Milvus 没起时这 8 条会判失败
  （模型会如实说"暂时查不到"，这是**正确行为**，不是 agent 坏了）。基线文件里记了
  `environment`，`--diff` 会提示两次结果是不是同一环境。
- 评测会**绕开预算层**（一轮 20 条约 30 万 token，正好等于默认日配额），也不会写 `agent_usage`。

---

## 五、可观测性

- **日志**：`logs/app-YYYY-MM-DD.jsonl`，每行一个 JSON，自动带 `trace_id / user_id / thread_id`。
- **回合汇总**：一轮一条 `span=turn`，含决策结论、模型/工具次数、重试、tokens、耗时、是否降级。
- **按 trace 回放**：

  ```powershell
  .\.venv\Scripts\python.exe -m app.observability.replay <X-Trace-Id>
  ```

  输出「决策 → 每次模型调用（耗时/token）→ 每次工具调用（耗时/重试）→ 回合汇总」，
  只读本地日志，断网可用。`HARNESS_SPAN_LOG=0` 可只保留汇总。

---

## 六、常见问题

**Q：`/health` 返回 `degraded`，但服务能用？**
看具体字段：`memory/knowledge: down` 说明 Milvus 没起（问答能用、查不了校规）；`database: down`
才是真故障。`model: missing` 说明 `.env` 里模型 key 没配。

**Q：主模型挂了会不会整站 500？**
不会。`.env` 里配了 `FALLBACK_MODEL` 的话会自动切备用模型（响应里 `degraded: true`）；
两者都不可用则返回 200 + 一句友好话术。没配备用模型时保持原来的报错行为（503/500），
让运维看到真实故障。详见 [data/生产必需档补齐需求.md](data/生产必需档补齐需求.md) 需求 1。

**Q：日志只有控制台，没有 `logs/*.jsonl`？**
目录不可写时会自动降级为"仅控制台"并打一条 warning（`日志落盘不可用`）。检查 `logs/` 的写权限。

**Q：知识库检索没结果？**
先确认 Milvus 起来了、且 `rag_child_chunks` 这个 collection 已建。重建索引见
`app/rag/groundwork.py`（会读 `docs/management_rules/` 下的 Markdown）。

**Q：`/api/chat` 的回答不含条款？**
看响应里的 `decision.stage`：`offtopic_short_circuit` / `intent_denied` 表示决策层判定本轮不查库。
调试时可以用 `force_rag: true` 强制放行。

**Q：能不能多线程 / 多副本部署？**
见 `app/common/limiter.py` 与 `app/harness/budget.py` 的说明：限流和预算的"进程内"部分是单机假设，
上多副本需要换成 Redis 之类的共享存储；会话记忆（Milvus）和业务数据（MySQL）本身是共享的。

---

## 七、数据库迁移（alembic）

启动时的 `create_all()` 只建**不存在**的表，**不会改已存在的表结构** ——
线上加列/改索引必须走 alembic（首次接入已经用 `stamp` 标记过，不用重复执行）。

```powershell
.\.venv\Scripts\python.exe -m alembic current          # 当前版本（应是 head）
.\.venv\Scripts\python.exe -m alembic upgrade head     # 升到最新
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "add xxx"   # 生成新迁移
.\.venv\Scripts\python.exe -m alembic downgrade -1     # 回退一步
```

- 连接串由 `alembic/env.py` 从 `settings.DATABASE_URL` 读，可用 `ALEMBIC_DATABASE_URL` 覆盖；
- `env.py` 里那 4 个 table 模块的 import 不是摆设：少 import 一个，autogenerate 会以为
  那张表该删，生成一条 `drop_table`。**新增业务表后要在这里补一行**。

---

## 八、开发约定（改代码前先看这几条）

1. **分层**：`api/` 只做参数校验 + 调业务 + 包信封；业务规则在 `services/`；Agent 编排在 `harness/`。
2. **同步阻塞调用**（模型 / 数据库 / 向量库）在 async 路由里必须 `run_in_threadpool`，别堵事件循环。
3. **新增表**要在 `app/database.py` 的 `init_db()` 里 import（否则 `create_all()` 建不出来），
   并在 `alembic/env.py` 里补一行（否则 autogenerate 会生成 drop）。
4. **新增工具**要同时加进 `app/tool/__init__.py` 的 `user_tool`，否则模型看不到。
5. **新增路由**记得在 `app/api/__init__.py` 里 `include_router`，并把用例加进
   `scripts/smoke_test_api.py`（业务 / 管理接口）或 `scripts/smoke_test_web_api.py`（前端接口）。
6. **环境变量**统一 `HARNESS_*` / 业务前缀，并同步写进 `.env.example`。
7. 改完至少跑：`python -m pytest` + `python scripts\smoke_test_api.py --skip-chat`；
   动到提示词/决策层就再跑一次 `python scripts\eval_agent.py --diff`。

### 前端（`frontend/`）的约定

1. **类型跟着后端走**：后端 Pydantic 模型改了，同步改 `src/schemas/*.ts`；
   `src/api/*.ts` 只做「路径 + 参数」，不要在组件里拼 URL。
2. **状态文案统一用 `utils/dict.ts`**，不要在各个页面里写 `status === 0 ? '待审核' : ...`。
3. **时间格式**：`formatTime` 只用于 `08:00:00` 这种纯时间；完整时间串（`2026-10-29T09:00:00`）
   用 `timeOfDay` / `formatDateTime` / `formatReserveRange`。
4. **权限只是显示**：按钮显隐可以按角色判断，但真正的权限永远以后端 `require_admin` 为准。
5. **写操作提示**：提交预约后是「待审核」，不要在 UI 上写「预约成功」；
   助手侧的写操作必须渲染成确认卡片，不能自动提交。
6. 改完跑 `npm run type-check`（或 `npm run build`），提交前顺手看一眼浏览器控制台。
