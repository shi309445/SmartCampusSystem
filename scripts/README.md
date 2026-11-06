# 测试数据与接口冒烟

这几个脚本解决「刚把项目跑起来，所有接口都没数据、没法测」和「改完不知道有没有变差」：

| 脚本 | 作用 |
|------|------|
| `seed_test_data.py` | 把测试数据写进 MySQL（+ Milvus 会话历史），可反复执行 |
| `smoke_test_api.py` | 把 19 个 HTTP 路由逐条打一遍（43 条用例），输出通过 / 失败清单 |
| `smoke_test_web_api.py` | **前端用到的接口**逐条打一遍（58 条断言：实验室 / 自习室 / 座位 / 我的预约 / 用户管理） |
| `eval_agent.py` | Agent 端到端评测 20 条（规则打分 + 基线对比），`rule_qa` 类需要 Milvus |

> 业务规则的单元测试在 `tests/`（`python -m pytest`），不在这个目录。
> 前端本身在 `frontend/`，说明见 `frontend/README.md`。

---

## 一、三步跑通

```powershell
# 1) 起服务（另开一个窗口）
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000

# 2) 写测试数据
.\.venv\Scripts\python.exe scripts\seed_test_data.py

# 3) 逐条验证所有路由
.\.venv\Scripts\python.exe scripts\smoke_test_api.py
#    前端接口（实验室 / 自习室 / 我的预约 / 用户管理）单独一套
.\.venv\Scripts\python.exe scripts\smoke_test_web_api.py
```

第 3 步会在终端打出 43 条用例的 OK / FAIL，全部通过时退出码为 0，可以直接接 CI。
不想花钱调大模型就加 `--skip-chat`。

`smoke_test_web_api.py` 是给前端 UI 用的那组接口（本轮新增的 26 个路由）准备的：
58 条断言覆盖列表 / 详情 / 排期 / 座位视图 / 提交预约 / 越权 / 审核 / 取消 / 用户增删改。
它跑完会**删掉自己创建的临时数据**（预约、实验室、自习室、用户），不污染种子数据；
想看它造的那条预约就加 `--keep`。

> 控制台中文乱码时先执行 `chcp 65001`，或设置 `$env:PYTHONIOENCODING='utf-8'`。

---

## 二、种子账号

密码都是明文写死在脚本顶部的常量里，**只用于本地/测试环境**。

| 账号 | 密码 | 角色 | 状态 | 用来测什么 |
|------|------|------|------|-----------|
| `admin` | `Admin@123456` | admin | 正常 | `/api/admin/usage/{user_id}` 有权限 |
| `20210001` | `Student@123456` | student | 正常 | **主测试账号**：预约、会话历史都挂在它身上 |
| `20210002` | `Student@123456` | student | 正常 | 第二个学生，制造「别人」的数据（越权 / 冲突场景） |
| `20210003` | `Student@123456` | student | **禁用** | 登录返回 403 的分支 |
| `probe01` | `Probe@123456` | student | 正常 | 冒烟脚本改资料 / 改密码专用，不碰上面的人 |

---

## 三、写进去的是哪些数据

`seed_test_data.py --verify` 可以随时查看行数。

| 表 | 行数 | 内容 | 服务哪个路由 |
|----|------|------|-------------|
| `users` | 5 | 上表 5 个账号 | 所有需要登录的接口、`/api/auth/*` |
| `agent_usage` | 4 | 今天 + 昨天的 token / 调用次数 | `GET /api/admin/usage/{user_id}` |
| `labs` | 5 | 4 间开放 + 1 间关闭的实验室 | `/api/chat` 里 `search_labs`、`list_lab_slots` |
| `lab_equipments` | 10 | 每间实验室 1~3 台设备 | 同上 |
| `lab_reservations` | 6 | **0/1/2/3 四种状态齐全**，都在未来 1~4 天 | `list_my_reservations`、`list_lab_slots`、`cancel_my_reservation` |
| `library_study_rooms` | 4 | 3 间开放 + 1 间关闭 | `search_study_rooms` |
| `library_seats` | 22 | 每间 2~8 个座位（含「靠窗 / 静音区 / 有电源」差异） | `find_available_seats` |
| `library_reservations` | 5 | **0/1/2/3 四种状态齐全**，未来 1~3 天 | 座位预约 / 取消 |
| Milvus `agent_short_term_memory` | 11 条消息 | 3 个会话（见下） | `/api/chat/history`、`/api/chat/threads`、`/api/chat/confirm` |

预约单号是固定的：实验室 `RESSEED0001`~`RESSEED0006`，自习室 `LIBSEED0001`~`LIBSEED0005`。
`--reset` 靠这个前缀精确清理，不会误删你自己的数据。

### Milvus 里的 3 个会话

| thread_id | 内容 | 用途 |
|-----------|------|------|
| `u{20210001的id}` | 6 条消息：奖学金问答 + 一次检索工具调用 | `/api/chat/history` 有内容可看 |
| `u{id}:demo-pending` | 一条**待确认**的实验室预约（`tool_call_id = call_seed_pending_0001`） | `/api/chat/confirm` 直接拿它试「同意 / 拒绝」 |
| `u{id}:demo-deletable` | 2 条消息 | `DELETE /api/chat/thread` 删的是它，删完重跑种子就回来 |

> **Milvus 没起来时**这一部分会自动跳过并给出提示，MySQL 部分照常写入。
> 此时 `/api/chat/history`、`/api/chat/threads` 仍然能调通，只是返回空列表。
> 起来之后补写：`.\.venv\Scripts\python.exe scripts\seed_test_data.py --memory-only`

---

## 四、路由覆盖矩阵

| # | 方法 | 路径 | 冒烟脚本覆盖的分支 |
|---|------|------|-------------------|
| 1 | GET | `/` | 服务信息 |
| 2 | GET | `/health` | 数据库连通 / 降级字段 |
| 3 | POST | `/api/auth/register` | 注册成功、账号重复 409、参数非法 422 |
| 4 | POST | `/api/auth/login` | 成功、密码错 401、账号禁用 403 |
| 5 | GET | `/api/auth/me` | 成功、无令牌 401、假令牌 401 |
| 6 | PUT | `/api/auth/profile` | 改姓名/邮箱、空姓名 422 |
| 7 | PUT | `/api/auth/password` | 原密码错 400、改密码、改回原密码 |
| 8 | POST | `/api/chat` | 一轮问答、空消息 422 |
| 9 | POST | `/api/chat/confirm` | 找不到 404、越权 403、兜底回放拒绝 200 |
| 10 | GET | `/api/chat/history` | 有数据、空会话、`limit` 越界 422 |
| 11 | GET | `/api/chat/threads` | 会话列表 |
| 12 | DELETE | `/api/chat/thread` | 清空会话（删 `demo-deletable`） |
| 13 | GET | `/api/admin/usage/{user_id}` | 管理员 200、学生 403、未登录 401、参数错 422 |

一次完整运行 = **43 条用例**（19 个路由 + 错误分支 + 收尾的密码/资料往返；下表只展开 13 条核心路由，另 6 条管理员预约路由由同一脚本覆盖）。

---

## 五、常用命令

```powershell
# 幂等写入（推荐日常用）
.\.venv\Scripts\python.exe scripts\seed_test_data.py

# 先清掉种子数据再写一遍（id 会变，manifest 会更新）
.\.venv\Scripts\python.exe scripts\seed_test_data.py --reset

# 只看各表行数
.\.venv\Scripts\python.exe scripts\seed_test_data.py --verify

# 只删掉冒烟脚本注册的 probe_reg_* 临时账号
.\.venv\Scripts\python.exe scripts\seed_test_data.py --cleanup-probe

# 只补写 Milvus 会话历史（Milvus 后启动时用）
.\.venv\Scripts\python.exe scripts\seed_test_data.py --memory-only

# 清空全部业务表（危险；要加 --yes）
.\.venv\Scripts\python.exe scripts\seed_test_data.py --purge --yes

# 冒烟：跳过 /api/chat，不花钱
.\.venv\Scripts\python.exe scripts\smoke_test_api.py --skip-chat

# 冒烟：前端接口（实验室 / 自习室 / 座位 / 我的预约 / 用户管理）
.\.venv\Scripts\python.exe scripts\smoke_test_web_api.py

# 冒烟：前端接口，保留它创建的预约方便到页面上手工核对
.\.venv\Scripts\python.exe scripts\smoke_test_web_api.py --keep

# 冒烟：打别的地址
.\.venv\Scripts\python.exe scripts\smoke_test_api.py --base-url http://127.0.0.1:9000
```

`seed_manifest.json` 是脚本之间交接 id 和密码的凭据（冒烟脚本读它），
不想要就随种子脚本一起重新生成，**不要手动改**。

---

## 六、几个必须知道的点

1. **写完数据要重启服务吗？**
   不用。数据在数据库里，服务每次都实时读。

2. **`/api/chat` 会花真钱。**
   它走 DeepSeek，一次问答消耗几百到几千 token。赶时间或没网就 `--skip-chat`。

3. **`agent_usage` 是按天记账、且当天内累加的。**
   只要跑过 `/api/chat`，**当天**的 `tokens` 就会比种子值大 —— 这不是 bug。
   想恢复成种子值，重跑一次 `seed_test_data.py`（它是覆盖写，不是累加）。
   注意 `usage_date` 是写入当天的日期：**隔天再测，昨天的种子行不算今天的用量**，
   `/api/admin/usage` 会显示 0（冒烟脚本已按「种子是不是今天生成的」自动放宽断言）。

4. **预约时间和用量日期都是相对「今天」算的。**
   种子数据每次写入都按 `date.today()` 重新计算「未来 1~4 天」和「今天的用量」，
   所以放着不动也不会变成「过去的时间」。隔天再测，重跑一次种子即可。

5. **冒烟脚本会留下痕迹（都是可清理的）。**
   - 注册用例会建一个 `probe_reg_<时间戳>` 账号 → `--cleanup-probe` 或 `--reset` 清掉；
   - `probe01` 的姓名/密码会改一下再改回去（万一中断了，重跑种子脚本即可复位）；
   - `demo-deletable` 会话会被删掉 → 重跑种子补回。

6. **手动测「确认预约成功」这条路径。**
   它是唯一没法自动化的（会真的写一条预约单），步骤：

   ```powershell
   # ① 学生登录拿 token
   $login = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/auth/login `
            -ContentType 'application/json' -Body '{"username":"20210001","password":"Student@123456"}'
   $token = $login.data.token

   # ② 读待确认会话，拿到 tool_call_id（种子数据里是 call_seed_pending_0001）
   $history = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/chat/history?session_id=demo-pending' `
              -Headers @{ Authorization = "Bearer $token" }

   # ③ 确认提交（approve=true）
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/chat/confirm `
     -Headers @{ Authorization = "Bearer $token" } -ContentType 'application/json' `
     -Body '{"thread_id":"u<学生id>:demo-pending","tool_call_id":"call_seed_pending_0001","approve":true}'

   # ④ 想重来：重跑种子脚本，待确认动作会被恢复
   .\.venv\Scripts\python.exe scripts\seed_test_data.py
   ```

   注意第 ③ 步之后再跑同样的请求会命中**幂等**分支，返回第一次的结果，不会重复下单。

7. **这些账号只适合本地/测试库。**
   `seed_test_data.py` 会把上述 5 个账号的密码**重置成表里的明文**，
   所以不要拿它对着有真实用户的库跑（`--reset` / `--purge` 更危险，看清楚再用）。

8. **排查「这一轮为什么慢」用 `replay`（不属于测试数据，但常一起用）。**
   `/api/chat` 的响应头里有 `X-Trace-Id`，拿它回放整轮时间线：

   ```powershell
   .\.venv\Scripts\python.exe -m app.observability.replay <X-Trace-Id 的值>
   ```

   输出是「决策 → 每次模型调用（耗时/token）→ 每次工具调用（耗时/重试）→ 回合汇总」，
   只读 `logs/app-*.jsonl`，不重跑模型、不碰数据库。
