# any-auto-register 代码分析

> 本文档由代码静态分析生成,面向**新接手项目的开发者**。所有结论均出自当前 `main` 分支的实际代码,引用格式为 `path/to/file.py:行号`。
>
> 分析基线:提交 `dfc697c`(2026-09-02),Python + TypeScript 约 58,500 行 / 288 个受版本控制文件。
>
> ⚠️ 本文档只描述代码**事实上实现了什么**,不构成对其用途的推荐。项目涉及自动化注册、验证码绕过、指纹伪装等技术,合规责任在使用者。

## 目录

- [一、项目总览](#一项目总览)
- [二、API 层与鉴权配置](#二api-层与鉴权配置)
- [三、任务编排与调度](#三任务编排与调度)
- [四、ChatGPT 注册状态机](#四chatgpt-注册状态机)
- [五、ChatGPT 反检测层与插件装配](#五chatgpt-反检测层与插件装配)
- [六、ChatGPT 手机接码](#六chatgpt-手机接码)
- [七、ChatGPT TOTP 2FA 绑定](#七chatgpt-totp-2fa-绑定)
- [八、ChatGPT 账号后置能力](#八chatgpt-账号后置能力)
- [九、邮箱池子系统](#九邮箱池子系统)
- [十、iCloud 登录与凭据存储](#十icloud-登录与凭据存储)
- [十一、iCloud 别名生成与收件箱](#十一icloud-别名生成与收件箱)
- [十二、外部集成服务层与部署](#十二外部集成服务层与部署)
- [十三、前端基础架构与注册任务页](#十三前端基础架构与注册任务页)
- [十四、前端账号页与导出](#十四前端账号页与导出)
- [十五、前端设置页、iCloud 页与付费页](#十五前端设置页icloud-页与付费页)

---

## 一、项目总览

### 1.1 这是什么

**any-auto-register** 是一套**多平台账号自动注册与管理系统**。它把"批量注册第三方平台账号"这件事拆成了可编排、可观测、可插拔的流水线:前端下发一个注册任务 → 后端从邮箱池/接码平台领取一个身份 → 平台插件按协议跑完注册链路 → 账号连同 Token 落库 → 再由一系列后置能力(状态探测、Token 补齐、2FA 绑定、外部系统同步、多格式导出)接管其生命周期。

| 维度 | 说明 |
| --- | --- |
| 项目定位 | 自托管的账号注册流水线 + Web 管理后台 |
| 当前在线平台 | ChatGPT、iCloud(Hide My Email),其余插件已下线 |
| 部署形态 | 本地 Conda / Docker 完整版 / Docker 无头服务器版 / Electron 桌面壳 |
| 许可与声明 | MIT,README 首行声明"仅供学习研究,禁止商业用途" |

> ⚠️ 本文档只描述代码**事实上实现了什么**,不代表对其用途的推荐。项目涉及自动化注册、验证码绕过、指纹伪装等技术,使用时的合规责任在使用者。

### 1.2 代码规模

统计自 `git ls-files`(不含 `node_modules`、构建产物):

| 项 | 数值 |
| --- | --- |
| 版本控制文件总数 | 288 |
| Python 文件 | 189 |
| TypeScript / TSX | 41 (18 `.ts` + 23 `.tsx`) |
| Python + TS 总行数 | ~58,500 |
| 测试文件 | 60 个 `tests/test_*.py` |
| 提交历史 | 350 次提交 |

**最大的 10 个源文件**(行数降序,这些是理解项目的重点,也是重构时的风险点):

| 行数 | 文件 | 承担的职责 |
| --- | --- | --- |
| 4596 | `core/base_mailbox.py` | 全部临时邮箱 provider 的实现集中在此单文件 |
| 3484 | `platforms/chatgpt/protocol/auth_flow.py` | OpenAI authorize 全链路状态机 |
| 1919 | `frontend/src/pages/Accounts.tsx` | 账号列表页(筛选/批量/详情/导出/同步) |
| 1648 | `frontend/src/pages/Settings.tsx` | 全局配置页(全部配置分区) |
| 1360 | `core/luckmail/user.py` | LuckMail 用户侧能力(登录/签到/领邮箱) |
| 1307 | `api/tasks.py` | 注册任务编排与控制的 HTTP 入口 |
| 1158 | `services/sms_service.py` | 手机接码平台抽象与租号收码时序 |
| 1139 | `services/turnstile_solver/api_solver.py` | 本地 Turnstile 求解服务(Quart) |
| 1104 | `tests/test_chatgpt_phone_register.py` | 手机注册链路的行为测试 |
| 953 | `platforms/chatgpt/protocol/phone_flow.py` | add-phone 分支流程 |

几个超大单文件(`base_mailbox.py`、`auth_flow.py`、`Accounts.tsx`、`Settings.tsx`)是当前架构上最明显的可维护性负担,详见各章末尾的问题小节。

### 1.3 技术栈

| 层级 | 选型 | 关键版本约束 |
| --- | --- | --- |
| Web 框架 | FastAPI + Uvicorn | `fastapi>=0.110`、`uvicorn>=0.29` |
| ORM / 存储 | SQLModel + SQLite | `sqlmodel>=0.0.16` |
| HTTP 客户端 | **curl_cffi**(TLS 指纹伪装)、httpx、requests | `curl-cffi>=0.16.2,<0.17` ← 上界锁死 |
| 浏览器自动化 | Playwright / Patchright / Camoufox | 仅 headless/headed 执行器与 Solver 用 |
| 本地验证码求解 | Quart(独立 ASGI 服务) | `quart>=0.19.4` |
| 加密 | cryptography(AES-256-GCM)、jwcrypto、cbor2 | 凭据落库与 Apple SRP 用 |
| HTML 解析 | selectolax | OTP 邮件正文提取 |
| 前端框架 | React 19 + TypeScript 5.9 + Vite 8 | `react@^19.2.4` |
| 前端 UI 库 | **Ant Design 5** + `@ant-design/v5-patch-for-react-19` | React 19 需打补丁 |
| 前端路由 | react-router-dom 7 | |
| 桌面壳 | Electron | 不自动拉起 Python 后端 |
| 非 Python 运行时 | **Node.js ≥18**(Sentinel PoW 必需) | 缺失会导致验证码静默丢失 |

`curl-cffi` 的版本上界 `<0.17` 是刻意锁定的——TLS 指纹的 `impersonate` 目标在小版本间会变,升级需重新验证 ChatGPT 链路。

### 1.4 目录职责与依赖方向

```text
any-auto-register/
├── main.py                 # 应用装配:lifespan、鉴权中间件、路由挂载、SPA 托管
├── api/         (17 文件)  # HTTP 路由层,103 个 endpoint
├── core/        (24 文件)  # 与平台无关的底层:DB、配置、邮箱池、代理、执行器、任务运行时
├── services/    (30 文件)  # 与平台相关的业务服务:接码、同步、导出、插件管理、Solver
├── platforms/   (46 文件)  # 平台插件:chatgpt/ 与 icloud/,各自封装注册协议
├── frontend/    (React SPA) # 构建产物输出到 ./static,由 FastAPI 直接托管
├── electron/               # 桌面壳
├── tests/       (60 文件)  # pytest
└── docs/                   # 部署与设计文档
```

**实测的包间依赖方向**(`grep` 统计 import):

```mermaid
flowchart TD
    FE["frontend/ (SPA)"] -->|"HTTP /api/*"| API["api/ — 路由层"]
    API -->|17| SVC["services/ — 业务服务"]
    API -->|16| CORE["core/ — 底层能力"]
    API -->|3| PLAT["platforms/ — 平台插件"]
    SVC -->|11| CORE
    SVC <-->|"7 ⇄ 3 双向"| PLAT
    PLAT -->|16| CORE
    CORE -.->|"延迟导入(少量)"| PLAT
    CORE -.->|"延迟导入(少量)"| SVC
```

三条值得注意的结论:

1. **`core/` 基本是干净的底层**,但不是完全零反向依赖。存在 8 处对上层的**函数内延迟导入**,集中在两个地方:
   - `core/registry.py:34` —— 插件扫描必须 `import platforms`,这是注册表模式的固有耦合,合理。
   - `core/base_mailbox.py:3640/3686/3925/3945` 与 `core/scheduler.py:58/125` —— 前者为取 `platforms.chatgpt.constants` 的 IMAP/OAuth 常量和 `services.mail_imports` 的号池筛选,后者为调度 CPA 维护任务。这几处是真实的**分层泄漏**:底层邮箱实现反过来知道了 ChatGPT 平台的细节。用延迟导入规避了循环 import 报错,但耦合本身还在。

2. **`services/` 与 `platforms/` 是双向依赖**。`platforms/chatgpt/plugin.py` 调 `services` 的接码、同步、2FA、付费;`services/chatgpt_*.py` 又反过来调 `platforms.chatgpt.protocol` 的 AuthFlow。两者实际上是同一业务域被切成了两个包,边界并不清晰——判断一段 ChatGPT 逻辑该放哪,当前没有可依据的规则。

3. **`platforms/` 内部 31 次自引用**是正常的,插件内部模块化程度高。

### 1.5 启动流程

`main.py` 的 `lifespan` 定义了完整的启动顺序(`main.py:62`):

```mermaid
flowchart LR
    A["进程启动"] --> B["_print_runtime_info()<br/>校验 conda 环境"]
    B --> C["init_db()<br/>建表 + 手写迁移"]
    C --> D["load_all()<br/>扫描 platforms/ 注册插件"]
    D --> E["scheduler.start()<br/>启动周期任务"]
    E --> F["solver_manager.start_async()<br/>异步拉起 Turnstile Solver"]
    F --> G["服务就绪"]
    G -.->|"关闭"| H["scheduler.stop()<br/>solver.stop()"]
```

| 步骤 | 位置 | 作用与注意点 |
| --- | --- | --- |
| 运行时自检 | `main.py:44` | 检测当前 conda 环境名是否为 `any-auto-register`(可用 `APP_CONDA_ENV` 覆盖;值为 `docker` 时跳过)。不匹配只打印 `[WARN]` **不阻断启动**——这正是 README 里"Solver 起不来 / `ModuleNotFoundError: quart`"这类问题的根因,警告很容易被忽略 |
| 建表与迁移 | `core/db.py:247` | `SQLModel.metadata.create_all` + 两个**手写迁移函数**,详见 01 章 |
| 插件加载 | `core/registry.py:32` | `pkgutil.iter_modules` 扫描 `platforms/`,只导入白名单内的包;`ModuleNotFoundError` 被**静默 `pass`**,插件导入失败不会报错,只会表现为"平台列表里少了一个" |
| 调度器 | `core/scheduler.py` | 周期任务 |
| Solver | `services/solver_manager.py` | 后台线程拉起本地 Turnstile 求解器,默认 `:8889`,`APP_ENABLE_SOLVER=0` 可禁用 |

**插件白名单**是硬编码的(`core/registry.py:12`):

```python
SUPPORTED_PLATFORMS = ("chatgpt", "icloud")
```

`register()` 装饰器会检查这个元组,不在白名单里的插件类即使被导入也**不会进注册表**;`get()` 对下线平台抛的是 `KeyError: 平台 'x' 已下线`。要恢复一个历史平台,除了补代码还必须改这一行。

### 1.6 平台插件契约

所有平台插件继承 `core/base_platform.py:41` 的 `BasePlatform`,契约如下:

| 成员 | 类型 | 说明 |
| --- | --- | --- |
| `name` / `display_name` / `version` | 类属性 | 注册表键与前端展示名 |
| `supported_executors` | 类属性 | 声明支持的执行器,**未列出的会在 `__init__` 里自动降级**到 `protocol`(`core/base_platform.py:52`) |
| `register(email, password)` | **抽象** | 跑完注册链路,返回 `Account` |
| `check_valid(account)` | **抽象** | 探测账号是否有效 |
| `get_trial_url(account)` | 可选 | 生成试用激活链接 |
| `get_platform_actions()` | 可选 | 声明平台特有操作(id/label/params),前端据此动态渲染操作菜单 |
| `execute_action(id, account, params)` | 可选 | 执行上述操作,返回 `{ok, data, error}` |
| `get_quota(account)` | 可选 | 查询配额 |
| `bind_task_control(control)` | 基类实现 | 把任务控制器同时注入插件和其 `mailbox` 属性(`core/base_platform.py:98`),这是"停止/跳过"能穿透到邮箱等待环节的关键 |
| `get_mailbox_otp_timeout(default)` | 基类实现 | 按 `mailbox_otp_timeout_seconds` → `email_otp_timeout_seconds` → `otp_timeout` → 默认值的优先级解析超时,避免各平台散落魔法值 |

`get_platform_actions` / `execute_action` 这对**自描述动作**机制是这套插件体系里设计得最好的部分:平台声明自己能做什么,前端不需要为每个平台硬编码按钮。

两个工厂方法决定了运行时组件:

- `_make_executor()`(`core/base_platform.py:125`)—— `protocol` → `ProtocolExecutor`;`headless`/`headed` → `PlaywrightExecutor`。
- `_make_captcha()`(`core/base_platform.py:139`)—— `yescaptcha` / `manual` / `local_solver` 三选一,`local_solver` 的地址按 `config.extra.solver_url` → `LOCAL_SOLVER_URL` → `http://127.0.0.1:${SOLVER_PORT|8889}` 逐级回退。

### 1.7 账号统一模型

不论哪个平台,注册产物都收敛到 `core/base_platform.py:19` 的 `Account` dataclass,并由 `core/db.py:145` 的 `save_account()` 以 **(platform, email) 为唯一键做 upsert** 落到 `accounts` 表。

| 字段 | 含义 |
| --- | --- |
| `platform` / `email` / `password` | 联合标识与登录凭据 |
| `user_id` / `region` / `token` | 平台侧 ID、地区、主 Token |
| `status` | `AccountStatus` 枚举:`registered` / `trial` / `subscribed` / `expired` / `invalid` |
| `trial_end_time` | unix 时间戳 |
| `extra` | **平台自定义字段的容器**,序列化进 `extra_json` |

`extra` 是这套模型的弹性来源,也是它的代价。ChatGPT 的 Refresh Token、`totp_secret`、手机号、绑定失败原因全塞在里面,**没有 schema 约束、没有类型检查、在 SQL 层不可索引**。想知道 `extra` 里到底有哪些键,只能全仓 grep `get_extra()`。注册流程之外,`cashier_url` 被单独从 `extra` 提升成了一个真实列(`core/db.py:160`),说明这种"需要查询的字段被迫出表"的压力是真实存在的。

### 1.8 后续章节导航

| 章节 | 内容 |
| --- | --- |
| 02 API 层与鉴权配置 | 103 个 endpoint 清单、鉴权中间件、配置存储、凭据加密、代理池、数据模型 |
| 03 任务编排与调度 | 批量注册生命周期、并发与节流、停止/跳过语义、重试策略、执行器与 Solver |
| 04 ChatGPT 平台 | authorize 状态机、Sentinel PoW、TLS 指纹、注册模式矩阵、接码、2FA、Token 生命周期 |
| 05 邮箱池子系统 | 全部临时邮箱 provider、OTP 提取、LuckMail、邮件导入、Outlook 后端 |
| 06 iCloud 平台 | Apple SRP 登录、双重认证、Cookie 导入、Hide My Email 生成与限流、免登录分享页 |
| 07 外部集成服务层 | 插件管理、CLIProxyAPI 同步、账号导出格式、付费渠道、部署形态与环境变量 |
| 08 前端 | 路由与页面地图、鉴权与请求层、注册任务页表单、实时日志、状态管理 |

---

## 二、API 层与鉴权配置

### API 层与配置/鉴权子系统

本章覆盖 `main.py` 的应用装配、`api/` 下 16 个路由模块，以及 `core/` 中支撑它们的配置存储、凭据加密、代理池与数据模型。

#### 总览

| 项目 | 结论 |
|---|---|
| Web 框架 | FastAPI，单进程 uvicorn（`main.py:163`），SPA 静态资源由同一进程兜底 |
| 路由模块 | `api/*.py` 共 16 个（不含 `__init__.py`） |
| HTTP 端点总数 | **103**（`grep -c '@router\.' api/*.py` 求和），其中 **97 个实际可访问** |
| 未挂载模块 | `api/chatgpt.py` 的 6 个端点定义了但从未 `include_router`，是死代码 |
| `main.py` 内联端点 | 另有 2 个：`GET /api/solver/status`、`POST /api/solver/restart` |
| 鉴权 | 自实现 HS256 JWT + 可选 TOTP，靠一个 HTTP 中间件统一拦截 `/api/*` |
| 数据库 | SQLite via SQLModel，7 张表 + 1 张配置表，无迁移框架（手写 `_migrate_*`） |

##### 路由挂载与前缀

`main.py:116-131` 逐个挂载路由。除 `shared_mail` 外全部带 `/api` 前缀；各模块自身的 `APIRouter(prefix=...)` 再叠一层，所以完整路径是 `/api` + 模块前缀 + 装饰器路径。

| 挂载语句 | 模块前缀 | 最终前缀 |
|---|---|---|
| `main.py:116-129`（14 个模块） | `/accounts`、`/tasks`、`/config` 等 | `/api/<模块前缀>` |
| `main.py:131` `shared_mail_router` | 无 | `/m/{share_token}`（**刻意不带 `/api`**） |

> `api/chatgpt.py` 既未 import 也未 include（`main.py:12-26` 无该模块）。其 `POST /chatgpt/{id}/refresh-token` 等能力已由 `api/actions.py` 的通用 action 通道覆盖，该文件应视为待清理的历史残留。

---

#### 一、HTTP 接口清单

下表按路由文件分组，路径均为客户端实际可请求的完整路径。

##### 账号管理 `api/accounts.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/accounts` | `api/accounts.py:87` list_accounts | 分页列出账号，支持 platform/status/email/plus_status/创建时间区间筛选 |
| POST | `/api/accounts` | `api/accounts.py:115` create_account | 手工新增一条账号记录 |
| GET | `/api/accounts/stats` | `api/accounts.py:131` get_stats | 账号总数与按平台、按状态的分布统计 |
| GET | `/api/accounts/export` | `api/accounts.py:143` export_accounts | 导出 CSV 流（含明文密码列） |
| GET | `/api/accounts/export-formats` | `api/accounts.py:172` get_export_formats | 返回可用导出格式清单与默认格式 |
| POST | `/api/accounts/export-text` | `api/accounts.py:178` export_accounts_text | 按指定格式导出文本，可传 account_ids 或复用筛选条件 |
| POST | `/api/accounts/import` | `api/accounts.py:211` import_accounts | 按 `邮箱 密码 [extra_json]` 逐行批量导入 |
| POST | `/api/accounts/batch-delete` | `api/accounts.py:239` batch_delete_accounts | 按 ID 批量删除，单次上限 1000 |
| POST | `/api/accounts/check-all` | `api/accounts.py:277` check_all_accounts | 后台任务：批量检测账号有效性 |
| GET | `/api/accounts/{account_id}` | `api/accounts.py:285` get_account | 按 ID 取单个账号 |
| PATCH | `/api/accounts/{account_id}` | `api/accounts.py:293` update_account | 局部更新 status / token / cashier_url |
| DELETE | `/api/accounts/{account_id}` | `api/accounts.py:312` delete_account | 删除单个账号 |
| POST | `/api/accounts/{account_id}/check` | `api/accounts.py:322` check_account | 后台任务：检测单账号有效性并回写状态 |

##### 任务编排 `api/tasks.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| POST | `/api/tasks/backfill-rt` | `api/tasks.py:1058` create_backfill_rt_task | 创建批量补 refresh_token 任务 |
| POST | `/api/tasks/bind-2fa` | `api/tasks.py:1110` create_bind_2fa_task | 创建批量绑定 2FA 任务 |
| POST | `/api/tasks/register` | `api/tasks.py:1162` create_register_task | 创建批量注册任务（核心入口） |
| POST | `/api/tasks/{task_id}/skip-current` | `api/tasks.py:1171` skip_current_account | 跳过任务当前正在处理的账号 |
| POST | `/api/tasks/{task_id}/stop` | `api/tasks.py:1182` stop_task | 请求停止任务 |
| GET | `/api/tasks/logs` | `api/tasks.py:1193` get_logs | 查询历史任务日志（TaskLog 表） |
| POST | `/api/tasks/logs/batch-delete` | `api/tasks.py:1205` batch_delete_logs | 批量删除任务日志 |
| GET | `/api/tasks/{task_id}/logs/stream` | `api/tasks.py:1238` stream_logs | SSE 流式推送任务实时日志 |
| GET | `/api/tasks/{task_id}` | `api/tasks.py:1283` get_task | 查询单个任务运行态快照 |
| GET | `/api/tasks` | `api/tasks.py:1289` list_tasks | 列出任务运行记录 |
| DELETE | `/api/tasks/{task_id}` | `api/tasks.py:1296` delete_task | 删除任务记录 |

##### 平台注册表 `api/platforms.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/platforms` | `api/platforms.py:9` get_platforms | 列出注册表中已加载的平台插件 |

##### 代理池 `api/proxies.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/proxies` | `api/proxies.py:26` list_proxies | 列出全部代理（含成功/失败计数、启用态） |
| POST | `/api/proxies` | `api/proxies.py:32` add_proxy | 新增单条代理，URL 完全相同才判重 |
| POST | `/api/proxies/bulk` | `api/proxies.py:44` bulk_add_proxies | 批量新增代理，逐条按原始字符串去重 |
| DELETE | `/api/proxies/{proxy_id}` | `api/proxies.py:59` delete_proxy | 删除单条代理 |
| POST | `/api/proxies/batch-delete` | `api/proxies.py:69` batch_delete_proxies | 批量删除，单次上限 1000 |
| PATCH | `/api/proxies/{proxy_id}/toggle` | `api/proxies.py:90` toggle_proxy | 翻转 is_active 启用状态 |
| POST | `/api/proxies/check` | `api/proxies.py:101` check_proxies | 后台任务：全量探测代理连通性并回写健康统计 |

##### 全局配置 `api/config.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/config` | `api/config.py:147` get_config | 返回 CONFIG_KEYS 白名单内的全部配置（含默认值兜底） |
| PUT | `/api/config` | `api/config.py:196` update_config | 批量写配置，非白名单 key 静默丢弃 |
| POST | `/api/config/applemail/import` | `api/config.py:227` import_applemail_pool | 导入 AppleMail 邮箱池文本，可绑定到配置 |
| GET | `/api/config/applemail/pool` | `api/config.py:256` get_applemail_pool_snapshot | 读取 AppleMail 池文件快照 |

##### 平台操作 `api/actions.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/actions/{platform}` | `api/actions.py:259` list_actions | 列出该平台插件声明的可执行操作 |
| POST | `/api/actions/{platform}/{action_id}/batch` | `api/actions.py:267` execute_batch_action | 对一批账号（显式 ID 或按筛选全选）批量执行操作 |
| POST | `/api/actions/{platform}/{account_id}/{action_id}` | `api/actions.py:353` execute_action | 对单个账号执行平台操作，结果回写 extra_json/status |

##### 外部服务集成 `api/integrations.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/integrations/services` | `api/integrations.py:27` get_services | 列出外部子服务及其运行状态 |
| POST | `/api/integrations/services/start-all` | `api/integrations.py:32` start_all_services | 启动全部外部服务 |
| POST | `/api/integrations/services/stop-all` | `api/integrations.py:37` stop_all_services | 停止全部外部服务 |
| POST | `/api/integrations/services/{name}/start` | `api/integrations.py:42` start_service | 启动指定外部服务 |
| POST | `/api/integrations/services/{name}/install` | `api/integrations.py:47` install_service | 安装指定外部服务 |
| POST | `/api/integrations/services/{name}/uninstall` | `api/integrations.py:52` uninstall_service | 卸载指定外部服务 |
| POST | `/api/integrations/services/{name}/stop` | `api/integrations.py:57` stop_service | 停止指定外部服务 |
| POST | `/api/integrations/backfill` | `api/integrations.py:62` backfill_integrations | 把已有账号回灌到 CPA / CLIProxyAPI |

##### 鉴权 `api/auth.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/auth/status` | `api/auth.py:166` auth_status | 查询是否已设密码、是否已启用 2FA（免鉴权） |
| POST | `/api/auth/setup` | `api/auth.py:175` setup_password | 首次设置密码；已设密码时须带合法 token 才能改 |
| POST | `/api/auth/disable` | `api/auth.py:193` disable_auth | 关闭密码保护，同时清空 TOTP 密钥 |
| POST | `/api/auth/login` | `api/auth.py:206` login | 密码登录；启用 2FA 时返回 temp_token 而非正式 token |
| POST | `/api/auth/verify-totp` | `api/auth.py:223` verify_totp_route | 用 temp_token + 6 位验证码换取正式 JWT |
| POST | `/api/auth/logout` | `api/auth.py:239` logout | 仅返回 ok，服务端无任何状态变更（token 不会失效） |
| POST | `/api/auth/change-password` | `api/auth.py:244` change_password | 校验旧密码后改密（新密码≥6 位） |
| GET | `/api/auth/2fa/setup` | `api/auth.py:256` setup_2fa | 生成 TOTP 密钥与 otpauth:// URI（尚未启用） |
| POST | `/api/auth/2fa/enable` | `api/auth.py:262` enable_2fa | 校验验证码后把密钥写入配置，正式启用 2FA |
| POST | `/api/auth/2fa/disable` | `api/auth.py:272` disable_2fa | 清空 TOTP 密钥 |

##### 邮箱导入 `api/mail_imports.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/mail-imports/providers` | `api/mail_imports.py:15` list_mail_import_providers | 列出所有邮箱导入策略描述符 |
| GET | `/api/mail-imports/snapshot` | `api/mail_imports.py:20` get_mail_import_snapshot | 读取指定导入源的当前快照 |
| POST | `/api/mail-imports` | `api/mail_imports.py:42` execute_mail_import | 按 type 分发到对应策略执行导入 |
| POST | `/api/mail-imports/delete` | `api/mail_imports.py:53` delete_mail_import_item | 删除导入源中的单条记录 |
| POST | `/api/mail-imports/batch-delete` | `api/mail_imports.py:64` batch_delete_mail_import_items | 批量删除导入源记录 |

##### 微软邮箱 `api/outlook.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| POST | `/api/outlook/batch-import` | `api/outlook.py:23` batch_import_outlook | 按 `----` 分隔批量导入微软邮箱（OAuth 或 MailAPI URL 两种格式） |

##### 贡献服务代理 `api/contribution.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| POST | `/api/contribution/quota-stats` | `api/contribution.py:125` get_quota_stats | 代理请求贡献服务器的额度统计（多候选路径依次重试） |
| POST | `/api/contribution/key-info` | `api/contribution.py:204` get_key_info | 代理查询贡献 key 的详情 |
| POST | `/api/contribution/redeem` | `api/contribution.py:238` redeem | 代理发起提现兑换，返回兑换码 |
| POST | `/api/contribution/generate-key` | `api/contribution.py:288` generate_key | 代理在贡献服务器生成新 key |

##### iCloud 主号与隐私邮箱 `api/icloud.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| POST | `/api/icloud/login-sessions` | `api/icloud.py:144` start_login | 发起 Apple ID 登录会话（可能进入 2FA 挑战） |
| GET | `/api/icloud/login-sessions/{login_id}` | `api/icloud.py:165` get_login | 查询登录会话当前状态 |
| POST | `/api/icloud/login-sessions/{login_id}/verify` | `api/icloud.py:173` verify_login | 提交两步验证码；完成后自动落库主号 |
| POST | `/api/icloud/login-sessions/{login_id}/resend` | `api/icloud.py:181` resend_login_code | 重发验证码 |
| POST | `/api/icloud/login-sessions/{login_id}/sms` | `api/icloud.py:189` send_login_sms | 改用短信通道下发验证码 |
| DELETE | `/api/icloud/login-sessions/{login_id}` | `api/icloud.py:197` cancel_login | 取消并销毁登录会话 |
| GET | `/api/icloud/accounts` | `api/icloud.py:206` list_accounts | 列出已接入的 iCloud 主号 |
| POST | `/api/icloud/accounts/import-cookie` | `api/icloud.py:211` import_cookie | 用 Cookie/Session 直接导入主号，绕过交互登录 |
| PATCH | `/api/icloud/accounts/{account_id}` | `api/icloud.py:231` update_account | 启用/停用主号 |
| DELETE | `/api/icloud/accounts/{account_id}` | `api/icloud.py:239` delete_account | 删除主号 |
| POST | `/api/icloud/accounts/{account_id}/sync` | `api/icloud.py:248` sync_account | 从 Apple 侧同步隐私邮箱列表 |
| GET | `/api/icloud/accounts/{account_id}/messages` | `api/icloud.py:256` account_messages | 拉取主号收件箱（可按收件人过滤） |
| GET | `/api/icloud/aliases` | `api/icloud.py:270` list_aliases | 列出隐私邮箱，可按主号过滤 |
| POST | `/api/icloud/aliases` | `api/icloud.py:275` generate_aliases | 批量生成隐私邮箱（1~5 个，自动编号标签） |
| POST | `/api/icloud/aliases/batch-delete` | `api/icloud.py:290` batch_delete_aliases | 批量删除隐私邮箱，remote 控制是否同步删远端 |
| POST | `/api/icloud/aliases/import-to-pool` | `api/icloud.py:298` import_aliases_to_pool | 把隐私邮箱导进 MailAPI URL 号池，等价于导出 `mail_url` 再手工导入 |
| DELETE | `/api/icloud/aliases/{alias_id}` | `api/icloud.py:312` delete_alias | 删除单个隐私邮箱 |
| GET | `/api/icloud/aliases/{alias_id}/messages` | `api/icloud.py:321` alias_messages | 拉取某隐私邮箱的邮件列表 |

##### 免登录邮件页 `api/shared_mail.py`（无 /api 前缀）

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/m/{share_token}` | `api/shared_mail.py:150` shared_latest_mail | 免登录 HTML 页：按 share_token 展示该隐私邮箱最新一封邮件。**这条链接本身也是号池里的取码地址**——「导入 MailAPI 号池」把它按 `隐私邮箱----<origin>/m/<token>` 落进 `outlook_accounts.mailapi_url`，注册任务靠 `MailApiUrlOtpBackend` 反复 GET 它取码 |

##### 接码探针 `api/sms.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/sms/providers` | `api/sms.py:51` list_sms_providers | 列出接码平台及默认服务、OpenAI 白名单国家 |
| GET | `/api/sms/country-options` | `api/sms.py:63` list_sms_country_options | 国家 ID 与中文名对照选项 |
| POST | `/api/sms/balance` | `api/sms.py:84` get_sms_balance | 探针：查接码平台余额（入参留空回落全局配置） |
| POST | `/api/sms/countries` | `api/sms.py:94` get_sms_countries | 探针：查指定服务的国家可用量与价格排名 |

##### 支付渠道 `api/payments.py`

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| GET | `/api/payments/channels` | `api/payments.py:109` list_channels | 列出已注册支付渠道 |
| GET | `/api/payments/channels/{channel}/cards` | `api/payments.py:115` list_cards | 列出 direct 渠道卡片（仅返回 last4） |
| POST | `/api/payments/channels/{channel}/cards` | `api/payments.py:123` add_card | 新增卡片（卡号/CVC 明文入库） |
| DELETE | `/api/payments/channels/{channel}/cards/{card_id}` | `api/payments.py:133` delete_card | 删除卡片 |
| POST | `/api/payments/channels/{channel}/cards/reset-uses` | `api/payments.py:143` reset_card_uses | 重置卡片使用次数 |
| POST | `/api/payments/jobs` | `api/payments.py:211` create_payment_task | 创建批量提链/支付任务，复用 tasks 的任务运行时 |
| POST | `/api/payments/{account_id}/link` | `api/payments.py:255` create_payment_link | 为单账号生成支付链接 |
| POST | `/api/payments/{account_id}/pay` | `api/payments.py:261` execute_payment | 为单账号执行支付 |

##### ChatGPT 专用 `api/chatgpt.py`（⚠ 未挂载）

| 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|
| POST | `/api/chatgpt/{account_id}/refresh-token` | `api/chatgpt.py:63` refresh_token | 刷新 access_token 并回写 extra/token |
| POST | `/api/chatgpt/{account_id}/payment-link` | `api/chatgpt.py:98` generate_payment_link | 生成 Plus / Team 订阅支付链接 |
| GET | `/api/chatgpt/{account_id}/subscription` | `api/chatgpt.py:117` check_subscription | 本地探测订阅套餐并持久化探测结果 |
| POST | `/api/chatgpt/{account_id}/probe-local` | `api/chatgpt.py:134` probe_local_status | 本地探测账号状态并按策略更新 status |
| POST | `/api/chatgpt/{account_id}/upload-cpa` | `api/chatgpt.py:153` upload_cpa | 把 token JSON 上传到 CPA |
| POST | `/api/chatgpt/{account_id}/upload-sub2api` | `api/chatgpt.py:170` upload_sub2api | 把账号上传到 sub2api |

---

#### 二、鉴权机制

##### 2.1 整体模型

只有一个「面板管理员」身份，没有多用户、没有角色。密码哈希存在配置表里，**密码未设置时全站完全开放**——这是设计上的"可选鉴权"，便于本机单机使用。

##### 2.2 中间件拦截逻辑（`main.py:84-106`）

判定顺序如下，任一条命中即放行：

| 顺序 | 条件 | 行为 |
|---|---|---|
| 1 | 路径以 `/api/auth/` 开头 | 直接放行（否则无法登录） |
| 2 | 路径**不以 `/api/` 开头** | 直接放行（静态资源、SPA fallback、`/m/{token}`） |
| 3 | `config_store.get("auth_password_hash")` 为空 | 直接放行（未启用鉴权） |
| 4 | 无 `Authorization: Bearer ` 头 | 401 + `PANEL_AUTH_HEADERS` |
| 5 | `verify_token()` 抛 HTTPException | 原样返回其 status/detail |

注意第 1 条是**整个 `/api/auth/` 前缀放行**，而不只是 login。`api/auth.py` 内部再用 `Depends(require_auth)` 给需要保护的端点补校验（`auth.py:243/255/261/271`），`/auth/setup` 与 `/auth/disable` 则是手写的条件式校验（`api/auth.py:183-186`、`api/auth.py:196-199`）：已设密码时必须带合法 token，未设密码时任何人都能调。

##### 2.3 Token 签发与校验

| 环节 | 实现 | 位置 |
|---|---|---|
| 算法 | HS256，**纯 stdlib 手写**（hmac + hashlib + base64），未用 PyJWT | `api/auth.py:58-89` |
| 密钥来源 | 环境变量 `APP_JWT_SECRET` 优先；否则读配置 `auth_jwt_secret`；再否则生成 32 字节 hex 并写回配置 | `api/auth.py:36-44` |
| Payload | `{"sub":"admin","exp":now+7d,"iat":now}`，默认有效期 7 天 | `api/auth.py:58-68` |
| 签名校验 | `hmac.compare_digest` 定时安全比较 | `api/auth.py:79` |
| 校验项 | 分段数、签名、payload 可解析、`exp` 未过期 | `api/auth.py:71-89` |

**未校验 `alg` 字段**：`verify_token` 直接对 `header.payload` 重算 HMAC-SHA256，不解析 header 里声明的 alg。这恰好规避了经典的 `alg:none` 攻击（伪造的 header 会让重算签名对不上），属于"因简陋而安全"，不是显式防御。

##### 2.4 密码与 2FA

| 项 | 实现 | 风险 |
|---|---|---|
| 密码哈希 | **单轮 `hashlib.sha256(password)`，无 salt、无 KDF**（`api/auth.py:100-101`） | 面板密码若泄露哈希，彩虹表可直接反查；应换 bcrypt/argon2/PBKDF2 |
| 密码强度 | 仅校验长度 ≥ 6（`api/auth.py:180`、`api/auth.py:249`） | 弱 |
| 登录比较 | `hmac.compare_digest`（`api/auth.py:211`） | 合理 |
| TOTP | RFC 6238 手写实现，SHA1/6 位/30 秒步长，接受 ±1 时间窗（`api/auth.py:115-130`） | 标准做法 |
| 2FA 中间态 | `_pending_2fa` 是**进程内字典**，5 分钟过期（`api/auth.py:135`、`api/auth.py:216`） | 重启即失效；多 worker 部署会串不上 |
| 登出 | `POST /auth/logout` 只返回 `{"ok":True}`（`api/auth.py:238-240`） | **服务端不吊销 token**，签发后 7 天内始终有效；无黑名单机制 |

##### 2.5 `PANEL_AUTH_HEADERS` 的作用

定义在 `api/auth.py:24`，值为 `{"X-Panel-Auth-Required": "1"}`。

存在原因：401 这个状态码在本系统里有**两种语义**——「面板登录态失效」和「业务侧凭据被上游拒绝」（例如 iCloud 主号的 Apple 密码错误）。前端如果见到 401 就清 token 跳登录页，用户在 iCloud 验证弹窗里点一下保存就会被踢出面板。因此只有面板鉴权失败的响应才附带这个头，前端据此区分处理。

配套做法见 `api/icloud.py:32-52`：iCloud 的业务错误码映射表**刻意完全不使用 401**，`invalid_credentials` 映射成 400，`session_expired` 映射成 409。同处还有一条与反向代理相关的考量——`upstream_rejected` 映射成 422 而非 5xx，因为 Cloudflare 会把 5xx 的响应体整个替换成自己的错误页，导致 Apple 返回的真实原因全部丢失。

##### 2.6 `shared_mail` 绕过鉴权的设计意图

`api/shared_mail.py:1-6` 的模块 docstring 写明了意图：这条路由**刻意不挂在 `/api` 下**，因为中间件只拦 `/api/*`（`main.py:87`）。目标是把链接复制给他人，对方不登录面板也能打开查看某个隐私邮箱的最新邮件。

安全性依赖"链接即权限"：

| 机制 | 实现 |
|---|---|
| 凭证 | `share_token` = `secrets.token_urlsafe(16)`（128 位随机），而非自增 id——用 id 谁都能从 1 数到 100（`core/db.py:14-16`、`core/db.py:128`） |
| 邮件正文渲染 | 塞进 `sandbox="allow-same-origin"` 的 iframe，**不给 `allow-scripts`**，另加 `script-src 'none'` 的 CSP（`api/shared_mail.py:76-85`） |
| 缓存与索引 | 响应头 `Cache-Control: no-store` + `X-Robots-Tag: noindex, nofollow`（`api/shared_mail.py:68`） |
| 错误处理 | 捕获全部异常，绝不把栈信息返回给外部（`api/shared_mail.py:158-160`） |
| OpenAPI | `include_in_schema=False`，不出现在文档中（`api/shared_mail.py:149`） |

遗留风险：token **永不轮换、无过期**，链接一旦外泄只能删除整个隐私邮箱；且该路径不受任何频率限制，token 可被离线暴力尝试（虽然 128 位空间下不现实）。

---

#### 三、配置存储

##### 3.1 读写模型（`core/config_store.py`）

存储载体是 SQLite 中的 `configs` 表，模型极简（`core/config_store.py:118-121`）：

```python
class ConfigItem(SQLModel, table=True):
    __tablename__ = "configs"
    key: str = Field(primary_key=True)
    value: str = ""
```

**全部值以明文字符串存储，无任何加密**——包括各类邮箱平台 API Key、`cloudmail_admin_password`、`sms_api_key`、`auth_jwt_secret`、`auth_totp_secret` 在内的敏感项，都是可直接 `SELECT` 出来的明文。`core/secret_box.py` 的加密能力并未用于配置表。

| 方法 | 行为 | 位置 |
|---|---|---|
| `get(key, default)` | 先查 DB；值为空则回落环境变量/`.env`；再回落 `default` | `core/config_store.py:127-135` |
| `set(key, value)` | 单条 upsert，立即 commit | `core/config_store.py:137-145` |
| `get_all()` | 读全表后与环境变量合并（DB 优先，env 补空缺） | `core/config_store.py:147-151` |
| `set_many(data)` | 批量 upsert，单事务提交 | `core/config_store.py:153-162` |

**缓存策略：完全没有缓存。** 每次 `get()` 都会新开一个 `Session` 查库，并且**每次都重新解析一遍 `.env` 文件和整个 `os.environ`**（`core/config_store.py:128` 调 `_runtime_env_values()`）。在 `api/actions.py:262/274/366` 这类每请求都调 `config_store.get_all()` 的路径上，这是可观的重复 IO；好处是配置改动立即生效，无需失效逻辑。

##### 3.2 环境变量回落与 key 归一化

回落链路为 **DB → `.env` 文件 → `os.environ` → 默认值**，且 `os.environ` 会覆盖 `.env`（`core/config_store.py:79-89`）。

为兼容不同书写习惯，查找时会生成多种 key 变体依次尝试（`core/config_store.py:29-49`）：原样、全小写、全大写、非字母数字替换为 `_` 后的三种形式。即配置项 `sms_api_key` 也能被环境变量 `SMS_API_KEY` 命中。`_normalize_config_value` 还会剥掉值两端配对的引号（`core/config_store.py:15-19`）。

##### 3.3 `api/config.py` 暴露的配置项分类

`CONFIG_KEYS` 白名单共 **113 项**（`api/config.py:15-132`），读写两侧都以它为准：`GET` 只返回白名单内的 key（`api/config.py:192`），`PUT` 只接受白名单内的 key、其余静默丢弃（`api/config.py:198`）。

| 分类 | 代表 key |
|---|---|
| 邮箱域名策略 | `email_domain_rule_enabled`、`email_domain_level_count` |
| 打码/验证码 | `yescaptcha_key`、`twocaptcha_key`、`default_captcha_solver` |
| 临时邮箱服务商（占比最大） | `duckmail_*`、`freemail_*`、`moemail_*`、`skymail_*`、`cloudmail_*`、`maliapi_*`、`applemail_*`、`gptmail_*`、`opentrashmail_*`、`cfworker_*`、`luckmail_*` |
| 邮箱路由 | `mail_provider`、`mail_import_source`、`outlook_backend`、`mailbox_otp_timeout_seconds` |
| 支付与代理 | `payment_link_proxy`、`payment_pay_proxy`、`payment_proxy` |
| 下游同步目标 | `cpa_*`、`sub2api_*`、`team_manager_*`、`codex_proxy_*`、`cliproxyapi_*` |
| iCloud | `icloud_region`、`icloud_alias_label`、`icloud_account_email`、`public_base_url`（面板对外访问地址，隐私邮箱导号池时拼免登录链接用；平时前端会把浏览器地址栏的 origin 带上来，只有服务端单独跑时才靠它兜底） |
| 短信接码 | `sms_*`（共 16 项，含自动选国、价格上限、复用策略） |
| 贡献系统 | `contribution_*`、`custom_contribution_*` |
| 执行器 | `default_executor`、`register_retry_times`、`external_apps_update_mode` |

`GET /api/config` 还承担**默认值兜底**职责（`api/config.py:147-192`）：对 `applemail_base_url`、`luckmail_base_url`、`outlook_backend`、`sms_provider` 等十余项，值为空时填入内置默认；并做历史值迁移（`mail_provider == "outlook"` 统一改写为 `"microsoft"`，`api/config.py:149-150`）。

`PUT` 侧有三处入参规整（`api/config.py:199-221`）：`mail_provider` 同样的 outlook→microsoft 改写；`mail_import_source` 按是否同请求携带 `mail_provider` 决定用 `align_source_with_provider` 还是 `normalize_mail_import_source`；`email_domain_level_count` 强制为 ≥2 的整数，否则 400。

##### 3.4 敏感字段的脱敏返回

**没有做任何脱敏。** `GET /api/config` 把 `yescaptcha_key`、`sms_api_key`、`cloudmail_admin_password`、`freemail_password`、`contribution_key` 等全部**原文返回**（`api/config.py:192` 一行 dict comprehension，无掩码逻辑）。

全仓库唯一的脱敏实现是 `core/proxy_utils.py:109-145` 的 `redact_proxy_url()`，且它只用于**日志输出**，不用于 API 响应。支付卡片是另一个例外：`api/payments.py:92-105` 的 `_public_card()` 只回传 `last4`，卡号与 CVC 不出接口——这是全项目唯一在响应层做收敛的地方（但底层 `cards.db` 仍是明文存储）。

前端能拿到全部明文密钥是有意为之（设置页需要回显已保存的值），代价是任何一次 `GET /api/config` 的越权访问即等于全量凭据泄露。

---

#### 四、凭据加密（`core/secret_box.py`）

| 项 | 实现 |
|---|---|
| 算法 | **AES-256-GCM**（`cryptography` 的 `AESGCM`），认证加密 |
| 密钥长度 | 32 字节（`core/secret_box.py:24`） |
| Nonce | 每次加密随机生成 12 字节（`core/secret_box.py:25`、`core/secret_box.py:85`） |
| 信封格式 | `"v1:" + base64(nonce ‖ ciphertext‖tag)`，带版本前缀便于未来轮换（`core/secret_box.py:26`、`core/secret_box.py:87`） |
| AAD | 未使用（传 `None`） |
| 初始化 | 惰性 + 双重检查锁，线程安全（`core/secret_box.py:77-82`） |

##### 4.1 密钥来源（优先级从高到低）

1. 环境变量 `CREDENTIAL_ENCRYPTION_KEY`——base64 或 hex 编码的 32 字节（`core/secret_box.py:53-55`）
2. 密钥文件——路径取 `CREDENTIAL_ENCRYPTION_KEY_FILE`，缺省为 `Path.cwd()/.secrets/credential_key`（`core/secret_box.py:47-49`）
3. 都没有则**自动生成**并落盘（`core/secret_box.py:61-67`）

落盘时先用 `os.open(..., 0o600)` 创建再写入，避免密钥短暂地对其他用户可读（`core/secret_box.py:63-66`）。`.secrets/` 已在 `.gitignore:51` 中排除；Docker 部署通过 `Dockerfile:27` 与 `docker-compose.yml:19` 把路径指向 `/runtime/.secrets/credential_key`，确保容器重建后密钥仍在挂载卷里——否则历史密文将永久无法解密。

##### 4.2 使用范围

目前**只有 iCloud 主号凭据**在用：

| 调用点 | 作用 |
|---|---|
| `services/icloud_service.py:285` | `encrypt_json(credentials.to_dict())` 写入 `ICloudAccountModel.credentials_cipher` |
| `services/icloud_service.py:81` | `decrypt_json(row.credentials_cipher)` 读出 Web Session Cookie 与 IMAP 凭据 |

**未覆盖的敏感数据**：`accounts.password`（明文）、`accounts.token`（明文）、`outlook_accounts.password` / `refresh_token`（明文）、`configs` 表全部 API Key（明文）、`cards.db` 的卡号与 CVC（明文）。即加密能力已具备但只用在一处，其余凭据仍是裸存。

---

#### 五、代理池

##### 5.1 格式规范化（`core/proxy_utils.py`）

`normalize_proxy_url()`（`core/proxy_utils.py:89-106`）同时处理两类输入：

| 输入形态 | 判定条件 | 规范化结果 |
|---|---|---|
| `host:port` | 2 段且第 2 段全数字 | `http://host:port` |
| `host:port:user:pass` | 4 段且第 2 段全数字 | `http://user:pass@host:port` |
| `scheme:host:port:user:pass` | ≥5 段、第 3 段全数字、首段是已知 scheme | `scheme://user:pass@host:port`，密码含 `:` 时后续段全部拼回 |
| 标准 URL | 其余 | 原样返回 |

两个细节：用户名/密码会 `quote(..., safe="")` 转义，IPv6 主机自动补方括号（`core/proxy_utils.py:44-50`）。

**`socks5` 一律改写为 `socks5h`**（`core/proxy_utils.py:42`、`core/proxy_utils.py:103-105`），因为 `socks5` 在本地解析 DNS 会泄漏目标域名，`socks5h` 才把解析交给代理端。

下游适配有两个构造器：`build_requests_proxy_config()` 产出 `{"http":..., "https":...}`（`core/proxy_utils.py:148-152`）；`build_playwright_proxy_config()` 把 `socks5h` **改回** `socks5`（Playwright 不认 `socks5h`），并将认证信息拆成独立的 `username`/`password` 字段（`core/proxy_utils.py:155-175`）。

`is_authenticated_socks5_proxy()`（`core/proxy_utils.py:59-86`）用于识别"带认证的 SOCKS5"这一 Playwright 无法直接支持的组合，兼容 JSON 字典与 URL 两种形态。

##### 5.2 选择策略（`core/proxy_pool.py:39-55`）

不是纯轮询，而是**按成功率排序后再轮询索引**：

```python
proxies.sort(key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1), reverse=True)
idx = self._index % len(proxies)   # self._index 单调递增，线程锁保护
```

只取 `is_active == True` 的记录，可按 `region` 过滤。副作用值得注意：`_index` 是全局单调计数，而排序后的列表长度会随启用数量变化，所以取到的代理分布并不均匀；且新代理（0 成功 0 失败）的成功率计算为 `0/1 = 0`，会排在**最末**，需靠 `_index` 轮到才有机会首次被使用。

##### 5.3 健康统计回写规则

| 事件 | 动作 | 位置 |
|---|---|---|
| `report_success(url)` | `success_count += 1`；**无条件 `is_active = True`**（已停用的代理会被自动恢复）；刷新 `last_checked` | `core/proxy_pool.py:57-65` |
| `report_fail(url)` | `fail_count += 1`；刷新 `last_checked`；仅当 **`success_count == 0` 且 `fail_count >= 5`** 时置 `is_active = False` | `core/proxy_pool.py:67-76` |

停用条件相当保守：**只要历史上成功过哪怕一次，就永远不会被自动停用**，无论之后连续失败多少次。即规则是"从未成功且连败 5 次"才下线，而非通常理解的"连续失败 N 次"（代码里也没有连续失败计数器，`fail_count` 是累计值）。

查找目标行时 `_find_by_url()` 做了三级回退（`core/proxy_pool.py:18-37`）：精确匹配 → 规范化后匹配 → 全表遍历逐条规范化比对。最后一级是 O(n) 全表扫描，代理量大时每次回写都会触发。

##### 5.4 `api/proxies.py` 的检测接口

`POST /api/proxies/check`（`api/proxies.py:100-103`）把 `proxy_pool.check_all` 丢进 FastAPI 的 `BackgroundTasks` 后立即返回 `{"message":"检测任务已启动"}`，不阻塞请求。

`check_all()`（`core/proxy_pool.py:78-100`）对**每条**代理（含已停用的）串行发起 `requests.get("https://httpbin.org/ip", timeout=8)`，200 即 `report_success`，否则 `report_fail`，最终返回 `{"ok":n, "fail":m}`——但这个返回值因为跑在后台任务里，**调用方永远拿不到**，前端只能靠轮询 `GET /api/proxies` 观察计数变化。

几个实际问题：串行执行，N 条代理最坏耗时 8N 秒；探测目标 `httpbin.org` 是第三方服务，其不可用会导致全部代理被误判失败（进而把从未成功过的代理批量停用）；无并发保护，重复调用会叠加多个后台任务。

另外 `POST /api/proxies` 与 `/bulk` 的去重是**按原始字符串**比较（`api/proxies.py:33`、`api/proxies.py:50`），未经 `normalize_proxy_url`，所以 `1.2.3.4:8080` 和 `http://1.2.3.4:8080` 会被存成两条独立记录。

##### 5.5 流量统计（`core/traffic.py`）

按 4 个功能块（`register` / `chain` / `pay` / `detect`）分别累计上下行字节，归类通过 `threading.local` 传递，提供 `with traffic.block(BLOCK_PAY):` 上下文管理器（`core/traffic.py:41-59`）。`_extract_bytes()` 同时兼容 curl_cffi 与 httpx 的 Response 对象（`core/traffic.py:62-98`）。

**存在断链**：`traffic.record()` 最终调用 `proxy_pool.record_traffic(block, up, down)`（`core/traffic.py:113`），但 `core/proxy_pool.py` 中**并不存在 `record_traffic` 方法**（全仓 grep 仅此一处引用）。该调用被包在 `try/except Exception: pass` 里（`core/traffic.py:111-115`），因此静默失败——所有流量统计实际上全部丢弃，不报错也不记录。目前唯一的调用点是 `platforms/chatgpt/payment_channels/direct/transport.py:44-46`。

---

#### 六、数据模型

`core/db.py` 用 SQLModel 定义 7 张表，另有 `configs` 表定义在 `core/config_store.py:118`。数据库地址取 `DATABASE_URL`，缺省 `sqlite:///account_manager.db`（`core/db.py:18`）。

##### 6.1 `accounts` — `AccountModel`（`core/db.py:22-43`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | 自增 |
| `platform` | str, index | 平台标识，与注册表插件名对应 |
| `email` | str, index | 账号邮箱；与 platform 组合构成业务唯一键（**未加数据库唯一约束**） |
| `password` | str | **明文存储** |
| `user_id` / `region` / `token` | str | 平台侧用户 ID、地区、access_token（明文） |
| `status` | str | `registered` / `invalid` 等，取自 `AccountStatus` 枚举 |
| `trial_end_time` | int | 试用到期时间戳 |
| `cashier_url` | str | 支付链接，同时冗余在 `extra_json` 内 |
| `extra_json` | str | **平台自定义字段的 JSON 逃生舱**，存 refresh_token、cookies、探测结果等 |
| `created_at` / `updated_at` | datetime | UTC |

`get_extra()` / `set_extra()` 是 JSON 读写辅助（`core/db.py:39-43`）。`save_account()`（`core/db.py:145-180`）按 `(platform, email)` 做 upsert。

**`extra_json` 的代价**：Plus 试用状态等信息存在 JSON 里，SQL 筛不动。`api/accounts.py:98-108` 明确注释了这点——带 `plus_status` 筛选时必须把整个结果集读进内存再过滤，分页也是在 Python 里切片（`api/accounts.py:110-111`），数据量增长后会成为瓶颈。

##### 6.2 `task_logs` — `TaskLog`（`core/db.py:46-55`）

| 字段 | 说明 |
|---|---|
| `id` | PK |
| `platform` / `email` | 归属 |
| `status` | `success` \| `failed` |
| `error` | 失败原因文本 |
| `detail_json` | 结构化详情 |
| `created_at` | UTC |

单条账号级别的历史结果记录，无索引。

##### 6.3 `task_runs` — `TaskRunModel`（`core/db.py:58-77`）

| 字段 | 说明 |
|---|---|
| `id` | **str PK**（形如 `payment_link_1736...`，由调用方生成，非自增） |
| `platform` / `source` / `status` | 均带 index；`source` 区分 manual/payment 等 |
| `total` / `progress` / `success` / `registered` / `skipped` | 进度计数，`progress` 是 `"3/10"` 形式的字符串 |
| `error` | 整体错误 |
| `meta_json` / `logs_json` / `errors_json` / `cashier_urls_json` / `control_json` | 5 个 JSON 列，分别存元数据、日志数组、错误数组、产出链接、控制指令（停止/跳过） |
| `created_at` / `updated_at` | 均带 index |

任务运行态的持久化快照。日志以 JSON 数组整列覆写，追加一条日志即重写全列，高频任务下写放大明显。

##### 6.4 `outlook_accounts` — `OutlookAccountModel`（`core/db.py:80-94`）

| 字段 | 说明 |
|---|---|
| `email` | index + **unique** |
| `password` / `client_id` / `refresh_token` | 微软 OAuth 凭据，**明文** |
| `account_type` | `microsoft_oauth` 或 MailAPI 类型 |
| `mailapi_url` | MailAPI URL 轮询取码地址 |
| `enabled` | 是否参与调度 |
| `status` | index，`available` / `used` |
| `created_at` / `updated_at` / `last_used` | 时间戳 |

##### 6.5 `icloud_accounts` — `ICloudAccountModel`（`core/db.py:97-112`）

| 字段 | 说明 |
|---|---|
| `email` | index + unique |
| `display_name` / `region` | 展示名、地区（默认 `global`） |
| `status` / `enabled` | 可用性 |
| `credentials_cipher` | **AES-256-GCM 密文**，含 Web Session Cookie 与 IMAP 凭据 |
| `sync_error` / `last_sync_at` | 最近一次同步的结果 |

全库唯一采用加密存储的凭据字段。

##### 6.6 `icloud_aliases` — `ICloudAliasModel`（`core/db.py:115-130`）

| 字段 | 说明 |
|---|---|
| `account_id` | index，FK → `icloud_accounts.id` |
| `address` | index + unique，Hide My Email 地址 |
| `label` / `note` / `status` / `provider_id` | 标签、备注、状态、Apple 侧 ID |
| `share_token` | index，`secrets.token_urlsafe(16)` 默认值，免登录页的唯一凭证 |

##### 6.7 `proxies` — `ProxyModel`（`core/db.py:133-142`）

| 字段 | 说明 |
|---|---|
| `url` | **unique**，按原始字符串约束（非规范化后） |
| `region` | 可选地区标签，用于定向取代理 |
| `success_count` / `fail_count` | 累计计数，驱动排序与停用判定 |
| `is_active` | 启用态 |
| `last_checked` | 最近探测时间 |

##### 6.8 `configs` — `ConfigItem`（`core/config_store.py:118-121`）

`key` 为主键，`value` 为字符串，全明文。

##### 6.9 `init_db` 与手写迁移（`core/db.py:247-250`）

```python
def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_outlook_accounts_schema()
    _migrate_icloud_aliases_schema()
```

项目**没有引入 Alembic**，靠两个手写函数补列。关键点是 `create_all` 只会创建不存在的表，**不会给已存在的表加列**——所以每次给模型加字段，老库都需要一段对应的 `ALTER TABLE` 补丁。两个函数都会先 `PRAGMA table_info` 探测现有列，且开头判断后端是否为 SQLite，非 SQLite 直接跳过。

**`_migrate_outlook_accounts_schema()`（`core/db.py:183-217`）** 补 3 列并做数据回填：

| 补的列 | 默认值 | 为什么需要 |
|---|---|---|
| `account_type` | `'microsoft_oauth'` | 早期只支持 OAuth 一种接入方式，后来新增 MailAPI URL 轮询，需要字段区分 |
| `mailapi_url` | `''` | 配合上一列，存 MailAPI 轮询地址 |
| `status` | `'available'` | 早期没有"该邮箱是否已被用于注册"的概念，新增后需要区分可用/已用 |

数据回填部分（`core/db.py:203-217`）：把 NULL/空串统一成默认值；最后一条 UPDATE 用 `EXISTS` 子查询关联 `accounts` 表，把**已经在 accounts 里出现过（忽略大小写）的邮箱**批量标记为 `used`——即根据历史数据反推出 `status` 的正确值，而不是让所有老数据都停留在 `available` 被重复使用。

**`_migrate_icloud_aliases_schema()`（`core/db.py:220-244`）** 补 `share_token` 列，分两步：

1. `ALTER TABLE ... ADD COLUMN share_token TEXT DEFAULT ''`（`core/db.py:227-229`）
2. **逐行**补随机值（`core/db.py:233-244`）

第二步不能用一条 UPDATE 完成——代码注释明确指出每行必须是**不同的**随机值，SQL 的 DEFAULT 只能给统一常量。于是先 `select` 出所有 `share_token` 为 NULL 或空串的行，在 Python 里逐行调 `new_alias_share_token()` 赋值再统一 commit。若所有老 alias 共用同一个 token，免登录链接的隔离性就完全失效了。

注意这个函数的第二步**在 SQLite 判断之外**（不带 backend 条件），所以任何后端都会执行补值逻辑。

---

#### 七、设计要点与潜在问题

以下按影响面排列，均为代码事实陈述。

##### 7.1 CORS 配置为通配（`main.py:109-114`）

```python
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
```

三项全通配。由于未设 `allow_credentials=True`，浏览器不会随跨域请求自动携带 Cookie，且本项目用 Bearer token 而非 Cookie 鉴权，所以经典 CSRF 路径不成立。但任意网站的 JS 都可以向该实例发起跨域请求并读取响应——在未设置面板密码（默认状态）时，这等于把 `GET /api/config`（含全部明文 API Key）暴露给用户浏览过的任何页面。

##### 7.2 中间件顺序

`auth_middleware` 用 `@app.middleware("http")` 注册（`main.py:84`），CORS 在其后 `add_middleware`（`main.py:109`）。Starlette 的中间件栈是后注册者在外层，因此 CORS 先于鉴权执行——401 响应也能正确带上 CORS 头，这个顺序是对的。

##### 7.3 无任何限流

全仓库没有 rate limit 中间件或依赖。直接受影响的面：

- `POST /api/auth/login` 可无限次爆破密码（配合无 salt 的 SHA-256 与 6 位最小长度，风险叠加）
- `POST /api/auth/verify-totp` 可在 5 分钟窗口内无限次尝试 6 位验证码（100 万空间，接受 ±1 时间窗实际为 300 万分之 3）
- `GET /m/{share_token}` 可无限枚举（128 位空间下不现实，但无任何监测）
- `POST /api/proxies/check` 可重复触发，堆积后台任务

##### 7.4 SQLite 并发

`create_engine(DATABASE_URL)` 未传任何参数（`core/db.py:19`）——无 `connect_args={"check_same_thread": False}`、无连接池配置、未启用 WAL 模式。

而系统存在大量并发写入场景：FastAPI 的 `BackgroundTasks`（账号检测、代理检测）、`core/scheduler` 定时任务、`api/tasks.py` 的多线程批量注册（`ThreadPoolExecutor`，`api/tasks.py:695`）。其中注册任务的 `concurrency` 字段**没有上界校验**（`api/tasks.py:57`，仅默认 1，`max_workers = min(concurrency, count)`），而 `api/payments.py:29` 的同名字段限制为 `le=10`——同一概念两套约束。SQLite 默认 journal 模式下写操作会锁全库，并发写将触发 `database is locked`。`api/tasks.py` 的日志追加是"读全列 → 改 → 整列写回"模式，进一步拉长持锁时间。生产部署前应至少启用 WAL 并设置 `busy_timeout`。

##### 7.5 明文凭据面

| 位置 | 内容 |
|---|---|
| `accounts.password` / `token` / `extra_json` | 账号密码、access/refresh token、cookies |
| `outlook_accounts.password` / `refresh_token` | 微软 OAuth 凭据 |
| `configs.value` | 全部第三方 API Key、`auth_jwt_secret`、`auth_totp_secret` |
| `cards.db` 的 `cards` 表 | 卡号、CVC、有效期（`platforms/chatgpt/payment_channels/direct/card_store.py:46-48`） |
| `GET /api/accounts/export` | CSV 导出直接包含 password 列（`api/accounts.py:157-162`） |

`secret_box` 已提供可用的 AES-GCM 能力，但只接了 iCloud 一处。特别地，`auth_jwt_secret` 与 `auth_totp_secret` 明文存在同一个 SQLite 文件里——拿到 db 文件即可自行签发任意 JWT 并绕过 2FA，鉴权体系的安全边界实际等同于该文件的文件系统权限。

##### 7.6 死代码与断链

| 问题 | 位置 |
|---|---|
| `api/chatgpt.py` 全部 6 个端点从未挂载 | `main.py:12-26` 无该模块 import |
| `traffic.record()` 调用不存在的 `proxy_pool.record_traffic` | `core/traffic.py:113` vs `core/proxy_pool.py` |
| `POST /api/auth/logout` 无实际效果 | `api/auth.py:238-240` |
| `check_all()` 的返回值不可达（跑在 BackgroundTask 里） | `api/proxies.py:102` |

##### 7.7 路由顺序依赖

`api/accounts.py` 中 `/stats`、`/export`、`/export-formats` 等静态路径均声明在 `/{account_id}` **之前**（`api/accounts.py:130-282` vs `api/accounts.py:284`），顺序正确。`api/actions.py` 的 `/{platform}/{action_id}/batch`（3 段）声明在 `/{platform}/{account_id}/{action_id}`（3 段）之前（`api/actions.py:266` vs `api/actions.py:352`）——两者段数相同，靠声明顺序区分，因此**任何 `action_id` 为字面量 `batch` 的操作都会被前者截获**。这属于隐式约束，新增 action 时需留意。

##### 7.8 其他

- **SPA fallback 吞掉未匹配路由**：`@app.get("/{full_path:path}")`（`main.py:152-154`）返回 index.html，静态目录存在时任何拼错的 `/api` 路径会得到 200 + HTML 而非 404（因为路由已在前面注册，实际只影响未注册前缀）。
- **`POST /api/config/applemail/import` 接受任意 `pool_dir`**（`api/config.py:226-252`），路径合法性校验位于 `services/mail_imports` 侧，API 层未做限制。
- **`api/contribution.py` 的多候选路径重试**（`api/contribution.py:14-29`）：对每个操作准备 2~4 个候选 endpoint 依次尝试，用于兼容贡献服务器的不同版本；失败时把全部 attempts 连同各自 status_code 返回给前端，便于排障但也暴露了上游拓扑。
- **`api/accounts.py:277-281` 的 `background_tasks` 默认值为 `None`**：`check_all_accounts` 签名写成 `background_tasks: BackgroundTasks = None`，FastAPI 会正常注入，但该默认值使静态检查无法发现误用。
- **错误处理不一致**：`api/actions.py:374-375` 的 `execute_action` 把异常转成 `{"ok": False, "error": ...}` 并返回 **200**，而同文件其他端点用 HTTPException 返回 4xx/5xx。前端需按端点分别判断成功与否。

---

## 三、任务编排与调度

面向新接手项目的开发者。本文覆盖「批量注册任务」从 HTTP 创建到线程池执行、协作式中断、重试、持久化、日志回传，以及执行器/验证码选型与定时调度。


#### 子系统组成一览

| 模块 | 职责 |
| --- | --- |
| `api/tasks.py` | 任务编排核心:请求模型、任务创建、线程池并发、单账号 attempt、重试轮次、日志与持久化、控制接口、SSE 日志流 |
| `core/task_runtime.py` | 运行时原语:`RegisterTaskControl`(停止/跳过)、`RegisterTaskStore`(内存快照)、`AttemptResult`、中断异常体系 |
| `core/db.py` | 持久化表 `TaskRunModel`(task_runs) 与 `TaskLog`(task_logs) |
| `core/base_platform.py` | 平台插件抽象基类 + `Account` / `RegisterConfig` / `AccountStatus`,以及执行器/验证码工厂 |
| `core/base_executor.py`、`core/executors/*` | 执行器抽象与 protocol / playwright 两种实现 |
| `core/base_captcha.py`、`services/solver_manager.py`、`services/turnstile_solver/*` | 验证码求解器抽象、本地 Turnstile Solver 进程管理与 HTTP 服务 |
| `core/scheduler.py` | 后台周期任务:trial 到期、CPA 凭证维护 |

#### 关键常量与默认值

| 常量 | 值 | 位置 | 含义 |
| --- | --- | --- | --- |
| `MAX_FINISHED_TASKS` | 200 | `api/tasks.py:22` | 内存里最多保留多少条已结束任务 |
| `CLEANUP_THRESHOLD` | 250 | `api/tasks.py:23` | 记录总数超过这个值才触发淘汰 |
| `MAX_REGISTER_RETRY_TIMES` | 10 | `api/tasks.py:32` | 重试轮数上限(表单值会被夹到 `[0,10]`) |
| `DEFAULT_REGISTER_RETRY_TIMES` | 1 | `api/tasks.py:33` | 未指定时的默认重试轮数 |
| `MAX_DEAD_END_ROUNDS` | 2 | `api/tasks.py:40` | 连续出现「不可重试失败」几轮后放弃剩余轮次 |
| `LUCKMAIL_PROJECT_CODES` | `{"chatgpt": "openai"}` | `api/tasks.py:25` | LuckMail 项目编码与本项目平台标识的映射 |

---

### 一、批量注册任务的完整生命周期

#### 编号步骤

1. **HTTP 入口**:前端 `POST /tasks/register`,请求体解析为 `RegisterTaskRequest`(`api/tasks.py:52-65`),字段含 `platform / count / concurrency / register_retry_times / register_delay_seconds / proxy / executor_type / captcha_solver / extra`。
2. **请求归一化**:`_prepare_register_request()`(`api/tasks.py:333`)深拷贝 `extra`、把 `platform` 转小写去空格、用 `is_platform_enabled()` 校验平台是否下线(下线直接 400),并在 `mail_provider == "luckmail"` 时补 `extra["luckmail_project_code"]`。
3. **生成任务 ID 并建记录**:`enqueue_register_task()`(`api/tasks.py:369`)用毫秒时间戳生成 `task_id = f"task_{int(time.time()*1000)}"`,调用 `_create_task_record()`(`api/tasks.py:356`)在内存 `RegisterTaskStore` 里 `create()`,随后 `_persist_task_snapshot()` 立即落一行 `task_runs`。此时 `status="pending"`、`progress="0/{count}"`。
4. **交给后台执行**:若调用方传了 FastAPI `BackgroundTasks` 就 `add_task(_run_register, ...)`;否则直接起一个 `daemon=True` 的 `threading.Thread`(`api/tasks.py:379-385`)。接口立刻返回 `task_id`,不阻塞。
5. **任务主体启动**:`_run_register()`(`api/tasks.py:438`)取出 `control = _task_store.control_for(task_id)`,`mark_running()` 把状态推到 `running` 并持久化。初始化 `success / skipped / errors` 累加器和启动闸门 `start_gate_lock / next_start_time`(`api/tasks.py:448-452`)。
6. **共享上下文预计算**(`api/tasks.py:466-504`):
   - `PlatformCls = get(req.platform)` 拿到平台插件类;
   - `_base_extra` = `config_store.get_all()` 的副本 + 请求 `extra` 中的非空覆盖项,**所有线程共享只读副本**,避免每线程重复读配置;
   - 当没有固定代理且 `count > 1` 时,一次性把 `ProxyModel.is_active == True` 的代理 URL 批量预取到 `_prefetched_proxies`,`_get_proxy()` 从中随机取,取不到再退回 `proxy_pool.get_next()`;
   - `_build_mailbox(proxy)` 按 `_base_extra["mail_provider"]`(默认 `luckmail`)创建邮箱客户端。
7. **算出总轮次**:`retry_times = normalize_register_retry_times(req.register_retry_times)`,`total_rounds = 1 + retry_times`(`api/tasks.py:506-513`);`total_rounds > 1` 时写一条说明日志,明确「每轮都是全新的代理/邮箱/号码/会话」。
8. **每个序号一个交付单元**:`_do_one(i)`(`api/tasks.py:518`)是提交给线程池的单位。它内部循环 `total_rounds` 轮,每轮调用一次单账号 attempt;失败且可重试就重开下一轮,成功/跳过/停止立即收敛。连续 `MAX_DEAD_END_ROUNDS` 轮拿到「不可重试失败」就提前收手。
9. **单轮 attempt 内部**(见下节「单轮 attempt 细节」):启动延迟闸门 → `control.start_attempt()` 领 `attempt_id` → 领代理 → 领邮箱 → 构造 `RegisterConfig` → 实例化平台插件 → `plugin.register()` → 成功则落库 `save_account()` + 触发自动上传 → `finish_attempt()`。
10. **计数与日志**:每轮结果转成 `AttemptResult`;主循环据 `outcome` 累加 `success / skipped / errors`,`set_progress()` 刷新 `已完成/总数`,`update_counters()` 刷新成功数与已注册数,每次变更都 `_persist_task_snapshot()` 写回 `task_runs`。
11. **任务收尾**:全部序号跑完(或收到停止)后调用 `_task_store.finish()`(`core/task_runtime.py:finish`)写终态 `status ∈ {done, failed, stopped}`、最终 `success / registered / skipped / errors / error`,再持久化一次,并调用 `_task_store.cleanup()` 触发淘汰。

#### mermaid 流程图

```mermaid
flowchart TD
    A["POST /tasks/register<br/>RegisterTaskRequest"] --> B["_prepare_register_request<br/>平台校验/extra 归一化"]
    B -->|平台已下线| B1["HTTP 400"]
    B --> C["enqueue_register_task<br/>task_id = task_{ms}"]
    C --> D["_create_task_record<br/>Store.create + upsert task_runs<br/>status=pending"]
    D --> E{"传入 BackgroundTasks?"}
    E -->|是| E1["background_tasks.add_task"]
    E -->|否| E2["threading.Thread(daemon=True)"]
    E1 --> F["_run_register(task_id, req)"]
    E2 --> F
    F --> G["mark_running + 持久化<br/>预取 _base_extra / 代理池"]
    G --> H["total_rounds = 1 + retry_times"]
    H --> I["ThreadPoolExecutor(max_workers=concurrency)<br/>提交 count 个 _do_one(i)"]
    I --> J["_do_one: 轮次循环"]
    J --> K["启动延迟闸门<br/>next_start_time 串行推进"]
    K --> L["control.start_attempt() -> attempt_id"]
    L --> M["领代理 _get_proxy()"]
    M --> N["领邮箱 _build_mailbox().get_email()"]
    N --> O["构造 RegisterConfig<br/>executor_type / captcha_solver / proxy"]
    O --> P["PlatformCls(config).register()"]
    P -->|成功| Q["save_account 落库<br/>_auto_upload_integrations"]
    P -->|StopTaskRequested| R["AttemptResult.stopped"]
    P -->|SkipCurrentAttemptRequested| S["AttemptResult.skipped"]
    P -->|NonRetryableRegisterError| T["AttemptResult.failed(retryable=False)"]
    P -->|其它异常| U["AttemptResult.failed(retryable=True)"]
    Q --> V["AttemptResult.success"]
    R --> W["control.finish_attempt(attempt_id)"]
    S --> W
    T --> W
    U --> W
    V --> W
    W --> X{"结果判定"}
    X -->|success/skipped/stopped| Y["结束该序号"]
    X -->|failed 且 retryable 且有余轮| J
    X -->|dead-end 连续 >= 2 轮| Y
    Y --> Z["累加 success/skipped/errors<br/>set_progress + update_counters<br/>_persist_task_snapshot"]
    Z --> AA["_task_store.finish(status)<br/>done / failed / stopped"]
    AA --> AB["持久化 + cleanup 淘汰"]
```

#### 单轮 attempt 细节(`_do_one_round`,`api/tasks.py:558-691`)

| 顺序 | 动作 | 代码位置 | 说明 |
| --- | --- | --- | --- |
| 1 | `control.checkpoint()` | `api/tasks.py:565` | 还没领 attempt_id 时先看一眼是否已被停止/有待消费的跳过 |
| 2 | `attempt_id = control.start_attempt()` | `api/tasks.py:566` | 领一个单调递增的 attempt 编号,加入活跃集合 |
| 3 | `control.checkpoint(attempt_id=...)` | `api/tasks.py:567` | 领号后立刻再检查一次 |
| 4 | 领代理 | `api/tasks.py:568` | `normalize_proxy_url(_get_proxy())` |
| 5 | 启动延迟闸门 | `api/tasks.py:569-583` | 见「并发与节流」 |
| 6 | 构造 `RegisterConfig` | `api/tasks.py:588-593` | 带 `executor_type / captcha_solver / proxy / extra` |
| 7 | 创建邮箱 + 实例化平台插件 | `api/tasks.py:594-601` | 注入 `_task_attempt_token`、`_log_fn`、`bind_task_control(control)`,并把同样的 token/log 透传给 `mailbox` |
| 8 | 刷进度 + 写日志 | `api/tasks.py:602-606` | `set_progress(f"{i+1}/{count}")`,记录序号与代理 |
| 9 | `plugin.register(email, password)` | `api/tasks.py:607-610` | 真正的注册流程(邮箱领取在插件内部或由 mailbox 完成) |
| 10 | 邮箱域名策略校验 | `api/tasks.py:613-629` | 仅 `mail_provider == "cfworker"` 且 `account.email` 含 `@` 时,调 `validate_email_domain_policy` |
| 11 | 补 `account.extra` | `api/tasks.py:630-656` | 回写 `mail_provider`;LuckMail + chatgpt 时还补 `mailbox_token / luckmail_project_code / luckmail_email_type / luckmail_domain / luckmail_base_url` |
| 12 | 落库 | `api/tasks.py:657` | `save_account(account)` 返回 `saved_account` |
| 13 | 代理打分 | `api/tasks.py:658-659` | 成功 `proxy_pool.report_success`,异常分支 `report_fail`(`api/tasks.py:682-683`) |
| 14 | 成功日志与外部同步 | `api/tasks.py:660-667` | `_save_task_log(status="success")`、`_auto_upload_integrations()`;若 `extra["cashier_url"]` 存在则 `add_cashier_url` |
| 15 | `finally: control.finish_attempt(attempt_id)` | `api/tasks.py:690-691` | 无论何种出口都把 attempt 从活跃集合摘掉 |

**注意**:`_task_attempt_token`(`api/tasks.py:596`)和 `bind_task_control()`(`api/tasks.py:598`)是「跳过当前账号」能穿透到插件深处(邮箱轮询、验证码等待、短信等待)的关键 —— 插件内部长时间等待时会拿这个 token 调 `control.checkpoint(attempt_id=token)`。

---

### 二、并发与节流参数

#### 三个参数的来源与生效点

| 参数 | 请求字段 | 默认 | 读取/生效位置 | 生效方式 |
| --- | --- | --- | --- | --- |
| 注册数量 | `count` | 1 | `api/tasks.py:56`;`_create_task_record` 里作为 `total`(`api/tasks.py:362`);`range(req.count)` 提交任务(`api/tasks.py:698`) | 决定提交给线程池的 `_do_one` 数量,也是进度分母 `{i+1}/{req.count}` |
| 并发数 | `concurrency` | 1 | `api/tasks.py:57`;`max_workers = min(req.concurrency, req.count)`(`api/tasks.py:695`) | 直接作为 `ThreadPoolExecutor(max_workers=...)` |
| 每账号启动延迟 | `register_delay_seconds` | 0 | `api/tasks.py:61`;闸门逻辑 `api/tasks.py:569-583` | 全局串行闸门,把「下一次允许启动的时刻」往后推 |
| 重试轮数 | `register_retry_times` | 1 | `api/tasks.py:60`;`normalize_register_retry_times`(`api/tasks.py:43-49`) | `total_rounds = 1 + retry_times` |

#### 启动延迟闸门的语义

延迟不是「每个线程各睡 N 秒」,而是**全批共享一条时间线**:

1. 只有 `req.register_delay_seconds > 0` 才进入闸门(`api/tasks.py:569`)。
2. 抢 `start_gate_lock`(`api/tasks.py:570`),这把锁保证同一时刻只有一个 attempt 在算自己的开跑时间。
3. `wait_seconds = max(0.0, next_start_time - now)`(`api/tasks.py:573`);需要等就写一条「启动前延迟 X 秒」日志,再 `_sleep_with_control()` 分片睡。
4. 醒来后 `next_start_time = time.time() + register_delay_seconds`(`api/tasks.py:583`),把闸门往后推给下一个 attempt。

效果:并发数决定「同时最多几个在跑」,启动延迟决定「相邻两个开跑至少间隔多久」,两者叠加成一个匀速放行的令牌闸门。

#### 边界处理

| 情形 | 行为 | 位置 |
| --- | --- | --- |
| `concurrency > count` | `min()` 收敛,不会开出多余线程 | `api/tasks.py:695` |
| `register_delay_seconds <= 0` 或 None | `max(float(x or 0), 0.0)` 归零,完全跳过闸门 | `api/tasks.py:459`、`api/tasks.py:569` |
| `register_retry_times` 非数字/None | `normalize_register_retry_times` 返回默认 1 | `api/tasks.py:43-48` |
| `register_retry_times` 越界 | 夹到 `[0, 10]`(`max(0, min(parsed, 10))`) | `api/tasks.py:49` |
| 批量账号任务(补 RT / 绑 2FA)的并发 | `max(1, min(int(concurrency or 1), max(total, 1)))`,比注册任务多一层「至少 1」保护 | `api/tasks.py:878` |
| 延迟期间被停止/跳过 | `_sleep_with_control` 每 0.25 秒 checkpoint 一次,中断能在延迟途中生效 | `api/tasks.py:454-464` |
| 无固定代理且 `count > 1` | 批量预取 `is_active` 代理,随机取用,减少 DB 往返 | `api/tasks.py:480-497` |

补 RT / 绑 2FA 任务默认 `concurrency=1`、`delay_seconds=5`(`api/tasks.py:86-87`、`api/tasks.py:105-106`),注释明确是为了避免连续打 OpenAI 授权/登录链撞风控。

---

### 三、任务控制语义:停止整个任务 vs 跳过当前账号

#### `RegisterTaskControl` 的内部状态(`core/task_runtime.py:73-141`)

| 字段 | 含义 |
| --- | --- |
| `_stop_requested` | 全局停止标志,一旦置位永久有效 |
| `_pending_skip_requests` | 「此刻没有活跃 attempt」时收到的跳过请求计数,留给下一个进 checkpoint 的 attempt 消费 |
| `_next_attempt_id` | 单调递增的 attempt 号发号器,从 1 开始 |
| `_active_attempt_ids` | 当前在跑的 attempt 号集合 |
| `_skip_active_attempt_ids` | 被定向标记为「要跳过」的活跃 attempt 号集合 |

#### 两种控制的区别

| 维度 | 停止整个任务 | 跳过当前账号 |
| --- | --- | --- |
| 入口方法 | `request_stop()`(`core/task_runtime.py:82`) | `request_skip_current()`(`core/task_runtime.py:86`) |
| 状态变化 | `_stop_requested = True` | 有活跃 attempt → 把**所有**活跃 id 加入 `_skip_active_attempt_ids`;否则 `_pending_skip_requests += 1` |
| 抛出异常 | `StopTaskRequested` | `SkipCurrentAttemptRequested` |
| 是否可消费 | 不可消费,checkpoint 每次都抛 | 一次性,消费即从集合移除/计数减 1 |
| 结果 outcome | `AttemptResult.stopped` | `AttemptResult.skipped` |
| 对剩余序号 | 主循环 `pending.cancel()` 取消未开始的 future,`_do_one` 轮次循环也提前 break | 只影响当前 attempt,后续序号照跑 |
| 最终任务状态 | `stopped` | 不影响,计入 `skipped` 计数 |

#### `checkpoint()` 的判定顺序(`core/task_runtime.py:108-125`)

```python
if self._stop_requested:
    raise StopTaskRequested()
if consume_skip:
    if attempt_id is not None and attempt_id in self._skip_active_attempt_ids:
        self._skip_active_attempt_ids.discard(attempt_id)
        raise SkipCurrentAttemptRequested()
    if self._pending_skip_requests > 0:
        self._pending_skip_requests -= 1
        raise SkipCurrentAttemptRequested()
```

要点:
1. **停止优先于跳过** —— 已请求停止时永远抛 `StopTaskRequested`,跳过请求不会被误消费。
2. **定向跳过优先于 pending 跳过** —— 带 `attempt_id` 的定向标记先匹配,避免 A 线程把本该给 B 的 pending 跳过吃掉。
3. `consume_skip=False` 可以只检查停止而不消费跳过,用于那些「不该被跳过打断」的临界区。
4. `attempt_id=None` 调用时只能消费 `_pending_skip_requests`,不能命中定向跳过。

#### checkpoint 插桩点

| 插桩位置 | 代码位置 | 用途 |
| --- | --- | --- |
| 领 attempt_id 之前 | `api/tasks.py:565` | 排队阶段就能被停掉,不浪费 attempt 号 |
| 领 attempt_id 之后 | `api/tasks.py:567` | 定向跳过的第一个生效点 |
| 进入延迟闸门时 | `api/tasks.py:571` | 拿到锁后立刻检查 |
| 延迟睡眠分片循环内 | `api/tasks.py:461`(`_sleep_with_control`) | 每 0.25 秒一次,长延迟也能秒级响应 |
| 闸门之后、构造插件之前 | `api/tasks.py:584` | 真正开销大的动作前最后一道关 |
| 插件内部(通过 `bind_task_control` + `_task_attempt_token`) | `api/tasks.py:596-601` | 邮箱轮询、验证码等待、短信等待等长阻塞点 |
| 批量账号任务同构插桩 | `api/tasks.py:834`、`api/tasks.py:836-837`、`api/tasks.py:822`、`api/tasks.py:825` | 补 RT / 绑 2FA 复用同一套语义 |

#### 中断异常如何转成 AttemptResult

`_do_one_round` 的 `except` 顺序决定了映射(`api/tasks.py:669-691`):

| 捕获的异常 | 转换结果 | 附带动作 |
| --- | --- | --- |
| `SkipCurrentAttemptRequested` | `AttemptResult.skipped(str(e))` | 写 `[SKIP]` 日志 + 一条 `status="skipped"` 的 `task_logs` |
| `StopTaskRequested` | `AttemptResult.stopped(str(e))` | 写 `[STOP]` 日志(**不**写 task_logs) |
| `Exception`(兜底) | `AttemptResult.failed(msg, retryable=not isinstance(e, NonRetryableRegisterError), email=current_email)` | `proxy_pool.report_fail`、写 `[FAIL]` 日志 |
| `finally` | —— | `control.finish_attempt(attempt_id)` 一定执行 |

异常类继承关系(`core/task_runtime.py:12-27`):`TaskInterruption(RuntimeError)` → `StopTaskRequested` / `SkipCurrentAttemptRequested`。因为两者都是 `RuntimeError` 子类,`except` 子句的**先后顺序**必须在通用 `Exception` 之前,否则中断会被误判成失败并触发重试。

`NonRetryableRegisterError`(`core/task_runtime.py:29`)不属于 `TaskInterruption`,它走通用 `Exception` 分支,只是把 `retryable` 置为 `False`。

---

### 四、重试策略

#### 轮次循环(`_do_one`,`api/tasks.py:518-556`)

1. 先跑第 1 轮:`result = _do_one_round(i, 1, total_rounds)`(`api/tasks.py:524`)。
2. `dead_end_rounds` 初始化:第 1 轮就是 dead-end 则记 1(`api/tasks.py:525`)。
3. 进入 `for round_no in range(2, total_rounds + 1)` 循环(`api/tasks.py:526`),每轮开头做三次早退判断:
   - `result.outcome != FAILED` → break(成功、跳过、停止都不重试);
   - `control.is_stop_requested()` → break;
   - `dead_end_rounds >= MAX_DEAD_END_ROUNDS`(=2)→ 写「剩下 N 轮不再重开」日志后 break(`api/tasks.py:531-539`)。
4. 否则写 `[RETRY] 第 X 个账号第 n/N 轮失败,开始第 n+1/N 轮重试(全新会话)` 日志,再跑一轮(`api/tasks.py:540-545`)。
5. `dead_end_rounds` 的更新是**连续计数**:`dead_end_rounds + 1 if _is_dead_end(result) else 0`(`api/tasks.py:546`),中间只要出现一次可重试失败就归零。
6. 全部轮次跑完后仍是 `FAILED`,才写一条 `status="failed"` 的 `task_logs`(`api/tasks.py:547-555`)。这样重试成功的序号不会在历史里留下失败记录 —— 「一个序号一条结果」。

#### 可重试 vs 不可重试

| 判定 | 规则 | 位置 |
| --- | --- | --- |
| `retryable` | `not isinstance(e, NonRetryableRegisterError)` | `api/tasks.py:687` |
| `_is_dead_end(result)` | `outcome == FAILED and not retryable` | `api/tasks.py:515-516` |
| `AttemptResult.retryable` 默认值 | `True` | `core/task_runtime.py:50` |

`NonRetryableRegisterError` 的语义(`core/task_runtime.py:29-35`,docstring 说明):**重开一轮也是同样结局的失败**。典型来源是手机注册场景「账号已经在平台建好了、但接码平台一条短信都没收到」—— 号源被静默拦码,再开一轮只会用同样的号源再造一个没人认领的孤号,还多花一次租号钱。由平台插件在这类判定成立时主动 `raise`。

`MAX_DEAD_END_ROUNDS = 2` 的设计取舍(`api/tasks.py:34-40` 注释):以前 dead-end 是一票否决,第一轮撞上就把用户填的重试轮数整个作废,用户感受是「我填了没反应」。但一轮只用了一个号,凭它断定整个号源都被拦码证据太薄,所以改成**连续两轮**同样结局才收手。

#### 各类结果对重试的影响

| outcome | retryable | 是否继续重试 | 计入 |
| --- | --- | --- | --- |
| `SUCCESS` | — | 否(break) | `success` |
| `SKIPPED` | — | 否(break) | `skipped` |
| `STOPPED` | — | 否(break,并置 `stopped=True`) | 触发全局停止 |
| `FAILED` + `retryable=True` | 是 | 是,直到轮次用尽 | 最终一次的 `message` 进 `errors` |
| `FAILED` + `retryable=False` | 否 | 连续 2 轮后停止重试 | 同上 |

#### 重试时身份如何处理

**每一轮都是彻底重开**,不是「接码层换号」。`_do_one` 的 docstring 明确(`api/tasks.py:519-523`):

> 重开的是整条链(新代理、新邮箱/号码、新会话),不是接码层的换号 —— 半路建出来的号已经被占了,拿它死磕只会一直撞同一堵墙。

具体表现:

| 资源 | 重试时的行为 | 位置 |
| --- | --- | --- |
| 代理 | 每轮重新 `_get_proxy()` + `normalize_proxy_url()` | `api/tasks.py:568` |
| 邮箱 | 每轮重新 `_build_mailbox(_proxy)` → 新的 mailbox 实例、新邮箱地址 | `api/tasks.py:594` |
| 手机号 | 由插件在新会话内重新租号 | 插件内部 |
| 平台会话 | 每轮重新 `PlatformCls(config=..., mailbox=...)`,浏览器/HTTP 会话全新 | `api/tasks.py:595` |
| `attempt_id` | 每轮 `start_attempt()` 领新号,旧号 `finish_attempt()` 摘掉 | `api/tasks.py:566`、`api/tasks.py:691` |
| 身份回传 | `AttemptResult.failed(email=current_email)` 把这一轮用的身份带回主循环,供最终失败落 `task_logs` | `core/task_runtime.py:52-53`、`api/tasks.py:688` |

**唯一的例外**:请求里显式指定了 `req.email`(单号定向注册)时,`current_email = req.email or ""`(`api/tasks.py:561`),`register(email=req.email or None)` 每轮都用同一个邮箱。

---

### 五、任务状态持久化

#### 内存 Store 与 DB 表的分工

| 关注点 | 内存 `RegisterTaskStore` | DB `task_runs`(`TaskRunModel`) | DB `task_logs`(`TaskLog`) |
| --- | --- | --- | --- |
| 用途 | 运行时唯一可写状态源 + 控制器载体 | 快照镜像,供列表/详情/重启后查询 | 每个账号一条最终结果的业务历史 |
| 主键 | `task_id` → `RegisterTaskRecord` | `id`(= task_id) | 自增 `id` |
| 是否持有 `RegisterTaskControl` | **是**(只在内存) | 否,只存 `control_json` 快照 | 否 |
| 写入时机 | 每个状态变更 | 每次 `_persist_task_snapshot()`(几乎每次内存变更后) | `_save_task_log()` 在成功/跳过/最终失败时 |
| 写入方式 | 加锁直改 | `_upsert_task_run()` 全量覆盖(`api/tasks.py:195-241`) | 独立 daemon 线程 fire-and-forget(`api/tasks.py:404-419`) |
| 进程重启后 | 全部丢失 | 保留 | 保留 |

关键约束:**控制操作只能作用于内存中还存在的任务**。`stop` / `skip-current` 接口先 `_ensure_task_mutable()`,再显式判断 `_task_store.exists(task_id)`,不存在直接 409「任务已结束或服务已重启,无法停止/跳过」(`api/tasks.py:1170-1189`)。

#### 快照字段含义(`RegisterTaskRecord.to_dict`,`core/task_runtime.py:161-186`;归一化见 `api/tasks.py:149-168`)

| 字段 | 含义 |
| --- | --- |
| `id` | 任务 ID,`task_{ms}` / `backfill_rt_{ms}` / `bind_2fa_{ms}` |
| `status` | `pending` → `running` → `done` / `failed` / `stopped` |
| `platform` | 目标平台标识(小写) |
| `source` | 任务来源,`manual` 为默认,批量任务为 `backfill_rt` / `bind_2fa`,自动任务可传其它值 |
| `meta` | 任务参数与自定义元数据(`kind`、`concurrency`、`delay_seconds`、`missing_ids` 等) |
| `total` | 目标总数(注册数量 / 账号数) |
| `progress` | 字符串 `"已开始序号/总数"`,如 `"3/10"` |
| `logs` | 全量日志行数组,每行 `[HH:MM:SS] 内容` |
| `success` | 成功数 |
| `registered` | 已出结果数 = `success + skipped + len(errors)`;`finish()` 未显式传值时按同式兜底(`core/task_runtime.py:318-321`) |
| `skipped` | 跳过数 |
| `errors` | 失败消息数组(每个序号最终失败才进一条) |
| `control` | `RegisterTaskControl.snapshot()`:`stop_requested` / `pending_skip_requests` / `active_attempts` / `targeted_skip_attempts` |
| `cashier_urls` | 注册过程中产出的升级/收银台链接(仅非空时出现在 `to_dict`) |
| `error` | 任务级致命错误消息(仅非空时出现) |
| `created_at` / `updated_at` | epoch 秒;DB 里存 `datetime`,读出时 `_to_epoch_seconds` 转回 |

`_to_datetime()`(`api/tasks.py:137-146`)兼容毫秒时间戳(`> 1e12` 时自动 `/1000`),非法值回落到当前时间。

#### cleanup 淘汰策略(`core/task_runtime.py:369-384`)

三个条件依次判定,任一不满足就直接返回:

1. `len(self._records) <= cleanup_threshold`(250)→ 不动。
2. 收集 `status in ("done", "failed", "stopped")` 的记录;`len(finished) <= max_finished_tasks`(200)→ 不动。
3. 按 `created_at` 升序排序,删掉最旧的 `len(finished) - 200` 条。

要点:**只淘汰已结束任务,`pending` / `running` 永不被淘汰**;淘汰只作用于内存,`task_runs` 行仍在 DB 里。调用点是每个任务收尾处 `_task_store.cleanup()`(`api/tasks.py:739`、`api/tasks.py:759`、`api/tasks.py:924`、`api/tasks.py:939`)。

#### 进程重启后的行为

`_finalize_orphan_tasks()`(`api/tasks.py:278-301`)负责收拾「DB 说还在跑、内存里查不到」的孤儿任务:

1. 查 `task_runs` 里 `status in ("pending", "running")` 的行。
2. 逐行判断 `_task_store.exists(row.id)`,内存里还在的跳过(说明是当前进程的活任务)。
3. 内存里没有的:`status = "stopped"`、`error` 兜底为「任务因服务重启中断」、追加一条 `[SYSTEM] 任务因服务重启中断,已自动标记为已停止` 日志(去重),更新 `updated_at`。

调用点覆盖所有查询与控制入口:`skip-current`(`api/tasks.py:1172`)、`stop`(`api/tasks.py:1183`)、`stream_logs`(`api/tasks.py:1240`)、`GET /{task_id}`(`api/tasks.py:1284`)、`GET /tasks`(`api/tasks.py:1290`)、`DELETE /{task_id}`(`api/tasks.py:1297`)。

其它重启相关行为:
- `GET /tasks` 明确「以 DB 为主返回,避免进程重启导致列表丢失」(`api/tasks.py:1288-1292`)。
- `GET /{task_id}` 走 `_get_task_snapshot()`:内存里有就先持久化再从 DB 读,DB 读不到才回落到内存快照(`api/tasks.py:321-330`)。
- `DELETE /{task_id}` 拒绝删除 `pending` / `running` 任务,要求先停止(`api/tasks.py:1300-1301`)。
- 中断的任务**不会自动续跑**,需要重新发起。

---

### 六、实时日志链路

#### 后端缓冲

| 环节 | 实现 | 位置 |
| --- | --- | --- |
| 写一条日志 | `_log(task_id, msg)`:打时间戳 → `Store.append_log` → `_persist_task_snapshot` → `print(entry)` | `api/tasks.py:395-401` |
| 内存缓冲 | `RegisterTaskRecord.logs` 是无上限 `list[str]`,加锁 append 并刷 `updated_at` | `core/task_runtime.py:151`、`core/task_runtime.py:246-252` |
| DB 缓冲 | 整个 `logs` 数组 JSON 序列化后覆盖写 `task_runs.logs_json` | `api/tasks.py:233` |
| 读取接口 | `Store.log_state(task_id)` 返回 `(logs 副本, status)` | `core/task_runtime.py:363-366` |

**日志条目格式**:`f"[{time.strftime('%H:%M:%S')}] {msg}"`,例如 `[14:23:05] 开始注册第 3/10 个账号(第 2/3 轮)`。约定前缀:

| 前缀 | 含义 |
| --- | --- |
| `[OK]` | 注册/操作成功 |
| `[FAIL]` | 本轮失败 |
| `[SKIP]` | 跳过当前账号 |
| `[STOP]` | 收到停止 |
| `[RETRY]` | 开始下一轮重试 / 放弃剩余轮次 |
| `[ERROR]` | 线程级异常 |
| `[SYSTEM]` | 系统注入(重启中断标记) |
| 两空格缩进 | 子步骤明细(自动上传结果、插件内部日志、TOTP 密钥等) |

`_log` 里的 `print(entry)` 让日志同时进服务端 stdout,便于 docker logs 排查。

#### 前端拉取方式:SSE

确认结论:**Server-Sent Events**,不是轮询也不是 WebSocket。

- 端点:`GET /tasks/{task_id}/logs/stream?since=N`(`api/tasks.py:1237-1279`)。
- 返回 `StreamingResponse`,`media_type="text/event-stream"`,响应头带 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`(后者关掉 Nginx 缓冲,否则 SSE 会被攒住)(`api/tasks.py:1272-1279`)。
- 断点续传:`since` 参数是「客户端已收到的行数」,生成器 `sent = since` 起步,只推 `logs[sent:]`(`api/tasks.py:1244`、`api/tasks.py:1260-1262`)。前端重连时带上已读行数即可不重复。
- 推送粒度:每条 `data:` 是一个 JSON,含 `line` + 三个计数器 `success` / `registered` / `total`(`api/tasks.py:1255-1261`)。也就是说日志和进度共用一条流。
- 轮询间隔:服务端侧 `await asyncio.sleep(0.5)`(`api/tasks.py:1270`),即 0.5 秒扫一次内存日志数组。
- 终止帧:状态进入 `done` / `failed` / `stopped` 时推 `{"done": true, "status": ..., 计数器}` 后 `break`(`api/tasks.py:1263-1265`)。
- 冷任务保护:`use_memory = _task_store.exists(task_id)` 在进入循环前一次性确定(`api/tasks.py:1245`);内存里没有的任务只读 DB 快照,推完就发一个 `{"done": true, "status": "stopped"}` 结束,**不进无限轮询**(`api/tasks.py:1266-1269`)。
- 副作用:内存任务的每一轮扫描都会顺手 `_persist_task_snapshot(task_id)`(`api/tasks.py:1250`),等于 SSE 连接期间持久化频率提高到 2Hz。

#### 业务历史(task_logs)链路

| 接口 | 行为 | 位置 |
| --- | --- | --- |
| `GET /tasks/logs?platform=&page=&page_size=` | 分页查 `TaskLog`,`id desc` 排序,返回 `{total, items}` | `api/tasks.py:1192-1201` |
| `POST /tasks/logs/batch-delete` | 批量删,先 `dict.fromkeys` 去重,单次上限 1000,返回 `{deleted, not_found, total_requested}`;异常回滚 + 500 | `api/tasks.py:1204-1234` |

`TaskLog` 一条记录含 `platform / email / status / error / detail_json`。`detail_json` 用来区分动作,例如 `{"action": "backfill_rt"}`、`{"action": "bind_2fa"}`(`api/tasks.py:972`、`api/tasks.py:1029`)。

#### 全部任务相关 HTTP 端点

| 方法 + 路径 | 说明 | 位置 |
| --- | --- | --- |
| `POST /tasks/register` | 创建批量注册任务 | `api/tasks.py:1161-1167` |
| `POST /tasks/backfill-rt` | 创建批量补 RT 任务(ChatGPT) | `api/tasks.py:1057-1106` |
| `POST /tasks/bind-2fa` | 创建批量绑 TOTP 2FA 任务(ChatGPT) | `api/tasks.py:1109-1158` |
| `POST /tasks/{task_id}/stop` | 停止整个任务 | `api/tasks.py:1181-1189` |
| `POST /tasks/{task_id}/skip-current` | 跳过当前账号 | `api/tasks.py:1170-1178` |
| `GET /tasks/{task_id}/logs/stream` | SSE 实时日志 + 计数 | `api/tasks.py:1237-1279` |
| `GET /tasks/{task_id}` | 单任务快照 | `api/tasks.py:1282-1285` |
| `GET /tasks` | 任务列表(DB 为准,按 running/pending/done/failed/stopped + 创建时间倒序) | `api/tasks.py:1288-1292` |
| `DELETE /tasks/{task_id}` | 删除已结束任务 | `api/tasks.py:1295-1307` |
| `GET /tasks/logs` | 业务历史分页 | `api/tasks.py:1192-1201` |
| `POST /tasks/logs/batch-delete` | 业务历史批量删除 | `api/tasks.py:1204-1234` |

#### 复用编排骨架的两类批量任务

`_run_account_batch_task()`(`api/tasks.py:782-939`)把「逐个号跑一遍」抽成骨架:排队限速、可停可跳、计数收尾全复用,只有 `handle_account` 回调不同。

| 任务 | 入口 | 每号动作 | 特殊结果映射 |
| --- | --- | --- | --- |
| 补 RT | `_run_backfill_rt`(`api/tasks.py:942-993`) | `backfill_account_data()` → `apply_backfill_result()` 落库 | 成功/失败两态 |
| 绑 2FA | `_run_bind_2fa`(`api/tasks.py:996-1054`) | `bind_account_two_factor()` → `apply_two_factor_result()` 落库 | `already_bound` 映射为 `SKIPPED`;成功时把 TOTP 密钥写进任务日志(`api/tasks.py:1026-1028`,注释说明这是用户当场导入验证器的唯一途径) |

两者都把 `control` 与 `attempt_id` 透传给 service 层(`api/tasks.py:961-962`、`api/tasks.py:1015-1016`),让长时间网络等待也能被停止/跳过打断。`_load_account_fields()`(`api/tasks.py:762-779`)先把账号读成纯 dict 再放开 DB 连接,避免几十秒网络请求期间攥着连接池把面板其它请求拖住。

---

### 七、执行器与验证码

#### 执行器三种模式

`RegisterConfig.executor_type` 取值 `protocol | headless | headed`(`core/base_platform.py:33-34`),由 `BasePlatform._make_executor()` 落地(`core/base_platform.py:127-139`)。

| 模式 | 实现类 | 底层 | 特点 | 适用 |
| --- | --- | --- | --- | --- |
| `protocol` | `ProtocolExecutor`(`core/executors/protocol.py:8`) | `curl_cffi` Session,默认 `impersonate="chrome124"` | 无浏览器,最快最省资源;TLS/JA3 指纹伪装 | 纯 HTTP API 型注册流程 |
| `headless` | `PlaywrightExecutor(headless=True)`(`core/executors/playwright.py:13`) | Playwright Chromium | 真浏览器执行 JS,无 GUI | 需要跑 JS/前端校验 |
| `headed` | `PlaywrightExecutor(headless=False)` | 同上 | 有 GUI,可人工观察/介入 | 调试、人工打码 |

统一接口 `BaseExecutor`(`core/base_executor.py:19-47`):`get / post / get_cookies / set_cookies / close`,外加 `__enter__/__exit__` 支持 `with`。响应统一封成 `Response(status_code, text, headers, cookies)` dataclass,带 `.json()`(`core/base_executor.py:8-16`)。

差异细节:
- `ProtocolExecutor` 的 `cookies` 从 `self.s.cookies.jar` 提取(`core/executors/protocol.py:25-32`),代理走 `build_requests_proxy_config`。
- `PlaywrightExecutor.get()` 是 `page.goto()` + `page.content()`,返回的 `text` 是**渲染后的 HTML**,不是原始 body(`core/executors/playwright.py:64-79`)。
- `PlaywrightExecutor.post()` 走 `page.request.post()`,根据传 `json` 还是 `data` 自动切 `Content-Type`(`core/executors/playwright.py:81-101`)。
- `PlaywrightExecutor` 暴露 `page` / `context` property,兼容插件直接操作浏览器(`core/executors/playwright.py:56-63`)。
- `set_cookies` 在 `page.url` 是 http(s) 时按 URL 注入,否则退回 `domain + path`(`core/executors/playwright.py:107-121`)。

#### 降级规则

**平台级降级**(`core/base_platform.py:57-69`):`BasePlatform.__init__` 里,请求的 `executor_type` 不在子类 `supported_executors` 列表中时自动降级:

1. 优先降到 `"protocol"`(若在支持列表里);
2. 否则取 `supported_executors[0]`;
3. 列表为空则兜底 `"protocol"`;
4. 降级会 `print` 一条 `执行器 'X' 不受支持,自动切换为 'Y' (支持: [...])`。

`supported_executors` 默认是 `["protocol", "headless", "headed"]`(`core/base_platform.py:44`),子类可收窄。空字符串 / None 的 `executor_type` 先归一化成 `"protocol"`(`core/base_platform.py:56`)。

**运行时 headless 覆盖**(`core/browser_runtime.py:34-52`):`resolve_browser_headless()` 的优先级从高到低:

1. 环境变量 `PLAYWRIGHT_HEADLESS`;
2. 环境变量 `REGISTER_HEADLESS`;
3. 代码请求值(即 `headed`/`headless` 的选择);
4. 默认 `True`。

布尔解析:`{"1","true","yes","on"}` 为真,`{"0","false","no","off"}` 为假,其它值打 warning 并当作未设置(`core/browser_runtime.py:12-31`)。返回值第二项是 reason 字符串(如 `env:PLAYWRIGHT_HEADLESS=false`),会写进 `PlaywrightExecutor` 的 INFO 日志(`core/executors/playwright.py:30-35`)。

**有头模式的前置校验**(`core/browser_runtime.py:55-68`):`ensure_browser_display_available(headless=False)` 在 Linux 且 `DISPLAY` 未设置时直接 `RuntimeError`,提示「Docker 内请启用 Xvfb;本地 Linux 请先启动图形环境或改用无头模式」。非 Linux 平台不校验。

#### 三种验证码求解器

`BaseCaptcha`(`core/base_captcha.py:11-21`)只有两个抽象方法:`solve_turnstile(page_url, site_key)` 与 `solve_image(image_b64)`。由 `_make_captcha()` 按 `config.captcha_solver` 分发(`core/base_platform.py:141-155`)。

| 求解器 | `captcha_solver` 值 | 实现 | 调用方式 | `solve_image` |
| --- | --- | --- | --- | --- |
| YesCaptcha | `yescaptcha` | `core/base_captcha.py:23` | `POST {api}/createTask`(`TurnstileTaskProxyless`)→ 轮询 `POST {api}/getTaskResult` | `NotImplementedError` |
| 人工打码 | `manual` | `core/base_captcha.py:54` | 阻塞 `input()` 让人贴 token | 支持(`input()` 输入文字) |
| 本地 Solver | `local_solver` | `core/base_captcha.py:63` | `GET {solver_url}/turnstile?url=&sitekey=` → 轮询 `GET {solver_url}/result?id=` | `NotImplementedError` |

具体参数:

| 求解器 | 端点 | 轮询 | 超时 | 失败信号 |
| --- | --- | --- | --- | --- |
| YesCaptcha | `https://api.yescaptcha.com` | 60 次 × 3 秒 | ~180 秒 → `TimeoutError` | `errorId != 0` → `RuntimeError`;无 `taskId` → `RuntimeError` |
| LocalSolver | `solver_url`(默认 `http://127.0.0.1:8889`) | 60 次 × 2 秒 | ~120 秒 → `TimeoutError` | `status == "CAPTCHA_FAIL"` → `RuntimeError`;无 `taskId` → `RuntimeError` |

`YesCaptcha` 的 key 解析顺序:`_make_captcha(key=...)` 参数 → `config.extra["yescaptcha_key"]`(`core/base_platform.py:145-146`)。它对 `requests` 调用带 `verify=False` 并 `urllib3.disable_warnings()`(`core/base_captcha.py:29-30`)。

`local_solver` 的 URL 解析顺序(`core/base_platform.py:149-153`):`config.extra["solver_url"]` → `LOCAL_SOLVER_URL` → `http://127.0.0.1:{SOLVER_PORT或8889}`。同一套兜底逻辑也在 `core/base_captcha.py:6-7` 的 `_default_solver_url()`。

**注意**:`ManualCaptcha` 用 `input()`,在无 TTY 的服务进程(uvicorn/docker)里会直接抛异常;它只适合本地终端调试。

#### 本地 Turnstile Solver

##### 自动拉起

`services/solver_manager.py` 是进程管理层,由 FastAPI lifespan 驱动(`main.py:72-73` 启动 `start_async()`,`main.py:77-78` 退出时 `stop()`)。

1. `start_async()`(`services/solver_manager.py:100-104`)起一个 daemon 线程跑 `start()`,不阻塞应用启动。
2. `start()`(`services/solver_manager.py:44-89`)在 `_lock` 保护下:
   - `_solver_enabled()` 检查 `APP_ENABLE_SOLVER`,值在 `{0,false,no}` 时打印「已禁用,跳过自动启动」并返回;
   - `is_running()` 已在运行则打印「已在运行」返回(幂等);
   - `subprocess.Popen([sys.executable, "-u", start.py, "--browser_type", ..., "--host", ..., "--port", ...])`,stdout/stderr 全部重定向到 `services/turnstile_solver/solver.log`(append 模式);
   - 健康检查循环最多 30 次 × 1 秒。
3. `stop()`(`services/solver_manager.py:91-98`):`terminate()` + `wait(timeout=5)`,关日志文件句柄。

##### 端口与环境变量

| 环境变量 | 默认 | 作用 | 位置 |
| --- | --- | --- | --- |
| `APP_ENABLE_SOLVER` | `1` | 是否自动拉起 solver | `services/solver_manager.py:13-14` |
| `SOLVER_PORT` | `8889` | solver 监听端口 | `services/solver_manager.py:17-18` |
| `LOCAL_SOLVER_URL` | `http://127.0.0.1:{SOLVER_PORT}` | 客户端访问地址(优先于端口拼接) | `services/solver_manager.py:21-22` |
| `SOLVER_BIND_HOST` | `0.0.0.0` | solver 绑定网卡 | `services/solver_manager.py:25-26` |
| `SOLVER_BROWSER_TYPE` | `camoufox` | solver 用哪种浏览器 | `services/solver_manager.py:29-30` |

`start.py` 自己的 argparse 默认值与之不同:`--browser_type` 默认 `chromium`、`--thread` 默认 4、`--host` 默认 `0.0.0.0`、`--port` 默认 `os.getenv('SOLVER_PORT', '8889')`(`services/turnstile_solver/api_solver.py:1096-1108`)。也就是说**手工命令行启动的默认浏览器是 chromium,由 solver_manager 拉起时是 camoufox**。

##### 健康检查

| 层 | 方式 | 判定 |
| --- | --- | --- |
| `solver_manager.is_running()` | `GET {solver_url}/`,timeout 2s | `status_code < 500` 即算活;任何异常算死(`services/solver_manager.py:36-41`) |
| 启动等待 | 30 次 × 1 秒;每轮同时看 `_proc.poll()` | 进程提前退出 → 打印「启动失败,退出码=N,日志: ...」;30 秒没起来 → 「启动超时,日志: ...」(`services/solver_manager.py:78-89`) |
| 面板接口 | `GET /api/solver/status` → `{"running": bool}`;`POST /api/solver/restart` → `stop()` + `start_async()` | `main.py:135-144` |
| `LocalSolverCaptcha.start_solver()` | 备用静态方法,20 次 × 1 秒探 `http://localhost:{port}/`,超时抛 `RuntimeError("LocalSolver 启动超时")` | `core/base_captcha.py:107-131` |

##### Solver 服务内部

- Web 框架是 **Quart**(异步 Flask),路由只有三个:`GET /turnstile`、`GET /result`、`GET /`(`services/turnstile_solver/api_solver.py:146-148`)。
- `POST` 不支持;`/turnstile` 必须带 `url` 与 `sitekey`,缺参返回 `errorCode: ERROR_WRONG_PAGEURL`(HTTP 200)。
- 提交后立刻返回 `{"errorId": 0, "taskId": uuid4}`,真正求解在 `asyncio.create_task(self._solve_turnstile(...))` 里异步跑(`services/turnstile_solver/api_solver.py:951-990`)。
- 结果存 `services/turnstile_solver/db_results.py` 的**纯内存 dict** `results_db`,进程重启即清空;`_periodic_cleanup()` 每 3600 秒清理超过 7 天的结果(`services/turnstile_solver/api_solver.py:258-267`)。
- `/result` 的三态:`status="processing"`(未就绪)、`{"status":"ready","solution":{"token":...}}`、`errorCode="ERROR_CAPTCHA_UNSOLVABLE"`(失败/不存在)。全部 HTTP 200,错误靠 body 里的 `errorId` 区分(`services/turnstile_solver/api_solver.py:992-1032`)。
- 浏览器池:`--thread`(默认 4)个浏览器实例放进 `self.browser_pool` asyncio 队列,求解时借出用完还回(`services/turnstile_solver/api_solver.py:166-256`)。
- 浏览器类型分两条路:`chromium/chrome/msedge` 走 **patchright** 的 `async_playwright`;`camoufox` 走 **AsyncCamoufox**(`services/turnstile_solver/api_solver.py:170-182`)。
- `start.py` 在 Linux + camoufox 时会把 `platformdirs.user_cache_dir("camoufox")` 前插到 `LD_LIBRARY_PATH`,解决 camoufox 动态库找不到的问题(`services/turnstile_solver/start.py:11-29`)。
- `browser_configs.py` 提供随机 UA / Sec-CH-UA(Chrome 120/121/122/124 四个版本池),对应 `--random` 开关。

##### 失败排查点(按顺序)

| # | 检查项 | 怎么查 |
| --- | --- | --- |
| 1 | solver 是否被禁用 | `APP_ENABLE_SOLVER` 是否为 `0/false/no` |
| 2 | 进程是否活着 | `GET /api/solver/status`;或看后端 stdout 有无 `[Solver] 已启动 PID=` |
| 3 | 启动日志 | `services/turnstile_solver/solver.log`(append,不轮转,长期跑会一直变大) |
| 4 | 依赖缺失 | 日志里 `需要 patchright,但未安装` → `pip install patchright`;`需要 camoufox,但未安装` → `pip install camoufox && python -m camoufox fetch`(`services/turnstile_solver/api_solver.py:174-181`) |
| 5 | conda 环境不对 | 后端启动时 `_print_runtime_info()` 会 WARN「未检测到 conda 环境,推荐使用 ... 启动,否则 Turnstile Solver 可能因依赖缺失而无法启动」(`main.py:55-59`) |
| 6 | 端口冲突 / 地址错 | `SOLVER_PORT` 与 `LOCAL_SOLVER_URL` 是否一致;容器内 `127.0.0.1` 是否可达 |
| 7 | 有头模式无 DISPLAY | 见 `ensure_browser_display_available` 的报错,Docker 内需 Xvfb |
| 8 | 客户端超时 | `LocalSolverCaptcha` 只等 ~120 秒;solver 侧 `CAPTCHA_FAIL` 会立刻抛 `RuntimeError` |
| 9 | 重启 | `POST /api/solver/restart`(先 `stop()` 再 `start_async()`) |
| 10 | 任务结果丢失 | `results_db` 是内存态,solver 重启后旧 `taskId` 一律 `Task not found` |

---

### 八、定时调度(core/scheduler.py)

#### 调度器结构

单例 `scheduler = Scheduler()`(`core/scheduler.py:129`),由 FastAPI lifespan 在启动时 `scheduler.start()`、退出时 `scheduler.stop()`(`main.py:70-71`、`main.py:75-76`)。

- `start()`(`core/scheduler.py:19-32`):幂等(`_running` 已为真直接返回);**把两个 `_last_*_at` 都设为当前时间**,注释说明这是为了「避免应用一启动就瞬间触发定时任务(如 CPA 自动注册)」;然后起 daemon 线程跑 `_loop()`,打印 `[Scheduler] 已启动`。
- `stop()`(`core/scheduler.py:34-35`):只把 `_running` 置 False,线程在下一次 `sleep` 结束后自然退出(最长滞后一个 tick)。
- `_loop()`(`core/scheduler.py:37-55`):固定 60 秒一个 tick(`_loop_interval_seconds`),每个 tick 内用「上次执行时间 + 间隔」判断各任务是否该跑;每个任务单独 `try/except` 捕获并打印,单个任务出错不会拖死整个调度线程。

#### 注册的周期任务

| 任务 | 间隔 | 间隔来源 | 做什么 |
| --- | --- | --- | --- |
| `check_trial_expiry()` | 3600 秒(硬编码 `_trial_check_interval_seconds`,`core/scheduler.py:16`) | 常量 | 扫 `status == "trial"` 的账号,`trial_end_time < now` 的改成 `AccountStatus.EXPIRED`,一次性 commit,有变更时打印条数(`core/scheduler.py:58-73`) |
| `check_cpa_credentials()` | 动态,`services.cpa_manager.get_cpa_maintenance_interval_seconds()`(`core/scheduler.py:57-60`) | 配置(返回 0/None 时该任务不跑) | 调 `maintain_cpa_credentials()`:清理 CPA 中的 error 凭证,低于阈值时**自动补注册**(`core/scheduler.py:124-127`) |

CPA 自动补注册就是「非手动来源的注册任务」入口 —— 它最终会走 `enqueue_register_task(..., source=...)`,并借 `has_active_register_task(platform=..., source=...)`(`api/tasks.py:389-392`)避免重复起任务。

#### 非周期方法

`check_accounts_valid(platform=None, limit=50)`(`core/scheduler.py:75-122`)**不在 `_loop` 里自动跑**,是供接口/手工调用的批量账号有效性检测:

1. `load_all()` 确保平台插件已加载;
2. 查 `status in ("registered","trial","subscribed")` 的账号,可按 `platform` 过滤,`limit` 默认 50;
3. 逐个 `PlatformCls(config=RegisterConfig()).check_valid(account_obj)`;
4. 写回状态时有一条特例:**`platform != "chatgpt"` 才会把无效账号改成 `INVALID`**,ChatGPT 只更新 `updated_at`(`core/scheduler.py:104-106`);
5. 返回 `{"valid": n, "invalid": n, "error": n}`。

注意它对每个账号开一次独立 `Session` 写回(`core/scheduler.py:102`),避免长事务;查询与写回是两个 session,存在读后写窗口。

#### 调度相关的注意点

| 现象 | 原因 |
| --- | --- |
| 修改间隔配置后不立即生效 | tick 粒度是 60 秒,且 CPA 间隔每 tick 重新读一次,trial 间隔是常量需改代码 |
| `stop()` 后线程还活一会儿 | `_running` 只在 `sleep(60)` 结束后被检查 |
| 重启应用后周期任务不会立刻跑 | `start()` 主动把 `_last_*_at` 设为 now |
| 任务异常不会告警 | 只 `print`,没有落库/通知 |

---

### 九、行为印证:相关测试

| 测试文件 | 印证的行为 |
| --- | --- |
| `tests/test_register_task_controls.py` | 停止/跳过的端到端语义:控制接口、`attempt_id` 定向跳过、pending 跳过消费、AttemptResult 映射、任务终态 |
| `tests/test_task_runtime.py` | `RegisterTaskControl` / `RegisterTaskStore` 的单元行为:checkpoint 判定顺序、快照字段、cleanup 淘汰 |
| `tests/test_mailbox_task_control.py` | 控制器透传到邮箱层后,长时间等待邮件也能被停止/跳过打断 |
| `tests/test_backfill_rt_task_api.py` | 补 RT 任务的目标筛选、400 分支、任务创建与 meta |
| `tests/test_bind_2fa_task_api.py` | 绑 2FA 任务的目标筛选、`already_bound` → skipped、TOTP 密钥写日志 |

---

### 十、新接手者速查

#### 想改并发/节流

`api/tasks.py:695`(`max_workers`)、`api/tasks.py:569-583`(启动延迟闸门)、`api/tasks.py:878`(批量账号任务并发)。

#### 想加一个「可被跳过」的长等待

在等待循环里调 `control.checkpoint(attempt_id=self._task_attempt_token)`,并确保外层已 `bind_task_control()`。参考 `_sleep_with_control`(`api/tasks.py:454-464`)的 0.25 秒分片写法。

#### 想让某类失败不再重试

在插件里 `raise NonRetryableRegisterError(...)`(`core/task_runtime.py:29`),连续 2 轮后 `_do_one` 会自动收手。

#### 想加一个新的批量账号任务

复用 `_run_account_batch_task()`(`api/tasks.py:782`),只写 `handle_account` 回调,返回 `AttemptResult`;参考 `_run_backfill_rt` / `_run_bind_2fa`。

#### 已知的粗糙处

| 点 | 说明 |
| --- | --- |
| `logs` 无上限 | `RegisterTaskRecord.logs` 是无界 list,长任务 + 高频日志会让 `logs_json` 单行膨胀 |
| 持久化是全量覆盖 | 每条日志都触发一次 `task_runs` 整行 JSON 重写(`api/tasks.py:400`),日志越多写放大越严重 |
| SSE 期间持久化加倍 | `stream_logs` 每 0.5 秒又 upsert 一次(`api/tasks.py:1250`) |
| `GET /tasks/logs` 的 total | 用 `len(s.exec(q).all())` 全量拉一遍算总数(`api/tasks.py:1199`),历史多了会慢 |
| `_save_task_log` 无重试 | fire-and-forget daemon 线程,写失败静默丢(`api/tasks.py:404-419`) |
| `stopped` 只取消未开始的 future | 已在跑的 attempt 靠 checkpoint 协作退出,没有强杀 |
| solver.log 不轮转 | append 模式长期追加(`services/solver_manager.py:56-60`) |
| `ManualCaptcha` 依赖 TTY | 服务进程里 `input()` 会失败 |

---

## 四、ChatGPT 注册状态机

### 一、注册状态机全流程

ChatGPT 平台是纯协议实现(`curl_cffi` + TLS 指纹伪装),不启动浏览器。整条链路的编排入口只有一个:`platforms/chatgpt/protocol/auth_flow.py:2811`(`run_register`),它把下面各阶段按顺序串起来,状态全部挂在 `self.session`(cookie jar)与 `self.result`(`AuthResult`,`platforms/chatgpt/protocol/auth_flow.py:43`)两个对象上。

#### 1.1 阶段总表

顺序号即 `run_register` 内的真实调用顺序;日志里 `[N/10]` 是源码自带的阶段编号(与本表顺序略有错位,因为 warmup/check_proxy 不占编号)。

| # | 阶段名 | 请求目标 | 关键入参 | 成功判定 | 函数位置 |
|---|--------|----------|----------|----------|----------|
| 0 | 网络预检 | `GET https://cloudflare.com/cdn-cgi/trace` | 无 | HTTP 200 且能解析 `loc=`/`ip=`;失败只告警不终止 | `platforms/chatgpt/protocol/auth_flow.py:1680` `check_proxy` |
| 1 | **warmup(种 cookie)** | `GET https://chatgpt.com`(整页导航头) | `_navigation_headers()` 全套 client hints + Sec-Fetch-* | **唯一判据:cookie jar 里出现 `oai-did`**;不看 status_code | `platforms/chatgpt/protocol/auth_flow.py:1570` `warmup` |
| 2 | 创建邮箱 | mail provider(非 OpenAI) | provider 配置 | 返回非空 email,写入 `result.email` | `platforms/chatgpt/protocol/auth_flow.py:2826`(调 `mail_provider.create_mailbox()`) |
| 3 | 取 CSRF | `GET https://chatgpt.com/api/auth/csrf` | 无(带 warmup 的 cookie) | JSON `csrfToken` 非空,写 `result.csrf_token`;403 重试 3 次(5s/10s) | `platforms/chatgpt/protocol/auth_flow.py:1719` `get_csrf_token` |
| 4 | 取 authorize URL | `POST https://chatgpt.com/api/auth/signin/openai?...` | query:`prompt=login`、`screen_hint=login_or_signup`、`ext-oai-did`、`auth_session_logging_id`、`ext-passkey-client-capabilities=1111`、可选 `login_hint`;body:`csrfToken`/`callbackUrl`/`json=true` | JSON `url` 非空且是 `auth.openai.com/authorize`(有 `oai-did` 才会返这个,否则返 NextAuth 页) | `platforms/chatgpt/protocol/auth_flow.py:1758` `get_auth_url` |
| 5 | authorize 初始化 | `GET <auth_url>`(跟随 302 到 auth.openai.com) | 导航头 + `sec-fetch-site: cross-site`,**摘掉 `sec-fetch-user`** | 取到 `oai-did`(cookie → `_get_cookie_value_by_name` → HTML 正则 → 生成 UUID 兜底);落点记入 `_last_auth_landing_url` | `platforms/chatgpt/protocol/auth_flow.py:1795` `auth_oauth_init` |
| 6 | Sentinel PoW | `sentinel.openai.com`(见 sentinel 模块) | `device_id`、`flow="authorize_continue"`、`_sentinel_fp_kwargs()` 整套指纹 | 返回 `(token, so_token)`,分别存 `_last_sentinel_token` / `_last_sentinel_so_token` | `platforms/chatgpt/protocol/auth_flow.py:1887` `get_sentinel_token`;指纹入参 `platforms/chatgpt/protocol/auth_flow.py:1856` |
| 7 | **提交身份** | `POST https://auth.openai.com/api/accounts/authorize/continue` | `username={value,kind}`(`kind` = `email` 或 `phone_number`)、`screen_hint=signup`;头带 `openai-sentinel-token` + `openai-sentinel-so-token` | HTTP 200 且能按 `page.type` 归类;非 200 直接 `RuntimeError`(带 `req_id`) | `platforms/chatgpt/protocol/auth_flow.py:1903` `authorize_continue`;分流 `platforms/chatgpt/protocol/auth_flow.py:1954` `signup` |
| 8 | 设密码(新号) | `GET /create-account/password` 建状态 → `POST https://auth.openai.com/api/accounts/user/register` | `{password, username}`;**必须重刷 sentinel,`flow="username_password_create"`** | HTTP 200 → `logger.info("密码注册成功")` 并立刻触发 `_on_password` 落盘;非 200 返回 False 走降级 | `platforms/chatgpt/protocol/auth_flow.py:2012` `register_password` |
| 9 | 发码 | 三个端点按策略选一:`POST /api/accounts/passwordless/send-otp`、`GET /api/accounts/email-otp/send`、`POST /api/accounts/email-otp/resend` | referer 决定服务端认定的页面状态;沿用实例上的 sentinel 头 | 对应方法返 True / 不抛异常 | `platforms/chatgpt/protocol/auth_flow.py:2092` `send_otp`、`:2110` `send_passwordless_otp`、`:2132` `resend_otp`、策略选择 `:2155` `kickoff_otp_delivery` |
| 10 | 等码 | mail provider(IMAP / API) | `timeout=OTP_TIMEOUT`(默认 60,下限 10)、**`issued_after=otp_sent_at`** | 取到 6 位码 | `platforms/chatgpt/protocol/auth_flow.py:2902`(新号)/`:3021`(已有号) |
| 11 | 验码 | `POST https://auth.openai.com/api/accounts/email-otp/validate` | `{code}` | HTTP 200;响应 `continue_url` 决定下一跳 | `platforms/chatgpt/protocol/auth_flow.py:2318` `verify_otp` |
| 12 | **create_account** | `POST https://auth.openai.com/api/accounts/create_account` | `{name, birthdate}`;**先重刷 sentinel,`flow="oauth_create_account"`** | HTTP 200 且拿到 `continue_url`;拿不到就退到 workspace/select;仍拿不到 → `RuntimeError("创建账户后未获取到 continue_url")` | `platforms/chatgpt/protocol/auth_flow.py:2338` `create_account` |
| 13 | workspace 选择 | `POST https://auth.openai.com/api/accounts/workspace/select` | `{workspace_id}`(来自 `oai-client-auth-session` cookie 的 base64 段,或页面 HTML 正则) | 200 且响应含 `continue_url` | `platforms/chatgpt/protocol/auth_flow.py:2422` `_workspace_select`;取 id `:2393`+`:2534` |
| 14 | 多账号页 | `POST /api/accounts/session/select`(兜底 `POST /choose-an-account` form) | `{session_id}`(HTML 里正则出的 `us_*`) | 200/201/302/303 且能取到下一跳;200 无 `continue_url` 时假定 cookie 已设,返回当前 URL 让外层重 GET | `platforms/chatgpt/protocol/auth_flow.py:2435` `_choose_account_select` |
| 15 | 重定向链 | 逐跳 `GET`,`allow_redirects=False`,最多 12 跳 | 每跳重算 `sec-fetch-site`(同域 `same-origin`,跨域 `cross-site`) | 捕获到含 `code=` 的 `/api/auth/callback/openai` URL;**捕获后立刻 break,不主动 GET**(避免 code 被提前消费) | `platforms/chatgpt/protocol/auth_flow.py:2555` `follow_redirect_chain` |
| 16 | 消费 callback | `GET <callback_url>` 逐跳跟到 chatgpt.com 首页 | 最多 8 跳 | cookie `__Secure-next-auth.session-token` 出现 | `platforms/chatgpt/protocol/auth_flow.py:2764` `_consume_callback_for_session` |
| 17 | **取 session** | `GET https://chatgpt.com/api/auth/session` | 无 | `session_token`(cookie 优先,再 JSON `sessionToken`/`session_token`)+ `accessToken`;顺带刷新 `result.cookie_header` | `platforms/chatgpt/protocol/auth_flow.py:2716` `get_auth_session` |
| 18 | Codex 换 RT | 独立 PKCE authorize 链 + `POST /oauth/token` | 见第三章 | `result.refresh_token` 非空 | `platforms/chatgpt/protocol/auth_flow.py:1308` `oauth_codex_rt_exchange` |
| 19 | 终态校验 | 无 | 无 | `AuthResult.is_valid()` = `session_token and access_token`;`OAUTH_REFRESH_ONLY=1` 时改判 `refresh_token or access_token` | `platforms/chatgpt/protocol/auth_flow.py:61`、判定点 `:3161`-`:3165` |

#### 1.2 主干 + 分支流程图

```mermaid
flowchart TD
    A[check_proxy 网络预检<br/>:1680 失败仅告警] --> B{warmup 拿 oai-did<br/>:1570 最多 4 次}
    B -- 4 次都没拿到 --> BX[["RuntimeError<br/>拦在建邮箱之前"]]
    B -- 拿到 --> C[create_mailbox 建邮箱]
    C --> D[get_csrf_token :1719]
    D --> E[get_auth_url :1758<br/>POST signin/openai]
    E --> F[auth_oauth_init :1795<br/>建 authorize state]
    F --> G[get_sentinel_token :1887<br/>flow=authorize_continue]
    G --> H[authorize/continue :1903<br/>signup 分流 :1954]

    H -- page.type=create_account_password --> N1[新号:register_password :2012]
    H -- page.type=email_otp_verification<br/>mode=passwordless_signup --> N2[新号无密码<br/>也走 register_password]
    H -- email_otp_verification 其它 mode --> E1{号池邮箱?}
    H -- 未知 page.type --> E1
    E1 -- 是 且 WEBUI_ALLOW_LOGIN≠1 --> EX[["mark_dead + fast-fail<br/>换下一个号 :2864"]]
    E1 -- 否 或 允许登录 --> E2[已有账号分支 :2938]

    N1 --> S1[send_otp :2092<br/>重发,旧码已失效]
    N2 --> S1
    S1 --> V1[wait_for_otp<br/>issued_after=发码时刻]
    V1 --> V2[verify_otp :2318]
    V2 -- 401 --> S1
    V2 --> CA[create_account :2338<br/>name + birthdate]

    E2 -- page_type=login_password --> P1[login_password_verify :2263]
    P1 -- mfa-challenge --> P2[submit_mfa_totp :2286]
    P1 -- 需二次邮件验证 --> P3[kickoff_otp_delivery :2155]
    E2 -- passwordless_* --> P4[跳过 send_otp<br/>直接等邮件 回看 8s]
    E2 -- 其它 --> P5[send_otp]
    P3 --> P6[verify_otp]
    P4 --> P6
    P5 --> P6
    P2 --> CU
    P6 -- continue_url 含 add-phone --> P7[_handle_add_phone_verification :981]
    P6 --> CU
    P7 --> CU
    CA -- registration_disallowed --> RA[_reauthorize_for_session :2629]
    CA --> CU{continue_url}
    RA --> CU
    CU -- 含 /about-you --> CA
    CU -- 含 /workspace --> WS[workspace/select :2422]
    CU -- 空 --> RA
    WS --> CU

    CU --> PRE{无 RT 且无 on_session_ready 钩子<br/>且 OAUTH_CODEX_RT_BEFORE_CALLBACK=1}
    PRE -- 是 --> CX1[Codex 抢跑换 RT :3121]
    PRE -- 否 --> RC
    CX1 --> RC[follow_redirect_chain :2555<br/>捕获 callback 不消费]
    RC --> CC[_consume_callback_for_session :2764<br/>NextAuth 设 session-token]
    CC --> GS[get_auth_session :2716<br/>拿 session_token + access_token]
    GS --> HK[on_session_ready 钩子<br/>绑 2FA :3146]
    HK --> CX2[oauth_codex_rt_exchange :3156]
    CX2 --> GS2[再拉一次 session :3159]
    GS2 --> Z{is_valid?}
    Z -- 否 --> ZX[["RuntimeError 注册完成但未获取有效凭证"]]
    Z -- 是 --> OK[返回 AuthResult]
```

#### 1.3 几个容易踩的顺序约束

1. **warmup 必须在建邮箱之前**(`platforms/chatgpt/protocol/auth_flow.py:2818`)。邮箱是花钱资源,为一个注定 409 的轮次消耗一个号是纯亏损,所以这里是 hard fail。
2. **`register_password` 成功后必须主动重新发码**(`platforms/chatgpt/protocol/auth_flow.py:2877`-`:2885`)。`POST user/register` 200 会让服务端把流程切到 `email_otp_send` 页(响应 `page.type=email_otp_send`),signup 阶段自动发的那封 OTP 立刻失效,拿旧码去 validate 会 409 invalid_state。
3. **`otp_sent_at` 必须在发码请求之前取**(`platforms/chatgpt/protocol/auth_flow.py:2883`)。邮件是服务端在这次请求里发出的,先发再取时间会让 `otp_sent_at` 晚于邮件时间戳,自己把码用 `issued_after` 过滤掉。
4. **密码落盘点选在 `POST user/register` 返回 200 之后**(`platforms/chatgpt/protocol/auth_flow.py:2084`)。此时 OpenAI 侧账号+密码已经建好,但后面还有发码/验码/create_account 三步;密码只活在内存里,进程一退号就成了谁也登不进的僵尸号。放在 200 之前落盘则会把没生效的密码存进库,更误导。
5. **`register_password` 里的 SO token 只在本次请求内生效**(`platforms/chatgpt/protocol/auth_flow.py:2030`-`:2034`)。`username_password_create` 这个 flow 服务端不下发 so 块,若把空值回写实例字段,会顺手摘掉后续 `send_otp`/`verify_otp` 的 SO 头,而那些 flow 是要 SO 的。
6. **`page.type` 的 bool 压缩曾是重大 bug**(`platforms/chatgpt/protocol/auth_flow.py:2868`-`:2872`)。`signup()` 用一个 bool 表达三种服务端状态,`passwordless_signup`(新号、服务端选择不设密码)被压成和"已有账号"同一个 `False`,导致 `register_password` 从没被调用,注册出来的号全是无密码号。现在 `run_register` 的条件是 `is_new or self._existing_email_verification_mode == "passwordless_signup"`(`platforms/chatgpt/protocol/auth_flow.py:2873`)。

#### 1.4 create_account 的字段生成

`platforms/chatgpt/protocol/auth_flow.py:2360`-`:2366` 现场生成两个字段:

```python
name = f"{random.choice(_FIRST)} {random.choice(_LAST)}"
birthdate = f"{random.randint(1985, 2000)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
```

- 年份区间 **1985–2000 硬编码**,即请求当下年龄约 26–41 岁,稳定落在 OpenAI 的成年门槛之上。
- 日只取 1–28,规避 2 月与大小月的合法性判断,代价是分布上永远不出现 29/30/31 号。
- 名字池是方法内的局部常量 `_FIRST`/`_LAST`,**和 `platforms/chatgpt/constants.py:163` 的 `FIRST_NAMES`、`:171` 的 `generate_random_user_info()` 是两套独立实现**。constants 那套用 `MIN_REGISTRATION_AGE=20` / `MAX_REGISTRATION_AGE=45`(`platforms/chatgpt/constants.py:159`)按当前年份反推生日,并按月份区分 28/30/31 天,但 `create_account` 没有调用它,属于事实上的死代码分叉 —— 改年龄策略时必须改 `platforms/chatgpt/protocol/auth_flow.py:2366`,改 constants 无效。

### 二、失败征候与错误处理

#### 2.1 409 invalid_state:头号杀手

`409 invalid_state` 是这条链路上最常见也最致命的错误。它的语义是"服务端认为你现在这个 authorize 会话状态不允许执行该动作",成因分三类:

| 成因 | 触发点 | 判据/征候 | 修复手段 |
|------|--------|-----------|----------|
| **没有 `oai-did` cookie** | `authorize/continue` | `POST /api/auth/signin/openai` 返的是 NextAuth 页而不是 `auth.openai.com/authorize` URL,继续走必 409。实测无 `oai-did` 的 5 轮 **5/5 全 409**;有 `oai-did` 的 17 轮只有 3 次 409 | warmup 拿到 `oai-did` 才继续,拿不到直接 hard fail(`platforms/chatgpt/protocol/auth_flow.py:2818`) |
| **导航头不像浏览器** | `auth_oauth_init` 建 state 那一跳 | 缺 client hints、缺整组 `Sec-Fetch-*`。A/B 对照各 6 轮:裸头会话正常 2/6、**409=3**;补齐 CH+Sec-Fetch 会话正常 5/6、**409=0** | 三处整页导航(`warmup` / `auth_oauth_init` / `follow_redirect_chain`)统一走 `_navigation_headers()`(`platforms/chatgpt/protocol/auth_flow.py:1531`) |
| **用了已失效的 OTP / 走错发码端点** | `email-otp/validate`、`passwordless/send-otp` | `POST user/register` 成功后服务端把流程切到 `email_otp_send` 页,signup 阶段那封码立即失效;已有账号场景调 `passwordless/send-otp` 也会 409 | 密码注册后主动重发(`platforms/chatgpt/protocol/auth_flow.py:2885`);已有账号只能 `resend` 不能 `send`(`platforms/chatgpt/protocol/auth_flow.py:2171`-`:2178`) |

**源码里的那条长中文 RuntimeError**(`platforms/chatgpt/protocol/auth_flow.py:2819`-`:2823`),这是判断"这一轮该不该开始"的唯一闸门:

```python
raise RuntimeError(
    "warmup 失败：4 次重试均未拿到 chatgpt.com 的 oai-did cookie，"
    "继续注册必然 409 invalid_state（多为代理出口 IP 不通或被 CF 拦），"
    "请检查代理后重试"
)
```

配套的 `logger.error`(`platforms/chatgpt/protocol/auth_flow.py:1673`-`:1676`)措辞一致:`"warmup 4 次均未种到 oai-did cookie（已轮换浏览器家族） —— 此时继续走注册链必然 409 invalid_state"`。看到这两条中任一条,**不要去查 authorize 代码,去查代理出口**。

#### 2.2 warmup 自身的失败形态与重试策略

`warmup` 是全链路唯一带"换指纹家族"重试的阶段(`platforms/chatgpt/protocol/auth_flow.py:1632`-`:1671`),因为它挡的是三种不同的病:

| 征候 | 根因 | 现行对策 |
|------|------|----------|
| `curl: (35)` TLS 断连 | 出口 IP 到 chatgpt.com 的链路坏 | 重建 session(代理池按会话分配出口 ≈ 换 IP) |
| 15s 超时 | 成功轮实际耗时 3.4~10.9s,15s 卡在边缘 | timeout 提到 **40s** |
| CF 403,只给 `__cf_bm` | ①自称 Chrome 却不发 `sec-ch-ua`(已修);②**CF 按家族封,chrome 全族 403** | 重试时 `_switch_browser_family()` 跨家族换(`platforms/chatgpt/protocol/auth_flow.py:1455`),家族顺序随机,不写死"safari 更安全" |

三个必须记住的设计:
1. **唯一判据是 cookie jar 里有没有 `oai-did`**,不是 status_code(`platforms/chatgpt/protocol/auth_flow.py:1653`-`:1660`)。旧实现只 catch 异常:CF 403 照样 `return True`(实测 3 轮 True 但没 cookie),超时前其实已种上 cookie 却 `return False`(实测 1 轮)。
2. 重试**同时**换出口 IP 和浏览器家族。只换 IP 等于拿同一张脸连撞 4 次。
3. 失败轮的 cookie 跟 session 一起丢掉是对的 —— 那一轮本来就没种到有用东西。

#### 2.3 其它典型失败分支

| 征候 | 位置 | 处理 |
|------|------|------|
| `CSRF Token 获取失败` | `platforms/chatgpt/protocol/auth_flow.py:1752` | JSON 里没 `csrfToken`,直接抛;通常是被 CF 挡在 HTML 挑战页 |
| chatgpt.com TLS 握手失败(长中文提示) | `platforms/chatgpt/protocol/auth_flow.py:1735`-`:1738` | 先 `_rotate_impersonate_session()` 同族换版本重试,仍失败才抛"请切换可直连 chatgpt.com 的网络或在界面中配置可用代理" |
| CF 403 on `/api/auth/csrf` | `platforms/chatgpt/protocol/auth_flow.py:1740` | 5s / 10s 退避,重试 3 次 |
| `authorize/continue 失败(screen_hint=…): HTTP xxx req_id=…` | `platforms/chatgpt/protocol/auth_flow.py:1945` | 一定带 `x-request-id` 与 `Content-Type`,便于判断是不是 IP 风控 |
| 密码注册返回非 200 | `platforms/chatgpt/protocol/auth_flow.py:2073` | **不抛**,`return False` → 降级走"已有账号 OTP 路径",并 `fetch_client_auth_session_dump("post_register_password_failed_new")` 留证据 |
| OTP 验证 401 | `platforms/chatgpt/protocol/auth_flow.py:2910`-`:2925` | 补发一次 OTP 并重试一轮;非 401 直接上抛 |
| OTP 验证 401/409(已有账号) | `platforms/chatgpt/protocol/auth_flow.py:3063`-`:3077` | 401 与 409 都重发重试一次 |
| `registration_disallowed` | `_is_registration_disallowed_error` `platforms/chatgpt/protocol/auth_flow.py:482`,用点 `:2931`/`:3090` | 退到 `_reauthorize_for_session()` 用已有 auth session cookie 直接换 callback code;拿不到才抛 |
| `创建账户后未获取到 continue_url` | `platforms/chatgpt/protocol/auth_flow.py:2388` | 已尝试过 workspace/select 兜底仍无下一跳 |
| OTP 超时 + 号池邮箱 | `platforms/chatgpt/protocol/auth_flow.py:3026`-`:3059` | provider 已 `exhausted` → 不重试直接抛;否则重发再等一轮;两次都超时且 `pooled` → `mark_dead("OpenAI 静默拒绝发 OTP（'已有账号'但 INBOX 无邮件）")` |
| 号池邮箱被判"已有账号" | `platforms/chatgpt/protocol/auth_flow.py:2844`-`:2866` | `WEBUI_ALLOW_LOGIN=1` 走 OTP 登录拿凭证;否则 `mark_dead` + fast-fail,让外层 claim 下一个号,避免 register-only 模式被 honeypot 拖死 |
| `注册完成但未获取有效凭证` | `platforms/chatgpt/protocol/auth_flow.py:3165` | 终态校验失败:`session_token` 与 `access_token` 没同时到手 |
| `/choose-an-account` 全部候选失败 | `platforms/chatgpt/protocol/auth_flow.py:2510` | 直连 `POST /api/accounts/session/select`,兜底 form POST 回 react-router 路径;都失败返回空串 |

#### 2.4 `response_summary.py` 在错误诊断里的作用

`platforms/chatgpt/protocol/response_summary.py` 只有 73 行,但决定了错误日志能不能用。它的立场很明确:**任务日志是给人看的,不是抓包窗口**。

| 函数 | 行为 | 位置 |
|------|------|------|
| `compact(text, limit=160)` | 折成一行 + 掐到 160 字符,超长加 `…` | `platforms/chatgpt/protocol/response_summary.py:18` |
| `describe_error(text)` | 只取服务端明写的字段:`error.message`/`error.code`,或 OAuth 风格的 `error`+`error_description`+`code`,或扁平的 `message`/`detail`+`code` | `platforms/chatgpt/protocol/response_summary.py:26` |
| `describe_page(payload)` | 只挑真正指示下一步的两个字段,输出形如 `page=email_otp_verification continue=/…` | `platforms/chatgpt/protocol/response_summary.py:61` |

关键设计:**取不到就直说取不到**,回落成常量 `_NO_DETAIL = "(响应体未给出 message/code，完整报文开 AUTH_TRACE_DUMP 看)"`(`platforms/chatgpt/protocol/response_summary.py:15`)。它刻意**不**退回去打原文 —— 那等于把整页 HTML 或整段 JSON 又倒进日志,而这正是本模块要挡掉的东西。`tests/test_response_summary.py:29` 就锁死了这条:`test_html_error_page_never_reaches_the_log`。

因此排错的正确姿势是两级:
1. **日志级**:`describe_error` 给的一行 message/code,加上 `authorize_continue` 额外打的 `req_id`(`platforms/chatgpt/protocol/auth_flow.py:1939`)。
2. **报文级**:开 `AUTH_TRACE_DUMP=1`,`_trace_http`(`platforms/chatgpt/protocol/auth_flow.py:256`)把每一跳落成 `outputs/auth_trace_<ts>_<pid>.jsonl`;想连 Cookie 一起看再加 `AUTH_TRACE_INCLUDE_COOKIE=1`(Cookie 截断到 8000 字符,`platforms/chatgpt/protocol/auth_flow.py:380`)。

另有一个专门的诊断探针:`fetch_client_auth_session_dump(stage)`(`platforms/chatgpt/protocol/auth_flow.py:403`),`GET /api/accounts/client_auth_session_dump`,在 6 个关键阶段被调用(`post_register_password_failed_new`、`post_verify_otp_new`、`post_verify_otp_retry_new`、`post_verify_otp_existing`、`post_verify_otp_retry_existing`、`pre_oauth_exchange_register`)。它做两件事:落 `session_id` / 校准 `_oauth_client_id`;顺带用 `_walk_collect_str_fields`(`platforms/chatgpt/protocol/auth_flow.py:388`,最深 6 层)递归搜 `refresh_token`/`access_token`/`id_token`,极少见但白给的 token 直接收下。

### 三、Codex OAuth 换 RT

`refresh_token` **只由** `oauth_codex_rt_exchange` 提供(`platforms/chatgpt/protocol/auth_flow.py:1308`)。原因写在模块 docstring(`platforms/chatgpt/protocol/auth_flow.py:8`-`:9`):它自建一条带 PKCE 的 Codex authorize 链路,**verifier 攥在自己手里**;而主注册链那条 callback 的 `code` 属于 chatgpt.com 的 NextAuth,verifier 在服务端,拿不到也不能抢。

#### 3.1 什么时候跑

两个调用点,互斥:

| 调用点 | 条件 | 位置 |
|--------|------|------|
| **抢跑**(callback 消费前) | `not result.refresh_token` **且** `_on_session_ready is None` **且** `OAUTH_CODEX_RT_BEFORE_CALLBACK=1`(默认开) | `platforms/chatgpt/protocol/auth_flow.py:3116`-`:3121` |
| **常规**(拿 session 之后) | `callback_url or continue_url` 为真,且 `not result.refresh_token` 且 `OAUTH_CODEX_RT_EXCHANGE=1`(默认开) | `platforms/chatgpt/protocol/auth_flow.py:3153`-`:3156` |

抢跑的意义是"趁 `auth.openai.com` 登录态最新鲜时先把 RT 换掉"。但挂了 `on_session_ready` 钩子(绑 2FA)时**必须跳过抢跑**:钩子要等 `get_auth_session` 拿到 `access_token` 才能跑,而那步在 callback 之后;抢跑成功的话 Codex 就跑到钩子前面,指定的顺序 `创建账户 → 重定向链 → 拿 session → 绑 2FA → Codex 授权 → 接码` 等于白调(`platforms/chatgpt/protocol/auth_flow.py:3111`-`:3115`、`:136`-`:138`)。

#### 3.2 完整步骤

1. **幂等守卫**(`platforms/chatgpt/protocol/auth_flow.py:1315`-`:1319`):读 `OAUTH_CODEX_RT_ALLOW_RETRY`(默认 `0`)。若 `_codex_rt_attempted` 已为真且不允许重试,打 debug 日志后 `return False`。随后**无条件**置 `_codex_rt_attempted = True` —— 置位在 try 之前,所以哪怕本次异常退出也算"试过了"。
2. **构造独立 authorize URL**(`_build_codex_authorize`,`platforms/chatgpt/protocol/auth_flow.py:604`):`client_id`/`redirect_uri`/`scope`/`prompt` 各自可被 env 覆盖,默认值见第六章;`state` = 24 字节随机 base64url;PKCE 由 `_build_pkce_pair`(`platforms/chatgpt/protocol/auth_flow.py:594`)生成 —— 64 字节随机 → base64url 去 pad → 长度夹在 43~128 → `challenge = S256(verifier)`。固定带两个 Codex 专属参数:`id_token_add_organizations=true`、`codex_cli_simplified_flow=true`。
3. **跟随授权链捕获 code**(`_follow_authorize_for_callback`,`platforms/chatgpt/protocol/auth_flow.py:649`):最多 12 跳,`allow_redirects=False`。判据是 `_callback_has_code`(`platforms/chatgpt/protocol/auth_flow.py:635`) —— URL 去掉 query 后与 `redirect_uri` 基址完全相等且有非空 `code`。链上会顺手处理两类 200 页面:workspace/consent 页(`/workspace`、`/sign-in-with-chatgpt/`、`/consent`)主动调 workspace/select;`/choose-an-account` 用 `_choose_account_select` 选第一个 `us_*`,**每条链路只选一次**(`chose_account` 标志,防 200/同 URL 死循环)。
4. **分支兜底**(按顺序尝试,任一拿到 code 即止):
   - 落到 `/log-in` → `_codex_drive_login_from_log_in`(`platforms/chatgpt/protocol/auth_flow.py:791`)补走一次纯协议登录(重新 sentinel + `authorize_continue`),拿到 `continue_url` 后继续跟;
   - `continue_url` 命中 add-phone 且 `OAUTH_CODEX_ADD_PHONE_REFRESH_RETRY=1`(默认开)→ `_codex_refresh_retry_after_add_phone`(`platforms/chatgpt/protocol/auth_flow.py:1278`),去掉 `prompt` 后重复发起 authorize,次数 `OAUTH_CODEX_ADD_PHONE_REFRESH_RETRY_COUNT`(默认 3)、间隔 `OAUTH_CODEX_ADD_PHONE_REFRESH_SLEEP`(默认 1.2s),期望命中不需要 add-phone 的分支;
   - authorize **直接**落到 `/add-phone` 且配了 `_sms_callback` → `_handle_add_phone_via_sms` 绑号后重新 authorize,再不行去掉 `prompt` 再来一次;
   - 通用兜底:`_drop_query_keys(auth_url, {"prompt"})` 去掉 `prompt=login` 重发一次(`platforms/chatgpt/protocol/auth_flow.py:1390`-`:1397`)。
5. **换 token**(`_exchange_codex_callback_code`,`platforms/chatgpt/protocol/auth_flow.py:728`):先校 `code` 非空、`state` 与期望一致(不一致直接 False);再 `POST https://auth.openai.com/oauth/token`,`Content-Type: application/x-www-form-urlencoded`,body 五个字段 `grant_type=authorization_code` / `client_id` / `code` / `redirect_uri` / `code_verifier`。200 则把 `id_token` / `access_token` / `refresh_token` 写回 `self.result`(**用 `data.get(k, 旧值)` 形式,响应缺字段不会把已有值抹成空**)。
6. **收尾**:`run_register` 会再拉一次 `get_auth_session()`(`platforms/chatgpt/protocol/auth_flow.py:3159`),因为 Codex 流程可能刷新了 cookie 与 `access_token`。

#### 3.3 开关一览

| 开关 | 默认 | 作用 |
|------|------|------|
| `OAUTH_CODEX_RT_EXCHANGE` | `1` | 常规调用点总开关,关掉就完全不换 RT |
| `OAUTH_CODEX_RT_BEFORE_CALLBACK` | `1` | 是否在消费 callback 前抢跑一次 |
| `OAUTH_CODEX_RT_ALLOW_RETRY` | `0` | **绕过 `_codex_rt_attempted` 幂等守卫**。默认一轮只试一次:失败多半是账号状态问题(要 add-phone / 被风控),重试只是多打几发同样的请求,徒增风控特征。确认是偶发网络抖动才开 |
| `OAUTH_CODEX_ADD_PHONE_REFRESH_RETRY` | `1` | 命中 add-phone 时是否刷新重试 |
| `OAUTH_CODEX_ADD_PHONE_REFRESH_RETRY_COUNT` | `3` | 刷新重试轮数 |
| `OAUTH_CODEX_ADD_PHONE_REFRESH_SLEEP` | `1.2` | 刷新重试间隔(秒) |
| `OAUTH_CODEX_CLIENT_ID` / `_REDIRECT_URI` / `_SCOPE` / `_PROMPT` | 见第六章 | 覆盖 Codex OAuth 四参数 |
| `OAUTH_REFRESH_ONLY` | `0` | 只要 RT:跳过 `_consume_callback_for_session` 与 `get_auth_session`,终态判据放宽为 `refresh_token or access_token`(`platforms/chatgpt/protocol/auth_flow.py:3130`、`:3161`) |

#### 3.4 失败后的降级行为

**Codex 换 RT 失败绝不会让注册失败。** 整个 `oauth_codex_rt_exchange` 被一个 `try/except Exception` 兜住(`platforms/chatgpt/protocol/auth_flow.py:1409`-`:1411`),异常只降级成 `logger.warning("Codex OAuth 交换异常: …")` 并 `return False`;没捕到 code 时打 debug 日志带 `final_url` 前 180 字符(`platforms/chatgpt/protocol/auth_flow.py:1400`)。

后果分三档:
- **非 `OAUTH_REFRESH_ONLY` 模式**:`result.refresh_token` 为空,但 `session_token` + `access_token` 齐全 → `is_valid()` 为真 → 注册照样成功落库。产物是"AT/Session 有、RT 无"的号,后续要 RT 只能重跑登录链路。
- **`OAUTH_REFRESH_ONLY=1` 模式**:终态判据是 `refresh_token or access_token`,两者全空才抛 `RuntimeError("流程完成但未获取 refresh_token/access_token")`(`platforms/chatgpt/protocol/auth_flow.py:3163`)。
- **抢跑失败**:因为 `_codex_rt_attempted` 已置位,后面那个常规调用点默认**不会再试**;想让它补一枪必须开 `OAUTH_CODEX_RT_ALLOW_RETRY=1`。这是个容易踩的坑:看日志会看到 Codex 只跑了一次,而代码里明明有两个调用点。

### 四、注册模式矩阵

两个维度互相独立,由 `platforms/chatgpt/registration_engine.py` 决定:

- **要不要 RT**:`mode` 取 `REGISTRATION_MODE_REFRESH_TOKEN`(默认)或 `REGISTRATION_MODE_ACCESS_TOKEN_ONLY`(`platforms/chatgpt/registration_engine.py:37`-`:38`)。
- **用什么身份注册**:`register_flow` 取 `email` / `phone` / `phone_with_email`(`platforms/chatgpt/registration_engine.py:41`-`:43`)。

分派点是 `ChatGPTRegistrationEngine.run()`(`platforms/chatgpt/registration_engine.py:126`):`phone` 与 `phone_with_email` 都进 `_run_phone()`,其余进 `_run_email()`。

#### 4.1 六种组合

| 组合 | 代码分支入口 | 身份提交方式 | 产物 AT | 产物 RT | 产物 Session | 相对耗时 |
|------|--------------|--------------|---------|---------|--------------|----------|
| **有RT × 邮箱** | `platforms/chatgpt/registration_engine.py:131` `_run_email` → `platforms/chatgpt/protocol/auth_flow.py:2811` `run_register` | `authorize/continue` `username.kind="email"` | ✅ 先来自 `/api/auth/session`,再被 `oauth/token` 覆盖 | ✅ `oauth_codex_rt_exchange` | ✅ `__Secure-next-auth.session-token` | 基准(实测完整链约 100s) |
| **有RT × 手机** | `platforms/chatgpt/registration_engine.py:147` `_run_phone`(`bind_email=False`) → `platforms/chatgpt/protocol/phone_flow.py:444` `run_phone_register` | `phone_authorize_continue` `kind="phone_number"` | ✅ | ✅ | ✅ | 基准 + 租号/收短信开销;`_do_phone_register_loop` 最多换 3 个号(`OPENAI_PHONE_MAX_ATTEMPTS`),单号窗口 120s(`OPENAI_PHONE_OTP_TIMEOUT`,下限 40s) |
| **有RT × 手机+绑邮箱** | 同上,`bind_email=True`(`platforms/chatgpt/registration_engine.py:149`) | 手机号注册 + `add_email_send`/`bind_email`(`platforms/chatgpt/protocol/phone_flow.py:355`/`:377`) | ✅ | ✅ | ✅ | 手机链路 + 一次 add-email + 一封邮件 OTP;**绑邮箱失败不影响注册成功**,只在 metadata 里记 `bind_email_error`(`platforms/chatgpt/registration_engine.py:178`-`:181`) |
| **无RT × 邮箱** | `_run_email`,`env_overrides` 里 `OAUTH_CODEX_RT_EXCHANGE=0` + `OAUTH_CODEX_RT_BEFORE_CALLBACK=0`(`platforms/chatgpt/registration_engine.py:358`-`:361`) | 同"有RT × 邮箱" | ✅ 仅来自 `/api/auth/session` | ❌ | ✅ | **基准 − 约 10 秒**:整条 Codex authorize 链 + `oauth/token` 完全不跑 |
| **无RT × 手机** | `_run_phone`,同上两个开关置 0 | 同"有RT × 手机" | ✅ | ❌ | ✅ | 手机链路 − 约 10 秒 |
| **无RT × 手机+绑邮箱** | `_run_phone`,`bind_email=True`,同上两个开关置 0 | 同上 | ✅ | ❌ | ✅ | 手机+绑邮箱链路 − 约 10 秒 |

#### 4.2 无RT 究竟省了哪一步

`access_token_only` 模式省掉的是**整个第三章**:两个 `oauth_codex_rt_exchange` 调用点都不进(抢跑点判 `OAUTH_CODEX_RT_BEFORE_CALLBACK`,常规点判 `OAUTH_CODEX_RT_EXCHANGE`),因此不跑:

1. `_build_codex_authorize` 构 PKCE authorize URL;
2. `_follow_authorize_for_callback` 最多 12 跳的独立授权链(含可能的 workspace/select、`/choose-an-account` 选号);
3. `/log-in` 回落时的 `_codex_drive_login_from_log_in`(那里还要**再跑一次 Sentinel PoW**);
4. add-phone 刷新重试(默认 3 轮 × 1.2s);
5. 去掉 `prompt` 的兜底 authorize;
6. `POST /oauth/token`;
7. `run_register:3159` 那次收尾的 `get_auth_session()`。

源码给出的理由很直白(`platforms/chatgpt/registration_engine.py:359`):**"不要 refresh_token 就别跑 Codex OAuth:每次都要多花约 10 秒且必然告警"**。注意"必然告警" —— 不换 RT 却让它跑,日志里会稳定出现 Codex 相关 warning,污染排错视线。

其余阶段(warmup → csrf → authorize → continue → 密码 → OTP → create_account → redirect chain → session)在两种模式下**完全一致**,无RT 模式不省任何注册动作,只省 token 换取。

#### 4.3 与模式相关的三个易错点

1. **`WEBUI_ALLOW_LOGIN` 被引擎硬置为 `1`**(`platforms/chatgpt/registration_engine.py:355`)。协议层默认是不允许的(fast-fail 换号),但引擎认为"单任务场景下 fast-fail 没有意义,外层本来就会换下一个邮箱重试",所以从 WebUI 跑一定走 OTP 登录路径。直接调协议层(CLI)时行为相反,别把两种日志混着看。
2. **`bind_2fa` 会改变 Codex 的执行位置**(`platforms/chatgpt/registration_engine.py:253`)。开 2FA 才挂 `on_session_ready`,而挂了钩子就等于关掉 callback 前那次 Codex 抢跑 —— 顺序变成 `拿 session → 绑 2FA → Codex 授权`。不开 2FA 则一个钩子都不挂。
3. **末段失败会被抢救成成功**(`_salvage`,`platforms/chatgpt/registration_engine.py:387`)。只要 `access_token` / `session_token` / `refresh_token` 任一到手就按成功返回;`mode=refresh_token` 而 RT 为空时打 `metadata["partial"]=True` 与 `last_error`。典型场景就是 Codex 换 RT 失败:号完全可用,只是没 RT,重跑一遍反而浪费一个邮箱。所以**"任务显示成功"不等于"RT 已到手"**,要看 `metadata.partial`。

### 五、会话与 cookie 管理

#### 5.1 必需的 cookie

| cookie | 何时产生 | 作用 | 缺失后果 |
|--------|----------|------|----------|
| `oai-did` | warmup 阶段 `GET chatgpt.com` | 设备标识。`POST /api/auth/signin/openai` 靠它决定返 `auth.openai.com/authorize` URL 还是 NextAuth 页 | `authorize/continue` 必 409 invalid_state(实测 5/5) |
| `__Host-next-auth.csrf-token` | `GET /api/auth/csrf` | NextAuth CSRF 双提交校验 | 拿不到 `csrfToken`,`signin/openai` 无法提交 |
| `oai-client-auth-session` | authorize 链路中服务端下发 | 分段 base64 里带 `workspace_id`/`workspaces[].id`,`_extract_workspace_id` 靠它 | 只能退到 HTML 正则兜底(`platforms/chatgpt/protocol/auth_flow.py:2534`) |
| `__Secure-next-auth.session-token` | 消费 callback 后由 NextAuth `Set-Cookie` | 就是 `result.session_token`,`is_valid()` 的必要条件之一 | 非 `OAUTH_REFRESH_ONLY` 模式下注册判失败 |
| `__Secure-next-auth.callback-url` | NextAuth 流程内 | 回跳目标 | 一般不致命 |
| `cf_clearance` / `__cf_bm` / `_cfuvid` / `__cflb` | Cloudflare 下发 | 过 CF 挑战 | 只拿到 `__cf_bm` 而没有 `oai-did` 就是被 CF 403 的典型征候 |
| `oai-sc`、`oai-client-auth-info`、`oai-gn`、`oai-nav-state`、`oai-hlib`、`__stripe_mid`/`__stripe_sid`、`_puid` 等 | 各阶段陆续种上 | `/backend-api/payments/checkout` 的 modern/custom 入口不只校验 session-token,还校验这一批同域 cookie | 后置的付费/checkout 能力不可用 |

#### 5.2 阶段间如何传递与持久化

- **传递靠一个共享的 `curl_cffi` session**:`self.session = create_http_session(...)`(`platforms/chatgpt/protocol/auth_flow.py:118`)。所有阶段共用同一个 cookie jar,不手动搬 cookie。**代价**:`warmup` 重试和 `_rotate_impersonate_session` 都会**重建 session,cookie 全清**(`platforms/chatgpt/protocol/auth_flow.py:1639`、`:1433`),这在 warmup 阶段是对的(失败轮没种到有用东西),但意味着 TLS 重试不能发生在拿到关键 cookie 之后。
- **按名取值统一走 `_get_cookie_value_by_name`**(`platforms/chatgpt/protocol/auth_flow.py:487`):**优先 `chatgpt.com` 域**,避免跨域同名冲突;jar 里找不到再退到 `session.cookies.get(name, domain=".chatgpt.com")`。`tests/test_auth_flow_cookies.py:8` `test_cookie_lookup_prefers_chatgpt_domain_when_name_is_duplicated` 锁死了这条优先级。
- **session token 三路兜底**(`_extract_session_cookie`,`platforms/chatgpt/protocol/auth_flow.py:2686`):直接 `get` → 遍历 jar 按名精确匹配 → `_get_cookie_value_by_name`。因为 curl_cffi 某些情况下按 domain 隔离 cookie,单靠 `get(name)` 拿不到。`get_auth_session` 之上还有一层:cookie 拿不到就用 JSON 里的 `sessionToken`/`session_token`(`platforms/chatgpt/protocol/auth_flow.py:2746`-`:2751`)。
- **持久化产物是 `result.cookie_header`**:`_build_chatgpt_cookie_header()`(`platforms/chatgpt/protocol/auth_flow.py:173`)导出当前 jar 里所有 `chatgpt.com` 域 cookie,再按一张 24 项的 `critical_names` 清单(`platforms/chatgpt/protocol/auth_flow.py:209`-`:236`)兜底补齐(应对不同 cookiejar 迭代行为差异),拼成 `name=value; …`。每次 `get_auth_session()` 都会刷新它(`platforms/chatgpt/protocol/auth_flow.py:2758`)。
- **⚠️ `_build_chatgpt_cookie_header` 里有一段死代码**:`platforms/chatgpt/protocol/auth_flow.py:245` 已经 `return`,`:246`-`:254` 那段初始化 `_trace_dump_path` 的逻辑**永远不会执行**。看起来是从 `__init__` 误粘过来的,导致 `AUTH_TRACE_DUMP=1` 时 `_trace_dump_path` 一直是空串。开抓包发现 `outputs/` 下没有 jsonl 时,先查这里。
- **不落磁盘**:协议层不认识 `webui.db`,cookie/密码怎么存由回调决定 —— `on_password`(`platforms/chatgpt/protocol/auth_flow.py:2084`)与 `on_session_ready`(`platforms/chatgpt/protocol/auth_flow.py:3146`)是唯一两个出口。

#### 5.3 `protocol/config.py` 配置项清单

`platforms/chatgpt/protocol/config.py` 全文只有一个 dataclass、一个字段:

| 字段 | 类型 | 默认 | 含义 |
|------|------|------|------|
| `proxy` | `Optional[str]` | `None` | 出口代理 URL,例 `socks5://user:pass@host:port`;`None` 走系统直连 |

模块 docstring 明确了边界(`platforms/chatgpt/protocol/config.py:3`-`:4`):**"协议层只关心出口代理,其余行为(OTP 超时、Codex 交换开关等)一律通过 `AuthFlow(env_overrides=...)` 注入,不读进程环境变量。"**

#### 5.4 并发隔离:任务级参数为什么不写进程环境变量

这是本模块最重要的一条架构约束,证据链有三处:

1. **`__init__` 里的注释**(`platforms/chatgpt/protocol/auth_flow.py:93`-`:97`)说明了病因:"以前 registrar 是直接写 `os.environ` 再在 finally 里还原的,但 auto_loop 会并发跑多个 worker —— A 写的 `OTP_TIMEOUT` 会被 B 看见,B 跑完还原成 A 之前的值,A 后半程就读到别人的配置了。现在覆盖值只挂在实例上,进程全局环境一个字节都不动。"
2. **读取入口只有一个**:`_get_env(name, default)`(`platforms/chatgpt/protocol/auth_flow.py:559`)先查 `self._env_overrides`,只有该 key 不存在(`is None`)时才回退 `os.getenv`。派生的 `_env_flag`(`platforms/chatgpt/protocol/auth_flow.py:569`)**为此从 `@staticmethod` 改成了实例方法**,注释特意说明"调用点全是 `self._env_flag(...)`,签名不变"。这个签名变更就是隔离设计留下的疤。
3. **注入方只填翻译层**:`ChatGPTRegistrationEngine._env_overrides()`(`platforms/chatgpt/registration_engine.py:348`)把仓库配置翻译成协议层开关后作为构造参数传入(`platforms/chatgpt/registration_engine.py:250`);模块 docstring 再强调一次"所有环境相关的开关都通过 `env_overrides` 以实例级配置传进去,进程环境变量一个字节都不动 —— 多个注册任务并发跑时互不污染"(`platforms/chatgpt/registration_engine.py:11`-`:13`)。

引擎实际下传的键(`platforms/chatgpt/registration_engine.py:350`-`:372`):`OTP_TIMEOUT`(默认 180,可被 `mailbox_otp_timeout_seconds`/`email_otp_timeout_seconds`/`otp_timeout` 覆盖)、`WEBUI_ALLOW_LOGIN=1`、`access_token_only` 时的 `OAUTH_CODEX_RT_EXCHANGE=0` + `OAUTH_CODEX_RT_BEFORE_CALLBACK=0`、以及四个手机链路参数 `OPENAI_PHONE_OTP_TIMEOUT` / `OPENAI_PHONE_MAX_ATTEMPTS` / `OPENAI_PHONE_OTP_CODE_RETRIES` / `OPENAI_PHONE_NUMBER`。

协议层认得的全部开关(散落在各 `_get_env`/`_env_flag` 调用点):

| 开关 | 默认 | 位置 |
|------|------|------|
| `OAUTH_IMPERSONATE` | `chrome146` | `platforms/chatgpt/protocol/auth_flow.py:104` |
| `AUTH_HTTP_TRACE` | `0` | `platforms/chatgpt/protocol/auth_flow.py:144` |
| `AUTH_TRACE_DUMP` | `0` | `platforms/chatgpt/protocol/auth_flow.py:164` |
| `AUTH_TRACE_INCLUDE_COOKIE` | `0` | `platforms/chatgpt/protocol/auth_flow.py:165` |
| `OTP_TIMEOUT` | `60`(引擎传 180) | `platforms/chatgpt/protocol/auth_flow.py:2899`、`:2945` |
| `OTP_FORCE_RESEND` | `0` | `platforms/chatgpt/protocol/auth_flow.py:3011` |
| `LOGIN_PASSWORD` | 空 | `platforms/chatgpt/protocol/auth_flow.py:2230` |
| `WEBUI_ALLOW_LOGIN` | 空(不允许) | `platforms/chatgpt/protocol/auth_flow.py:2846` |
| `OAUTH_CODEX_RT_EXCHANGE` | `1` | `platforms/chatgpt/protocol/auth_flow.py:3155` |
| `OAUTH_CODEX_RT_BEFORE_CALLBACK` | `1` | `platforms/chatgpt/protocol/auth_flow.py:3119` |
| `OAUTH_CODEX_RT_ALLOW_RETRY` | `0` | `platforms/chatgpt/protocol/auth_flow.py:1315` |
| `OAUTH_CODEX_CLIENT_ID` / `_REDIRECT_URI` / `_SCOPE` / `_PROMPT` | 见第六章 | `platforms/chatgpt/protocol/auth_flow.py:609`-`:615` |
| `OAUTH_CODEX_ADD_PHONE_REFRESH_RETRY` / `_COUNT` / `_SLEEP` | `1` / `3` / `1.2` | `platforms/chatgpt/protocol/auth_flow.py:1343`-`:1350` |
| `OAUTH_REFRESH_ONLY` | `0` | `platforms/chatgpt/protocol/auth_flow.py:3130` |
| `OPENAI_PHONE_OTP_TIMEOUT` / `OPENAI_PHONE_MAX_ATTEMPTS` / `OPENAI_PHONE_OTP_CODE_RETRIES` | `120` / `3` / `2` | `platforms/chatgpt/protocol/phone_flow.py:510`-`:515` |
| `OPENAI_PHONE_NUMBER`(+ `OPENAI_PHONE_OTP_CMD` / `OPENAI_PHONE_OTP`) | 空 | `platforms/chatgpt/protocol/auth_flow.py:1231`(无接码平台时的环境变量回退路径,`_handle_add_phone_via_env` `platforms/chatgpt/protocol/auth_flow.py:1224`) |

⚠️ 新增开关时**必须**用 `self._get_env` / `self._env_flag`,直接 `os.getenv` 会绕过隔离,并发场景下再现上面那个串配置的 bug。

### 六、硬编码清单与脆弱点

#### 6.1 `platforms/chatgpt/constants.py` 里的硬编码

| 常量 | 值 | 位置 | 实际被谁用 | 失效后果 |
|------|-----|------|-----------|----------|
| `OAUTH_CLIENT_ID` | `app_EMoamEEZ73f0CkXaXp7hrann` | `platforms/chatgpt/constants.py:53` | `platforms/chatgpt/token_refresh.py:58`;`platforms/chatgpt/protocol/auth_flow.py:609` 里**又独立硬编码了一份同样的值**作为 `OAUTH_CODEX_CLIENT_ID` 的默认 | OpenAI 换 Codex CLI 的 client_id → `oauth/token` 报 `invalid_client`,RT 全线拿不到。**要改必须改两处** |
| `OAUTH_AUTH_URL` | `https://auth.openai.com/oauth/authorize` | `platforms/chatgpt/constants.py:54` | 只进 `DEFAULT_SETTINGS`;`platforms/chatgpt/protocol/auth_flow.py:632` 自己拼同一个 URL | 端点迁移 → Codex 授权链第一跳就 404 |
| `OAUTH_TOKEN_URL` | `https://auth.openai.com/oauth/token` | `platforms/chatgpt/constants.py:55` | 同上;实际请求写死在 `platforms/chatgpt/protocol/auth_flow.py:762` | 换 RT 直接失败,注册仍成功(降级为无 RT) |
| `OAUTH_REDIRECT_URI` | `http://localhost:1455/auth/callback` | `platforms/chatgpt/constants.py:56` | `platforms/chatgpt/token_refresh.py:59`;`platforms/chatgpt/protocol/auth_flow.py:610` 独立硬编码同值 | 与服务端登记的回调不一致 → authorize 报 `invalid_redirect_uri`,或 `_callback_has_code` 永远匹配不上(基址比较是精确相等,`platforms/chatgpt/protocol/auth_flow.py:640`-`:642`) |
| `OAUTH_SCOPE` | `openid email profile offline_access` | `platforms/chatgpt/constants.py:57` | 同上;`platforms/chatgpt/protocol/auth_flow.py:611` 独立硬编码同值 | 少了 `offline_access` → 服务端不下发 `refresh_token`,但 `oauth/token` 仍 200,表现为"交换成功但 RT 为空",最难排查的一类 |
| `OPENAI_API_ENDPOINTS` | 10 个端点(sentinel / signup / register / password_verify / passwordless_send_otp / send_otp / validate_otp / create_account / select_workspace / select_organization) | `platforms/chatgpt/constants.py:60`-`:71` | 只有 `sentinel` 被 `platforms/chatgpt/http_client.py:147` 用;**其余 9 个是文档性质**,`auth_flow.py` 各处把 URL 写死在调用点 | 改这里不会改变实际请求。端点真的迁移时要逐个搜 `auth_flow.py` 里的字面量 |
| `OPENAI_PAGE_TYPES` | `email_otp_verification` / `create_account_password` / `login_password` | `platforms/chatgpt/constants.py:74`-`:78` | **无人引用**;`platforms/chatgpt/protocol/auth_flow.py:1973`/`:1981`/`:2949` 全用字符串字面量比较 | 服务端改 `page.type` 命名 → `signup()` 落进"未知 page_type"分支,按已有账号处理(保守但会误判新号) |
| `MIN_REGISTRATION_AGE` / `MAX_REGISTRATION_AGE` | `20` / `45` | `platforms/chatgpt/constants.py:159`-`:160` | `generate_random_user_info()`(`platforms/chatgpt/constants.py:171`)与 `platforms/chatgpt/utils.py:65` `generate_random_birthday()`;**`create_account` 都没用**,它自己写死 `randint(1985, 2000)`(`platforms/chatgpt/protocol/auth_flow.py:2366`) | 改这两个常量对注册链**无效**。`tests/test_chatgpt_birthdate_range.py` 测的是这两个未被注册链使用的函数,绿灯**不代表** `create_account` 的生日合规 |
| `OTP_CODE_PATTERN` / `OTP_CODE_SEMANTIC_PATTERN` | `(?<!\d)(\d{6})(?!\d)` / `(?:code\s+is\|验证码[是为]?\s*[:：]?\s*)(\d{6})` | `platforms/chatgpt/constants.py:127`、`:134` | 邮件取码 | OpenAI 改成 8 位码或改邮件模板 → 取不到码,表现为 OTP 超时 |
| `OTP_MAX_ATTEMPTS` | `40` | `platforms/chatgpt/constants.py:128` | 收件轮询上限 | 太小则慢投递的邮件收不到 |
| `OPENAI_EMAIL_SENDERS` / `OPENAI_VERIFICATION_KEYWORDS` | 4 个发件人模式 / 6 个关键词 | `platforms/chatgpt/constants.py:137`、`:145` | 邮件筛选 | OpenAI 换发件域(如新的 `*.openai.com` 子域)或改标题 → 邮件被过滤掉 |
| `MICROSOFT_TOKEN_ENDPOINTS` / `OUTLOOK_IMAP_SERVERS` / `MICROSOFT_SCOPES` | Live/Consumers/Common 三端点、两个 IMAP 主机、三组 scope | `platforms/chatgpt/constants.py:351`-`:374` | `core/base_mailbox.py:3640`/`:3925`/`:3945` | 微软侧端点或 scope 策略变更 → Outlook 号池收不到码 |

**UA 与指纹相关的硬编码不在 `constants.py`**,分散在别处:

| 项 | 值 | 位置 | 失效后果 |
|----|-----|------|----------|
| 默认 impersonate | `chrome146` | `platforms/chatgpt/protocol/auth_flow.py:104`(`OAUTH_IMPERSONATE` 默认值) | curl_cffi 不认这个 target 就直接起不来 session |
| 同族回退链 | `[preferred, chrome142, chrome136]` | `platforms/chatgpt/protocol/auth_flow.py:111` | 版本停更后回退到过旧指纹,反而更可疑 |
| 兜底 UA | 写死的 macOS 14_5 / Safari 18.0 UA 串 | `platforms/chatgpt/protocol/http_client.py:25`-`:28` | 只在没传 `user_agent` 时用;版本老化 → CF 眼里是过时浏览器 |
| Sentinel SDK / req 端点 | `https://sentinel.openai.com/sentinel/<版本>/sdk.js`、`https://sentinel.openai.com/backend-api/sentinel/req` | `platforms/chatgpt/protocol/sentinel_quickjs.py:35`-`:36` | 版本号或 URL 变更 → PoW 拿不到 token,`authorize/continue` 被风控 |
| Codex 专属 authorize 参数 | `id_token_add_organizations=true`、`codex_cli_simplified_flow=true` | `platforms/chatgpt/protocol/auth_flow.py:627`-`:628` | 参数被废弃 → 授权链落到非预期页面 |
| `signin/openai` 的 query | `prompt=login`、`screen_hint=login_or_signup`、`ext-passkey-client-capabilities=1111` | `platforms/chatgpt/protocol/auth_flow.py:1765`-`:1769` | 参数改名 → 拿不到 authorize URL |
| 各阶段 Referer 字面量 | `create-account`、`create-account/password`、`email-verification`、`about-you`、`mfa-challenge`、`sign-in-with-chatgpt/codex/consent` 等 | 散布于 `_common_headers(referer=…)` 各调用点 | **Referer 决定服务端认定的页面状态**,路由改名会直接引发 invalid_state |
| `us_[A-Za-z0-9]{16,}` / `workspace_id` 三条正则 | — | `platforms/chatgpt/protocol/auth_flow.py:2443`、`:2542`-`:2544` | 前端 SSR 结构一改就抽不出 id,多账号页与 workspace 页卡死 |

#### 6.2 脆弱点

1. **全链路强依赖 OpenAI 的私有接口与响应结构。** `/api/accounts/*` 全是内部接口,无版本号、无兼容承诺。状态机的每一次分流都基于 `page.type` 与 `continue_url` 的字面值(`create_account_password`、`email_otp_verification`、`passwordless_signup`、`login_password`、`email_otp_send`、`external_url`),任何一个改名都会让 `signup()` 掉进"未知 page_type → 按已有账号处理"的保守分支(`platforms/chatgpt/protocol/auth_flow.py:1993`-`:2002`),表现为新号被当成老号、走错发码端点、最终 409。
2. **反爬对抗的结论有明确保质期。** 源码 docstring 里每条实测都带日期,且**已经互相推翻过**:2026-08-10 的结论是"补齐 client hints 后 chrome 全好",到 2026-08-21 就变成"CF 改成按家族封,chrome 全族 403"(`platforms/chatgpt/protocol/auth_flow.py:1604`-`:1615`)。默认 `chrome146` 今天可用,不代表下周可用。看到大面积 warmup 403,先试 `OAUTH_IMPERSONATE=mac_safari` 或 `firefox133`,不要先怀疑代码。
3. **同一份配置被硬编码了两遍。** Codex 的 client_id / redirect_uri / scope 在 `platforms/chatgpt/constants.py:53`-`:57` 与 `platforms/chatgpt/protocol/auth_flow.py:609`-`:611` 各有一份**内容相同但彼此无引用**的字面量;`constants.py` 那份实际只喂给 `token_refresh.py` 和 `DEFAULT_SETTINGS`。改一处而漏另一处,会做出"设置页显示新值、实际请求仍用旧值"的幽灵故障。
4. **生日/姓名生成有两套实现,测试只覆盖了没被用的那套。** `create_account` 用 `platforms/chatgpt/protocol/auth_flow.py:2360`-`:2366` 的局部实现(年份写死 1985–2000、名字池只有 20+14 个);`platforms/chatgpt/constants.py:171` 的 `generate_random_user_info()` 与 `platforms/chatgpt/utils.py:65` 的 `generate_random_birthday()` 才是被 `tests/test_chatgpt_birthdate_range.py` 覆盖的,但注册链一次都不调。随着时间推移,1985–2000 对应的年龄区间会持续漂移(2026 年是 26–41 岁,再过十年成 36–51 岁),而测试永远绿。
5. **姓名池太小,是可被聚类的注册特征。** 20 个名 × 14 个姓 = 280 种组合,批量注册时重名率极高。
6. **cookie 一旦被 session 重建就全丢。** `warmup` 重试与 `_rotate_impersonate_session` 都会重建 session;后者可能在 `get_csrf_token` 阶段触发(`platforms/chatgpt/protocol/auth_flow.py:1732`),此时丢 cookie 尚可接受,但这个模式不能被复制到拿到 `oai-did` 之后的任何阶段。
7. **`_build_chatgpt_cookie_header` 尾部有 9 行不可达代码**(`platforms/chatgpt/protocol/auth_flow.py:246`-`:254`,`return` 之后),它本该初始化 `_trace_dump_path`。结果是 `AUTH_TRACE_DUMP=1` 时抓包文件路径始终为空 —— 排错工具本身是坏的。
8. **`create_account` 的 sentinel flow 名与其它三处不一致。** 注释写 `flow 为 create_account`,代码实际传 `oauth_create_account`(`platforms/chatgpt/protocol/auth_flow.py:2340`、`:2345`)。flow 名与服务端不匹配是明确的风控特征,改动前务必抓包确认真实值。
9. **`AuthResult.is_valid()` 只校验 `session_token and access_token`**(`platforms/chatgpt/protocol/auth_flow.py:61`),不含 RT。配合 `_salvage`(`platforms/chatgpt/registration_engine.py:387`)的"部分成功也算成功",一个 `mode=refresh_token` 的任务可能报成功却没有 RT。判断产物完整性要看 `metadata.partial`,不要只看 `success`。

---

## 五、ChatGPT 反检测层与插件装配

### Sentinel PoW：为什么这是整个项目里最隐蔽的坑

#### 一句话结论

OpenAI 的 `authorize/continue`、`send_otp` 等发码相关接口都要求携带 Sentinel PoW token。**这个 token 只能由 OpenAI 官方的 `sdk.js` 在一套完整的浏览器运行时里算出来**，项目的做法是：拉下真实的 `sdk.js`，在 `node:vm` 沙箱里给它伪造一整套 `window/document/navigator/canvas/WebGL`，然后调用它自己的 API 取 token。**没有 Node 运行时 = 注册链路全绿但验证码永远收不到。**

#### 为什么必须有 Node，不能纯 Python 算

`platforms/chatgpt/protocol/sentinel_quickjs.py:1-8` 的模块 docstring 把原因写死了，这是全项目最重要的一段注释：

| 环节 | 自造（纯 Python 合成）token 的表现 |
|---|---|
| `POST /backend-api/sentinel/req` | **200**，正常下发 challenge |
| `POST /api/accounts/authorize/continue` | **200**，返回正常的 `page.type` |
| Python 侧日志 | **完全没有报错**，`[4/10] Sentinel`、`[5/10] 提交注册邮箱` 都打成功 |
| 发码服务（服务端异步复核） | 用真正的 sentinel SDK 重算 token → **不匹配** |
| 最终结果 | **验证码邮件被静默丢弃**，不回 4xx、不回错误码、不发任何信号 |

##### 完整原因链（为何日志看不到错、码永远收不到）

1. `/sentinel/req` 只做 **表层校验**：它校验的是请求体 `{p, id, flow}` 的形态与 `p`（requirements proof）的语法合法性，不做完整的 PoW 语义复核，所以一个"看起来像"的 token 就能换到 challenge。
2. `authorize/continue` 同理，它只把 `openai-sentinel-token` 转交给下游，自身按 HTTP 语义返回 200 —— 于是 Python 侧的 `resp.status_code != 200` 检查（`platforms/chatgpt/protocol/auth_flow.py:1935`）永远不会触发。
3. 真正的复核发生在**下游的发码服务**，它是异步的、带外的：它拿 token 去跑服务端 sentinel SDK 做二次验证，失败就**不投递邮件**并静默结束。
4. 这条失败路径**不产生任何面向客户端的响应**——没有 HTTP 状态码、没有 webhook、没有错误 JSON。客户端能观测到的唯一现象就是"轮询邮箱一直空"。
5. 于是排查者会被误导去查邮箱适配器、代理出口 IP、域名投递率，而真因在几十步之前的 PoW 环节。

因此代码在缺 Node 时**不静默降级**，而是显式抛出把原因说透的错误：`platforms/chatgpt/protocol/sentinel_quickjs.py:108-115` 单独捕获 `FileNotFoundError`，错误文案直接写"缺少它会导致验证码邮件被服务端静默丢弃"。同样地，`platforms/chatgpt/protocol/sentinel.py:81` 在求解失败时抛 `RuntimeError("…中止注册以避免封号")`，宁可整单失败也不带着废 token 往下走。

#### sentinel_quickjs.py：Node 沙箱怎么起、传什么、拿什么

##### 关键常量与路径解析

| 项 | 位置 | 值 / 行为 |
|---|---|---|
| SDK 版本号 | `platforms/chatgpt/protocol/sentinel_quickjs.py:34` | `SENTINEL_VERSION = "20260219f9f6"`，硬编码 |
| SDK 下载地址 | `platforms/chatgpt/protocol/sentinel_quickjs.py:35` | `https://sentinel.openai.com/sentinel/{VERSION}/sdk.js` |
| challenge 接口 | `platforms/chatgpt/protocol/sentinel_quickjs.py:36` | `https://sentinel.openai.com/backend-api/sentinel/req` |
| Node 可执行文件 | `platforms/chatgpt/protocol/sentinel_quickjs.py:39-40` | `os.getenv("OPENAI_SENTINEL_NODE_PATH")` → 空则回退字面量 `"node"`（靠 PATH 查找） |
| JS 胶水脚本 | `platforms/chatgpt/protocol/sentinel_quickjs.py:43-44` | 与 `.py` 同目录的 `openai_sentinel_quickjs.js` |

`OPENAI_SENTINEL_NODE_PATH` 的作用与回退：容器/裸机里 `node` 不在 `PATH`（常见于精简镜像、nvm 未 source）时，用这个环境变量填**绝对路径**。回退链只有一级：**环境变量 → `"node"`**，没有第三级兜底，因此该变量填错等同于没装 Node。脚本文件不存在时（`platforms/chatgpt/protocol/sentinel_quickjs.py:196-198`）只 log 一行并 `return None`，由上层 `sentinel.py` 转成 `RuntimeError`。

##### sdk.js 缓存

`_ensure_sdk_file()`（`platforms/chatgpt/protocol/sentinel_quickjs.py:50-82`）两级缓存：进程内全局变量 `_sdk_file_cache` + 磁盘 `{tempdir}/openai-sentinel-demo/{SENTINEL_VERSION}/sdk.js`。下载时伪装成浏览器加载脚本（`sec-fetch-dest: script`、`referer: https://auth.openai.com/`）。版本号变化会自动落到新目录，**但版本号是代码里写死的**，不会跟随上游更新。

##### 进程起法与回收

`_run_quickjs_action()`（`platforms/chatgpt/protocol/sentinel_quickjs.py:85-124`）：

| 维度 | 实现 |
|---|---|
| 启动方式 | `subprocess.run([node, script])`，**一次性子进程，不常驻** |
| 输入 | `stdin` 一整段 JSON（`json.dumps(payload)`，含 `action` 字段） |
| 输出 | `stdout` 一整段 JSON，`json.loads` 后必须是 dict |
| sdk.js 传递 | 走环境变量 `OPENAI_SENTINEL_SDK_FILE`（不走命令行参数，避免路径转义问题） |
| 超时 | `max(10, timeout_ms/1000 + 5)` 秒，默认 `timeout_ms=45000` → 50s |
| 回收 | 依赖 `subprocess.run` 的同步语义自动 wait/回收；超时抛 `TimeoutExpired` 并杀子进程 |
| 失败判定 | `returncode != 0` → 取 `stderr`（截断 300 字符）抛 `RuntimeError`；空 stdout 单独报错 |

**每取一次 token 起两次 Node 进程**（requirements + solve）。进程无复用、无池化，因此单账号注册的 Node 冷启动开销是固定成本。

##### 两趟交互的完整时序

| 步 | 谁执行 | action | 输入 | 输出 |
|---|---|---|---|---|
| 1 | Node 子进程 #1 | `requirements` | `env_payload`（指纹画像） | `{request_p}` |
| 2 | **Python** | — | `POST /sentinel/req`，body `{"p": request_p, "id": device_id, "flow": flow}` | challenge JSON（含 `token`、可能含 `so` 块） |
| 3 | Node 子进程 #2 | `solve` | `env_payload` + `request_p` + `challenge` + `flow` + `behavior_duration_ms=4200` | `{token, so_token}` |

注意第 2 步的网络请求由 **Python 侧**发出（`_fetch_sentinel_challenge`，`platforms/chatgpt/protocol/sentinel_quickjs.py:127-157`），走的是主 session，因此**继承 TLS 指纹和代理**；JS 沙箱里的 `fetch` 被刻意做成必抛异常（`platforms/chatgpt/protocol/openai_sentinel_quickjs.js:392`），确保 JS 绝不自己联网、绝不绕过代理泄露真实 IP。`/sentinel/req` 的请求头刻意伪装成 sentinel iframe 发出的：`origin: https://sentinel.openai.com`、`referer: …/frame.html?sv={VERSION}`、`content-type: text/plain;charset=UTF-8`。

##### 传进沙箱的 env_payload 字段

`platforms/chatgpt/protocol/sentinel_quickjs.py:235-252` 构造：

| 键 | 来源 | 缺省 |
|---|---|---|
| `device_id` | 调用方传入，空则 `uuid4()` | 随机 UUID |
| `user_agent` | 指纹 | `Mozilla/5.0` |
| `screen_width` / `screen_height` | 指纹 `screen`（`"1920x1080"` 拆分） | `1920` / `1080` |
| `language` / `languages` | 指纹 `lang` / `lang_full`（按 `,` 拆、去 `;q=`） | `en-US` |
| `platform` | 指纹 `navigator_platform`，未传时**按 UA 推断**（iphone→iPhone / windows→Win32 / mac→MacIntel） | `Win32` |
| `vendor` | 指纹 `navigator_vendor`，未传时按 UA 推断（firefox→`""`、chrome→`Google Inc.`、其它→`Apple Computer, Inc.`） | 推断值 |
| `hardware_concurrency` | 指纹 | `8` |
| `device_pixel_ratio` | 指纹 | `1.0` |
| `max_touch_points` | 指纹 | `0` |
| `timezone` | 指纹（IANA 名，如 `Asia/Tokyo`） | `UTC` |
| `device_memory` | 指纹；**为 `None` 时整个键不下发**，JS 侧保持 `undefined` | 不下发 |
| `browser_type` | 指纹 | `""` |

`device_memory` 的条件下发（`platforms/chatgpt/protocol/sentinel_quickjs.py:250-252`）不是洁癖：`navigator.deviceMemory` 只有 Chromium 暴露，Firefox/Safari 画像下出现这个键本身就是指纹矛盾。

`sec_ch_ua_*` 全套参数虽然在函数签名里（`platforms/chatgpt/protocol/sentinel_quickjs.py:179-184`），但 **QuickJS 路径不使用**，纯粹为了和 `sentinel.py` 的统一签名对齐。

##### SO token：要不要由服务端说了算

这是一个已经踩过的坑，`platforms/chatgpt/protocol/sentinel_quickjs.py:294-315` 记录了完整结论：

| flow | `/sentinel/req` 响应里的 `so` 块 | 真实浏览器是否产出 SO token |
|---|---|---|
| `authorize_continue` | 有，`required=true` | 是 |
| `oauth_create_account` | 有，`required=true` | 是 |
| `username_password_create` | **顶层根本没有 `so` 键** | **否** |

判定逻辑：`so_required = challenge["so"]["required"] is True`；主 token 空 → 一律失败；`so_required and not so_token` → 才算失败。旧实现无条件要求 SO token 非空，把"服务端没要"误判成"我们没算出来"，更糟的是降级时会沿用**上一个 flow** 的 SO token 继续发 —— 给一个明说不需要 SO 的请求塞别的 flow 的凭证，比不发更像机器人特征。

sdk.js 里 SO 采集器的启动条件（去混淆后）：`challenge.so.required === true && typeof challenge.so.collector_dx === 'string'`。

##### 异常分类：TLS 瞬断 vs PoW 算不出来

`platforms/chatgpt/protocol/sentinel_quickjs.py:319-333` 与 `platforms/chatgpt/protocol/sentinel.py:86-94` 是同一处修复的两半。旧代码是纯 catch-all：任何异常都降级成一行 INFO + `return None`，上层只看到"主 token 缺失"，真因全被掩盖。实际事故是 `/sentinel/req` 那个 POST 撞了链路级 TLS 瞬断（`curl:(35)`，全局约 5.4% 偶发），日志却写着"Sentinel QuickJS 失败（主 token 缺失…）"，排查方向被带偏一整轮。

现在两处都调用 `http_client._is_tls_handshake_error(e)` 判别：

| 异常类别 | 处理 | 目的 |
|---|---|---|
| TLS 握手/瞬断类 | **原样上抛**，不包装 | 让 `registrar.classify_error` 稳定判成 `network`；让 `http_client` 的 TLS 重试有机会兜住 |
| 其它（真 JS/PoW 问题） | `return None` → 上层转 `RuntimeError` | 语义准确，不误报成网络问题 |

#### openai_sentinel_quickjs.js：610 行胶水在做什么

##### 三处对 sdk.js 源码的字符串补丁

`platforms/chatgpt/protocol/openai_sentinel_quickjs.js:10-21`，加载前对下载来的混淆代码做定点替换，目的是把内部符号暴露到全局：

| 补丁 | 作用 |
|---|---|
| `var SentinelSDK=` → `globalThis.SentinelSDK=` | 让沙箱能拿到 SDK 对象（原本是模块私有变量） |
| `var P=new _;` → 追加 `globalThis.__debugP=P;` | 暴露 SDK 内部实例，用于直接调 `getRequirementsToken()` / `getEnforcementToken()` |
| `…t.token=ye,t}({});` → 追加 `t.__debug_n=_n,t.__debug_bindProof=D,t}({});` | 暴露两个内部函数，用于 fallback 路径手工绑定 proof、算 turnstile 的 `t` 值 |

**这三个补丁是 sdk.js 版本强绑定的**：上游一改混淆产物，字符串匹配失效，`replace` 静默不生效（JS 的 `String.replace` 不报错），随后 `context.SentinelSDK` 为 `undefined` → 抛异常 → 子进程 `exit(1)`。这是最脆的一环。

##### 伪造的浏览器运行时清单

`vm.createContext(context)` + `vm.runInContext(sdk, context, {timeout: 10000})`（`platforms/chatgpt/protocol/openai_sentinel_quickjs.js:497-498`）。context 里造了：

| 类别 | 内容 | 位置 |
|---|---|---|
| ECMAScript 内建 | `Math/Date/JSON/Proxy/Reflect/TypedArray/TextEncoder…` 全量透传 | `:252-286` |
| `navigator` | UA、language(s)、platform、vendor、hardwareConcurrency、maxTouchPoints、**`webdriver: false`**、plugins.length=5、mimeTypes.length=2、connection(4g/rtt50)、getBattery、permissions、mediaDevices、sendBeacon | `:202-230` |
| `screen` | width/height/avail*/colorDepth=24/pixelDepth=24/orientation | `:316-324` |
| `window` 尺寸 | inner*/outer*(高+80)/devicePixelRatio/scroll*/pageOffset | `:337-345` |
| `document` | readyState=complete、visibilityState=visible、referrer/URL=`https://auth.openai.com/`、**`cookie: 'oai-did=<device_id>'`**、currentScript.src 指向 sentinel、createElement/querySelector 等一套 DOM API | `:415-486` |
| canvas 2d | fillRect/getImageData/measureText/gradient… 全部空实现，`toDataURL()` 返回空 data URL | `:73-99` |
| WebGL | `WEBGL_debug_renderer_info` 扩展 + 固定 vendor `Google Inc. (Intel)` / renderer `ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)` | `:100-118` |
| 时区伪造 | `Intl.DateTimeFormat` 被包一层，`resolvedOptions().timeZone` 强制返回 `input.timezone` | `:190-200` |
| crypto | `getRandomValues` 走 `node:crypto.randomFillSync`，`randomUUID`、`subtle` 直接借 `webcrypto` | `:232-240` |
| 存储 | `localStorage` / `sessionStorage` 由 `Map` 实现 | `:25-35, 334-335` |
| 网络 | **`fetch` 故意抛 `fetch should not be called`** | `:392` |
| 反检测杂项 | `chrome: {runtime:{}, app:{}}`、`CSS.supports`、`indexedDB`、`matchMedia`、`requestAnimationFrame`/`requestIdleCallback` | `:347-390` |
| 自引用 | `window = globalThis = self = top = parent = context` | `:489-493` |

`origin` / `location` 全部固定为 `https://auth.openai.com`，因为 sdk.js 会校验宿主页面来源。

##### iframe mock：request_p 的截获点

sdk.js 真实运行时会创建一个 iframe 并用 `postMessage` 与之通信。胶水在 `document.createElement('iframe')`（`:443-469`）里返回一个假 iframe：

- `contentWindow.postMessage(message)` 被劫持，**`capturedProof = message.p`** —— 这就是 `request_p` 的来源。
- 立刻用 `setTimeout(…, 0)` 回一个 `{type:'response', requestId, result}` 的假 message 事件，让 SDK 的 Promise 兑现。
- `result` 因 action 而异：`solve` 时返回 `{cachedChatReq: input.challenge, cachedProof: request_p}`（把 Python 拿回来的 challenge 灌回 SDK）；`requirements` 时返回 `null`。
- `body.appendChild` 被重写（`:174-185`），检测到 append 的是 iframe 就 `setTimeout(1ms)` 触发它注册的 `load` 回调，否则 SDK 会一直等 iframe 加载。

##### 行为模拟：SO token 的前置条件

`dispatchBehavior(durationMs)`（`:506-547`）在 `solve` 拿到主 token 之后、取 SO token 之前跑。sdk.js 的行为采集器（session observer）必须"看到"人类交互轨迹才肯出 SO token：

| 事件 | 数量 / 参数 |
|---|---|
| `pointermove` | 12–16 次，起点随机 `x∈[260,420] y∈[180,300]`，每步 `dx∈[5,18] dy∈[-4,12]`，间隔 70–145ms |
| `click` | 1 次，落在最后的鼠标位置 |
| `scroll` | 3–4 次，每次 `scrollY += 35~120`，同步更新 `context.scrollY`/`pageYOffset` |
| `wheel` | 1 次，`deltaY∈[70,140]` |
| `keydown` | 3 次，按键序列固定 `['L','u','Tab']`，`code` 拼成 `KeyL/KeyU/Tab` |
| 补时 | 不足 `behavior_duration_ms`（默认 4200ms）的部分用 `setTimeout` 空等补齐 |

事件总线是手写的（`_listeners: Map`，`:125-156`），`window.addEventListener` / `document.addEventListener` 共用同一个 bucket，`dispatch` 会 `await` 每个回调（吞掉回调异常）。

##### 两个 action 的主逻辑与 fallback

`platforms/chatgpt/protocol/openai_sentinel_quickjs.js:551-610`：

| action | 主路径 | fallback |
|---|---|---|
| `requirements` | `SentinelSDK.init(flow)`（8s 超时竞速）→ 若 `capturedProof` 有值就输出 `{request_p}` | 直接调 `__debugP.getRequirementsToken()` |
| `solve` | `SentinelSDK.token(flow)`（8s 超时）→ `dispatchBehavior()` → `SentinelSDK.sessionObserverToken(flow)`（5s 超时，失败置空）→ 输出 `{token, so_token}` | `__debugP.getEnforcementToken(challenge)` + `__debug_bindProof(challenge, request_p)` + `__debug_n(challenge, turnstile.dx)`，输出 `{final_p, t, so_token: ''}` |

**注意 fallback 的输出形状（`final_p`/`t`）和 Python 侧期望的 `token`/`so_token` 不一致**：`platforms/chatgpt/protocol/sentinel_quickjs.py:308` 只读 `solved["token"]`，走 fallback 时必然为空 → `return None` → 上层中止。也就是说 solve 的 fallback 分支实际上是**死路**，只保留了诊断价值。

最外层 `.catch()` 把 `err.stack` 写 stderr 并 `process.exit(1)`，Python 侧因此能拿到 JS 栈（截断 300 字符）。

#### token 结构与它被放在哪里

Sentinel 求解出来的是**两个** token，均以**请求头**形式携带，不进 body：

| 请求头 | 来源 | 何时携带 |
|---|---|---|
| `openai-sentinel-token` | `SentinelSDK.token(flow)` 主 token | 只要非空就带 |
| `openai-sentinel-so-token` | `SentinelSDK.sessionObserverToken(flow)` | 仅当服务端下发了 `so` 块且求解成功 |

注入点（全部在 `auth_flow.py` / `phone_flow.py`，属 authorize 状态机，本文只标位置不展开）：

| 位置 | 接口 |
|---|---|
| `platforms/chatgpt/protocol/auth_flow.py:1919-1922` | `POST /api/accounts/authorize/continue`（提交邮箱/手机号） |
| `platforms/chatgpt/protocol/auth_flow.py:2060-2065` | `username_password_create`（注册前会重刷 flow 专属 token） |
| `platforms/chatgpt/protocol/auth_flow.py:2095-2098` | 后续 OTP 相关请求 |
| `platforms/chatgpt/protocol/auth_flow.py:2116-2118` | 同上 |
| `platforms/chatgpt/protocol/phone_flow.py:201-202` | 手机号 `authorize/continue` |

设备标识**不走请求头**：走 `oai-did` cookie 和 `/sentinel/req` body 里的 `id`（`platforms/chatgpt/protocol/auth_flow.py:1488` 注释明确）。

#### 指纹一致性约束

`_sentinel_fp_kwargs()`（`platforms/chatgpt/protocol/auth_flow.py:1856-1885`）从 `self._fingerprint` 抽出 sentinel 需要的全部字段，保证 4 处 sentinel 调用（`authorize_continue` / `username_password_create` / `create_account` / phone）用的是**同一套画像** —— UA↔platform↔vendor↔硬件全程不变。硬伤示例：UA 说 Windows Chrome 但 `navigator.platform` 报 `MacIntel`、`vendor` 报 Apple。

`flow` 参数必须与实际接口对应：`username_password_create` 前会重新求一次 token（`platforms/chatgpt/protocol/auth_flow.py:2036-2045`），且**刻意不把空 SO token 写回实例字段**，因为 `send_otp`/`verify_otp` 不自己刷 sentinel、直接复用实例字段，写空会顺手把后续所有请求的 SO 头清掉。

### TLS 指纹与请求层

#### curl_cffi：impersonate 与版本锁

`platforms/chatgpt/protocol/http_client.py:11-18` 用 try/except 软导入 `curl_cffi.requests.Session`，导入失败降级到 `requests`。**降级路径只保留 UA 和重试，不具备 TLS 指纹能力**，实际不足以过 CF，仅用于让代码在缺依赖时不 crash。

版本约束在 `requirements.txt:5`：

```
curl-cffi>=0.16.2,<0.17
```

锁 `<0.17` 的原因是 0.17 起 curl-cffi 重整了 `impersonate` 目标命名与底层 BoringSSL/curl-impersonate 的构建矩阵，本项目依赖的目标名（`chrome136/142/146`、`safari15_3/15_5/17_0/18_0`、`safari17_2_ios/18_0_ios`、`firefox133/144`）在 0.16.x 上是稳定可用的一组；跨到 0.17 会出现目标名不存在直接抛异常，或同名目标的 JA3/JA4 与 HTTP/2 SETTINGS 指纹与预期不符——两种都会让 warmup 全线 403 而看不出原因。**升级 curl-cffi 前必须先核对 `fingerprint.py` 里全部 11 个 impersonate 名在新版是否仍存在。**

#### 指纹家族与权重

`platforms/chatgpt/protocol/fingerprint.py:368-373`：

| browser_type | 权重 | impersonate 池 | 位置 |
|---|---|---|---|
| `chrome` | 35 | `chrome136` / `chrome142` / `chrome146` | `platforms/chatgpt/protocol/fingerprint.py:82-101` |
| `mac_safari` | 30 | `safari15_3` / `safari15_5` / `safari17_0` / `safari18_0` | `platforms/chatgpt/protocol/fingerprint.py:19-44` |
| `firefox` | 20 | `firefox133` / `firefox144` | `platforms/chatgpt/protocol/fingerprint.py:114-117` |
| `ios_safari` | 15 | `safari17_2_ios` / `safari18_0_ios` | `platforms/chatgpt/protocol/fingerprint.py:57-70` |

#### generate_fingerprint() 产出字段表

`generate_fingerprint(rng=None, country_code="")`（`platforms/chatgpt/protocol/fingerprint.py:552-632`）返回的 dict：

| 字段 | 类型 | 含义 | 取值来源 |
|---|---|---|---|
| `browser_type` | str | 家族标识 | 加权随机 |
| `impersonate` | str | curl_cffi TLS 指纹名 | 家族版本池随机 |
| `fallback_impersonates` | list[str] | **同家族**回退列表，当前项排第一 | 当前 + `r.sample(others, ≤2)` |
| `user_agent` | str | 完整 UA | 按家族模板拼装 |
| `sec_ch_ua` | str | Client Hints 品牌列表 | **仅 chrome 非空** |
| `sec_ch_ua_platform` | str | `"Windows"` | 仅 chrome |
| `sec_ch_ua_mobile` | str | `?0` | 仅 chrome |
| `sec_ch_ua_full_version_list` | str | 带完整版本号的品牌列表 | 仅 chrome |
| `sec_ch_ua_arch` | str | `"x86"` | 仅 chrome |
| `sec_ch_ua_bitness` | str | `"64"` | 仅 chrome |
| `sec_ch_ua_model` | str | `""`（引号包空串，桌面） | 仅 chrome |
| `sec_ch_ua_platform_version` | str | `"10.0.19045"`(Win10 22H2) / `"15.0.0"`(Win11) | 仅 chrome |
| `screen` | str | `"WxH"` | 按家族屏幕池 |
| `lang` | str | 主语言（如 `ja-JP`） | 国家画像第一项 |
| `lang_full` | str | 完整 Accept-Language，带递减 q 值 | 国家画像随机 3~5 项 |
| `timezone` | str | IANA 时区名 | 国家画像加权随机 |
| `navigator_platform` | str | `MacIntel` / `iPhone` / `Win32` | 家族硬件画像 |
| `navigator_vendor` | str | `Apple Computer, Inc.` / `Google Inc.` / `""` | 家族硬件画像 |
| `hardware_concurrency` | int | CPU 逻辑核数 | 家族池随机 |
| `device_memory` | int\|None | `navigator.deviceMemory` | **仅 chrome 为 4/8，其余 None** |
| `max_touch_points` | int | `navigator.maxTouchPoints` | **仅 ios_safari 为 5** |
| `device_pixel_ratio` | float | `window.devicePixelRatio` | 家族池随机 |

**canvas / WebGL 指纹不在这里**：它们是在 Node 沙箱里造的固定值（`platforms/chatgpt/protocol/openai_sentinel_quickjs.js:73-121`），`toDataURL()` 返回空 data URL、WebGL vendor 固定 `Google Inc. (Intel)`、renderer 固定 ANGLE Intel UHD 串。这意味着**所有账号的 canvas/WebGL 指纹完全相同**，是当前实现的已知同质化风险。

#### 硬件画像一致性（_HARDWARE_PROFILES）

`platforms/chatgpt/protocol/fingerprint.py:392-425`，`_apply_hardware()` 在 `:428-436` 按家族一次性定死，一次注册内不再变：

| 家族 | platform | vendor | hw_concurrency | deviceMemory | touchPoints | DPR |
|---|---|---|---|---|---|---|
| `mac_safari` | `MacIntel` | `Apple Computer, Inc.` | 8/10/12/16 | `None` | 0 | 2.0 |
| `ios_safari` | `iPhone` | `Apple Computer, Inc.` | 4/6 | `None` | **5** | 2.0/3.0 |
| `chrome` | `Win32` | `Google Inc.` | 4/6/8/12/16/24 | **4/8（spec 封顶 8）** | 0 | 1.0/1.25/1.5 |
| `firefox` | `Win32` | **`""` 空串** | 4/6/8/12/16 | `None` | 0 | 1.0/1.5 |

`vendor` 在 Firefox 下是**空串而非 undefined**，`deviceMemory` 只有 Chromium 暴露 —— 这两点写错就是一眼假的指纹矛盾。

#### 指纹与代理/IP 绑定

三层绑定：

| 绑定关系 | 实现 | 位置 |
|---|---|---|
| 指纹 ↔ session（同一会话内不变） | RNG 种子 = `id(self.session) % 2**32`，同一 session 生成同一套指纹 | `platforms/chatgpt/protocol/auth_flow.py:1695-1697` |
| 指纹 ↔ 出口 IP 国家（时区/语言联动） | `check_proxy()` 打 `cloudflare.com/cdn-cgi/trace` 拿 `loc=XX`，国家码变了就用新 `country_code` **重新生成整套指纹** | `platforms/chatgpt/protocol/auth_flow.py:1683-1699` |
| impersonate ↔ UA ↔ client hints | `_sync_fingerprint_to()` / `fingerprint_for_impersonate()` 同步换 | `platforms/chatgpt/protocol/auth_flow.py:1438-1453`、`platforms/chatgpt/protocol/fingerprint.py:651-706` |

`_COUNTRY_PROFILES`（`platforms/chatgpt/protocol/fingerprint.py:122-350`）覆盖 50 个国家，每国给「加权时区列表 + 语言池」。大国按数据中心分布加权（US: NY 0.4 / LA 0.3 / Chicago 0.2 / Denver 0.1；RU: Moscow 0.7；AU: Sydney 0.5；BR: São Paulo 0.7），单时区国家给 1.0。未知国家回退 `_DEFAULT_COUNTRY_PROFILE = {UTC, [en-US, en]}`（`platforms/chatgpt/protocol/fingerprint.py:353-356`）。

Accept-Language 按真实浏览器格式构造（`platforms/chatgpt/protocol/fingerprint.py:608-617`）：主语言无 q 值固定第一位，其余打乱后取 2~4 个，q 值 `1.0 - i*0.1` 递减（0.9/0.8/0.7…）。

**代理归一化**：`create_http_session()` 里 `socks5://` 自动改写成 `socks5h://`（`platforms/chatgpt/protocol/http_client.py:143-146`），让 DNS 在代理端解析，减少本地 DNS 导致的 TLS 握手异常。同时 `trust_env = False` 并显式写 `session.proxies`（无代理时写空串），因为 `trust_env=False` 对底层 libcurl 不够，不显式覆盖会被系统 `HTTP(S)_PROXY` 隐式污染（`platforms/chatgpt/protocol/http_client.py:138-150`）。

#### 指纹旋转的两种粒度

| 函数 | 粒度 | 触发场景 | 会不会重建 session | 位置 |
|---|---|---|---|---|
| `_rotate_impersonate_session()` | **同家族换版本** | 链路中出现 TLS 异常 | 会（`create_http_session`） | `platforms/chatgpt/protocol/auth_flow.py:1413-1436` |
| `_switch_browser_family()` | **跨家族** | warmup 重试 | 由 warmup 自己重建 | `platforms/chatgpt/protocol/auth_flow.py:1455-1462` |
| `cross_family_impersonates()` | 给出其它 3 个家族各一个，**顺序随机** | 供 warmup 逐次 pop | — | `platforms/chatgpt/protocol/fingerprint.py:761-785` |

`fingerprint_for_impersonate()`（`platforms/chatgpt/protocol/fingerprint.py:651-706`）只同步**随版本变化**的字段（`impersonate` / `browser_type` / `user_agent` / `sec_ch_ua` / `sec_ch_ua_full_version_list`），屏幕、语言、时区、硬件等**会话级属性保持不变** —— 那些与浏览器版本无关，换了反而破坏"同一台机器"的一致性。非 Chromium 家族则把 7 个 `sec_ch_ua*` 全部清成空串（真实浏览器行为）。

`UA` 必须从新指纹里取，**不能再单独调一次 `ua_for_impersonate()`**：那个函数每次都重新随机系统版本，调两次会得到「会话 UA 说 macOS 14_4、指纹里记的是 14_5」的自相矛盾组合（`platforms/chatgpt/protocol/auth_flow.py:1444-1446`）。

`not_a_brand` 三个 Chrome 版本各不相同（136 `"Not.A/Brand";v="99"` / 142 `"Not/A)Brand";v="8"` / 146 `"Not?A_Brand";v="99"`），UA 与 sec-ch-ua 版本不同步时连这个都对不上，是 CF 最容易抓的特征（`platforms/chatgpt/protocol/fingerprint.py:656-658`）。

#### warmup：为什么它不能失败

`warmup()`（`platforms/chatgpt/protocol/auth_flow.py:1570-1677`）：`GET https://chatgpt.com`（首页 `/`，不是 `/auth/login`），目标是让 CF/OpenAI 种下全套 cookie，**关键是 `oai-did`**。

因果链：`POST /api/auth/signin/openai` 依据 chatgpt.com 的 cookie 决定返回什么 —— 有 `oai-did` 就返 `auth.openai.com/authorize` URL，没有就返 NextAuth 页，后者到 `authorize/continue` 必然 **409 invalid_state**。

| 实现要点 | 细节 |
|---|---|
| 请求头 | `_navigation_headers()`（document/navigate/none + `sec-fetch-user: ?1` + `upgrade-insecure-requests`），client hints 与 `_common_headers` 必须一致 |
| 超时 | 40s（成功轮实测耗时 3.4~10.9s，旧版 15s 卡边缘） |
| 重试 | 最多 4 次，`sleep(3 + attempt*2)` |
| 每次重试做什么 | **同时换出口 IP（重建 session）和浏览器家族**（`_switch_browser_family(family_fallbacks.pop(0))`） |
| 成功判据 | **只看 `session.cookies.get_dict()` 里有没有 `oai-did`**，不看 status_code、不看异常 |

判据只认 cookie jar 的原因（`platforms/chatgpt/protocol/auth_flow.py:1653-1654`）：HTTP 200 不代表拿到 `oai-did`（CF 403 只给 `__cf_bm`），请求抛异常也不代表没拿到（超时前可能已经种上）。旧实现"只 catch 异常不看 status"导致 403 也 `return True`、超时也 `return False`，两个方向都错。

`_navigation_headers` vs `_common_headers`（`platforms/chatgpt/protocol/auth_flow.py:1480-1568`）只差 Sec-Fetch-* 一组：前者是整页导航（`document`/`navigate`/`none`），后者是 XHR（`empty`/`cors`/`same-origin`）。`_common_headers` 额外注入 Datadog RUM trace 头（`_datadog_trace_headers()`，`platforms/chatgpt/protocol/auth_flow.py:1464-1469`，随机 64 位 trace id / 63 位 span id），注释明确写这是为了**避免 OTP silent-drop**。`Origin` 从 `Referer` 反推保证同源，否则易触发 `invalid_state`。auth.openai.com 侧**刻意不带 `oai-device-id` 头**（抓包里浏览器在该域一次都没发过）。

#### TLS 瞬断重试：_TlsRetrySession

`platforms/chatgpt/protocol/http_client.py:39-124`，`create_http_session()` 返回的**不是裸 CffiSession，而是这个包装类**。

代理链路会偶发 `curl: (35) TLS connect error ... OPENSSL_internal`，请求根本没发出去。实测数据（148 轮扫描）：

| 维度 | 结论 |
|---|---|
| 发生率 | **5.4%（8/148）** |
| 与指纹的关系 | **无** —— chrome146/142/136、safari18_0/15_3、firefox133 都中过 |
| 与域名的关系 | **无** —— chatgpt.com 3/25、auth.openai.com 1/25，见过同一轮两域一起炸 |
| 结论 | 链路级瞬断，不是风控也不是指纹问题，摘任何指纹都没用 |
| 原 session 重试效果 | 恢复 **8/8**，全部第 1 次重试就成功，`oai-did` 仍在 8/8 |

设计要点：

| 决策 | 原因 |
|---|---|
| **只重试，绝不重建 session** | 链路中后段 session 里已装着 warmup 种的 `oai-did` 和 csrf，一重建就全丢 → 409 invalid_state |
| **包在 session 层，不逐个调用点加 try** | 这个错能打在链上任意一步；`auth_flow` 有 35 处 `session.get/post`，且 `sentinel.py` 直接拿 session 自己发请求 —— 逐点补丁治不完 |
| **只兜 TLS 瞬断** | HTTP 错误码、超时、业务异常一律原样抛，免得把"服务端明确拒绝"也变成重试，反而更像异常流量 |
| 重试参数 | `retries=2`，`backoff=1.5`，等待 `backoff * (attempt+1)` = 1.5s / 3.0s |
| 包装范围 | 只包 `get` / `post` / `put`；其余属性用 `__getattr__` / `__setattr__` / `__iter__` 全透传 |

识别标记 `_TLS_ERROR_MARKERS = ("curl: (35)", "tls connect error", "openssl_internal", "sslerror")`（`platforms/chatgpt/protocol/http_client.py:31`），`_is_tls_handshake_error()` 是全项目共用口径 —— `platforms/chatgpt/protocol/sentinel.py:90` 和 `platforms/chatgpt/protocol/sentinel_quickjs.py:327` 都 import 它来做异常分类。

透传安全性已实测：全项目在 session 上访问的非 get/post 属性只有 `cookies`(20处) / `trust_env`(3) / `proxies`(3) / `mount`(2) / `headers`(1)，包装前后行为一致。

#### 两份 http_client.py 的职责差异

| 文件 | 行数 | 定位 | 依赖 | 谁在用 |
|---|---|---|---|---|
| `platforms/chatgpt/protocol/http_client.py` | 169 | **协议层实际使用的 session 工厂**：`create_http_session(proxy, impersonate, user_agent)` + `_TlsRetrySession` + `_is_tls_handshake_error` | 直接 `curl_cffi.requests.Session`，无项目内基类 | `auth_flow.py`、`phone_flow.py`、`two_factor.py`、`sentinel*.py` |
| `platforms/chatgpt/http_client.py` | 198 | **老的面向对象封装**：`OpenAIHTTPClient(HTTPClient)`，继承 `core.http_client.HTTPClient`，带 `check_ip_location()` / `send_openai_request()` / `check_sentinel()` | `core.http_client.HTTPClient` + `core.constants` | 注册主链路**不用**；`check_sentinel()` 只发 `p:""` 的空探测请求，仅能判断 sentinel 端点是否通，无法产出可用 token |

两份必须分清：

- 新链路一律用 `protocol/http_client.py` 的 **函数式工厂**，返回带 TLS 重试的 session 对象，指纹通过 `impersonate` 参数注入。
- `platforms/chatgpt/http_client.py` 里 `OpenAIHTTPClient` 属于早期设计遗留，`send_openai_request()` 里还引用了未 import 的 `json` 和 `cffi_requests`（`platforms/chatgpt/http_client.py:124,127`）——**这两行一旦走到就是 `NameError`**，说明该路径实际未被执行过。改协议层时不要误认为它是主实现。
- 同域的其它模块（`status_probe.py`、`payment.py`、`token_refresh.py`、`cpa_upload.py`、`sub2api_upload.py`）各自直接 `from curl_cffi import requests as cffi_requests`，不走上面任何一个 —— 它们是 Token 后置能力，与注册链路解耦。

#### 超时与重试速查

| 环节 | 超时 | 重试 | 位置 |
|---|---|---|---|
| `check_proxy`（cdn-cgi/trace） | 15s | 无 | `platforms/chatgpt/protocol/auth_flow.py:1683` |
| `warmup` | 40s | 4 次，换 IP + 换家族 | `platforms/chatgpt/protocol/auth_flow.py:1632-1646` |
| 通用 get/post（TLS 瞬断） | 各调用点自定 | 2 次，1.5s/3.0s，原 session | `http_client.py:78,101-115` |
| `authorize/continue` | 30s | 无（非 200 直接抛） | `platforms/chatgpt/protocol/auth_flow.py:1932-1935` |
| sdk.js 下载 | `max(10, timeout_ms/1000)` = 45s | 无（一次性磁盘缓存） | `platforms/chatgpt/protocol/sentinel_quickjs.py:73` |
| `/sentinel/req` | 同上 45s | 靠 `_TlsRetrySession` | `platforms/chatgpt/protocol/sentinel_quickjs.py:150` |
| Node 子进程 | `max(10, timeout_ms/1000+5)` = 50s | 无 | `platforms/chatgpt/protocol/sentinel_quickjs.py:102` |
| VM 内 sdk.js 加载 | 10s（`vm.runInContext`） | 无 | `platforms/chatgpt/protocol/openai_sentinel_quickjs.js:498` |
| `SentinelSDK.init` / `.token` | 8s（Promise.race） | 有 fallback 分支 | `openai_sentinel_quickjs.js:559,577` |
| `sessionObserverToken` | 5s（Promise.race） | 失败置空串 | `platforms/chatgpt/protocol/openai_sentinel_quickjs.js:585` |
| requests 降级路径 | 30s | urllib3 `Retry(total=3, backoff=1, [429,5xx])` | `platforms/chatgpt/protocol/http_client.py:157-165` |

### 邮箱注入点

#### 分层意图

协议层（`AuthFlow`）**完全不认识具体邮箱实现**：它只知道两个动作——"要一个地址"、"等一个 6 位码"。所有具体邮箱（微软池、tempmail、catch-all 域名、IMAP…）都实现在 `core.base_mailbox.BaseMailbox` 里，接口更宽（带 `before_ids` 去重、任务暂停 checkpoint、`otp_sent_at` 时间窗）。中间由 `mailbox_adapter.py` 对齐两边。

```
AuthFlow ──依赖──> MailProvider (ABC, protocol/mail_provider.py)
                        ▲
                        │实现
             MailboxProviderAdapter (protocol/mailbox_adapter.py)
                        ▲
                        │继承
             FixedAddressProviderAdapter
                        │
                        └─持有─> BaseMailbox (core/base_mailbox.py, 各家实现)
```

#### MailProvider 契约

`platforms/chatgpt/protocol/mail_provider.py:72-126`。

##### 类属性（能力声明）

| 属性 | 默认 | 语义 |
|---|---|---|
| `kind` | `"base"` | 类型标识，用于日志 |
| `display_name` | `"未命名"` | 展示名 |
| `pooled` | `False` | 号是"买来的、有限的、废了要换下一个" → 决定 `AuthFlow` 超时后要不要 retry、要不要 `mark_dead` |
| `ephemeral` | `False` | 地址是不是"每次都新造一个" → 决定 OpenAI 把它当新号还是老号 |
| `accepts_existing_account` | `False` | "OpenAI 说这个邮箱已注册过"算不算失败。买来的老号走 `passwordless_login` 拿 token 才是正常流程，不该判失败 |

`pooled` 与 `ephemeral` **正交**，四种组合都真实存在（`platforms/chatgpt/protocol/mail_provider.py:14-15`）：

| provider 类型 | pooled | ephemeral |
|---|---|---|
| 接码池邮箱 | True | False |
| catch-all 域名 | False | True |
| iCloud 隐私邮箱中转 | False | False |

##### 方法

| 方法 | 是否抽象 | 契约 |
|---|---|---|
| `create_mailbox() -> str` | **abstract** | 返回本次注册要用的邮箱地址 |
| `wait_for_otp(email_addr, timeout=120, issued_after=None) -> str` | **abstract** | 阻塞等 OTP，拿到返回 6 位码，超时抛 `TimeoutError`；**实现必须尊重 `issued_after`** |
| `peek_otp(email_addr, issued_after=None, wait=0.0) -> str\|None` | 可选（默认返回 None） | 非阻塞瞄一眼；拿不到返回 None 而非抛异常；**必须非破坏性**（不得把看过的邮件记进 seen 集合） |
| `exhausted` (property) | 有默认 | 读 `self._dead` |
| `mark_dead(reason="")` | 有默认 | 仅 `pooled` 时置 `_dead = True` |

`peek_otp` 存在的理由（`platforms/chatgpt/protocol/mail_provider.py:110-112`）：`get_auth_url` 会带 `login_hint`，OpenAI 一看到就**抢跑发码，比正式提交邮箱早约 20 秒**。调用方先 peek 命中就不用让服务端补发。

`issued_after` 是**防串号时间窗**（`platforms/chatgpt/protocol/mailbox_adapter.py:9-11`）：OpenAI 一轮流程里会因为 login_hint 抢跑、密码注册后重发等原因发好几封码完全一样的信，时间窗没传下去就会读到上一轮的旧码然后 401。

##### 辅助工具

| 项 | 位置 | 说明 |
|---|---|---|
| `MailProviderError(message, fatal, kind)` | `platforms/chatgpt/protocol/mail_provider.py:31-41` | `fatal=True` 表示号本身废了（凭证失效/被封/收件链路不可用）；`fatal=False` 表示环境或网络问题，号是无辜的 |
| `extract_otp(raw, code_pattern=None)` | `platforms/chatgpt/protocol/mail_provider.py:44-69` | 从邮件原文提 6 位码 |

`extract_otp` 的防误判优先级：① HTML `<span>` 包裹的 6 位数最可信 → ② 跳过 MIME header（找 `\r\n\r\n`）只搜正文 → ③ 依次剔除邮箱地址（`user123456@x.com`）、`m=+XXXXXX.` 时间戳边界、`?t=XXXXXXXXXX` 时间戳参数 → ④ `(?<!#)(?<!\d)(\d{6})(?!\d)` 匹配（`(?<!#)` 是为了排除 hex 颜色值）。

#### MailboxProviderAdapter

`platforms/chatgpt/protocol/mailbox_adapter.py:28-135`。构造参数即能力开关：

| 参数 | 作用 |
|---|---|
| `mailbox: BaseMailbox` | 被适配的真实邮箱实现 |
| `kind` | 日志前缀用的类型名 |
| `fixed_email` | 非空则**覆盖** `BaseMailbox.get_email()` 返回的地址 |
| `pooled` / `ephemeral` / `accepts_existing_account` | 直接写到实例属性，供 `AuthFlow` 读 |
| `otp_timeout` | 非空且 >0 则**强制覆盖**协议层传下来的 timeout（`_resolve_timeout`，`:132-135`） |

方法映射：

| MailProvider 方法 | 落到 BaseMailbox 的什么 |
|---|---|
| `create_mailbox()` | `mailbox.get_email()` → 存 `self._account` → 调 `prime()` → 返回 `fixed_email or account.email` |
| `wait_for_otp(addr, timeout, issued_after)` | `mailbox.wait_for_code(account, keyword="", timeout=_resolve_timeout(...), before_ids=self._before_ids, otp_sent_at=issued_after)`，空值抛 `TimeoutError` |
| `mark_dead(reason)` | 非 pooled 直接 return；否则置 `_dead` 并 warning。**适配器自己不销号**，真正的号池状态回写在任务层 |

两个适配器专属方法：

| 方法 | 位置 | 作用 |
|---|---|---|
| `bind_task_control(task_control, attempt_id, log_fn)` | `:59-78` | 把任务的停止/跳过开关和日志函数**直接写进** `mailbox._task_control` / `_task_attempt_token` / `_log_fn` |
| `prime()` | `:90-105` | 记下收件箱现有邮件 id 作为基线（`mailbox.get_current_ids(account)`），往后只认新到的信 |

`bind_task_control` 不绑的后果（`:68-70`）：`BaseMailbox` 的轮询循环每 0.25 秒查一次 `_task_control`，不绑则"停止任务"要等整个 OTP 超时（默认三分钟）走完才生效，界面上像点了没反应；`_log_fn` 不接则等码那几分钟任务日志一行都不出。注意注释明确说**不要再包一层前缀**，邮箱实现自己会带 `[微软邮箱]` 这类前缀。

`prime()` 必须能重复调用（`:91-95`）：同一个 provider 被复用跑第二段流程（注册完接着绑 2FA）时要重新打基线，否则上一段收到的码还在"新邮件"里，会被当成本轮的码用。`get_current_ids` 不存在或抛异常时降级为空集合，靠 `issued_after` 时间窗兜底。

#### FixedAddressProviderAdapter

`platforms/chatgpt/protocol/mailbox_adapter.py:138-170`。用途：**面对库里已存在的老号**。

补 RT、补 2FA 这类后置动作的邮箱地址早就定了，走 `MailboxProviderAdapter` 会白白从池子里领一个新邮箱（**微软池还会直接把号弹出去**）。所以它：

| 差异点 | 值 |
|---|---|
| 构造入参 | 多一个必填的 `account: MailboxAccount`（调用方直接给） |
| `fixed_email` | 从 `account.email` 取 |
| `pooled` | **强制 `False`** → `mark_dead` 变成 no-op |
| `ephemeral` | 强制 `False` |
| `accepts_existing_account` | **强制 `True`** → "已注册过"不判失败 |
| `create_mailbox()` | **只调 `prime()` 后直接返回固定地址**，一次都不碰 `get_email()` |
| `self._account` | 构造时直接赋调用方给的 account |

#### 新增邮箱源的正确姿势

不要改协议层。写一个 `BaseMailbox` 子类，再用 `MailboxProviderAdapter(mailbox, kind=..., pooled=..., ephemeral=...)` 包一层即可 —— `AuthFlow` 侧零改动。只有当邮箱语义与"要地址 + 等码"根本不同（例如需要点链接而非填码）时，才需要直接继承 `MailProvider` 另写一个实现。

### 插件层装配

#### 调用栈全貌

```
WebUI / 任务运行时
   └─ ChatGPTPlatform (platforms/chatgpt/plugin.py)            ← BasePlatform 契约
        └─ ChatGPTRegistrationModeAdapter (chatgpt_registration_mode_adapter.py)  ← 模式解析 + 3 次换邮箱重试 + Account 构造
             └─ ChatGPTRegistrationEngine (registration_engine.py)   ← 依赖注入 + 抢救 + 2FA 编排
                  └─ AuthFlow (protocol/auth_flow.py)                ← 纯协议状态机，不认识本仓库任何东西
                       ├─ protocol/http_client.py     TLS 指纹 session
                       ├─ protocol/fingerprint.py     指纹生成
                       ├─ protocol/sentinel*.py        PoW（起 Node）
                       └─ MailProvider（由 mailbox_adapter 注入）
```

#### plugin.py 如何实现 BasePlatform 契约

`platforms/chatgpt/plugin.py:13-21`：`@register` 装饰器（`core.registry`）自动注册；`name="chatgpt"`、`display_name="ChatGPT"`、`version="1.0.0"`。构造额外收一个 `mailbox: BaseMailbox`（基类没有这个参数，是 ChatGPT 插件自己加的）。

| 契约方法 | 抽象? | 本插件的实现要点 | 位置 |
|---|---|---|---|
| `register(email, password) -> Account` | **abstract** | 补密码 → 取 proxy/extra/`_log_fn` → 邮箱缺省时兜底 `TempMailLolMailbox` → 建 mode adapter → `adapter.run(ChatGPTRegistrationContext(...))` → 失败按 `retryable` 抛不同异常 → `adapter.build_account(result, password)` | `platforms/chatgpt/plugin.py:39-76` |
| `check_valid(account) -> bool` | **abstract** | 拿 `extra["access_token"] or account.token` + `extra["cookies"]` 组一个鸭子类型对象，调 `payment.check_subscription_status`；状态不在 `{expired, invalid, banned, None}` 里就算有效；**整体 try/except 返 False** | `platforms/chatgpt/plugin.py:23-37` |
| `get_platform_actions() -> list` | 有默认（返 `[]`） | 返回 12 条动作声明，格式 `{"id", "label", "params": [{"key","label","type"}]}`，供 WebUI 动态渲染表单 | `platforms/chatgpt/plugin.py:78-136` |
| `execute_action(action_id, account, params) -> dict` | 有默认（抛 NotImplementedError） | 一条 if-链分发；统一返 `{"ok", "data", "error", "account_extra_patch"}`；每个分支**函数内部 import**（避免插件加载时拉进全部重依赖）；未知 id 抛 `NotImplementedError` | `platforms/chatgpt/plugin.py:138-370` |
| `bind_task_control` / `get_mailbox_otp_timeout` | 基类实现，不覆写 | 基类会把 `task_control` 同时写到 `self.mailbox._task_control` | `core/base_platform.py:98-103` |

`register()` 里的失败分流（`platforms/chatgpt/plugin.py:68-74`）：

| 条件 | 抛什么 | 外层行为 |
|---|---|---|
| `result.retryable == False` | `core.task_runtime.NonRetryableRegisterError` | 不重试，不再赔一个邮箱 |
| 其它失败 | `RuntimeError(message)` | 交给任务运行时按常规重试 |

`execute_action` 里的公共前置（`platforms/chatgpt/plugin.py:139-153`）：把 `Account` 摊平成一个匿名 `_A` 实例，字段 `email / access_token / refresh_token / id_token / session_token / client_id（默认 app_EMoamEEZ73f0CkXaXp7hrann）/ cookies / user_id`。**所有下游 Token 能力模块都吃这个鸭子类型**，不吃 `Account` 本身。

##### 平台动作清单

| action_id | label | params | 落到哪个模块 |
|---|---|---|---|
| `probe_local_status` | 探测本地状态 | — | `status_probe.probe_local_chatgpt_status`；返回 `account_extra_patch.chatgpt_local` |
| `check_plus_trial` | 检测 Plus 试用 | — | `status_probe.probe_plus_trial_status`；**没查出结论就不落库**，免得账号从"未检测"里消失 |
| `sync_cliproxyapi_status` | 同步 CLIProxyAPI 状态 | — | `services.cliproxyapi_sync`；`ok` 要求 `uploaded` 且远端不是 `unreachable/not_found` |
| `refresh_token` | 刷新 Token | — | `token_refresh.TokenRefreshManager.refresh_account` |
| `backfill_refresh_token` | 补 RT | — (读 `params.allow_login`) | `services.chatgpt_rt_backfill.backfill_account_data` |
| `bind_2fa` | 绑定 2FA | — (读 `params.allow_login`) | `services.chatgpt_two_factor.bind_account_two_factor`；`ok = result.ok or already_bound`；**secret 只下发这一次**，放 `data.totp_secret` 让用户当场导入 |
| `payment_channel_link` | 渠道提链 | `channel` / `country` / `currency` | `services.payment_channels.service.create_link_for_context` |
| `payment_channel_pay` | 渠道支付 | `channel` / `card_id`(number) / `taxfree_state` | `services.payment_channels.service.pay_for_context` |
| `upload_cpa` | 上传 CPA | `api_url` / `api_key` | `cpa_upload.generate_token_json` + `upload_to_cpa` |
| `upload_sub2api` | 上传 Sub2API | `api_url` / `api_key` | `sub2api_upload.upload_to_sub2api` |
| `upload_tm` | 上传 Team Manager | `api_url` / `api_key` | `cpa_upload.upload_to_team_manager` |
| `upload_codex_proxy` | 上传 CodexProxy | `api_url` / `api_key`(Admin Key) | 按 `upload_type`（`params` → `config.extra.codex_proxy_upload_type` → 默认 `at`）分流到 `upload_to_codex_proxy`(rt) 或 `upload_at_to_codex_proxy`(at) |

隐藏动作：`payment_link`（`platforms/chatgpt/plugin.py:274-290`，`plan`/`country`/`workspace_name`/`price_interval`/`seat_quantity`）**在 `get_platform_actions()` 里没有声明**，只能通过 API 直接指定 action_id 触发。

#### registration_engine.py 与 protocol/ 的分工边界

核心原则（`platforms/chatgpt/registration_engine.py:11-13`）：**协议层不认识本仓库的任何东西**（`config_store`、任务运行时、账号表都不认识）。所有环境相关开关通过 `env_overrides` 以**实例级配置**传进去，**进程环境变量一个字节都不动** —— 多个注册任务并发跑时互不污染。

| 关注点 | 归 `protocol/`（AuthFlow） | 归 `registration_engine.py` |
|---|---|---|
| HTTP / TLS 指纹 / Sentinel PoW | ✅ | ❌ |
| authorize 状态机、page_type 分支、重定向链 | ✅ | ❌ |
| 邮箱**接口**（MailProvider ABC） | ✅ 定义 | ❌ |
| 邮箱**实现**（BaseMailbox、号池状态回写） | ❌ | ✅ 注入 + 回写 |
| 接码平台配置解析 | ❌ | ✅ `services.sms_service` |
| 密码生成 / 落盘时机 | ❌（只给回调） | ✅ `_on_password` |
| 2FA 编排（快路径 / 慢路径选择） | ❌（只提供 `bind_totp_*`） | ✅ |
| 「部分成功」抢救 | ❌ | ✅ `_salvage` |
| 任务日志镜像 | ❌ | ✅ `mirror_protocol_logs` |
| 配置 → 协议开关翻译 | ❌ | ✅ `_env_overrides` |

##### 四个注入点

`platforms/chatgpt/registration_engine.py:6-9` 明确列出：

| 注入点 | 传什么 | 用途 |
|---|---|---|
| `mail_provider` | `MailboxProviderAdapter` | 要地址、等 6 位邮件验证码 |
| `sms_callback` | `services.sms_service.build_phone_callback(...)` 返回的控制器 | 命中 add-phone 时自动租号收短信 |
| `on_password` | `self._on_password` | 密码一在 OpenAI 侧生效就回调，避免中途失败把号跑丢 |
| `on_session_ready` | `self._on_session_ready`，**仅 `bind_2fa=True` 时才挂** | session 一到手就插入 2FA enroll |

`on_session_ready` 不能无条件挂（`platforms/chatgpt/registration_engine.py:252-253`、`platforms/chatgpt/protocol/auth_flow.py:136-138`）：挂上会**顺带关掉** `run_register` 里 callback 前那次 Codex 抢跑（`OAUTH_CODEX_RT_BEFORE_CALLBACK`），否则 Codex 会跑在钩子前面，2FA 的插入顺序就白调了。

`_on_password` 的价值（`platforms/chatgpt/registration_engine.py:261-265`）：协议层在 `POST user/register` 成功后立刻回调，此时账号已在 OpenAI 那边建好。后续任何一步失败（最常见的是 OTP 超时），**这行日志就是这个号唯一的线索** —— 没有它，号既登不进去也找不回来。

##### _env_overrides 翻译表

`platforms/chatgpt/registration_engine.py:348-373`：

| 协议层开关 | 值 | 来源 |
|---|---|---|
| `OTP_TIMEOUT` | `_otp_timeout()` | `extra_config` 里 `mailbox_otp_timeout_seconds` → `email_otp_timeout_seconds` → `otp_timeout`，都没有则 **180** |
| `WEBUI_ALLOW_LOGIN` | 固定 `"1"` | 邮箱池地址常被判成"已有账号"，默认走 OTP 登录拿凭证而不是判失败 |
| `OAUTH_CODEX_RT_EXCHANGE` | `"0"` | **仅 `access_token_only` 模式**：不要 RT 就别跑 Codex OAuth（每次多花约 10 秒且必然告警） |
| `OAUTH_CODEX_RT_BEFORE_CALLBACK` | `"0"` | 同上 |
| `OPENAI_PHONE_OTP_TIMEOUT` | `extra_config.sms_per_phone_timeout` | 非空才传 |
| `OPENAI_PHONE_MAX_ATTEMPTS` | `extra_config.sms_max_phone_attempts` | 非空才传 |
| `OPENAI_PHONE_OTP_CODE_RETRIES` | `extra_config.sms_code_retries_per_phone` | 非空才传 |
| `OPENAI_PHONE_NUMBER` | `extra_config.chatgpt_phone_number` | 非空才传 |

##### 两条主链路

| 方法 | 入口条件 | 调协议层的什么 | 特殊处理 |
|---|---|---|---|
| `_run_email()` | `register_flow == email` | `flow.run_register(provider)` | 无日志镜像包裹 |
| `_run_phone()` | `register_flow` 是 `phone` / `phone_with_email` | `flow.run_phone_register(mail_provider, bind_email)` | **用 `with mirror_protocol_logs(self.log)` 包裹**；sms_callback 为 None 直接返失败并提示去「设置 → 接码」；`phone_with_email` 未绑上邮箱时把 `bind_email_error` 记进 metadata |

`_run_phone` 特意加日志镜像的原因（`platforms/chatgpt/registration_engine.py:162-163`）：手机链路每一步只写在协议层 logger 上，不镜像出来任务日志里只有"开始/失败"两行，出事了连服务端回的是哪个 page 都看不见。

##### _salvage：部分成功不扔号

`platforms/chatgpt/registration_engine.py:387-417`。典型场景是 **Codex OAuth 交换失败**：`access_token` / `session_token` 早就到手、账号完全可用，只是没有 `refresh_token`。

| 判定 | 结果 |
|---|---|
| `access_token` / `session_token` / `refresh_token` 全空 | `success=False`，`retryable` 取 `exc.retryable`（默认 True） |
| 有任一凭证 + `mode == refresh_token` + 无 RT | `success=True`，`metadata["partial"]=True`，日志"缺 refresh_token" |
| 有任一凭证 + 其它情况 | `success=True`，`metadata["partial"]=False` |

抢救成功也要调 `_attach_two_factor()`（`:415-417`）：炸在 2FA 绑定之后是常态（Codex 换 RT 那步最容易出事），绑定结论得跟着号走，否则账号页会把已绑好的号显示成没绑过。

##### 2FA 的快慢两条路

| 路径 | 触发 | 代价 | 位置 |
|---|---|---|---|
| **快路径** `_on_session_ready` → `bind_totp_inline` | session 一到手（Codex 授权之前） | 零额外成本：注册链几十秒前刚验证过，服务端认"最近认证过"，直接 enroll | `platforms/chatgpt/registration_engine.py:270-282` |
| **慢路径** `_ensure_two_factor` → `bind_totp_via_login` | 快路径没绑上且账号有邮箱身份 + 有密码 | **一次 PoW + 可能一封验证码邮件** | `platforms/chatgpt/registration_engine.py:284-317` |

慢路径前必须 `mail_provider.prime()`（`:306-307`）：注册那封码信还躺在收件箱里，不重新打基线会被当成绑定链的码用。手机号身份（`"@" not in email`）直接跳过慢路径 —— 登录链认邮箱。

密钥写两遍（`:280-281`、`:316-317`）：enroll 内部写过一次，这里再兜一道，因为密钥丢了这个号的 2FA 就**永久锁死**。

#### chatgpt_registration_mode_adapter.py 的适配职责

`platforms/chatgpt/chatgpt_registration_mode_adapter.py`，三件事：**解析配置 → 带重试地跑引擎 → 把结果翻成 `Account`**。

##### 三个正交维度

| 维度 | 取值 | 语义 | 解析函数 |
|---|---|---|---|
| `mode` | `refresh_token`（默认） / `access_token_only` | 要不要 RT。`refresh_token` 会额外走一次独立 authorize 链换 RT | `resolve_chatgpt_registration_mode` `:72-82` |
| `register_flow` | `email`（默认） / `phone` / `phone_with_email` | 拿什么当注册身份 | `resolve_chatgpt_register_flow` `:99-101` |
| `bind_2fa` | bool，**默认 False** | 注册成功后顺手绑 TOTP。默认关是因为绑上之后每次登录都要动态码，密钥只下发一次 | `resolve_chatgpt_bind_2fa` `:104-113` |

`normalize_*` 函数做了大量别名兼容（`:47-96`）：mode 接受 `access_token` / `at_only` / `without_rt` / `no_rt` / `0` / `false` 等；flow 接受 `phone_bind_email` / `phone_and_email` / `sms` 等。还兼容一个历史布尔字段 `chatgpt_has_refresh_token_solution`（`:76-81`）。

##### 换邮箱重试

`run()`（`:140-167`）最多 `_MAX_ATTEMPTS = 3` 轮，**每轮新建一个引擎实例**。只有错误信息命中 `_MAILBOX_ERROR_MARKERS = ("service_abuse_mode", "oauth_token_failed", "imap")`（`:43`）才换邮箱重试，其它失败立即 break。这是**邮箱链路失效**与**注册链路失效**的分界：前者换个邮箱有用，后者重开只是再赔一次。

##### Account 构造

`build_account()`（`:169-178`）+ `_build_account_extra()`（`:180-194`）。`extra` 里除了四种 token / cookies / workspace_id 之外，还会把**三个维度自己**写回去（`chatgpt_registration_mode` / `chatgpt_has_refresh_token_solution` / `chatgpt_register_flow` / `chatgpt_bind_2fa` / `chatgpt_token_source`），最后把 `result.metadata` 里非空项展开合并（`device_id` / `totp_secret` / `phone_number` / `bound_email` / `partial` / `last_error` / `chatgpt_2fa` / 号池收件凭据…）。

号池收件凭据由 `_attach_mailbox_credentials()`（`platforms/chatgpt/registration_engine.py:194-229`）搬进 metadata：`mail_provider` / `provider` / `account_type` / `password` / `client_id` / `refresh_token` / `mailapi_url` / `mailbox_token`，**供补 RT / 补 2FA 按地址找回邮箱**。`OutlookMailbox` 内部用 `provider=microsoft`，而补 RT 解析器识别的是 `mail_provider`，所以额外补一份标准字段。

#### protocol_log_relay.py：协议日志转任务日志

`platforms/chatgpt/protocol_log_relay.py`，82 行。

| 项 | 值 |
|---|---|
| 被镜像的 logger | `platforms.chatgpt.protocol`、`services.sms_service`（`:17`） |
| 日志前缀 | `services.sms_service` → `[接码平台]`；其余 → `[协议]`（`:19-20`） |
| 级别 | `logging.INFO` |
| 使用方式 | `with mirror_protocol_logs(sink): ...`，`sink=None` 时直接 yield（no-op） |

**按线程过滤是必须的**（`_ThreadScopedLogRelay`，`:27-46`）：批量任务多线程跑，每线程有自己的任务日志回调；不过滤则 A 号的授权步骤会串进 B 号的日志里。`emit()` 里 `record.thread != self._thread_id` 直接 return，且 sink 抛异常一律吞掉（日志不能反过来搞崩业务）。

**引用计数 + 级别保存**（`:22-24`、`:59-82`）：并发进入时 `_relay_refcount` 计数，只有第一个进入者保存 `target.level` 并在必要时提到 INFO；最后一个退出者才恢复原级别。`_relay_lock` 保护这两张表。退出时逐个 `removeHandler`。这套机制保证多任务并发 attach/detach 不会互相把对方的 logger 级别改掉。

#### 测试印证了什么

| 测试文件 | 印证的行为 |
|---|---|
| `tests/test_chatgpt_warmup_fingerprint.py` | 用 `_Session` 按 impersonate 前缀模拟"CF 整族 403 只给 `__cf_bm`"；断言首次失败后**放弃整个家族**、可用家族被保留到流程后续、第一次就成功时不旋转、4 次全封时放弃、`cross_family_impersonates` 跳过当前家族且每族只给一个、`family_impersonates` 当前项排第一 |
| `tests/test_chatgpt_plugin.py` | `register` 把 mailbox / proxy / log_fn / extra 完整传给 adapter；未给密码时自动生成；给了就保留；未绑邮箱时兜底 `TempMailLolMailbox`；引擎错误消息原样抛出；`Account` 经 adapter 构造 |
| `tests/test_chatgpt_registration_engine.py` | 适配器的基线 id 记录/容错/空地址拒绝、`fixed_email` 优先、`issued_after` 透传、配置 timeout 覆盖协议请求、`prime()` 二次打基线；`_env_overrides` 默认含 `WEBUI_ALLOW_LOGIN` 与 OTP_TIMEOUT、`access_token_only` 关 Codex OAuth、SMS 配置翻译与空值不下发；`_salvage` 三种分支；**`test_missing_node_reports_the_real_cause`：`OPENAI_SENTINEL_NODE_PATH` 指向不存在的路径时错误消息里必须同时含该路径与环境变量名** |
| `tests/test_chatgpt_registration_mode_adapter.py` | mode 默认 `refresh_token`、布尔 no-RT 兼容、`bind_2fa` 是 opt-in 且接受字符串开关并一路传到引擎与账号记录、`build_account` 标记模式、密码回退、运行时上下文透传、**邮箱链路失败换下一个邮箱重试 / 非邮箱失败不重试** |

### 脆弱点与维护注意

| 风险 | 具体表现 | 观测/缓解 |
|---|---|---|
| **Node 依赖** | 缺 `node` 或 `OPENAI_SENTINEL_NODE_PATH` 填错 → 整条注册链看着全绿但验证码永远收不到 | `_run_quickjs_action` 单独捕获 `FileNotFoundError` 并给出说透原因的报错；部署清单里 `OPENAI_SENTINEL_NODE_PATH` 是必查项 |
| **sdk.js 上游变更（最脆）** | `platforms/chatgpt/protocol/openai_sentinel_quickjs.js:10-21` 的三处字符串补丁与混淆产物强绑定。上游改一个符号 → `String.replace` **静默不生效** → `SentinelSDK` 为 undefined → 子进程 exit(1) | 只能靠 Node stderr 里的 JS 栈发现；升级时必须重新去混淆定位 `var P=new _;`、`var SentinelSDK=`、`t.token=ye,t}({});` 三个锚点 |
| **SENTINEL_VERSION 硬编码** | `platforms/chatgpt/protocol/sentinel_quickjs.py:34` 写死 `20260219f9f6`，同时出现在 URL、`/sentinel/req` 的 referer、`platforms/chatgpt/http_client.py:150` 三处 | 版本过期时 sdk.js 下载 404 或 challenge 校验失败；改版本要**三处同步** |
| **impersonate 目标锁定 curl-cffi 0.16.x** | `requirements.txt:5` 锁 `<0.17`。升级后 11 个 impersonate 名可能不存在或指纹语义变化 → warmup 全线 403 | 升级前逐个核对 `fingerprint.py` 里的 impersonate 名 |
| **指纹时效性** | Chrome 版本池只到 `chrome146`、Safari 到 `18.0`、Firefox 到 `144`。真实浏览器版本每月往前走，UA 停在旧版本本身就是特征 | `_CHROME_VERSIONS` / `_SAFARI_VERSIONS` / `_IOS_SAFARI_VERSIONS` / `_FIREFOX_VERSIONS` 需要定期跟版；跟版时 `not_a_brand` 串必须照抄真实浏览器 |
| **canvas / WebGL 指纹全账号同质** | 沙箱里 `toDataURL()` 恒返空 data URL，WebGL vendor/renderer 是固定的 Intel UHD + ANGLE 串 | 所有账号这两项完全一样；如果 OpenAI 开始对 canvas 熵值做聚类，这是最先暴露的字段 |
| **CF 封锁策略会变粒度** | 2026-08-10 的结论是"补齐 client hints 就好"，2026-08-21 就被"按 TLS 家族整族封"推翻 | warmup 的重试必须保持**跨家族 + 随机顺序**，不要写死"safari 更安全" |
| **`platforms/chatgpt/http_client.py` 是死代码** | `send_openai_request()` 引用了未 import 的 `json` 和 `cffi_requests`（`:124,127`），走到就 `NameError` | 改协议层时不要误认为它是主实现；`check_sentinel()` 只能探端点通不通，不产出可用 token |
| **solve 的 fallback 是死路** | `platforms/chatgpt/protocol/openai_sentinel_quickjs.js:595-602` 输出 `{final_p, t}`，而 Python 只读 `token` → 必然 `return None` | 只剩诊断价值；真要修得让 fallback 也产出 `token` 形状 |
| **`_TlsRetrySession` 只包 get/post/put** | 新增 `delete`/`patch`/`request` 调用不会被 TLS 重试兜住 | 加新 HTTP 方法时记得同步加到包装类 |
| **`prime()` 漏调** | 任何"复用同一 provider 跑第二段流程"的新代码漏了 `prime()`，就会读到上一段的旧码然后 401 | 慢路径 2FA 已显式调用；新增多段流程时照抄 |
| **Node 进程无池化** | 每取一次 token 起两个一次性 Node 进程，并发注册时进程数 = 并发数 × 2 | 高并发下要注意宿主进程数与内存上限 |
| **`env_overrides` 是唯一合法通道** | 任何"图省事直接 `os.environ[...]=`"的改动都会污染并发任务 | 新增协议层开关一律走 `_env_overrides()` 返回的 dict |

---

## 六、ChatGPT 手机接码

### 一、接码平台抽象层

#### 1.1 分层结构

接码子系统全部集中在 `services/sms_service.py`（1158 行），对外只暴露三个入口，注册链路不直接碰 provider：

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 数据句柄 | `services/sms_service.py:59`（`SmsActivation`） | 一次租号的结果：`activation_id` / `phone_number`（E.164 带 `+`）/ `country` / `metadata` |
| 抽象基类 | `services/sms_service.py:69`（`BaseSmsProvider`） | 定义接码平台必须实现的能力；抽象方法只有 3 个，其余为可选钩子 |
| 协议实现 | `services/sms_service.py:240`（`SmsActivateProvider`） | sms-activate 系 `handler_api.php` 协议的唯一实现，SmsBower / HeroSMS 共用 |
| 工厂 | `services/sms_service.py:888`（`create_sms_provider`） | 按 `provider_key` 取 base_url，把 `sms_*` 配置装成 provider 实例 |
| 两阶段控制器 | `services/sms_service.py:916`（`PhoneCallbackController`） | 把 provider 包装成「租号 / 等码 / 回报」三段式回调，注入注册链路 |
| 配置装配 | `services/sms_service.py:1108`（`resolve_sms_settings`）、`services/sms_service.py:1123`（`build_phone_callback`） | 合并全局配置与任务覆盖；未启用或缺 Key 时返回 `None` |

#### 1.2 两家平台的差异点

平台注册表在 `services/sms_service.py:43`，只有两个字段（`label`、`base_url`）：

| provider_key | label | base_url |
| --- | --- | --- |
| `smsbower` | SmsBower | `https://smsbower.page/stubs/handler_api.php` |
| `herosms` | HeroSMS | `https://hero-sms.com/stubs/handler_api.php` |

两家协议同源，**代码里唯一的行为分叉是「固定价」参数的写法**，判断依据是 base_url 里是否含 `hero-sms.com`（`services/sms_service.py:487-496`）：

| 平台 | 固定价写法 | 说明 |
| --- | --- | --- |
| HeroSMS | `maxPrice=<价> & fixedPrice=true` | 平台自带 fixedPrice 开关 |
| SmsBower | `minPrice=<价> & maxPrice=<价>` | 必须上下价相等才被当成固定价 |

未设固定价时统一退化为 `maxPrice=<sms_max_price>`（`services/sms_service.py:495`）。除此之外 base_url 之外的一切（action 名、状态串、错误 token）都按同一套解析，`SmsActivateProvider.DEFAULT_BASE_URL` 默认指向 SmsBower（`services/sms_service.py:243`）。

#### 1.3 抽象基类契约

| 成员 | 位置 | 必须实现 | 语义 |
| --- | --- | --- | --- |
| `get_number(service, country, country_candidates)` | `:74` | 是 | 租号，返回 `SmsActivation` |
| `get_code(activation_id, timeout)` | `:84` | 是 | 阻塞等短信，返回码字符串（空串=没收到） |
| `cancel(activation_id)` | `:88` | 是 | 释放号码 |
| `get_balance()` | `:92` | 否 | 查余额，默认 `NotImplementedError` |
| `report_success(activation_id)` | `:96` | 否 | 业务侧验证通过 → 平台结算并允许复用 |
| `mark_code_failed(activation_id, reason)` | `:100` | 否 | 收到码但校验失败 → 把这个码拉黑，不再重复用 |
| `mark_send_failed(activation_id, reason)` | `:103` | 否 | 业务侧拒绝该号（send 返错）→ 停止复用并退款 |
| `stop_reuse(activation_id, reason)` | `:106` | 否 | 号已产出账号 → 不再复用，但**不退款** |
| `mark_send_succeeded(activation_id)` | `:109` | 否 | 已成功触发短信下发 |
| `auto_report_success_on_code` | `:72` / 覆写于 `:244` | 类属性 | 基类默认 `True`（收到码即报成功）；`SmsActivateProvider` 改成 `False`，等业务侧确认，为号码复用留窗口 |

#### 1.4 支持的平台操作（handler_api.php action 清单）

| 能力 | action | 实现位置 | 成功判定 / 解析 |
| --- | --- | --- | --- |
| 查余额 | `getBalance` | `services/sms_service.py:285` | 文本前缀 `ACCESS_BALANCE:` 后取浮点，否则抛错 |
| 查价格库存 | `getPrices` | `services/sms_service.py:291` | JSON `{country: {service: {cost,count}}}`，非 dict 视为结构异常 |
| 查国家排名 | `getTopCountriesByServiceRank` → `getTopCountriesByService` → 退回 `getPrices` 自算 | `services/sms_service.py:302`，解析器 `:334` | 三级降级；结果按 **price 升序、count 降序** 排序（`:310`、`:331`） |
| 租号 | `getNumberV2` → 退回 `getNumber` | `services/sms_service.py:484`，调度于 `:575` | V2 取 JSON 的 `activationId`；V1 取文本 `ACCESS_NUMBER:<id>:<phone>`（`:517`） |
| 查短信（V1） | `getStatus` | `services/sms_service.py:653` | 纯文本状态串，解析见 `:213` |
| 查短信（V2） | `getStatusV2` + **必须带 `type=sms`** | `services/sms_service.py:658` | 缺 `type` 平台只回 `{"error":"Bad type parameter"}`，而它解析下来与「还在等码」完全同形，等于白等一整窗口（注释见 `:659`） |
| 释放 / 取消 | `cancelActivation` → 退回 `setStatus status=8` | `services/sms_service.py:783` | 204 或响应含 `ACCESS_CANCEL` |
| 结算完成 | `finishActivation` → 退回 `setStatus status=6` | `services/sms_service.py:830` | 200/204 或含 `ACCESS` |
| 标记已发送 | `setStatus status=1` | `services/sms_service.py:850` | 失败静默吞掉 |
| 退款拉黑 | `setStatus status=8`（+ 清缓存、禁复用） | `services/sms_service.py:865` | 见 §4.4 |

状态串解析表（`services/sms_service.py:213`）：`STATUS_WAIT_CODE`→`wait_code`、`STATUS_WAIT_RETRY*`→`wait_retry`、`STATUS_WAIT_RESEND`→`wait_resend`、`STATUS_OK:<code>`→`ok`、`STATUS_CANCEL`→`cancel`，其余 `unknown`。数字状态 `8` 常量化为 `SMS_STATUS_CANCELED`（`services/sms_service.py:195`）。

日志卫生：`_status_token`（`services/sms_service.py:198`）只放长度 ≤60 且不含 `<` 的短 token，防止错误页 HTML 正文被打进任务日志；`_describe_status`（`:764`）明确规定**短信正文不进日志**。

#### 1.5 号码复用缓存

| 项 | 位置 | 说明 |
| --- | --- | --- |
| 缓存文件 | `services/sms_service.py:188` | `<repo>/data/.sms_phone_cache.json` |
| 进程内共享 | `services/sms_service.py:56`（`_SMS_CACHE`） | 跨线程共享一份，`_SMS_CACHE_LOCK`（`:54`）保护读写 |
| 并发互斥 | `services/sms_service.py:55`（`_SMS_VERIFY_LOCK`，RLock） | 防止两个注册任务并发抢同一份缓存号；在 `PhoneCallbackController.get_phone`（`:950`）获取，`_release_lock`（`:1099`）释放 |
| 缓存身份 | `services/sms_service.py:428` | `sha256(api_key)` + `service` + `country` 三者全等才认；换 Key/换服务码/换国家自动失效 |
| 失效条件 | `services/sms_service.py:451-459` | 超过 `SMS_PHONE_LIFETIME`（20 分钟，`:38`）、`reuse_stopped` 为真、或 `use_count ≥ sms_phone_success_max` |
| 已用码去重 | `services/sms_service.py:461`、`:740` | `used_codes` 集合，复用同号时旧码不会被当成新码返回 |

#### 1.6 api/sms.py 的 4 个接口

`api/sms.py` 定位很明确：**只做设置页自检探针，不租号**（模块注释 `api/sms.py:1-5`）。入参模型 `SmsProbeRequest`（`api/sms.py:25`）允许临时覆盖 provider/api_key/service，留空则回落已保存的全局配置（`api/sms.py:34`）。

| 方法 | 路径 | 位置 | 入参 | 返回 | 失败码 |
| --- | --- | --- | --- | --- | --- |
| GET | `/sms/providers` | `api/sms.py:50` | 无 | 平台清单（value/label/base_url）+ `default_service` + `openai_sms_countries` | — |
| GET | `/sms/country-options` | `api/sms.py:62` | 无 | 全部国家 ID→中文名下拉项，label 形如 `泰国 (52)`，带 `openai_sms_whitelisted` 标记；附 `default_country` | — |
| POST | `/sms/balance` | `api/sms.py:83` | `provider`/`api_key` | `{provider, balance}` | 配置错 400（`:47`）、平台错 502 |
| POST | `/sms/countries` | `api/sms.py:93` | `provider`/`api_key`/`service`/`limit`（1–200，默认 20，`:102`） | 排名列表：`country`/`name`/`price`/`count`/`openai_sms_whitelisted` | 配置错 400、平台错 502 |

国家 ID→中文名表在 `services/sms_service.py:113`（`SMS_COUNTRY_NAMES_CN`，0–191），展示标签统一走 `country_label()`（`services/sms_service.py:156`），输出形如 `52 泰国`。

### 二、租号到收码的完整时序

#### 2.1 调用入口与三段式契约

注册链路不直接使用 provider，而是通过 `PhoneCallbackController` 的三段式回调（模块注释 `services/sms_service.py:8-13`）：

```python
controller = build_phone_callback(config, log_fn=...)
phone = controller.get_phone()          # 阶段 1 租号
code = controller.get_code(timeout=80)  # 阶段 2 等短信
controller.report_success()             # 阶段 3 业务侧验证通过
```

两个消费方：
- **邮箱注册中途被要求绑手机**：`platforms/chatgpt/protocol/auth_flow.py:998`（`_handle_add_phone_via_sms`）→ 循环体 `platforms/chatgpt/protocol/auth_flow.py:1021`（`_do_sms_loop`）。
- **主动用手机号注册**：`platforms/chatgpt/protocol/phone_flow.py`（详见 §4）。

控制器构造在 `platforms/chatgpt/registration_engine.py:339`；Token 后置回填链路另有一份构造 `platforms/chatgpt/rt_backfill.py:257`。

#### 2.2 编号步骤（以 add-phone 循环为准）

1. **构造控制器** — `build_phone_callback`（`services/sms_service.py:1123`）。`sms_enabled` 为假返回 `None`（`:1135`）；`sms_api_key` 为空打告警后返回 `None`（`:1138`）。返回 `None` 意味着命中 add-phone 时回落手工号码路径 `_handle_add_phone_via_env`（`platforms/chatgpt/protocol/auth_flow.py:1224`，读 `OPENAI_PHONE_NUMBER` 系列）。
2. **读循环参数** — `platforms/chatgpt/protocol/auth_flow.py:1029`（`_read_int`）：先读 `controller.config` 的前端配置，空则读环境变量，再退默认。单号窗口下限被强制夹到 40s（`platforms/chatgpt/protocol/auth_flow.py:1039`）。
3. **换号外层循环** — `for phone_attempt in range(1, max_phone_attempts + 1)`（`platforms/chatgpt/protocol/auth_flow.py:1088`）。
4. **阶段 1 租号** — `ctrl.get_phone()`（`services/sms_service.py:946`）：
   - 取 `_SMS_VERIFY_LOCK`（`:950`）防并发抢缓存号；
   - 解析候选国家列表 `_resolve_country_candidates`（`:992`，见 §3.3）；
   - 命中复用缓存则直接返回旧号（`services/sms_service.py:553-567`，`metadata.reused=True`）；
   - 否则按候选国家顺序、每国先 `getNumberV2` 再退 `getNumber`（`:571-611`），HTTP 超时 30s（`:275`）；
   - 全部候选失败 → 抛 `RuntimeError` 并拼接每国失败原因（`:616`）；若失败串里含 `NO_NUMBERS`，额外调 `_explain_no_numbers`（`:620`）把挂牌价打进日志（只查一次，由 `_warned_low_bid` 守卫）；
   - **异常分支**：租号抛异常时先 `_release_lock()` 再向上抛（`:969-971`）；外层 `platforms/chatgpt/protocol/auth_flow.py:1094` 捕获后 `continue` 换下一个号。
   - 号码国家不在 `OPENAI_SMS_COUNTRIES` 时打一次提醒（`:982`），只告警不阻止。
5. **阶段 2 让 OpenAI 发码** — `self._add_phone_send(phone)`（`platforms/chatgpt/protocol/auth_flow.py:1106`）。异常按四类分流：

   | 错误类型 | 判定 | 动作 | 是否继续换号 |
   | --- | --- | --- | --- |
   | 频控（`too many phone verification` / `phone_verification_rate_limit`） | `platforms/chatgpt/protocol/auth_flow.py:1110` | `mark_send_failed` 退款 | **break**，放弃 add-phone（session_token 仍保留） |
   | 号码被拒（`_PHONE_REJECTED_PATTERNS`，`platforms/chatgpt/protocol/auth_flow.py:1057`：`phone_number_already_in_use` / `already_taken` / `disallowed_phone` / `invalid_phone_number` / `blocked_phone` / `suspicious behavior from phone` 等） | `platforms/chatgpt/protocol/auth_flow.py:1119` | `mark_send_failed` 退款 | **continue** 换号（换号有意义） |
   | 流程状态失效（`_FLOW_STATE_PATTERNS`，`platforms/chatgpt/protocol/auth_flow.py:1071`：`invalid authorization step` / `invalid state`） | `platforms/chatgpt/protocol/auth_flow.py:1125` | `mark_send_failed` 退款 | **break**，交回上层重走 authorize（与号码无关） |
   | 未识别错误 | `platforms/chatgpt/protocol/auth_flow.py:1136` | `mark_send_failed` 退款 | continue，但同一句错误连续 3 次即 break（`platforms/chatgpt/protocol/auth_flow.py:1140-1150`） |

6. **确认已进入验证码页** — 校验 `page_type ∈ {phone_otp_verification, external_url}` 或 continue_url 含 `phone-verification`（`platforms/chatgpt/protocol/auth_flow.py:1155`）；不满足则 `mark_send_failed("did not enter phone-verification page")` 并换号（`:1162`）。
7. **标记发送成功** — `ctrl.mark_send_succeeded()`（`platforms/chatgpt/protocol/auth_flow.py:1166`，底层 `setStatus status=1`），并清空重复错误计数器。
8. **阶段 3 轮询短信** — 内层 while（`platforms/chatgpt/protocol/auth_flow.py:1176`）双条件：本号窗口未用尽 **且** `code_attempt < max_code_retries_per_phone`；剩余不足 10s 直接 break（`:1178`）。每次把**剩余时间**作为 timeout 传给 `ctrl.get_code`（`:1185`）。
   - 底层 `wait_for_code`（`services/sms_service.py:701`）：轮询间隔 3s，每轮**同时**查 `getStatusV2` 和 `getStatus`（`:723`），任一返回 `ok` 且码不在 `used_codes` 中即返回（`:738-745`）；返回 `cancel` 立即返回 `None`（`:735`）；每 30s 打一次进度（`:748`）；超时返回 `None` 并告警"平台始终没收到短信"（`:759`）。
   - 明确设计决定：**只轮询，不催发**（`services/sms_service.py:708-712`）——码是 OpenAI 一次性下发的，催发换不来第二条短信，还会让当前 challenge 失效。
   - 单查询异常只 debug 记录并跳过该 source（`:730`），不中断轮询。
9. **提取码去重** — 收到空码 → `break` 换号（`platforms/chatgpt/protocol/auth_flow.py:1187`）；码已在本号 `seen_codes` 里 → 跳过继续等（`:1188`）。
10. **提交验证** — `self._phone_otp_validate(code)`（`platforms/chatgpt/protocol/auth_flow.py:1195`）。
    - 成功：`ctrl.report_success()`（`:1201`）→ `finishActivation`/`setStatus 6` 结算，返回 next_url。
    - 失败：`ctrl.mark_code_failed(err_text)`（`:1208`）把该码写入 `used_codes`，**留在同号窗口内**继续等下一条码。
11. **单号窗口用尽** — 告警后 `ctrl.cleanup()`（`platforms/chatgpt/protocol/auth_flow.py:1214`）：`cancel` 释放号、清 `activation`、释放锁（`services/sms_service.py:1088`）；下一轮 `get_phone()` 会租新号。
12. **全部换号失败** — 抛出最后一次异常，或 `RuntimeError(f"SMS 接码 {max_phone_attempts} 个号均失败")`（`platforms/chatgpt/protocol/auth_flow.py:1220-1222`）。
13. **兜底清理** — `_handle_add_phone_via_sms` 用 `try/finally`（`platforms/chatgpt/protocol/auth_flow.py:1008-1019`）保证任何异常路径都执行 `cleanup()` + `_release_lock()`，避免锁泄漏与号码悬挂。
14. **整段失败后的回退** — `_handle_add_phone_verification`（`platforms/chatgpt/protocol/auth_flow.py:981`）捕获 SMS 路径异常 → `cleanup()` → 回落 `_handle_add_phone_via_env`；此时若也没配 `OPENAI_PHONE_NUMBER`，日志会特意区分"接码配了但本轮没绑成"与"根本没配"两种措辞（`platforms/chatgpt/protocol/auth_flow.py:1234-1242`），避免把人误导去设置页找一个没问题的开关。

#### 2.3 时序流程图

```mermaid
flowchart TD
    A[build_phone_callback<br/>sms_service.py:1123] -->|sms_enabled=false 或缺 api_key| A0[返回 None<br/>回落 OPENAI_PHONE_NUMBER 手工路径]
    A -->|构造成功| B[读循环参数<br/>auth_flow.py:1029]
    B --> C{{换号循环 phone_attempt<br/>1..max_phone_attempts}}
    C --> D[阶段1 get_phone<br/>sms_service.py:946]
    D -->|命中复用缓存| E[返回旧号 reused=true]
    D -->|getNumberV2 → getNumber<br/>逐个候选国家| E
    D -->|全部候选失败 / NO_NUMBERS| D1[释放锁 + 抛错<br/>_explain_no_numbers 打挂牌价] --> C
    E --> E1{国家在<br/>OPENAI_SMS_COUNTRIES?}
    E1 -->|否| E2[打 WhatsApp 风险告警<br/>不阻止 sms_service.py:982] --> F
    E1 -->|是| F[阶段2 POST add-phone/send<br/>auth_flow.py:1106]
    F -->|频控 rate_limit| F1[mark_send_failed 退款<br/>break 放弃 add-phone]
    F -->|号被拒 already_in_use 等| F2[mark_send_failed 退款] --> C
    F -->|流程失效 invalid state| F3[mark_send_failed 退款<br/>break 交回上层重走 authorize]
    F -->|未识别错误| F4{同一错误<br/>连续 3 次?}
    F4 -->|是| F5[break 停止换号]
    F4 -->|否| C
    F -->|200 但页面不是<br/>phone_otp_verification| F6[mark_send_failed<br/>did not enter page] --> C
    F -->|进入验证码页| G[mark_send_succeeded<br/>setStatus=1]
    G --> H{{等码循环<br/>窗口未尽 且 code_attempt<上限}}
    H --> I[wait_for_code 3s 轮询<br/>getStatusV2 + getStatus<br/>sms_service.py:701]
    I -->|status=cancel| I1[立即返回 None] --> K
    I -->|超时无码| I2[告警 平台始终没收到短信] --> K
    I -->|拿到新码| J[phone_otp_validate<br/>auth_flow.py:1195]
    J -->|成功| J1[report_success<br/>finishActivation 结算] --> Z[返回 next_url 继续 authorize]
    J -->|失败| J2[mark_code_failed<br/>该码写入 used_codes] --> H
    K[窗口用尽 cleanup<br/>cancel 释放号 + 清缓存] --> C
    C -->|循环耗尽| Y[抛 last_err 或<br/>SMS 接码 N 个号均失败]
    Y --> Y1[finally: cleanup + _release_lock<br/>auth_flow.py:1008]
    Y1 --> Y2[回落 _handle_add_phone_via_env]
```

#### 2.4 超时与时间常量汇总

| 环节 | 值 | 位置 | 超时后行为 |
| --- | --- | --- | --- |
| 单次 HTTP 请求 | 30s | `services/sms_service.py:275` | `raise_for_status` / requests 异常，交给上层双重循环 |
| 号码租用生命周期 | 20 分钟（`SMS_PHONE_LIFETIME`） | `services/sms_service.py:38` | 缓存判定过期即弃用（`:452`）；剩余 ≤30s 时 `report_success` 直接结算并清缓存（`:820`） |
| 单号等码窗口 | 默认 80s，下限 40s | `platforms/chatgpt/protocol/auth_flow.py:1039` | `get_code` 返回空 → cleanup 换号 |
| 等码轮询间隔 | 3s | `services/sms_service.py:706` | — |
| 等码进度日志 | 每 30s | `services/sms_service.py:719`、`:748` | — |
| 剩余时间保护 | <10s 不再发起新一轮等码 | `platforms/chatgpt/protocol/auth_flow.py:1178` | 直接结束本号窗口 |
| `get_code` 默认 timeout | 180s（基类签名/`PhoneCallbackController`） | `services/sms_service.py:85`、`:774`、`:1033` | 实际调用方总是传"剩余时间"覆盖 |
| `_wait_phone_otp`（环境变量路径） | 默认 180s，下限 20s，4s 轮询 | `platforms/chatgpt/protocol/auth_flow.py:968-979` | 抛 `TimeoutError` |

> 注：`get_code` 的注释明确说明「传进来的 timeout 就是真 timeout」——号码有 20 分钟寿命，但 OpenAI 的 phone-otp challenge 撑不了那么久，超时就该换号（`services/sms_service.py:775`）。

#### 2.5 号码状态回报语义对照

| 场景 | 调用 | 平台动作 | 退款 | 复用缓存 |
| --- | --- | --- | --- | --- |
| validate 通过 | `report_success`（`:802`） | `finishActivation` / `setStatus 6` | — | `use_count+1`，码入 `used_codes`；达上限/寿命将尽则清缓存 |
| 收到码但 validate 失败 | `mark_code_failed`（`:840`） | 无网络调用 | 否 | 仅把该码拉黑，号继续可用 |
| 号被 OpenAI 拒 / send 失败 | `mark_send_failed`（`:865`） | `setStatus 8` | **是（退款）** | `reuse_stopped=true` 并清缓存 |
| 号已产出账号 | `stop_reuse`（`:856`） | 无网络调用 | 否（钱认了） | `reuse_stopped=true` 并清缓存 |
| 窗口用尽 / 流程收尾 | `cleanup`（`:1088`）→ `cancel`（`:783`） | `cancelActivation` → `setStatus 8` | 是 | 清缓存 |

### 三、参数总表与选号策略

#### 3.1 配置项总表

配置白名单（决定哪些 key 能通过设置页保存）在 `api/config.py:109-124`；服务端填默认值在 `api/config.py:183-190`。全部 `sms_*` 项由 `resolve_sms_settings`（`services/sms_service.py:1108`）从 `config_store` 抽出并叠加任务级覆盖（只接受 `sms_` 前缀且非空值，`:1117-1119`）。

| 配置 key | 默认值 | 作用 | 读取位置 | 能否按任务覆盖 |
| --- | --- | --- | --- | --- |
| `sms_enabled` | `"0"`（关） | 总开关；关则 `build_phone_callback` 返回 `None`，命中 add-phone 回落手工号码路径 | `services/sms_service.py:1135`；默认填充 `api/config.py:183` | 能（`resolve_sms_settings` 前缀匹配） |
| `sms_provider` | `smsbower` | 选平台，决定 base_url 与固定价参数写法 | `services/sms_service.py:1149`、工厂 `:894`；默认 `api/config.py:185` | 能 |
| `sms_api_key` | 空（必填） | 平台 API Key；为空时打告警并跳过接码 | `services/sms_service.py:1138`、`:899` | 能 |
| `sms_service` | `dr`（`SMS_DEFAULT_SERVICE`，`services/sms_service.py:36`） | 服务码，OpenAI 对应 `dr`；租号与查国家排名**必须同值** | `services/sms_service.py:1151`、`:906`；默认 `api/config.py:188` | 能（`api/sms.py:41` 也支持探针级覆盖） |
| `sms_country` | `52`（泰国，`SMS_DEFAULT_COUNTRY`，`services/sms_service.py:37`） | 默认国家 ID；未开自动选号时就是唯一候选 | `services/sms_service.py:1152`、`:907`、`:997`；默认 `api/config.py:190` | 能 |
| `sms_auto_country` | `False` | 自动选最优国家开关；关则只用 `sms_country` | `services/sms_service.py:1154`，消费于 `:996` | 能 |
| `sms_allowed_countries` | 空 | 允许的国家（逗号/分号分隔）；非空时**优先级最高**，只在这些国家里按价格升序依次尝试 | `services/sms_service.py:993` | 能 |
| `sms_auto_min_stock` | `20` | 自动选号的最低库存门槛；先按 20 挑，挑不到降到 1 再挑一遍 | `services/sms_service.py:1015`，逻辑 `:424` | 能 |
| `sms_auto_max_price` | `0`（不限） | 自动选号的价格上限（>0 才生效） | `services/sms_service.py:1016`，逻辑 `:412` | 能 |
| `sms_strict_whitelist` | `False` | 严格白名单：只从 `OPENAI_SMS_COUNTRIES` 里选国家 | `services/sms_service.py:1017`，逻辑 `:406` | 能 |
| `sms_max_price` | `-1`（不限） | 租号时的 `maxPrice` 出价上限 | `services/sms_service.py:908`，用于 `:495` | 能 |
| `sms_fixed_price` | `-1`（不用） | 固定价出价；>0 时按平台写法转成 fixedPrice / minPrice=maxPrice | `services/sms_service.py:909`，用于 `:487-494` | 能 |
| `sms_reuse_phone` | `False` | 复用同一号码（一个号连续验多次） | `services/sms_service.py:911`，消费于 `:554-559`、`:814` | 能 |
| `sms_phone_success_max` | `3` | 单号最多复用成功次数；0 = 不限 | `services/sms_service.py:912`，判定 `:455`、`:817` | 能 |
| `sms_per_phone_timeout` | add-phone `80`，手机注册 `120`；两处下限均 40 | 单号等短信窗口（秒） | `platforms/chatgpt/protocol/auth_flow.py:1039`、`platforms/chatgpt/protocol/phone_flow.py:509` | 能（也可用环境变量 `OPENAI_PHONE_OTP_TIMEOUT`） |
| `sms_max_phone_attempts` | `3` | 最多换号次数（外层循环轮数） | `platforms/chatgpt/protocol/auth_flow.py:1043`、`platforms/chatgpt/protocol/phone_flow.py:512` | 能（环境变量 `OPENAI_PHONE_MAX_ATTEMPTS`） |
| `sms_code_retries_per_phone` | `2` | 单号窗口内最多验几条码（validate 失败后继续等下一条） | `platforms/chatgpt/protocol/auth_flow.py:1047`、`platforms/chatgpt/protocol/phone_flow.py:513` | 能（环境变量 `OPENAI_PHONE_OTP_CODE_RETRIES`） |
| `sms_proxy` | 空 → 回落全局 `proxy` | 接码平台请求走的代理 | `services/sms_service.py:910`、`:1142` | 能（`build_phone_callback` 的 `proxy=` 参数会在未显式设置时填入） |

补充说明：
- 三个循环参数走的是 **配置 → 环境变量 → 默认值** 三级读取（`platforms/chatgpt/protocol/auth_flow.py:1029`、`platforms/chatgpt/protocol/phone_flow.py:500`）；`platforms/chatgpt/registration_engine.py:363-372` 负责把任务级 `sms_*` 配置翻译成协议层认识的 `OPENAI_PHONE_*` 环境变量，`platforms/chatgpt/rt_backfill.py:267-269` 同理。
- `sms_per_phone_timeout` / `sms_max_phone_attempts` / `sms_code_retries_per_phone` 不进 provider，只被两条注册循环消费；其余项才进 `create_sms_provider`。
- 接码日志被镜像进任务日志时统一加 `[接码平台]` 前缀（`platforms/chatgpt/protocol_log_relay.py:17-19`），控制器自身的日志前缀是 `[接码]`（`platforms/chatgpt/registration_engine.py:341`）。
- 非 `sms_*` 但相关的一项：`chatgpt_phone_number` → `OPENAI_PHONE_NUMBER`（`platforms/chatgpt/registration_engine.py:367`），用于接码不可用时的手工号码兜底。

#### 3.2 「自动选最优国家」的排序依据

排序发生在 `get_top_countries`（`services/sms_service.py:302`），排序键固定为：

```python
rows.sort(key=lambda r: (r.get("price") or 999, -(r.get("count") or 0)))
```

即 **价格升序为主键、库存降序为次键**（`services/sms_service.py:310` 与回落路径 `:331`）。数据来源三级降级：

| 顺序 | action | 说明 |
| --- | --- | --- |
| 1 | `getTopCountriesByServiceRank` | 平台的排名接口，优先 |
| 2 | `getTopCountriesByService` | 老接口名 |
| 3 | `getPrices` 自行计算 | 遍历 `{country: {service: {cost, count}}}`，只收 `price ≥ 0 且 count > 0` 的行（`services/sms_service.py:329`） |

解析器 `_parse_top_countries`（`services/sms_service.py:334`）同时兼容 dict 与 list 两种返回形状，字段名做了多重兜底（`price`/`cost`/`retail_price`，`count`/`qty`/`available`）。

挑选逻辑 `get_best_country`（`services/sms_service.py:373`）：
1. 取排好序的 rows，按顺序找第一个满足条件的国家；
2. 优先级：`allowed_countries`（显式白名单）> `strict_whitelist`（只认 `OPENAI_SMS_COUNTRIES`）> 全平台自由选（`services/sms_service.py:382-387`）；
3. 库存门槛两遍走：先用 `min_stock`（默认 20），一个都挑不到时降到 `1` 再挑一遍（`services/sms_service.py:424`）；
4. `max_price > 0` 时过滤超价国家（`:412`）；
5. 查询失败或结果为空返回 `None`，调用方回落默认国家（`:390-394`、`:1030`）。

国家排名查询接口对外暴露为 `POST /sms/countries`（`api/sms.py:93`），返回 `price`/`count`/`openai_sms_whitelisted`，`limit` 默认 20、上限 200。

#### 3.3 候选国家的三种解析路径

`_resolve_country_candidates`（`services/sms_service.py:992`）：

| 条件 | 结果 | 位置 |
| --- | --- | --- |
| 未开 `sms_auto_country`（或 provider 非 sms-activate 系） | 单元素列表 `[sms_country]`，空则 `52` | `:996-997` |
| 开了自动 且 `sms_allowed_countries` 非空 | 用排名把勾选国家**重排成价格升序**，排名里没出现的国家附在后面保底；排名查询失败则按勾选原始顺序 | `:999-1009` |
| 开了自动 且 未勾选国家 | 调 `get_best_country` 全平台挑一个，失败回落默认国家 | `:1011-1031` |

注意租号是**按候选国家顺序逐个尝试**、每国先 V2 后 V1（`services/sms_service.py:571-575`），所以「允许的国家」这条路径天然带跨国重试能力，而「全平台挑最优」只会返回单一国家。

#### 3.4 WhatsApp 事实在代码里的三处体现

事实：OpenAI 自 2025 年起对大部分国家改用 WhatsApp 验证，纯 SMS 实测只有泰国（`country_id=52`）稳定可用。代码用**白名单 + 告警，但绝不阻止**的方式表达：

| 位置 | 形式 | 行为 |
| --- | --- | --- |
| `services/sms_service.py:15-17`（模块 docstring） | 事实声明 | 明确写出「白名单之外的国家在自动选号时会打告警但不阻止」 |
| `services/sms_service.py:41` | 常量 `OPENAI_SMS_COUNTRIES = {"52"}` | 唯一白名单成员就是泰国 |
| `services/sms_service.py:414-420` | 自动选号时 `logger.warning` | 「自动选中非 OpenAI-SMS 白名单国家 … OpenAI 可能让此号走 WhatsApp 验证，收不到短信」；**warning 之后紧跟 `return cid`**，选择照常生效 |
| `services/sms_service.py:980-989` | 租到号后 `self.log` 提醒（进任务日志） | 列出白名单并解释会出现「发送成功但一直等不到短信」；括号里还补了一句「白名单内的号也只是概率更高，不保证收得到」 |
| `services/sms_service.py:1023-1027` | 自动选中后标注 | 日志直接打 `[OpenAI SMS 白名单]` 或 `[非白名单，可能走 WhatsApp]` |
| `api/sms.py:58`、`:75`、`:111` | 接口输出 `openai_sms_whitelisted` 布尔位 | 前端可标记，但不禁用非白名单选项 |
| `platforms/chatgpt/protocol/phone_flow.py:335-351`（`_warn_if_not_sms_channel`） | 解析发码响应体 | 响应里出现 `whatsapp` / `voice` / `flash_call` 关键字时告警「验证码可能不是以短信发出的，接码平台收不到属正常，换 52（泰国）这类纯短信号段再试」；只在 JSON 响应上判定（`platforms/chatgpt/protocol/phone_flow.py:323`），HTML 落地页里的 whatsapp 字样属于页面其它选项，不算本次通道 |
| `platforms/chatgpt/protocol/phone_flow.py:926-932`（`PhoneAccountCreatedError` 的 hint） | 失败归因 | 实测记录：尼日利亚 19 与泰国 52 都试过，OpenAI 返回 200 但接码平台全程 Waiting for SMS，结论是「换国家不一定有用，换号源/换更贵的实号池才有效」 |

`sms_strict_whitelist` 是唯一能把「告警」升级为「硬拦」的开关（默认关，`services/sms_service.py:1017`），开启后 `get_best_country` 只会从白名单里选（`:406`）。

### 四、手机注册 vs add-phone 的两条路径

#### 4.1 两条路径的定位

| 维度 | 主动手机注册 | 邮箱注册中途被要求 add-phone |
| --- | --- | --- |
| 触发方式 | 用户选注册流程 `phone` / `phone_with_email`（`platforms/chatgpt/registration_engine.py:42-43`，分发于 `:127`） | 邮箱注册链路命中 add-phone 页面，被动进入（`platforms/chatgpt/protocol/auth_flow.py:981`） |
| 入口 | `platforms/chatgpt/protocol/phone_flow.py:444`（`run_phone_register`）→ 循环 `:493`（`_do_phone_register_loop`） | `platforms/chatgpt/protocol/auth_flow.py:998`（`_handle_add_phone_via_sms`）→ 循环 `:1021`（`_do_sms_loop`） |
| 注册身份 | 手机号本身：`username.kind = phone_number`（`platforms/chatgpt/protocol/phone_flow.py:44`、`:167`） | 邮箱，手机号只是二次验证材料 |
| 接码是否必需 | 必需，`_sms_callback` 为 `None` 直接抛错（`platforms/chatgpt/protocol/phone_flow.py:450-453`）；引擎层提前拦截并返回失败（`platforms/chatgpt/registration_engine.py:151-157`） | 可选，未启用则回落 `OPENAI_PHONE_NUMBER` 手工路径（`platforms/chatgpt/protocol/auth_flow.py:996`、`:1224`） |
| 发码接口 | `GET /api/accounts/phone-otp/send`（整页导航），POST 版与 `add-phone/send` 只作兜底（`platforms/chatgpt/protocol/phone_flow.py:270-279`） | `POST /api/accounts/add-phone/send`（`platforms/chatgpt/protocol/auth_flow.py:904`） |
| 验证码页 | `/contact-verification`（`platforms/chatgpt/protocol/phone_flow.py:47`），`page_type` 认 `contact_verification` / `phone_otp_verification` / `phone_verification`（`:117`） | `page_type` 只认 `phone_otp_verification` / `external_url` 或 continue 含 `phone-verification`（`platforms/chatgpt/protocol/auth_flow.py:1155`） |
| 单号窗口默认值 | 120s（`platforms/chatgpt/protocol/phone_flow.py:510`） | 80s（`platforms/chatgpt/protocol/auth_flow.py:1039`） |
| 验证码提交 | `_phone_otp_validate(code, referer=<记住的验证码页>)`（`platforms/chatgpt/protocol/phone_flow.py:806`） | `_phone_otp_validate(code)`（`platforms/chatgpt/protocol/auth_flow.py:1195`） |
| 失败后果 | `user/register` 之后失败 = 孤号（见 §4.4） | 失败只是没绑手机，session_token 仍可保留（`platforms/chatgpt/protocol/auth_flow.py:1114`） |

#### 4.2 共用与各自实现的部分

**共用**（同一份 `AuthFlow` 实例，`PhoneRegisterMixin` 混入，`platforms/chatgpt/protocol/phone_flow.py:91`）：
- 接码控制器与整套三段式接口（`get_phone` / `get_code` / `report_success` / `mark_*` / `cleanup`）；
- 循环骨架完全同构：外层换号 `range(1, max_phone_attempts+1)`、内层双条件等码 while、剩余 <10s 提前 break、重复码去重、同一错误连续 3 次停止换号（`platforms/chatgpt/protocol/phone_flow.py:529` vs `platforms/chatgpt/protocol/auth_flow.py:1088`；`platforms/chatgpt/protocol/phone_flow.py:784` vs `platforms/chatgpt/protocol/auth_flow.py:1176`）；
- 三个循环参数与读取方式（配置 → 环境变量 → 默认）；
- 错误分类关键字表两处**各存一份、内容近乎相同**：`platforms/chatgpt/protocol/phone_flow.py:51`（`_PHONE_REJECTED_PATTERNS`，比 auth_flow 多几个大白话变体如 `already in use`、`number_in_use`）与 `platforms/chatgpt/protocol/auth_flow.py:1057`；流程状态错 `platforms/chatgpt/protocol/phone_flow.py:70` 与 `platforms/chatgpt/protocol/auth_flow.py:1071` 完全一致。这是明显的重复代码点。
- 身份验完之后的收尾（create_account → callback → session → Codex RT）由 `_finish_authorized_flow`（`platforms/chatgpt/protocol/phone_flow.py:837`）调用 `AuthFlow` 已有方法，与邮箱注册同一条链（模块注释 `platforms/chatgpt/protocol/phone_flow.py:3-6`）。

**手机注册独有**：
- 每个号重开一条 authorize 链，`login_hint` 带号码（`platforms/chatgpt/protocol/phone_flow.py:632-633`）；若 authorize 已凭 `login_hint` 直接落到 `/create-account/password`，**跳过 `authorize/continue`**（`platforms/chatgpt/protocol/phone_flow.py:637-643`）——多打那一枪等于把同一身份提交两遍，服务端有理由判 invalid state；
- `phone_register_user`（`platforms/chatgpt/protocol/phone_flow.py:178`）提交 `{"username": <手机号>, "password": ...}`，注册前刷新 sentinel（flow=`username_password_create`，`:190`），**成功后立刻把密码通过 `_on_password` 回调落盘**（`:217-221`），因为密码只活在内存里，后面任何一步挂掉这个号就再也登不进去；
- `send_phone_otp` 的三发候选序列与导航头（`platforms/chatgpt/protocol/phone_flow.py:228-242`）：这一步是浏览器整页跳转（`Accept: text/html`、`Sec-Fetch-Dest: document`、`Sec-Fetch-Mode: navigate`，无 Origin/Content-Type），按 XHR 发形状对不上，短信发不出来；
- 六个状态判定器：`_is_add_email_state`（`:100`）、`_is_phone_otp_state`（`:106`）、`_is_phone_otp_send_state`（`:122`）、`_is_forward_state`（`:135`）、`_is_create_password_state`（`:148`）、`_is_existing_identity_state`（`:154`）。其中 `phone_otp_send` 是「服务端把流程停在该发短信了、等客户端自己去打发码接口」，此时去打 `add-phone/send` 会被判 invalid authorization step，因为那是另一个步骤（`:128-129`）；
- 三个专用异常：`_PhoneUnusable`（`:940`，换下一个号）、`_PhoneFlowBroken`（`:948`，服务端状态坏了，换号没意义）、`PhoneAccountCreatedError`（`:895`，见 §4.4）。

#### 4.3 add-email：手机注册后绑邮箱

只在注册流程为 `phone_with_email` 时才领邮箱（`platforms/chatgpt/registration_engine.py:149`、`:159`；纯 `phone` 流程不占用邮箱池）。

**接口两步**（模块注释 `platforms/chatgpt/protocol/phone_flow.py:22-27`）：
1. `POST /api/accounts/add-email/send`，body `{"email": ...}`，**不带 sentinel**（`platforms/chatgpt/protocol/phone_flow.py:355`）；
2. `POST /api/accounts/email-otp/validate`，与邮箱注册同一个接口，因此直接复用 `AuthFlow.verify_otp`（`platforms/chatgpt/protocol/phone_flow.py:402`）。

**`bind_email` 流程**（`platforms/chatgpt/protocol/phone_flow.py:377`）：领邮箱 → `add_email_send` → 检查是否进入邮箱验证页（不满足只告警不中断，`:388-393`）→ 等 OTP（`OTP_TIMEOUT`，默认 180s、下限 10s，`:396`）→ `verify_otp`；**401/409 时重发一封再等一次**，别为一个错码/过期码丢掉整个号（`:403-411`）；成功后写 `self.result.bound_email`（`:413`）。

**「只在 OpenAI 把 add-email 摆进当前 authorize 流程时才被接受」** 的体现（`run_phone_register`，`platforms/chatgpt/protocol/phone_flow.py:478-489`）——共三次尝试机会，全部经由 `_try_bind_email`：
1. 手机验完后若 continue_url 已是 add-email 状态 → 立刻绑（`:479-480`）；
2. 没有 continue_url 或落在 `/about-you` → 先 `create_account()`，若返回值是 add-email 状态 → 绑（`:482-485`）；
3. 服务端没主动要求时，在跟重定向链之前**再试一次**（`:487-489`），注释明确：这一步只在 authorize 流程还停在 add-email 上时才会被接受，被拒了就当没绑。
   非 add-email 时刻提交会被服务端判 invalid state（模块注释 `platforms/chatgpt/protocol/phone_flow.py:28-30`）。

**被拒时账号保留 + 失败原因入账号详情**：
- `_try_bind_email`（`platforms/chatgpt/protocol/phone_flow.py:429`）吞掉任何异常，把原文存进 `self._bind_email_error`，日志写「账号本身已注册成功，可稍后重试绑定」，返回原 continue_url 让主流程继续（`:437-440`）；
- 引擎侧把它落进 metadata：`platforms/chatgpt/registration_engine.py:178-181` —— 需要绑但 `bound_email` 为空时写 `metadata["bind_email_error"] = bind_error or "未绑定邮箱"`，并在任务日志打一行「手机号注册成功但邮箱未绑上：…」；
- metadata 里同时保留 `phone_number` 与 `bound_email` 两个字段（`platforms/chatgpt/registration_engine.py:83-88`），导出表也有对应列（`services/account_export.py:86-87`）。

**记账标识从手机号切到邮箱**：`RegistrationResult.from_auth_result`（`platforms/chatgpt/registration_engine.py:68-89`）里一行决定：

```python
email=bound_email or result.email,   # registration_engine.py:75
```

注释写得很直白：「手机号注册的号，绑上邮箱之后按邮箱记账，没绑上就只能用手机号当标识」。而 `result.email` 在手机注册循环里被写成手机号本身（`platforms/chatgpt/protocol/phone_flow.py:541-542`：`result.phone_number = phone` 与 `result.email = phone` 同时赋值）。同一条切换逻辑还出现在 `_salvage` 的失败分支（`platforms/chatgpt/registration_engine.py:399`）、2FA 绑定取邮箱处（`:295`）。

#### 4.4 失败分类：可重试 vs 不可重试

`NonRetryableRegisterError` 定义在 `core/task_runtime.py:30`，注释指名典型场景是手机注册里「账号已建好、但接码平台一条短信都没收到」——号源被静默拦下，再开一轮只会用同样的号源再造一个没人认领的孤号，还多花一次租号钱。

**它在接码链路里的确切抛出位置与传递链（四跳）**：

| 跳 | 位置 | 动作 |
| --- | --- | --- |
| 1 | `platforms/chatgpt/protocol/phone_flow.py:835` | 单号窗口内没拿到可用码 → `raise _PhoneUnusable(last_err or TimeoutError(f"号 {phone} 在 {per_phone_timeout}s 内没收到短信"))` ——**「没收到短信」这句文案就是后面的判据** |
| 2 | `platforms/chatgpt/protocol/phone_flow.py:695-702` | 该异常发生在 `user/register` **之后**，被 `_register_with_phone` 捕获，改包成 `PhoneAccountCreatedError(str(cause), phone=..., password=..., sms_ever_received=self._phone_sms_ever_received)` |
| 3 | `platforms/chatgpt/protocol/phone_flow.py:919-920` | 构造函数里定标：`self.sms_never_arrived = "没收到短信" in reason and sms_ever_received is not True`；`self.retryable = not self.sms_never_arrived` |
| 4 | `platforms/chatgpt/registration_engine.py:402` → `platforms/chatgpt/plugin.py:70-73` → `api/tasks.py:687` | `_salvage` 无凭证分支把 `retryable=getattr(exc, "retryable", True)` 写进 `RegistrationResult`；plugin 见 `retryable=False` 时 `raise NonRetryableRegisterError(message)`；任务层 `retryable=not isinstance(e, NonRetryableRegisterError)` 记账 |

**关键判据只有一个布尔**：`_phone_sms_ever_received`（类属性声明 `platforms/chatgpt/protocol/phone_flow.py:96`，每轮开头重置 `:527`，收到任何非空码即置真 `:799`）。注释交代了为什么不用错误文本猜（`platforms/chatgpt/protocol/phone_flow.py:916-918`）：「超时」两个字太宽——换 RT 超时、网络超时都会被误判成号源问题，用户填的重试轮数就这么被无声地作废。测试印证：零短信 → `retryable is False`（`tests/test_chatgpt_phone_register.py:644`）；码进来过、后面才崩 → `retryable is True`（`:663`）。

**为什么重试只会造孤号 + 白花租号钱**（`PhoneAccountCreatedError` docstring `platforms/chatgpt/protocol/phone_flow.py:896-900` 与 hint `:926-932`）：
1. `user/register` 一旦 200，账号在 OpenAI 侧**已经是真账号**，手机号被它永久占用；
2. 换号重试会用新号再注册一个账号，旧的那个既没凭证也没人认领 = 孤号；
3. 用旧号重试只会一直撞 `phone_number_in_use`，所以 `_do_phone_register_loop` 在这条分支上调 `ctrl.stop_reuse(...)`（`platforms/chatgpt/protocol/phone_flow.py:564`）——**不上报「号码有问题」也不退款**（号是好的，退款理由不成立），只是禁止复用；
4. 每轮重试都要重新租号，钱按次烧掉，而症结在号源（OpenAI 受理 200、短信根本没进这些虚拟号，实测尼日利亚 19 与泰国 52 都如此），换国家救不了，得换号源/更贵的实号池。
5. 报错文案带上手机号与密码——这两样是把号找回来的唯一线索（`platforms/chatgpt/protocol/phone_flow.py:921-936`），提示可以用手机号 + 密码单独登录把凭证补回来。

**完整失败分类表**：

| 失败 | 判定位置 | 循环内行为 | 号码回报 | 对外可重试 |
| --- | --- | --- | --- | --- |
| 租号失败（NO_NUMBERS / 出价过低 / 网络） | `platforms/chatgpt/protocol/phone_flow.py:533`、`platforms/chatgpt/protocol/auth_flow.py:1094` | continue 换号 | 无（还没租到） | 可 |
| 号被 OpenAI 拒（`already_in_use` / `disallowed_phone` / `suspicious behavior from phone` 等） | `platforms/chatgpt/protocol/phone_flow.py:83`、`:546`；`platforms/chatgpt/protocol/auth_flow.py:1119` | continue 换号 | `mark_send_failed` → 退款 | 可 |
| 号已被注册过（落到 `login_password` / `mfa_challenge`） | `platforms/chatgpt/protocol/phone_flow.py:663-664` | 当 `_PhoneUnusable` 换号 | `mark_send_failed` | 可 |
| 服务端流程状态失效（`invalid authorization step` / `invalid state`） | `platforms/chatgpt/protocol/phone_flow.py:87`、`:568`；`platforms/chatgpt/protocol/auth_flow.py:1125` | 手机注册：抛出原因终止本轮；add-phone：break 交回上层重走 authorize | `mark_send_failed` → 退款 | 可（换号无意义，但重开一轮有意义） |
| OpenAI 频控（`too many phone verification`） | `platforms/chatgpt/protocol/auth_flow.py:1110` | break，放弃 add-phone；session_token 仍保留 | `mark_send_failed` | 可（建议换邮箱号/换代理 IP） |
| 收到码但 validate 失败 | `platforms/chatgpt/protocol/phone_flow.py:811`、`platforms/chatgpt/protocol/auth_flow.py:1203` | 同号继续等下一条码（受 `sms_code_retries_per_phone` 限制） | `mark_code_failed`（该码入黑名单，不退款） | 可 |
| 同一未识别错误连续 3 个号 | `platforms/chatgpt/protocol/phone_flow.py:588-598`、`platforms/chatgpt/protocol/auth_flow.py:1140-1150` | break 停止换号 | `mark_send_failed` | 可 |
| **账号已建好 + 全程零短信** | `platforms/chatgpt/protocol/phone_flow.py:835` → `:695` → `:919` | 抛 `PhoneAccountCreatedError` 终止整轮，**不换号** | `stop_reuse`（不退款） | **不可**（`NonRetryableRegisterError`） |
| 账号已建好 + 收到过码但后续崩了 | 同上，`sms_ever_received=True` | 抛 `PhoneAccountCreatedError` 终止整轮 | `stop_reuse` | 可 |
| 末段炸但凭证已到手 | `platforms/chatgpt/registration_engine.py:387`（`_salvage`） | 按成功处理，缺失项记 `metadata.partial` / `last_error` | — | 不涉及 |

### 五、脆弱点

#### 5.1 接码平台可用性与成本

1. **只有两家平台、同一套协议**（`services/sms_service.py:43`）。任一家改协议或下线，只有 base_url 与固定价参数两个旋钮可调，没有第三方 provider 类可切；`create_sms_provider` 对未知 key 直接抛 `RuntimeError`（`:897`）。
2. **出价撮合是隐性成本陷阱**。`NO_NUMBERS` 多半不是没货而是出价太低（`services/sms_service.py:621-624`），偶尔撮合成功的还是被反复回收的号段。`_explain_no_numbers` 只在**整个 provider 生命周期内查一次挂牌价**（`_warned_low_bid`，`:627-629`），换号多轮之后这条关键提示不会再出现。
3. **退款只覆盖部分失败**。`mark_send_failed` 才退款，`stop_reuse` 明确不退（`:856-863`）；「账号已建好但零短信」这条最烧钱的路径走的正是 `stop_reuse` + 终止，钱认了。
4. **号码复用是全局单条缓存**。`_SMS_CACHE` 只存一份（`:56`），换国家/换 Key 即失效（`:447-449`）；`_SMS_VERIFY_LOCK` 是全局 RLock，`get_phone` 里取、`report_success`/`cleanup` 里放（`:950`、`:1099`），并发注册任务在开启复用时会互相串行化，吞吐被压成 1。锁的 acquire/release 分散在 4 处方法里，`platforms/chatgpt/protocol/auth_flow.py:1016` 还额外补了一次 `_release_lock()` 兜底——说明确实出过泄漏。
5. **余额没有任何主动检查**。`get_balance` 只被设置页探针调用（`api/sms.py:87`），注册链路不查；余额耗尽只会表现为租号失败后的 3 次换号空转。

#### 5.2 号源质量

1. **虚拟号被静默拦下是常态**：OpenAI 受理发码请求返回 200，短信根本不进这些号（`platforms/chatgpt/protocol/phone_flow.py:926-932`）。系统只能事后靠「零短信」这个布尔判定，无法预判。
2. **接码平台的号是回收再卖的**，「已被占用」被明确注释为常态（`platforms/chatgpt/protocol/phone_flow.py:49-50`），所以拒绝关键字表要同时认下划线 code 与大白话 message 两种说法。
3. **20 分钟号码寿命 vs 80/120 秒等码窗口**：一个号最多被一轮尝试用掉一小段寿命，剩余寿命 ≤30s 时 `report_success` 强制结算清缓存（`services/sms_service.py:820`），复用价值受限。
4. **验证码内容不落盘、不入日志**（`services/sms_service.py:766`），排障时只能看到状态 token，无法回溯短信是否来自 OpenAI。

#### 5.3 WhatsApp 切换风险

1. **白名单硬编码为单一元素 `{"52"}`**（`services/sms_service.py:41`）。若泰国也切成 WhatsApp，整条纯 SMS 路径同时失效，且需要改代码而非改配置。
2. **默认不阻止非白名单国家**（`sms_strict_whitelist` 默认 `False`，`:1017`），自动选号按价格升序挑最便宜的，最便宜的国家往往正是 WhatsApp 化的国家 —— 默认配置与已知事实相反，只靠 warning 提示。
3. **通道检测是关键字启发式**：`_warn_if_not_sms_channel` 靠在响应体里找 `whatsapp` / `voice` / `flash_call` 字样（`platforms/chatgpt/protocol/phone_flow.py:336-351`），且只在 JSON 响应上生效；导航版发码返回 HTML 时（正路）这个检查被跳过（`platforms/chatgpt/protocol/phone_flow.py:323`），也就是**最常走的那条路径没有通道检测**。
4. **「发送成功但收不到短信」在两条链路里的归因不一致**：手机注册能定成不可重试（有 `_phone_sms_ever_received`），add-phone 循环没有等价标记，只会一路换号到 `max_phone_attempts` 用尽（`platforms/chatgpt/protocol/auth_flow.py:1186-1187` 的 `break` 后接下一轮租号），每轮都要付租号钱。
5. **告警只进日志**，没有任何指标/计数把「零短信率」暴露出来，运营侧要靠人读日志才能发现号源已经整体失效。

---

## 七、ChatGPT TOTP 2FA 绑定

### TOTP 2FA 自动绑定

ChatGPT 平台注册成功后的一个**可选后置动作**：给刚建好的号程序化绑一个 TOTP 双因子，把服务端下发的密钥随号落库。开关是注册引擎上的 `bind_2fa`（`platforms/chatgpt/registration_engine.py:290`），关掉则整段逻辑不执行。

代码只有三个文件：

| 文件 | 职责 |
| --- | --- |
| `platforms/chatgpt/protocol/two_factor.py` | mfa 三步 + 快慢两条取 token 的路 |
| `platforms/chatgpt/protocol/totp.py` | 手写 RFC 6238 算码 |
| `services/chatgpt_two_factor.py` | 落库、补绑任务、结果回写 |

#### 对外三个接口的分工

`platforms/chatgpt/protocol/two_factor.py:3-5` 的 docstring 已经把关系说清楚了，不要看混：

| 接口 | 位置 | 真实职责 |
| --- | --- | --- |
| `enroll_totp(flow, access_token)` | `platforms/chatgpt/protocol/two_factor.py:70` | **唯一真正干活的**，三步走完把号绑上 |
| `bind_totp_inline(flow, access_token="")` | `platforms/chatgpt/protocol/two_factor.py:110` | 只负责「从注册会话里拿 access_token」 |
| `bind_totp_via_login(config, email, password, ...)` | `platforms/chatgpt/protocol/two_factor.py:125` | 只负责「重跑登录链拿 access_token」 |

后两者拿到 token 之后调的是同一个 `enroll_totp`，绑定动作**完全一样**，区别只在拿 token 的代价。

`enroll_totp` 整条链只用 `flow.session` + Bearer token，不碰 authorize 状态机（`platforms/chatgpt/protocol/two_factor.py:73-74`），所以任何来源的 token 都能直接喂进去。

#### 一、enroll_totp 的三步（外加一次复核）

三个端点常量在 `platforms/chatgpt/protocol/two_factor.py:40-42`：

| 步骤 | 方法 | 端点 | 实现 |
| --- | --- | --- | --- |
| ① 查已绑 | GET | `/backend-api/accounts/mfa_info` | `platforms/chatgpt/protocol/two_factor.py:169` |
| ② enroll 拿密钥 | POST | `/backend-api/accounts/mfa/enroll` | `platforms/chatgpt/protocol/two_factor.py:182` |
| ③ activate 提交码 | POST | `/backend-api/accounts/mfa/user/activate_enrollment` | `platforms/chatgpt/protocol/two_factor.py:207` |
| ④ 复核（只记日志） | GET | 同 ① | `platforms/chatgpt/protocol/two_factor.py:223` |

三步共用的请求头由 `_mfa_headers`（`platforms/chatgpt/protocol/two_factor.py:161`）拼：`flow._common_headers(url)` 打底 + `Authorization: Bearer <token>`，带 body 的两步再加 `Content-Type: application/json`。

##### ① 查已绑 `_probe_existing_totp` — `platforms/chatgpt/protocol/two_factor.py:169`

返回 `(是否已绑 totp, 查不出来的原因)` 两元组。

| 响应字段 | 判定 |
| --- | --- |
| `mfa_enabled` (bool) | 必须为真 |
| `factors.totp` | 必须非空，且 `factors` 得是 dict（`platforms/chatgpt/protocol/two_factor.py:178`） |

两者同时成立才算已绑（`platforms/chatgpt/protocol/two_factor.py:179`）。非 200、或响应不是 JSON，都**不当已绑**，只把原因回上去 —— `enroll_totp:83-84` 拿到原因后仅记一条 info 日志就继续 enroll，即「查不出来不阻塞」。

命中已绑时返回 `TwoFactorBindResult(ok=False, already_bound=True)`（`platforms/chatgpt/protocol/two_factor.py:82`）。注意 `ok` 是 **False**：`already_bound` 是独立维度，见下文。

##### ② enroll 拿密钥 `_enroll` — `platforms/chatgpt/protocol/two_factor.py:182`

| 方向 | 字段 |
| --- | --- |
| 请求 body | `{"factor_type": "totp"}`（`platforms/chatgpt/protocol/two_factor.py:188`） |
| 响应 `secret` | Base32 密钥明文，**服务端只在这里下发一次** |
| 响应 `session_id` | 本次 enroll 会话，第三步必须原样带回 |
| 响应 `factor.id` | 因子 id，写进结果的 `factor_id`，缺失容忍为空串 |

`secret` 和 `session_id` 任一为空即失败（`platforms/chatgpt/protocol/two_factor.py:202-203`）。非 200 时错误串用 `describe_error(resp.text)` 压缩（`platforms/chatgpt/protocol/two_factor.py:193`）。

**这一步的响应体故意不进 trace dump**（`platforms/chatgpt/protocol/two_factor.py:191`）：里面是明文密钥，而 trace 文件是给人翻着看的。二维码不由服务端返回，前端要展示时用 `platforms/chatgpt/protocol/totp.py:49` 的 `otpauth_uri()` 本地拼 `otpauth://totp/...?secret=&issuer=&algorithm=SHA1&digits=6&period=30` 再渲染。

拿到密钥后**立刻**写 `flow.result.totp_secret = secret`（`platforms/chatgpt/protocol/two_factor.py:93`），抢在 activate 之前 —— 注释写明原因：activate 再失败，号那边也已经挂了一个待激活的 totp factor，此时密钥丢了就再也补不回来。

##### ③ activate 提交验证码 `_activate` — `platforms/chatgpt/protocol/two_factor.py:207`

验证码是本地算的，不是收来的：`totp_now(secret)`（`platforms/chatgpt/protocol/two_factor.py:96`）。算不出来（密钥不是合法 Base32）时返回带 `secret` 的失败结果，密钥仍然保住（`platforms/chatgpt/protocol/two_factor.py:97-100`）。

| 方向 | 字段 |
| --- | --- |
| 请求 body | `{"code": <6位码>, "factor_type": "totp", "session_id": <②的 session_id>}`（`platforms/chatgpt/protocol/two_factor.py:213`） |
| 成功 | HTTP 200 → 返回空串 |
| 429 | 追加提示「提交太频繁，等一个 30 秒窗口换新码再试」（`platforms/chatgpt/protocol/two_factor.py:219`） |

这一步**会**打 trace（`platforms/chatgpt/protocol/two_factor.py:216`，`_trace_http("mfa_activate_enrollment", resp)`）—— body 里只有一次性动态码，不是密钥。

##### ④ 复核 `_confirm` — `platforms/chatgpt/protocol/two_factor.py:223`

`sleep(2)` 后再查一次 `mfa_info`。**纯日志、不影响返回值**：enroll + activate 都 200 就判成功（`platforms/chatgpt/protocol/two_factor.py:224`）。查不到 `mfa_enabled` 只 warning，异常也只 warning（`platforms/chatgpt/protocol/two_factor.py:229`）。

##### 返回结构 `TwoFactorBindResult` — `platforms/chatgpt/protocol/two_factor.py:48`

| 字段 | 含义 |
| --- | --- |
| `ok` | 本次真的绑成功了 |
| `secret` | 密钥（失败但已拿到密钥时也可能非空） |
| `factor_id` | 因子 id |
| `already_bound` | 号本来就绑过 |
| `error_message` | 失败原因 |

`already_bound` 必须和失败分开看（`platforms/chatgpt/protocol/two_factor.py:52-53`）：服务端不下发第二次 secret，**给已绑的号再 enroll 一遍只会把它原有的验证器废掉**，所以这种情况要当「不用做」而不是「没做成」。`summary()`（`platforms/chatgpt/protocol/two_factor.py:62`）按 `already_bound` → `ok` → 错误 的优先级出人话。

#### 二、totp.py：手写 RFC 6238

| 常量/函数 | 位置 | 说明 |
| --- | --- | --- |
| `TOTP_PERIOD = 30` / `TOTP_DIGITS = 6` | `platforms/chatgpt/protocol/totp.py:17-18` | 标准参数 |
| `normalize_secret` | `platforms/chatgpt/protocol/totp.py:21` | 去空格 + 转大写，把展示用密钥还原成可 b32decode |
| `hotp` | `platforms/chatgpt/protocol/totp.py:26` | RFC 4226，HMAC-SHA1 + 动态截断，b32 右补 `=` |
| `totp_now` | `platforms/chatgpt/protocol/totp.py:36` | `int(time)//30` 当计数器，出 6 位码 |
| `verify_totp` | `platforms/chatgpt/protocol/totp.py:42` | 前后各放一个窗口（`-1/0/+1`）的本地自检 |
| `otpauth_uri` | `platforms/chatgpt/protocol/totp.py:49` | 拼验证器 App 认的 `otpauth://` 链接 |

不拉 `pyotp` 的两个理由写在 `platforms/chatgpt/protocol/totp.py:3-5`：算法一共二十行，为它多一个运行时依赖不划算（这条链路在 Docker 镜像里也要能跑）；更重要的是**绑 2FA 和登录撞上 mfa-challenge 两边共用这一份实现**，免得哪天改了容差窗口只改到一半。

#### 三、快路径 vs 慢路径

##### 两条路的代价对比

| 维度 | 快路径 `bind_totp_inline` | 慢路径 `bind_totp_via_login` |
| --- | --- | --- |
| 入口 | `platforms/chatgpt/protocol/two_factor.py:110` | `platforms/chatgpt/protocol/two_factor.py:125` |
| token 来源 | 复用注册那条 flow 的 `flow.result.access_token` | 新起 `AuthFlow` 重跑登录正式链 |
| PoW | 零 | 一次 |
| 邮件验证码 | 零 | 低信任新号多半要收一封 |
| 需要的输入 | 只要 flow | 邮箱 + 密码 + 收件通道 |
| 耗时 | 几秒 | 整条登录链 |
| 调用时机 | 注册链 `_on_session_ready`，Codex 授权之前 | 快路径失败，或给库里老号补绑 |

快路径成立的原因（`platforms/chatgpt/protocol/two_factor.py:8-10`）：注册链几十秒前才刚做完 OTP 验证 + create_account，**服务端眼里这就是「最近认证过」**，enroll 要的 recent-auth 条件本来就满足，所以不用重新登录、不用再收邮件。

快路径把**任何异常都收成失败结果**返回（`platforms/chatgpt/protocol/two_factor.py:121-122`），绝不上抛。原因写在 `platforms/chatgpt/protocol/two_factor.py:113-114`：这一步跑在「号已经注册成功」之后，不能因为没绑上 2FA 就把号一起丢掉；调用方看结果自己决定要不要回落。

慢路径用**独立 AuthFlow 实例**（`platforms/chatgpt/protocol/two_factor.py:143-147`），即独立 device_id + 独立指纹，批量补绑时不会几十个号共用一套特征（`platforms/chatgpt/protocol/two_factor.py:136-137`）。

##### 慢路径登录链的关键细节 `_login_for_access_token` — `platforms/chatgpt/protocol/two_factor.py:240`

| 环节 | 位置 | 要点 |
| --- | --- | --- |
| 打水印 | `platforms/chatgpt/protocol/two_factor.py:252` | `chain_started_at` 在**整条链开始前**就取 |
| warmup | `platforms/chatgpt/protocol/two_factor.py:255-256` | 没拿到 `oai-did` cookie 直接判死，否则必然 409 `invalid_state` |
| 标记老号 | `platforms/chatgpt/protocol/two_factor.py:259` | `_is_existing_account = True`，发码走 resend 复用同一个 challenge state |
| 密码页 | `platforms/chatgpt/protocol/two_factor.py:276-284` | `page_type == "login_password"` 或 URL 含 `/log-in/password` 时先 GET 密码页再 `login_password_verify` |
| 邮箱验证码 | `platforms/chatgpt/protocol/two_factor.py:286-293` | `page_type == "email_otp_verification"` 或 URL 含 `/email-verification`；没收件通道就失败 |
| **撞到已绑** | `platforms/chatgpt/protocol/two_factor.py:297-298` | `_is_mfa_challenge_state` 命中 → 返回 `_ALREADY_BOUND` |
| 换会话 | `platforms/chatgpt/protocol/two_factor.py:300-305` | `_consume_callback_for_session` → `get_auth_session()` 取 access_token |

水印提前打的原因（`platforms/chatgpt/protocol/two_factor.py:248-250`）：服务端早在 oauth_init 阶段就可能把码发出去了，卡在 `authorize/continue` 前一刻打基线反而会把**唯一那封带对码的信判成旧信**；同一次 challenge 内多封信的码是一样的，放宽窗口不会抓错。

`_ALREADY_BOUND`（`platforms/chatgpt/protocol/two_factor.py:45`）是**成功语义的提前退出**：登录链走到一半发现号早就绑过了，`bind_totp_via_login:149-150` 把它翻成 `already_bound=True` 而不是错误，绝不能继续 enroll。

`_obtain_otp`（`platforms/chatgpt/protocol/two_factor.py:308`）先 `peek_otp(wait=4)` 瞄一眼信箱，有码就直接用；没有才 `kickoff_otp_delivery("existing_bind_2fa")`，失败再退到 `send_otp`（`platforms/chatgpt/protocol/two_factor.py:334-335`）。先瞄的理由（`platforms/chatgpt/protocol/two_factor.py:316-319`）：走到这一步服务端往往已经投了一两封码信（`get_auth_url` 带 login_hint、`authorize/continue` 各触发一次），再 resend 纯属给风控送素材；绑定链的 resend 只是把同一个 challenge 的码再投一遍、不改服务端状态，所以已投递那封本来就有效。

##### 判定与降级顺序

快路径在 `_on_session_ready`（`platforms/chatgpt/registration_engine.py:270`）跑；降级判定在 `_ensure_two_factor`（`platforms/chatgpt/registration_engine.py:284`），按顺序四道闸门，任一命中即**跳过慢路径**：

| 顺序 | 条件 | 位置 | 理由 |
| --- | --- | --- | --- |
| 1 | `bind_2fa` 关 | `platforms/chatgpt/registration_engine.py:290` | 用户没要 2FA |
| 2 | `flow.result.totp_secret` 已有，或 `already_bound` | `platforms/chatgpt/registration_engine.py:292` | 已经有密钥/号本来就绑过，重绑会废掉现有验证器 |
| 3 | 邮箱里没有 `@` | `platforms/chatgpt/registration_engine.py:297` | **手机身份账号**：登录链只认邮箱，跑不通 |
| 4 | 没有密码 | `platforms/chatgpt/registration_engine.py:300` | 慢路径必须邮箱 + 密码 |

第 3 条就是「手机身份账号为何跳过慢路径」的全部原因：慢路径的入口 `_login_for_access_token` 走的是 `get_auth_url(csrf, email=email)` → 密码页 → 邮件验证码这条**纯邮箱正式链**（`platforms/chatgpt/protocol/two_factor.py:262`、`276`、`286`），手机号身份没有邮箱可填也没有信箱可收，重跑一遍必然卡死，白付一次 PoW。所以宁可不绑。

真要降级时，先 `mail_provider.prime()` 重打收件基线（`platforms/chatgpt/registration_engine.py:307`）—— 注册那封码信还躺在收件箱里，不重打会被当成绑定链的码用。

慢路径与快路径的密钥都**双写**兜底（`platforms/chatgpt/registration_engine.py:280-281`、`316-317`）：`enroll_totp` 内部已写过一次 `flow.result.totp_secret`，外层再兜一道，注释理由是「密钥丢了这个号的 2FA 就永久锁死，不值得赌它一定被写进去了」。

##### 路径选择与降级流程

```mermaid
flowchart TD
    A[注册链成功<br/>session 到手] --> B{bind_2fa 开?}
    B -- 否 --> Z[不绑，结束]
    B -- 是 --> C["快路径 bind_totp_inline<br/>registration_engine.py:277"]
    C --> D["复用 flow.result.access_token<br/>two_factor.py:117"]
    D --> E{有 token?}
    E -- 无 --> F[失败：注册会话没有 access_token]
    E -- 有 --> G["enroll_totp 三步<br/>two_factor.py:70"]
    G --> H{结果}
    H -- ok --> S["密钥写 flow.result.totp_secret<br/>调用方立刻落库"]
    H -- already_bound --> T[视为不用做，不重绑]
    H -- 失败 --> F
    F --> I["降级判定 _ensure_two_factor<br/>registration_engine.py:284"]
    I --> J{已有 secret<br/>或 already_bound?}
    J -- 是 --> T
    J -- 否 --> K{邮箱含 @?}
    K -- 否<br/>手机身份账号 --> L["跳过：登录链只认邮箱<br/>registration_engine.py:298"]
    K -- 是 --> M{有密码?}
    M -- 否 --> N["跳过：慢路径需邮箱+密码<br/>registration_engine.py:301"]
    M -- 是 --> O["mail_provider.prime 重打基线<br/>registration_engine.py:307"]
    O --> P["慢路径 bind_totp_via_login<br/>新 AuthFlow：独立 device_id/指纹"]
    P --> Q["warmup → PoW → 密码页<br/>→ 可能收一封验证码邮件"]
    Q --> R{撞 mfa-challenge?}
    R -- 是 --> T
    R -- 否 --> U["get_auth_session 取 access_token"]
    U --> G
    Z --> Z2[结束]
    S --> Z2
    T --> Z2
    L --> Z2
    N --> Z2
```


#### 四、密钥落库与取用

##### 存放位置

密钥统一存在**账号行 `extra_json` 的 `totp_secret` 键**下，和密码放在一起。读取封装在 `services/chatgpt_two_factor.py:36`：

| 函数 | 位置 | 作用 |
| --- | --- | --- |
| `account_totp_secret(model)` | `services/chatgpt_two_factor.py:36` | 取 `extra.totp_secret` 并 strip |
| `account_missing_two_factor(model)` | `services/chatgpt_two_factor.py:40` | 「这号还没密钥」的判定谓词 |

注册链方向：`enroll_totp` 写 `flow.result.totp_secret` → `AuthResult.to_dict()`（`platforms/chatgpt/protocol/auth_flow.py:75`）→ 注册结果 `metadata["totp_secret"]`（`platforms/chatgpt/registration_engine.py:85`）→ 注册模式适配器 `build_account` 塞进 `account.extra` → `save_account` 落 `extra_json`。这条交接链每一环都有落库断言（`tests/test_chatgpt_two_factor_persistence.py:77-104`），因为中途任何一环丢了密钥这个号的 2FA 就废了（该文件 docstring `:3-4`）。

##### build_extra_patch 回写机制 — `services/chatgpt_two_factor.py:144`

绑定函数 `bind_account_two_factor` **本身不落库**（`services/chatgpt_two_factor.py:83`），落库拆成独立的补丁构造，理由写在 `:85-86`：这一趟要跑几十秒网络请求，只收纯数据不收 ORM 对象，好让调用方在这期间把数据库连接还回池子。

补丁内容：

| 键 | 何时写入 | 说明 |
| --- | --- | --- |
| `totp_secret` | **只在 `result.secret` 非空时**（`:151-152`） | 密钥本体 |
| `chatgpt_2fa.bound` | 总是 | `result.ok or result.already_bound` |
| `chatgpt_2fa.message` | 总是 | `result.summary()` |
| `chatgpt_2fa.at` | 总是 | UTC ISO 时间戳 |

**`totp_secret` 的条件写入是这份代码里最关键的一行防御**（`:147-148`）：没拿到密钥时只留 `chatgpt_2fa` 一条留痕，绝不能用空串覆盖掉库里原有的密钥 —— 那等于把号的 2FA 弄成永久锁死。两个测试各守一头：失败时不写空键（`tests/test_bind_2fa_task_api.py:209`、`tests/test_chatgpt_two_factor_persistence.py:124`），失败时也不清掉已有密钥（`tests/test_chatgpt_two_factor_persistence.py:174-189`）。

补丁落到账号行由 `apply_two_factor_result`（`services/chatgpt_two_factor.py:161`）做：`get_extra()` → `update(patch)` → `set_extra()` → 刷 `updated_at`，`commit` 可选。手动 action 通道则由 `api/actions.py` 的 `_apply_action_result` 消费返回值里的 `account_extra_patch`（`platforms/chatgpt/plugin.py:271`）。

##### 后台补绑任务链路

补绑面向**库里已有的老号**，代价阶梯和注册链同构但两条腿都换了来源（`services/chatgpt_two_factor.py:3-9`）：

| 路 | 位置 | 做法 | 代价 |
| --- | --- | --- | --- |
| ① 会话复用 | `services/chatgpt_two_factor.py:181` `_bind_via_session` | 用库里 `session_token`/`access_token`/`device_id` 走 `from_existing_credentials` 恢复登录态，再调 `bind_totp_inline` | 不发邮件、不碰密码页、不跑 PoW，几秒 |
| ② 协议重登 | `services/chatgpt_two_factor.py:135` | 邮箱 + 密码调 `bind_totp_via_login` | 一次 PoW + 多半一封验证码邮件 |

`bind_account_two_factor`（`services/chatgpt_two_factor.py:70`）的闸门顺序：

| 顺序 | 条件 | 位置 | 结果 |
| --- | --- | --- | --- |
| 1 | `extra.totp_secret` 已有 | `:92-96` | 直接返回 `already_bound=True`，**带上库里那把旧密钥**，绝不重绑 |
| 2 | 有 `session_token` 或 `access_token` | `:103` | 走路 ①；`ok` 或 `already_bound` 就收工（`:113-114`） |
| 3 | 两个 token 都没有 | `:116-117` | 记日志跳过路 ① |
| 4 | `allow_login=False` | `:119-120` | 停在这里，错误串带「已关闭协议重登」 |
| 5 | 没有密码 | `:121-122` | 停在这里，错误串带「库里没有密码」 |
| 6 | 以上都过 | `:124-141` | 解析收件通道后走路 ② |

路 ① 的失效判定是 `flow.result.access_token` 为空（`:196-197`，「库里的 session/access token 已失效」）。路 ② 固定注入 `env_overrides={"OTP_TIMEOUT": ..., "WEBUI_ALLOW_LOGIN": "1"}`（`:140`），超时值按 `mailbox_otp_timeout_seconds` → `email_otp_timeout_seconds` → `otp_timeout` 顺序取，兜底 180 秒（`:229-237`）。两条路都包在 `mirror_protocol_logs(log_fn)` 里（`:104`、`:134`），协议层日志会镜像进任务日志。

**选号规则** `select_two_factor_targets`（`services/chatgpt_two_factor.py:44`）：

| 参数 | 行为 |
| --- | --- |
| `only_missing_2fa=True`（默认） | 用 `account_missing_two_factor` 过滤，只留没密钥的号 |
| `only_missing_2fa=False` | 不过滤，手动单号操作时用，让它把「已绑」这个结论也跑出来 |
| `account_ids` / `all_filtered` / `email` / `status` / `plus_status` | 转给 `select_chatgpt_accounts`，返回 `(账号列表, 找不到的 id)` |

默认过滤的理由（`:56-57`）：库里已经有密钥的号再 enroll 一遍**只会把用户手上的验证器废掉**。测试覆盖：`tests/test_bind_2fa_task_api.py:35`（只留无密钥）、`:53`（关掉开关后含已绑号）、`:61`（缺失 id 单独回报）、`:73`（不给范围直接 `ValueError`）。

**任务接口** `POST /tasks/bind-2fa`，行为由 `tests/test_bind_2fa_task_api.py:79` 起的一组测试钉住：

| 场景 | 响应 | 测试 |
| --- | --- | --- |
| 有可绑号 | 200，`task_id` 以 `bind_2fa_` 开头，返回 `total` | `:103` |
| 全部已有密钥 | 400，detail 含「已经有 2FA 密钥」，**不启动任务** | `:116` |
| 传的 id 库里没有 | 400，detail 为「所选账号不存在」（不误报成密钥问题） | `:126` |
| 不给范围 | 400，detail 提 `all_filtered` | `:132` |
| 绑成功 | `success=1`，密钥进 `extra.totp_secret` **且进任务日志** | `:138` |
| `already_bound` | `success=0`、`errors=[]`、日志打 `[SKIP]`，算跳过不算失败 | `:176` |
| 绑失败 | `success=0`、1 条 error，`extra` 里**没有** `totp_secret` 键 | `:193` |

任务 runner 会把停止开关和 attempt 一起交给引擎（`tests/test_bind_2fa_task_api.py:162`）：会话失效时要重登收验证码，那几分钟归邮箱管，拿不到 `task_control` 就停不下来。

密钥进任务日志是**故意的**（`tests/test_bind_2fa_task_api.py:155`、`platforms/chatgpt/registration_engine.py:322-323`、`platforms/chatgpt/plugin.py:268-269`）：密钥只下发这一次，日志/弹窗是用户当场导入验证器的唯一途径。前端另有三处取用入口（列表标签、账号菜单「复制 2FA 密钥」、详情密钥栏，见 `README.md:163`），批量则走 `services/account_export.py:81` 的导出字段。

##### 后续登录撞到 mfa-challenge 时自动过关

绑定即生效，之后该号所有登录都要过这关。识别与自动过关逻辑在 `platforms/chatgpt/protocol/auth_flow.py`：

| 环节 | 位置 | 做法 |
| --- | --- | --- |
| 识别 | `platforms/chatgpt/protocol/auth_flow.py:891` `_is_mfa_challenge_state` | `page_type == "mfa_challenge"` 或 URL 含 `/mfa-challenge/` |
| 取密钥（内存） | `platforms/chatgpt/protocol/auth_flow.py:830` | 先看 `self.result.totp_secret` |
| 取密钥（回库） | `platforms/chatgpt/protocol/auth_flow.py:831-838` | 空则调 `_account_callback(email)`，签名 `(email) -> {"password","totp_secret"}`（`platforms/chatgpt/protocol/auth_flow.py:140-141`），命中后回填 result |
| 拿不到密钥 | `platforms/chatgpt/protocol/auth_flow.py:841-843` | 只 warning 后放弃推进，**不抛异常** |
| 取 challenge_id | `platforms/chatgpt/protocol/auth_flow.py:845` | `continue_url` 最后一段 |
| 算码 | `platforms/chatgpt/protocol/auth_flow.py:850` | `_totp_now(totp_secret)`，即 `platforms/chatgpt/protocol/totp.py:36` 那一份 |
| 提交 | `platforms/chatgpt/protocol/auth_flow.py:2286` `submit_mfa_totp` | `POST auth.openai.com/api/accounts/mfa/verify`，body `{"code","type":"totp","id":challenge_id}`，带 sentinel 头；非 200 抛 `RuntimeError` |

同一套「取密钥 → 算码 → submit」在 `auth_flow.py` 里出现在三处登录推进分支（`:829`、`:2963`、`:3260`），这也是 `totp.py` 坚持共用一份实现的现实原因。补 RT 链路走的是 `platforms/chatgpt/rt_backfill.py:244-245`，把库里密钥先塞进 `flow.result.totp_secret` 再跑登录；密钥来源是 `services/chatgpt_rt_backfill.py:107` 从 `extra` 读出来的。

#### 五、不可恢复风险

这是本模块唯一需要真正记住的事，`platforms/chatgpt/protocol/two_factor.py:16-18` 的星标写得很直白。

| 事实 | 依据 | 后果 |
| --- | --- | --- |
| 密钥**只在 enroll 响应里下发一次** | `platforms/chatgpt/protocol/two_factor.py:16`、`platforms/chatgpt/protocol/two_factor.py:184` 的日志 | 没有第二次机会 |
| 服务端**不存明文，任何接口取不回** | `platforms/chatgpt/protocol/two_factor.py:16-17` | `mfa_info` 只回 `mfa_enabled` / `factors`，没有 secret |
| **绑定即生效** | `platforms/chatgpt/protocol/two_factor.py:20-21` | 之后每次登录都要过 mfa-challenge |
| 密钥丢了 | `platforms/chatgpt/protocol/two_factor.py:17-18` | **这个号再也登不进去，2FA 永久锁死** |
| 给已绑的号再 enroll | `platforms/chatgpt/protocol/two_factor.py:52-53` | 不会下发新密钥，只会把用户手上的验证器废掉 |
| activate 失败但 enroll 已成 | `platforms/chatgpt/protocol/two_factor.py:91-92` | 号上已挂待激活 factor，此时丢密钥同样补不回来 |

代码里为这一条风险堆的所有防御，接手时不要「顺手简化」掉任何一个：

| 防御 | 位置 |
| --- | --- |
| 拿到 secret 立刻写 `flow.result`，抢在 activate 之前 | `platforms/chatgpt/protocol/two_factor.py:93` |
| 快路径异常全收成失败结果，绝不上抛丢号 | `platforms/chatgpt/protocol/two_factor.py:121-122` |
| 外层再兜一道写 `flow.result.totp_secret` | `platforms/chatgpt/registration_engine.py:280-281`、`316-317` |
| enroll 响应体不进 trace dump | `platforms/chatgpt/protocol/two_factor.py:191` |
| `already_bound` 与失败分成两个维度 | `platforms/chatgpt/protocol/two_factor.py:59`、`:52-53` |
| 没密钥时**不写** `totp_secret` 键 | `services/chatgpt_two_factor.py:151-152` |
| 默认只挑没密钥的号补绑 | `services/chatgpt_two_factor.py:66`、`:56-57` |
| 库里已有密钥直接 `already_bound` 返回 | `services/chatgpt_two_factor.py:92-96` |
| 密钥打进任务日志 + 前端弹窗 | `platforms/chatgpt/registration_engine.py:322-323`、`platforms/chatgpt/plugin.py:268-269` |
| 绑定链的落库断言而非只测内存 result | `tests/test_chatgpt_two_factor_persistence.py:3-4` |
| activate 后的复核只记日志，不推翻已到手的密钥 | `platforms/chatgpt/protocol/two_factor.py:224`、`:229` |

最后一条尤其别改：`_confirm` 看不到 `mfa_enabled` 时**不能**把结果判成失败，否则调用方会以为没绑上而丢弃密钥 —— 但号那边其实已经绑了。

---

## 八、ChatGPT 账号后置能力

> 面向新接手项目的开发者。本文只覆盖**注册成功之后**的能力:账号状态探测、Token 刷新、补 RT、
> 外部系统同步上传、付费与绑卡。注册主链路(authorize / Sentinel / 指纹)与手机接码 / 2FA 不在本文范围。
>
> 涉及代码:`platforms/chatgpt/{status_probe,token_refresh,rt_backfill,cpa_upload,sub2api_upload,payment}.py`、
> `platforms/chatgpt/payment_channels/direct/*`、`services/chatgpt_{sync,rt_backfill,account_state,account_selection}.py`、`api/chatgpt.py`。

---

#### 一、账号状态探测

##### 1.1 两条互不相同的探测链

`platforms/chatgpt/status_probe.py` 里有**两个独立入口**,目的不同、打的接口不同、UA 不同,不要混用:

| 入口 | 位置 | 目的 | 请求 | UA |
|---|---|---|---|---|
| `probe_local_chatgpt_status(account, proxy)` | `platforms/chatgpt/status_probe.py:388` | 判"凭证还能不能用 / 套餐是什么 / Codex 能不能跑" | `GET /backend-api/me` → 通过后再 `GET /backend-api/wham/usage` | `CODEX_USER_AGENT` |
| `probe_plus_trial_status(account, proxy)` | `platforms/chatgpt/status_probe.py:345` | 判"这个号还能不能白嫖首月 Plus" | `GET /backend-api/accounts/check/v4-2023-04-27` | `BROWSER_USER_AGENT` |

三个 URL 常量集中在 `platforms/chatgpt/status_probe.py:18-21`:

- `CODEX_USAGE_URL = https://chatgpt.com/backend-api/wham/usage`
- `CHATGPT_ME_URL = https://chatgpt.com/backend-api/me`
- `CHATGPT_ACCOUNTS_CHECK_URL = https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27`

所有请求统一走 `_perform_get()`(`platforms/chatgpt/status_probe.py:174`),用 `curl_cffi` + `impersonate="chrome110"`,超时 20s,代理由 `_build_proxies()` 拼成 `{"http":…, "https":…}`。

##### 1.2 `CODEX_USER_AGENT` 的作用与 UA 分裂的原因

```python
CODEX_USER_AGENT = "codex_cli_rs/0.116.0 (Mac OS 26.0.1; arm64) Apple_Terminal/464"
```

- `/backend-api/me` 与 `/backend-api/wham/usage` 是 **Codex CLI 自己调的接口**,所以带 Codex CLI 的 UA 才是"正常客户端"。`wham/usage` 更是 Codex 专属额度接口,用浏览器 UA 去打反而可疑。
- `accounts/check` 相反,是**网页端自己调的接口**,用 Codex UA 去打属于明显的非浏览器特征(`platforms/chatgpt/status_probe.py:22` 原注释),因此换成 `BROWSER_USER_AGENT`(Chrome/145 on Win64),并补 `Origin` / `Referer` / `OAI-Device-Id`。

##### 1.3 请求头与账号标识的来源

| 头 | 来源 | 位置 |
|---|---|---|
| `Authorization: Bearer <at>` | `extra.access_token` → `account.access_token` → `account.token`,依次取第一个非空 | `platforms/chatgpt/status_probe.py:349-354` |
| `Chatgpt-Account-Id` / `ChatGPT-Account-Id` | `extract_chatgpt_account_id()`:先 `account.user_id`;否则**解 JWT** —— 先解 `id_token`,再解 `access_token`,取 `payload["https://api.openai.com/auth"].chatgpt_account_id` 或 `.account_id` | `platforms/chatgpt/status_probe.py:81-103` |
| `OAI-Device-Id`(仅 accounts/check) | 先从 `extra.cookies` 里抠 `oai-did=`;抠不到就 `uuid5(NAMESPACE_DNS, "chatgpt-plus-check:{email}")` —— **同号每次检测都是同一个 device**,比每次随机更像正常客户端 | `platforms/chatgpt/status_probe.py:236-252` |

JWT 解析用 `_decode_jwt_payload()`(`platforms/chatgpt/status_probe.py:60`),只做 base64url 补位 + `json.loads`,**不验签**(只是为了读 claim)。

##### 1.4 错误信息的三处提取

`ProbeHTTPResult`(`platforms/chatgpt/status_probe.py:164`)统一承载 `status_code / headers / body_text / body_json / error_code / message`。错误码与文案的取值顺序:

| 字段 | 优先级 |
|---|---|
| `error_code` | 响应头 `X-Openai-Ide-Error-Code` → `body_json.error.code` → `X-Error-Json`(base64 解码后的 JSON)`.error.code`(`platforms/chatgpt/status_probe.py:133`) |
| `message` | `body_json.error.message` → header JSON `.error.message` → `body_json.message` → 裸 body 文本 → `f"HTTP {code}"`,截断 500 字(`platforms/chatgpt/status_probe.py:151`) |

`X-Error-Json` 是 OpenAI 把结构化错误塞在头里的做法,`_parse_header_error_json()`(`platforms/chatgpt/status_probe.py:117`)负责 base64 解码;解不出就返回 `{}`,不抛异常。

##### 1.5 `probe_local_chatgpt_status` 返回结构

返回一个 `version:1` 的三段式字典,整份原样写进账号 `extra["chatgpt_local"]`:

| 段 | 字段 | 说明 |
|---|---|---|
| `auth` | `state` / `checked_at` / `source="backend_me"` / `http_status` / `error_code` / `message` / `refresh_available` | `refresh_available = bool(refresh_token or session_token)` —— 告诉上层"这号还救不救得回来" |
| `subscription` | `plan` / `source="backend_me"` / `workspace_plan_type` / `subscription_active_until` / `chatgpt_account_id` | `plan` 经 `_normalize_plan_type()` 归一 |
| `codex` | `state` / `source="wham_usage"` / `http_status` / `error_code` / `message` / `chatgpt_account_id` | 只有 `/me` 200 且拿到 account_id 才真去探 |

`_normalize_plan_type()`(`platforms/chatgpt/status_probe.py:197`)把 `plan_type` 与 `workspace_plan_type` 拼成一串小写后按 **enterprise → team → plus → pro → free** 的顺序做子串匹配,都不中返回 `unknown`。`workspace_plan_type` 从 `/me` 响应的 `orgs.data[*].settings.workspace_plan_type` 里取**第一个非空**(`platforms/chatgpt/status_probe.py:460-468`)。
`subscription_active_until` 取 `chatgpt_subscription_active_until`,回退 `subscription_active_until`。

###### auth.state 判定表

| 条件 | `auth.state` | 同时 `codex.state` |
|---|---|---|
| 没有 access_token(不发请求) | `missing_access_token` | `skipped_auth_invalid` |
| `/me` 200 且有 JSON body | `access_token_valid` | 继续探 Codex |
| `/me` 401 且 `error_code == "token_invalidated"` | `access_token_invalidated` | `skipped_auth_invalid` |
| `/me` 401 其他 | `unauthorized` | `skipped_auth_invalid` |
| `/me` 403 且 `is_account_deactivated_message()` 命中 | `account_deactivated` | `skipped_auth_invalid` |
| `/me` 403 其他 | `banned_like` | `skipped_auth_invalid` |
| 其他一切(5xx / 空 body / 网络异常前的非预期码) | `probe_failed` | `not_checked` |

###### codex.state 判定表(仅 auth 通过后)

| 条件 | `codex.state` |
|---|---|
| 缺 `Chatgpt-Account-Id`(解不出) | `probe_failed`,message 写"无法严格探测" |
| `wham/usage` 200 | `usable` |
| 401 且 `error_code == "token_invalidated"` | `access_token_invalidated` |
| 401 其他 | `unauthorized` |
| `is_account_deactivated_message()` 命中(不看状态码) | `account_deactivated` |
| 402 / 403 | `payment_required` |
| 429 | `quota_exhausted` |
| 其他 | `probe_failed` |

判定顺序有意义:`account_deactivated` 的判据插在 401 之后、402/403 之前(`platforms/chatgpt/status_probe.py:502-514`),所以"403 + 停用文案"归 `account_deactivated` 而不是 `payment_required`。

##### 1.6 Plus 试用识别

`PLUS_TRIAL_PROMO_ID = "plus-1-month-free"`(`platforms/chatgpt/status_probe.py:30`)是**唯一依据**:账号还能领时它出现在 `accounts.<key>.eligible_promo_campaigns.plus.id` 里;领过或不符合条件就没有。

`_pick_account_entry()`(`platforms/chatgpt/status_probe.py:272`)从 `body_json["accounts"]` 里优先取 `"default"`,没有就取第一个 dict 值。随后读三块:`entry.account`、`entry.entitlement`、`entry.eligible_promo_campaigns`。

###### `classify_plus_trial()` 完整判定表(`platforms/chatgpt/status_probe.py:284`)

| 顺序 | 条件 | `status` | 中文 `label` |
|---|---|---|---|
| 1 | HTTP 401/403 且 `looks_like_banned_response(body)` 或 `is_account_deactivated_message(code,msg)` | `banned` | 封号 |
| 2 | HTTP 401(未命中封号词) | `token_invalid` | 凭证失效 |
| 3 | HTTP 403(未命中封号词) | `error` | 检测失败 |
| 4 | 非 200 | `error` | 检测失败 |
| 5 | 200 但 `accounts` 里挑不出 entry | `error`,message="响应里没有账户数据" | 检测失败 |
| 6 | `account.is_deactivated == true` | `banned` | 封号 |
| 7 | `plan ∈ {plus,pro,team,enterprise}` **或** `entitlement.has_active_subscription` | `plus_active` | Plus 生效中 |
| 8 | `eligible_promo_campaigns.plus.id == "plus-1-month-free"` | `trial_eligible` | 可领首月免费 |
| 9 | 其余 | `free` | Free |

另外两个只由 `probe_plus_trial_status()` 产生的值:缺 AT → `no_access_token`(**不发网络请求**);请求抛异常 → `error`(`message="请求失败：…"`)。

**为什么 401/403 一定要读响应体**(`platforms/chatgpt/status_probe.py:287-289` 原注释):账号被封时 access_token 会一起被吊销,请求在 401/403 就被拒了,走不到 200 分支的 `is_deactivated` 判据 —— 所以要按响应体措辞区分"封号"与"单纯凭证失效"。

`PLUS_TRIAL_INCONCLUSIVE = {"no_access_token", "error"}`(`platforms/chatgpt/status_probe.py:44`):这两个**不是结论而是"没检测成"**,不能写进账号,否则该号从"未检测"里消失,看着像已经查过。落库时由 `platforms/chatgpt/plugin.py:182-191` 把 `conclusive` 为假的结果的 `account_extra_patch` 置空。

##### 1.7 `services/chatgpt_account_state.py` 的职责划分

`status_probe.py` 只负责**发请求 + 翻译成结构化 probe**;`chatgpt_account_state.py` 负责**把 probe 翻译成账号行上的 status**,两者严格分层(probe 模块反向 import 前者的两个文本判据函数)。

| 函数 | 位置 | 职责 |
|---|---|---|
| `is_account_deactivated_message(code,msg)` | `:52` | 窄判据。`code ∈ {account_deactivated, account_deleted}`,或文案含 `deleted or deactivated` 等三条 marker |
| `looks_like_banned_response(body)` | `:81` | 宽判据。小写后子串匹配 `_BANNED_BODY_MARKERS`:`account_deactivated / accountdeactivated / deactivated / disabled / suspended / banned / violat / potential abuse / terminated` |
| `account_plus_status(account)` | `:90` | 读 `extra["plus_check"]["status"]`,没查过返回 `"unchecked"` |
| `filter_accounts_by_plus_status(accounts, want)` | `:104` | 列表页/批量任务按 plus 结论筛号(空 want 不筛) |
| `classify_local_probe_state(probe)` | `:111` | 本地 probe → 失效原因码:`auth_401 / auth_deactivated / auth_403 / codex_401 / codex_deactivated / codex_403`,都不中返回 `""` |
| `classify_remote_sync_state(sync)` | `:145` | 远端同步结果 → `remote_401 / remote_deactivated / remote_403` |
| `classify_local_probe_health(probe)` | `:164` | **反方向**:本地 probe 有没有拿到"这号确实可用"的正面证据。唯一判据是 `auth.state == access_token_valid`,即 `/backend-api/me` 回了 200 |
| `classify_remote_sync_health(sync)` | `:180` | **反方向**:远端 `remote_state == usable` |
| `apply_chatgpt_status_policy(account, local_probe, remote_sync)` | `:189` | **唯一改 status 的地方**:命中失效原因 → 标 `invalid`;拿到健康证据 → 把 `invalid` 恢复回去。返回"这次为什么动了状态"(没动返回 `""`) |

`INVALID_ACCOUNT_STATUS = "invalid"`。策略是**双向的**,内部分三步:

| 顺序 | 条件 | 动作 | 返回 |
|---|---|---|---|
| 1 | `classify_*_state()` 命中失效原因 | `status = "invalid"`,并把**失效前**那份 status 记进 `extra["chatgpt_status_before_invalid"]`(`_mark_invalid()`,`:214`) | 原因码,如 `auth_401` |
| 2 | 没命中失效,但 `classify_*_health()` 拿到正面证据,**且当前 status 正是 `invalid`** | 还原成 `extra` 里那份失效前状态(白名单 `registered/trial/subscribed/expired` 之外回落 `registered`),标记用完即删(`_restore_status()`,`:227`) | `RECOVERED_STATUS_REASON = "recovered"` |
| 3 | 其余(结论不明 / 状态本来就正常) | 什么都不动 | `""` |

三条必须守住的边界:

- **异常优先于健康**:同一次探测里 auth 回 200、codex 说停用时按停用算 —— 第 1 步先跑。
- **只有正面证据才恢复**:`probe_failed` / `missing_access_token` / 远端 `unreachable` 都是"没检测成",不算证据,不能把 `invalid` 洗白(一次网络抖动就把刚判定的失效翻回去,比不恢复更糟)。
- **只动 `invalid`**:已经正常的号不会被每次探测重写一遍 —— 这正是"重探一个正常号、状态看着没变"的预期行为,不是 bug。

调用方只判返回值真假:`api/actions.py:78` 要拿到非空 reason 才 `session.add()` 落库并刷 `updated_at`;`services/chatgpt_sync.py:213/232` 则不管返回什么都落库。

`tests/test_chatgpt_account_state.py` 印证的关键行为:401(本地/远端)→ `invalid`;停用文案 → `invalid`;而 **`payment_required` 与 `quota_exhausted` 不标 invalid**(`test_payment_and_quota_do_not_mark_invalid`)—— 没钱/超额是业务状态,不是凭证坏了。

##### 1.8 两套 `AccountStatus` 与 `trial_end_time` 的真相

项目里有**两个同名枚举**,极易踩坑:

| 枚举 | 值 | 用在哪 |
|---|---|---|
| `core/base_platform.py:10` `AccountStatus` | `registered / trial / subscribed / expired / invalid` | **账号表 `AccountModel.status` 的真实取值域**(`core/db.py:32` 默认 `"registered"`) |
| `platforms/chatgpt/constants.py:15` `AccountStatus` | `active / expired / banned / failed` | ChatGPT 插件内部遗留常量,**不写库** |

###### 从探测结论到 `AccountModel.status` 的实际映射

| 探测结论 | 写库 status | 由谁写 |
|---|---|---|
| 注册成功 | `registered` | `platforms/chatgpt/chatgpt_registration_mode_adapter.py:176` 固定 `AccountStatus.REGISTERED` |
| 本地/远端探测出 401 / 403+停用 / 停用文案 | `invalid` | `apply_chatgpt_status_policy()` |
| 重探拿到正面证据(`/backend-api/me` 200 或远端 `usable`) | 还原成失效前那份 status,白名单外回落 `registered` | 同上,`_restore_status()` |
| `trial` 且 `trial_end_time < now` | `expired` | `core/scheduler.py:71-72`,每 3600s 一轮 `check_trial_expiry()` |
| `plan = plus/pro/team/enterprise` | **不自动写 `subscribed`** | 无代码路径 |
| `trial_eligible` / `plus_active` | **不自动写 `trial`** | 只写 `extra["plus_check"]` |

**`trial_end_time` 目前没有任何 ChatGPT 写入方**:全仓只有 3 处引用(`core/base_platform.py:27` 定义、`core/db.py:33` 定义、`core/scheduler.py:71` 读取),没有一处赋值。也就是说 `trial → expired` 这条自动流转在 ChatGPT 上是**死路径**:`probe_plus_trial_status()` 的 `plus_active` 结论里不含到期时间,`subscription_active_until`(来自 `/me`)虽然拿到了,却只落在 `extra["chatgpt_local"].subscription` 里,**没有回填到 `trial_end_time`**。要做到期管理必须自己补这一环。

另有一处 ChatGPT 专属豁免:`core/scheduler.py:110` 的批量有效性检测里,`if acc.platform != "chatgpt"` 才按 `check_valid()` 结果改 status —— ChatGPT 的 status 只允许由 `apply_chatgpt_status_policy` 改,避免 `check_valid()`(内部走 `payment.check_subscription_status`)的粗判据误杀。

---

#### 二、Token 刷新(`platforms/chatgpt/token_refresh.py`)

##### 2.1 两条刷新路径

`TokenRefreshManager`(`platforms/chatgpt/token_refresh.py:92`)持有三个入口,其中两个参与 `refresh_account(account)`(`:481`)的**代价从低到高**编排,第一条成了就不往下走:

| 顺序 | 策略 | 方法 | 端点 | 请求 | 取回字段 |
|---|---|---|---|---|---|
| ① | RT 刷新 | `refresh_by_oauth_token()` `:169` | `POST https://auth.openai.com/oauth/token` | form-urlencoded:`client_id` / `grant_type=refresh_token` / `refresh_token` / `redirect_uri` | `access_token`、`refresh_token`(缺省沿用旧的)、`expires_in`(默认 3600) |
| ② | 协议登录 | `refresh_by_login()` `:361` | 与注册同一条协议链(`flow.run_protocol_login`,纯协议不开浏览器) | 邮箱 + 密码;库里存了 `totp_secret` 就自动算码过 2FA,需要邮箱验证码时靠注入的收件通道 | 产出最全:AT + RT + session_token + id_token |

`client_id` / `redirect_uri` 默认取 `platforms/chatgpt/constants.py` 的 `OAUTH_CLIENT_ID` / `OAUTH_REDIRECT_URI`(`platforms/chatgpt/token_refresh.py:134-136`),可由入参覆盖。HTTP 会话统一 `cffi_requests.Session(impersonate="chrome120", proxy=…)`,超时 30s。

结果统一为 `TokenRefreshResult`(`platforms/chatgpt/token_refresh.py:63`):`success / access_token / refresh_token / session_token / id_token / cookie_header / expires_at / strategy / attempts / error_message`。**`success` 的唯一含义是"拿到了新的 access_token"**,顺带换到的 RT / session_token 一并带回,由调用方决定落库哪些。

> ⚠️ **Session 刷新(`refresh_by_session_token()` `:249`)已退出编排**,保留备用(见 2.2.1)。

##### 2.2 编排与降级

`refresh_account(account)`(`platforms/chatgpt/token_refresh.py:481`)按顺序:

1. 有 `refresh_token` → ① RT 刷新,成功直接返回;
2. `allow_login` 为真且库里有密码 → ② 协议登录兜底;
3. 两条都没材料或都没成 → `success=False`,`error_message` 是各条失败原因的拼接(带 `strategy_label()`)。

`_absorb(partial, usable=…)`(`:501`)把某条策略的产出并进最终结果,只覆盖非空值;`usable=False` 时**不吸收 access_token** —— 失败的那条路手里往往正攥着一个已被上游作废的 AT,收进来会被 `build_extra_patch` 写回库里,把原先还能用的 AT 一起弄坏。RT / session_token / cookies 无论成败都留下。

##### 2.2.1 Session 刷新为什么被移出编排(方法保留备用)

`/api/auth/session` 是 NextAuth 的会话端点,它**只把 session cookie 里已经嵌着的 AT 回放出来**,自己不会去找 OpenAI 换新的。会话里的 AT 早过期或被作废时,它照样回 `200` + 一个**与刷新前一模一样**的旧 AT —— 于是"HTTP 200 且有 `accessToken`"这个判据会造出**看得见的假成功**:上层以为号救回来了,库里存的还是那个废 AT。会话本身失效时更是直接 `403`。

要在它上面做对,只能靠比对旧值 + `/backend-api/me` 实测来"验货",而即使验成功拿到的也只是一张 AT(没有 RT)。既然走一遍协议登录(②)能稳稳拿到 AT + RT + session 全套新凭证,这条既慢又不可信的中间路径就没有留在编排里的价值 —— 它现在是**备用方法**,需要单点调用时可直接用:

| 情形 | 处理 |
|---|---|
| 换回的 AT 与刷新前相同 | 失败,"会话里没有新凭证"(调用方需自己传 `previous_access_token`) |
| 换回的是新 AT | 拿它打 `GET /backend-api/me` 实测:401/403 → 失败(`invalid`);200 → 成功 |
| 探测没跑通(网络异常 / 5xx / 429) | **不否定结果**,按成功算,只记一条"有效性未验证"日志 |
| HTTP != 200 | 失败,`error_message = "Session token 刷新失败: HTTP {code}"` |
| 200 但没有 `accessToken` | 失败,"未找到 accessToken" |
| 抛异常 | 失败,"Session token 刷新异常: {e}" |

服务端可能轮换 session cookie,换了就把新的 `__Secure-next-auth.session-token` 一起带回(`:345`)。

② 协议登录没有"验货"问题:拿的是刚登录出来的全新凭证,失败就明确失败,不装作成功。

##### 2.2.2 `_probe_access_token()`:本模块唯一"实打实打一次"的地方

`_probe_access_token(at)`(`platforms/chatgpt/token_refresh.py:577`)打 `GET /backend-api/me`(带 `CODEX_USER_AGENT`),返回**三态**:

| 判定 | 触发 | 语义 |
|---|---|---|
| `("valid", "")` | 200 | 上游认这个 AT |
| `("invalid", msg)` | 401 / 403 | 上游明确拒绝,这个 AT 已经废了 |
| `("unknown", msg)` | 网络异常、5xx、429 | **没结论**,不能据此否定 AT —— 一次抖动把好号判死比误判成功更糟 |

`validate_token(at)`(`:615`)是它的薄封装(`(bool, Optional[str])`,`unknown` 按有效处理),仓库里**没有生产调用方**(Session 策略退出编排后它连"备用链路的一部分"都算不上),只有测试在用 —— 别拿它当"强制判死"的入口。

##### 2.3 触发时机与收件通道

**没有定时刷新**(RT 快路径也不做 `/me` 复验,保持"一个 POST 就完事"的低成本),全部是**手动 / 批量 / 惰性触发**:

| 触发点 | 位置 |
|---|---|
| 列表页平台动作 `refresh_token`(label"刷新 Token") | `platforms/chatgpt/plugin.py:83` → `:217`,参数 `allow_login` 默认开 |
| 批量后台任务 | `api/tasks.py:1078` `_run_refresh_token()` → `services/chatgpt_token_refresh.refresh_account_data()` + `apply_refresh_result()` |
| HTTP API `POST /chatgpt/{id}/refresh-token` | `api/chatgpt.py:62` ⚠️ **该路由未挂载,不可访问** |

`core/scheduler.py` 的定时循环只做两件事:`check_trial_expiry()`(3600s)与 `check_cpa_credentials()`(间隔来自配置),**不含 token 刷新**。

收件通道是**惰性**的:`services/chatgpt_token_refresh.py:100` 交出去的是工厂 `_resolve_mail_provider` 而不是现成的 provider(`:137`),只在真要走 ② 且需要邮箱验证码那一刻才连一次收件服务;失败也只解析一次、不反复重试,原因记进 `mail_unavailable_reason`(`platforms/chatgpt/token_refresh.py:143` `_resolve_mail_provider()`)。RT 能成的号一次都不会碰邮箱。

##### 2.4 刷新后回写哪些字段

引擎**不认识数据库**,落库全在 `services/chatgpt_token_refresh.py`:`build_extra_patch(result)`(`:146`)整理补丁,`apply_refresh_result(model, result, session=…)`(`:177`)写回账号行。

| 落点 | 值 | 条件 |
|---|---|---|
| `extra["access_token"]` | 新 AT | 非空 |
| `extra["refresh_token"]` | 新 RT | 非空 |
| `extra["session_token"]` / `extra["id_token"]` | 顺带换到的 | 非空 |
| `extra["cookies"]` | `Set-Cookie` 串 | 非空 |
| `extra["chatgpt_has_refresh_token_solution"]` | `True` | 拿到了 RT |
| `extra["chatgpt_token_refresh"]` | 留痕:`ok / strategy / strategy_label / message / expires_at / attempts[] / at` | 总是 |
| `AccountModel.token` | 新 AT | 非空(冗余镜像,列表页读这一列) |
| `AccountModel.updated_at` | `utcnow()` | 总是 |

补丁**只写非空字段**是有意的:任何一条路拿不到 RT 时都不该用空串覆盖掉库里原有的 `refresh_token`,那等于把号弄坏。

**`expires_at` 没有独立的列**,只落在 `extra["chatgpt_token_refresh"]["expires_at"]`。列表页平台动作走的是 `api/actions.py:83-94` 的 `account_extra_patch` 通道(字段并进 `extra`,并单独把 `access_token` 同步到 `AccountModel.token`)。

⚠️ 刷新成功**不会**顺带改 `status`:`invalid` 要等下一次探测拿到正面证据才会被 `_restore_status()` 摘掉(见 1.7)。

---

#### 三、补 RT(refresh_token 回填)

##### 3.1 为什么会有缺 RT 的账号

`platforms/chatgpt/rt_backfill.py:1-5` 明确了两类来源:

1. 早期按 **`access_token_only` 模式**注册的号 —— 流程根本不跑 Codex OAuth 交换,只拿 AT/session;
2. 注册末段 **Codex 交换失败被 `registration_engine._salvage` 抢救回来**的号 —— 凭证可用但没 RT。

两类号本身是好的,只差最后那一次 Codex OAuth 交换。判据很简单:`services/chatgpt_rt_backfill.py:29` 的 `account_missing_rt()` = `extra.refresh_token` / `extra.refreshToken` 都为空。

##### 3.2 两条策略(按代价从低到高,先成先返回)

| 策略 | 常量 | 做什么 | 代价 / 风控痕迹 |
|---|---|---|---|
| ① 会话复用 | `STRATEGY_SESSION="session"` | `flow.from_existing_credentials(session_token, access_token, device_id)` 恢复登录态 → 直接 `flow.oauth_codex_rt_exchange(mail_provider)` | 不发邮件、不碰密码页,十几秒完事;**大部分号在这一步就拿到 RT** |
| ② 协议重登 | `STRATEGY_LOGIN="login"` | 邮箱+密码重跑 `flow.run_protocol_login(provider, email, password)` | 可能撞邮箱 OTP;收不到码就明确报错,**不装作成功** |

策略入口 `RefreshTokenBackfiller.run()`(`platforms/chatgpt/rt_backfill.py:163`)。

###### 环境开关(协议层 env_overrides)

| 开关 | 值 | 出现在 | 为什么 |
|---|---|---|---|
| `OAUTH_CODEX_RT_EXCHANGE` | `1` | 两条路(`_BASE_OVERRIDES` `:38`) | 只为拿 RT |
| `OAUTH_CODEX_RT_ALLOW_RETRY` | `1` | 两条路 | 补 RT 场景一轮里可能要试两次 authorize(第一次被打回 `/log-in`),默认"本轮只试一次"会把第二次吞掉 |
| `OAUTH_CODEX_PROMPT` | `""`(清空) | 仅会话复用(`:48`) | Codex authorize 默认带 `prompt=login`,含义正是"忽略现有会话、重走登录页" —— **不清掉会话复用必然白跑一趟** |
| `OAUTH_REFRESH_ONLY` | `1` | 仅协议重登(`:54`) | 跳过 `get_auth_session` 等只为刷 session 才做的请求 |
| `OAUTH_CODEX_RT_BEFORE_CALLBACK` | `1` | 仅协议重登 | 在 callback 前先换 RT |
| `WEBUI_ALLOW_LOGIN` | `1` | 仅协议重登 | 补 RT 对象必然是已有账号,别让协议层把"这邮箱已注册"当失败 |
| `OTP_TIMEOUT` | `_otp_timeout()` | 两条路 | 依次读配置 `mailbox_otp_timeout_seconds` / `email_otp_timeout_seconds` / `otp_timeout`,默认 **180s** |
| `OPENAI_PHONE_OTP_TIMEOUT` / `OPENAI_PHONE_MAX_ATTEMPTS` / `OPENAI_PHONE_OTP_CODE_RETRIES` / `OPENAI_PHONE_NUMBER` | 来自配置 | 两条路 | Codex 授权链可能被打到 `add-phone`,配了接码就顺手过掉(`platforms/chatgpt/rt_backfill.py:252-275`) |

###### 跳过条件(`_skip_reason()` `platforms/chatgpt/rt_backfill.py:289`)

| 策略 | 跳过条件 | 记录的 message |
|---|---|---|
| session | 库里既无 `session_token` 也无 `access_token` | "库里没有 session_token / access_token,跳过会话复用" |
| login | `allow_login=False` | "已关闭协议重登" |
| login | 没有密码 | "库里没有密码,无法协议重登" |

跳过也会作为一条失败 `BackfillAttempt` 记进 `attempts`,最终拼进 `error_message`(`_compose_error()` `:311`)。

##### 3.3 "末段报错但 RT 已到手"的抢救逻辑

这是本模块最关键的设计(`platforms/chatgpt/rt_backfill.py:192-203`):RT 是在链路**中段**换到的,末段(拉 session、写 cookie 等)再炸不该把已到手的 RT 一起扔掉。所以:

1. `self._active_flow` 记住当前 flow;
2. `except Exception` 后**仍然**调 `_absorb(result, self._active_flow)`;
3. `_absorb()`(`:299`)把 `refresh_token / access_token / session_token / id_token / cookie_header` 逐个搬进结果,**只覆盖非空值** —— 两条策略可能各拿到一半,谁先跑到的不该被后面的空值抹掉;
4. 只要 `result.refresh_token` 非空就判 `success=True`,attempt 的 message 记为 `"末段报错但 RT 已到手：{failure}"`。

`BackfillResult.summary()`(`:119`)把 strategy 翻成中文:`session`→"复用会话"、`login`→"协议重登"。

##### 3.4 邮箱收码怎么处理

- 正常路径:`services/chatgpt_rt_backfill.py:86` 调 `services.chatgpt_otp_mailbox.resolve_otp_mail_provider(email, account_extra, config, proxy, log_fn, task_control, attempt_id)` 解析出真实收件通道,日志打 `[补RT] 收件通道: {display_name} → {email}`。
- 解析不出(临时邮箱早过期、微软号已从池里弹出)且 `allow_login=True` 时,只打一句警告 `"暂时读不到收件箱(…),需要邮箱验证码时会失败"`,**仍然继续试密码登录** —— 因为 OpenAI 不是每次都要邮箱验证码(`platforms/chatgpt/rt_backfill.py:62-67`)。
- 真要码时由 `MailboxUnavailableProvider`(`platforms/chatgpt/rt_backfill.py:61`)的 `wait_for_otp()` 抛出说人话的错误:`"OpenAI 要求邮箱验证码,但读不到 {email} 的收件箱({reason})"` —— 好过让上层看到一个空洞的超时。该 provider 的 `accepts_existing_account = True`,`create_mailbox()` 原样返回账号邮箱。
- 2FA 走 `_account_callback(email)`(`:248`):协议层撞上 `mfa-challenge` 时回调要密钥,返回 `{"password":…, "totp_secret":…}`;`totp_secret` 若存在会预置到 `flow.result.totp_secret`(`:244`)。

##### 3.5 中断:`TaskInterruption` 必须原样抛出

`platforms/chatgpt/rt_backfill.py:183-187` 单独 catch 了 `core.task_runtime.TaskInterruption` 并 `raise`,**不能按"这条策略失败了"往下走**:

> 接着跑协议重登正是按停止的人想躲开的那几十秒。

停止开关一路传递:`backfill_account_data(task_control=…)` → `resolve_otp_mail_provider(task_control=…)` → 邮箱等码循环。等验证码是整个补号流程里最长的一段,**不接开关就停不下来**(`services/chatgpt_rt_backfill.py:76-79`)。

##### 3.6 日志实时外送

`platforms/chatgpt/rt_backfill.py:181` 用 `with mirror_protocol_logs(self._log_fn):` 包住每条策略的执行 —— `platforms/chatgpt/protocol_log_relay.py` 把协议层的 logging 输出镜像到传入的 `log_fn`,前端的实时日志窗口就是靠这一层拿到协议细节的(否则协议层日志只进 logger,前端看不到)。`log_fn` 缺省退化为 `logger.info`。

##### 3.7 库侧胶水:`services/chatgpt_rt_backfill.py`

引擎刻意**不认识数据库**;这个文件负责把 `accounts` 表一行翻译成引擎入参,再把凭证塞回 `extra_json`。

| 函数 | 位置 | 职责 |
|---|---|---|
| `account_refresh_token(model)` | `:24` | 读 `extra.refresh_token` / `refreshToken` |
| `account_missing_rt(model)` | `:29` | 缺 RT 判据 |
| `select_backfill_targets(...)` | `:33` | 转调 `select_chatgpt_accounts`,`only_missing_rt=True`(默认)时把 `account_missing_rt` 作为 `keep` 传下去 |
| `backfill_account_data(...)` | `:59` | **只收纯数据不收 ORM 对象** —— 一个号要跑几十秒网络请求,调用方得以在这期间把数据库连接还回池子 |
| `build_extra_patch(result)` | `:117` | 结果 → `extra_json` 补丁 |
| `apply_backfill_result(model, result, session, commit)` | `:146` | 落库 |

`only_missing_rt` 默认为真的理由(`:45-46`):已经有 RT 的号再跑一遍**纯属给 OpenAI 送风控素材**;只有怀疑旧 RT 失效才关掉。

###### `build_extra_patch()` 回写字段表

| key | 来源 | 条件 |
|---|---|---|
| `refresh_token` / `access_token` / `session_token` / `id_token` | `BackfillResult` 同名字段 | **仅非空**才写 —— 会话复用常常只刷新了 AT,用空串覆盖库里原有的 `session_token` 等于把号弄坏 |
| `cookies` | `result.cookie_header` | 非空 |
| `chatgpt_has_refresh_token_solution` | `True` | 仅拿到 RT 时 —— 号已经有 RT 了,别再被当成 `access_token_only` 方案的产物 |
| `chatgpt_rt_backfill` | `{ok, strategy, message, attempts:[{strategy,ok,message}], at}` | **总是**写,成败都留痕,方便事后查是哪条策略成的、失败卡在哪 |

`apply_backfill_result()` 额外把 `patch["access_token"]` 镜像到 `model.token`,并刷新 `updated_at`;`session`/`commit` 由调用方控制(批量任务通常逐个 `add`、末尾统一 `commit`)。

配置从 `core.config_store.config_store.get_all()` 惰性加载(`:168`)。

##### 3.8 后台任务 + 实时日志窗口 + 中途停止(后端实现)

补 RT 在页面上是"点一下 → 弹一个日志窗口一行行滚 → 想停就停"。后端由三层拼出来:

| 层 | 位置 | 作用 |
|---|---|---|
| 建任务(同步返回) | `api/tasks.py:1057` `POST /tasks/backfill-rt` | 先选号 → 建任务记录 → `background_tasks.add_task(_run_backfill_rt, …)` → **立刻返回 `{task_id, total, missing_ids}`**,不阻塞请求 |
| 跑任务(后台线程) | `api/tasks.py:942` `_run_backfill_rt` → `:782` `_run_account_batch_task` | 通用批量骨架:排队限速、可停可跳、计数收尾 |
| 看日志(SSE) | `api/tasks.py:1237` `GET /tasks/{task_id}/logs/stream` | `text/event-stream`,每 0.5s 轮询 `_task_store.log_state()`,只推 `since` 之后的新行 |

###### 建任务阶段的空集判据(`api/tasks.py:1073-1084`)

选不出号时按原因给不同 400 文案,而不是笼统报错:`missing_ids` 非空 → "所选账号不存在";`only_missing_rt=True` → "所选账号都已经有 RT 了";否则 → "没有匹配的账号"。

###### 默认串行 + 间隔(`BackfillRtTaskRequest` `api/tasks.py:72`)

`concurrency=1`、`delay_seconds=5`。原文注释说明理由:

> 补 RT 会对同一批号连续打 OpenAI 的授权链,并发拉满等于主动送风控素材,宁可慢点。

限速由 `_wait_turn()`(`api/tasks.py:818`)实现:一把 `start_gate` 锁 + `next_start_time` 单调递增,睡眠切成 **0.25s 一片**,每片前后都调 `control.checkpoint()` —— 所以"停止"在等待期间也能立即生效,不会卡满 5 秒。

###### 停止 / 跳过的传导链

| 端点 | 位置 | 落到哪 |
|---|---|---|
| `POST /tasks/{task_id}/stop` | `api/tasks.py:1181` | `RegisterTaskControl.request_stop()` 置 `_stop_requested` |
| `POST /tasks/{task_id}/skip-current` | `api/tasks.py:1170` | `request_skip_current()`,按 `attempt_id` 精确跳当前号 |

`RegisterTaskControl.checkpoint()`(`core/task_runtime.py:110`)是唯一的检查点:置了停止就抛 `StopTaskRequested`,该号被 skip 就抛 `SkipCurrentAttemptRequested`,两者都继承 `TaskInterruption`。这条异常一路穿过 `backfill_account_data` → `RefreshTokenBackfiller.run()`(在 `platforms/chatgpt/rt_backfill.py:183` 被识别并**原样重抛**)→ 被 `_do_one()` 的 `except StopTaskRequested / SkipCurrentAttemptRequested` 捕获(`api/tasks.py:864-868`),转成 `AttemptResult.stopped()` / `.skipped()`。

`control` 还会作为 `task_control` 参数一路交到邮箱等码循环(见 §3.5),因此**"等验证码"这段最长的阻塞也是可中断的**。检查点分布在:每号开始前、`_wait_turn` 的每 0.25s 切片、邮箱轮询循环内。

停止后 `_do_one` 返回 `stopped`,主循环 `stopped=True` 并 `pending.cancel()` 掉线程池里还没开始的 future(`api/tasks.py:907-910`),最终任务状态记为 `"stopped"`,收尾日志 `"补 RT已停止: 成功 X 个, 跳过 Y 个, 失败 Z 个"`。

###### 日志怎么进到窗口

`_run_backfill_rt` 把 `log_fn=lambda msg: _log(task_id, f"  {msg}")` 传给 `backfill_account_data`(`api/tasks.py:960`)。往下:`backfill_account_data(log_fn=…)` → `RefreshTokenBackfiller(log_fn=…)` → `mirror_protocol_logs(log_fn)` 把协议层 logging 也镜像进来。`_log()` 追加到 `_task_store` 的内存 log 列表,SSE 端点每 0.5s 把新行推给前端。任务结束(`done/failed/stopped`)时推一条 `{"done": true, "status": …}` 并断流。

每号还会额外落一条 `TaskLog`(`_save_task_log("chatgpt", email, "success"/"failed", detail={"action": "backfill_rt"})`),供 `GET /tasks/logs` 事后查询。

###### 代理

每号独立取代理:显式 `req.proxy` 优先,否则 `proxy_pool.get_next()`;结果回灌 `proxy_pool.report_success/report_fail`(`api/tasks.py:812-861`)。

---

#### 四、外部同步与上传

##### 4.1 三个外部目标 + 一个远端探测

| 目标 | 上传函数 | 端点 | 认证头 | 载荷形态 | 依赖凭证 |
|---|---|---|---|---|---|
| CPA / CLIProxyAPI | `cpa_upload.upload_to_cpa()` `:195` | `POST {base}/v0/management/auth-files` | `Authorization: Bearer {key}` | **multipart 文件**,字段名 `file`,文件名 `{email}.json` | AT(+RT/id_token) |
| Sub2API | `sub2api_upload.upload_to_sub2api()` `:135` | `POST {base}/api/v1/admin/accounts` | `x-api-key: {key}` | **JSON body** | AT + RT + id_token |
| Team Manager | `cpa_upload.upload_to_team_manager()` `:259` | `POST {base}/api/accounts/import` | `X-API-Key: {key}` | JSON,`import_type="single"` | AT |
| CodexProxy(RT) | `cpa_upload.upload_to_codex_proxy()` `:319` | `POST {base}/api/admin/accounts` | `x-admin-key: {key}` | `{refresh_token, proxy_url:""}` | **必须有 RT** |
| CodexProxy(AT) | `cpa_upload.upload_at_to_codex_proxy()` `:380` | `POST {base}/api/admin/accounts/at` | `x-admin-key: {key}` | `{access_token, proxy_url:""}` | AT |
| 连通性自检 | `cpa_upload.test_cpa_connection()` `:440` | `OPTIONS {base}/v0/management/auth-files` | Bearer | — | — |

所有上传的共性(**有意为之**):`proxies=None`(不走代理,注释明确写"直连")、`verify=False`(**跳过 TLS 校验**)、`timeout=30`、`impersonate="chrome110"`。`api_url` / `api_key` 为空时从 `core.config_store` 惰性读取,配置键见下表。

| 目标 | URL 配置键 | Key 配置键 |
|---|---|---|
| CPA | `cpa_api_url` | `cpa_api_key` |
| CLIProxyAPI | `cliproxyapi_base_url`(回退 `cpa_api_url`) | `cliproxyapi_management_key`(回退 `cpa_api_key`) |
| Sub2API | `sub2api_api_url` | `sub2api_api_key`(+ `sub2api_group_ids`) |
| Team Manager | `team_manager_url` | `team_manager_key` |
| CodexProxy | `codex_proxy_url` | `codex_proxy_key` |

`test_cpa_connection()` 把 **200/204/401/403/405 都视为"连上了"**(401 单独回"连接成功,但 API Token 无效")—— 因为 OPTIONS 在不同网关实现下返回码不一,只要不是连接错误就说明网络通。

##### 4.2 auth-file / token.json 字段表(`generate_token_json()` `platforms/chatgpt/cpa_upload.py:157`)

CPA 收的是一个文件,内容就是这个 dict,`json.dumps(indent=2)`,文件名 `{email}.json`:

| 字段 | 取值 | 说明 |
|---|---|---|
| `type` | 固定 `"codex"` | CPA 侧靠这个字段识别 provider,`cliproxyapi_sync._match_auth_file()` 反查时也只认 `provider/type == "codex"` |
| `email` | `account.email` | 与文件名一致 |
| `expired` | `access_token` 的 JWT `exp` → **东八区**字符串 `%Y-%m-%dT%H:%M:%S+08:00` | 解不出 exp 就是空串 |
| `id_token` | `account.id_token`;**为空且有 AT 时用 `_build_compat_id_token()` 现造一个** | 见 §4.3 |
| `account_id` | AT 的 `payload["https://api.openai.com/auth"].chatgpt_account_id` | 解不出为空 |
| `access_token` | 原始 AT | — |
| `last_refresh` | **上传当刻**的东八区时间戳字符串 | 不是真的刷新时间,是生成文件的时间 |
| `refresh_token` | 原始 RT | 可为空 |

时区**硬编码 `timezone(timedelta(hours=8))`**,不随机器时区变。

##### 4.3 `_build_compat_id_token()`:自造兼容 id_token

`platforms/chatgpt/cpa_upload.py:62`。当账号只有 AT 没有 id_token 时,基于 AT 的 payload **合成一个三段式 JWT**,让 CPA 侧能解析出结构化字段。

- 签名部分是固定串 `_b64url_bytes(b"compat_signature_for_cpa_parsing_only")`,header `{"alg":"RS256","typ":"JWT","kid":"compat"}` —— **签名是假的**,函数 docstring 明确限定"仅用于不校验签名、只解析 payload 的本地兼容场景"。
- 合成的 `https://api.openai.com/auth` 段字段:`chatgpt_account_id / chatgpt_plan_type(缺省 free) / chatgpt_subscription_active_start / _active_until / _last_checked / chatgpt_user_id / completed_platform_onboarding / groups / is_org_owner(缺省 True) / localhost(缺省 True) / organization_id / organizations / project_id / user_id`。
- 缺失的 `organization_id` / `project_id` 用 **sha1 派生**占位(`org-{sha1[:24]}` / `proj_{sha1[:24]}`);`session_id` 缺失时造 `compat_session_{…}`;`at_hash` 用 `sha256(AT)[:22]`;`jti` 用 `compat-{sha1(AT)[:32]}`;`aud` 固定 `["app_EMoamEEZ73f0CkXaXp7hrann"]`;`amr` 固定 `["pwd","otp","mfa","urn:openai:amr:otp_email"]`。
- `name` 由 `_derive_display_name(email)`(`:42`)从邮箱本地部分派生(`.`/`_`/`-` 换空格、首字母大写、最多 3 段),兜底 `"OpenAI User"`。
- `_get_auth_info()`(`:50`)兼容两种 claim 布局:嵌套 `payload["https://api.openai.com/auth"]`,或**扁平** `payload["https://api.openai.com/auth.xxx"]` 逐 key 拆。

##### 4.4 Sub2API 的差异

`_build_sub2api_account_payload()`(`platforms/chatgpt/sub2api_upload.py:93`)先复用 `generate_token_json()` 拿到基础字段,再重新组装成 Sub2API 的账号结构:

| 字段 | 值 |
|---|---|
| `name` | email |
| `platform` / `type` | `"openai"` / `"oauth"` |
| `credentials.access_token` / `.refresh_token` / `.id_token` | 来自 token_data |
| `credentials.expires_in` | 固定 `863999` |
| `credentials.expires_at` | AT 的 `exp`;解不出则 `now + 863999` |
| `credentials.chatgpt_account_id` | AT auth 段,回退 `token_data.account_id` |
| `credentials.chatgpt_user_id` | AT auth 段 |
| `credentials.organization_id` | **从 id_token(不是 AT)解**:`auth.organization_id`,没有就取 `auth.organizations[*].id` 第一个非空(`_extract_organization_id()` `:77`) |
| `credentials.client_id` | `account.client_id`,缺省 `app_EMoamEEZ73f0CkXaXp7hrann` |
| `extra.email` | email |
| `group_ids` | `_parse_group_ids()`,默认 `[2]`;配置 `sub2api_group_ids` 支持逗号串或列表 |
| `concurrency` / `priority` / `auto_pause_on_expired` | `10` / `1` / `True` |

**与 CPA 的核心差异**:

| 维度 | CPA | Sub2API |
|---|---|---|
| 传输 | multipart 文件上传 | JSON body |
| 认证头 | `Authorization` 头(bearer 方案) | `x-api-key`(另带 `Referer: {base}/admin/accounts` 伪装后台来源) |
| 字段深度 | 扁平 8 个字段 | 嵌套 `credentials`,要求 `organization_id` 等 OAuth 结构化字段 |
| 分组/调度 | 无 | `group_ids` / `concurrency` / `priority` / `auto_pause_on_expired` |
| id_token 必要性 | 可自造兼容版 | 同样吃自造版,但 `organization_id` 只能从 id_token 解 —— **自造版里这个值是 sha1 派生的假值**,Sub2API 侧若较真会出问题 |

错误文案提取:CPA 只看 `message`;Sub2API 依次看 `message` / `msg` / `error`(`platforms/chatgpt/sub2api_upload.py:180-185`)。

##### 4.5 `services/chatgpt_sync.py`:同步状态的记账层

这个文件本身**不发上传请求**,它负责"把上传/同步的结果记在账号上",以及编排"补传"流程。

三个同步通道名(`services/chatgpt_sync.py:13-15`):`CPA_SYNC_NAME="cpa"`、`SUB2API_SYNC_NAME="sub2api"`、`CLIPROXY_SYNC_NAME="cliproxyapi"`,全部写在 `extra["sync_statuses"][<name>]` 下。

###### `_record_sync_result()` 写入的状态字段(`:95`)

| 字段 | 语义 |
|---|---|
| `last_attempt_ok` | 最近一次尝试成功与否 |
| `last_message` | 最近一次的文案 |
| `last_attempt_at` | 最近一次尝试时间(UTC ISO) |
| `uploaded` | **粘性布尔**:`已有值 or 本次ok` —— 一旦成功过就永远为真,后续失败不会翻回 false |
| `uploaded_at` | 仅成功时更新 |

`cliproxyapi` 通道不走这套增量逻辑,而是 `record_cliproxy_sync_result()` 用远端探测结果**整体覆盖**(`:125`)。

###### 关键函数

| 函数 | 位置 | 职责 |
|---|---|---|
| `build_chatgpt_sync_account(account)` | `:134` | duck-typing 适配:把 `AccountModel` 抹平成带 `email/user_id/access_token/refresh_token/id_token/session_token/client_id/cookies` 的裸对象,给上传/探测模块用 |
| `get_cpa_sync_state` / `get_sub2api_sync_state` / `get_cliproxy_sync_state` | `:73/:77/:86` | 读某通道状态 |
| `has_cpa_upload_success(x)` | `:81` | `uploaded` 或 `uploaded_at` 任一为真 |
| `update_account_model_*_sync(...)` | `:166/:185/:204` | 落库三件套(set_extra + updated_at + 可选 commit/refresh) |
| `update_account_model_local_probe(...)` | `:223` | 写 `extra["chatgpt_local"]` **并**调 `apply_chatgpt_status_policy(local_probe=…)` |
| `persist_cpa_sync_result` / `persist_sub2api_sync_result` | `:242/:255` | 兜底落库:是 `AccountModel` 就开新 session 写库,否则只改内存 `extra` dict |
| `upload_account_model_to_cpa(...)` | `:268` | 上传 + 记账一步到位 |

只有 `update_account_model_cliproxy_sync` 和 `update_account_model_local_probe` 会触发 `apply_chatgpt_status_policy` —— 也就是说**只有 CLIProxyAPI 同步与本地探测能把号标 `invalid`**,CPA/Sub2API 的普通上传失败不影响 status。

##### 4.6 "补传远端未发现的 auth-file"

入口 `backfill_chatgpt_account_to_cpa()`(`services/chatgpt_sync.py:299`),对**单个账号**执行"先对比、缺了才传、传完复核"。

###### 本地与远端如何对比

远端清单由 `services/cliproxyapi_sync.py:165` `list_auth_files()` 拉取(`GET /v0/management/auth-files`,读 `data["files"]`)。匹配靠 `_match_auth_file()`(`:182`):

1. 只看 `provider`/`type` 为 `"codex"` 的条目;
2. 邮箱小写后比 `item.email == email` **或** `item.name == f"{email}.json"`(即上传时用的文件名);
3. 多条命中时按 `_status_rank()`(`active 0 < refreshing 1 < pending 2 < error 3 < disabled 4 < 其他 9`)再按时间排序,**取最优的一条**。

"远端算不算已有"由 `_remote_auth_missing()`(`services/chatgpt_sync.py:280`)判定:`remote_state == "not_found"` → 缺;或 `sync_result["uploaded"]` 为假 → 缺。

远端**能不能用**则另做一次探测:`_probe_remote_auth()`(`services/cliproxyapi_sync.py:207`)通过 CLIProxyAPI 的 `POST /v0/management/api-call` 代打 `GET https://chatgpt.com/backend-api/wham/usage`,`Authorization` 用占位符 `Bearer $TOKEN$` 让远端自己替换,UA 复用 `CODEX_USER_AGENT`。返回的 `remote_state` 枚举与本地 codex 探测**完全对齐**:`usable / access_token_invalidated / unauthorized / account_deactivated / payment_required / quota_exhausted / probe_failed`,外加 `probe_skipped`(缺 auth_index 或缺 account_id)和 `unreachable`。

###### 完整流程与每一步的退出条件

| 步骤 | 动作 | 提前返回条件 |
|---|---|---|
| 0 | 解析目标:`_resolve_cliproxy_target()` 按 `cliproxyapi_* → cpa_*` 回退 | — |
| 1 | 读缓存:`get_cliproxy_sync_state(account)`;**缓存存在且 `remote_state != "unreachable"` 就直接复用,不再打远端** | — |
| 2 | 无缓存 → `sync_chatgpt_cliproxyapi_status()` 拉远端并落库(`commit=False`) | — |
| 3 | `remote_state == "unreachable"` | `{ok:False, uploaded:False, skipped:False}`,msg"CLIProxyAPI 无法连接" |
| 4 | `not _remote_auth_missing(...)` → 远端已有 | `{ok:True, uploaded:False, **skipped:True**}`,msg"远端已存在 (状态),跳过上传" |
| 5 | 本地探测 `probe_local_chatgpt_status()` 并落库 | `_local_probe_uploadable()` 要求 **`auth.state == "access_token_valid"`**,不满足则 `{ok:False}`,msg 取 `auth.message` |
| 6 | `upload_account_model_to_cpa()` 真上传 | 失败则 `{ok:False}` |
| 7 | **复核**:再拉一次远端同步状态 | 仍 `_remote_auth_missing` → `{ok:False}`,msg"上传后远端仍未发现 auth-file" |
| 8 | 成功 | `{ok:True, uploaded:True, skipped:False}`,msg"补传完成,远端状态=xxx" |

设计要点:**"传了"不等于"成了"** —— 第 7 步的复核是独立的一次远端查询,防止 CPA 收下 201 却没真正落盘。整个过程 `commit=False` 逐步累积,只在每个 return 分支前统一 `session.commit() + refresh()`,保证一次调用一个事务。

返回结构 `{ok, uploaded, skipped, message, results:[{name, ok, msg}]}`,`results` 里的 name 依次是 `"CLIProxyAPI 同步"` / `"本地状态探测"` / `"CLIProxyAPI 上传"` / `"CLIProxyAPI 复核"`,前端按这个列表逐行展示。

###### 批量入口与 `pending_only`

`api/integrations.py:60` `POST /integrations/backfill` 是批量壳子。选号支持 `account_ids` / `platforms` / `status` / `email` / `plus_status`,并有一个 **`pending_only`** 开关(`:85-90`):只保留 `get_cliproxy_sync_state(row).remote_state == "not_found"` 的号 —— 这就是 UI 上"只补远端未发现的"。ChatGPT 号逐个走 `backfill_chatgpt_account_to_cpa`,按 `skipped / ok / else` 累加到 `summary` 的三个计数;单号异常会 `s.rollback()` 后继续下一个,不中断整批。

注意:`platforms` 与 `account_ids` 都为空时**直接返回空 summary**(`:73-74`),不会误伤全库。

##### 4.7 作用范围:"当前筛选" vs "当前所选"

页面上的批量按钮只有这两种范围,由 `services/chatgpt_account_selection.py` **统一解析**(补 RT、绑 2FA 共用):

| 范围 | 入参 | 解析逻辑(`select_chatgpt_accounts()` `:36`) |
|---|---|---|
| 当前所选 | `account_ids=[…]` | 走 id 分支:`WHERE platform='chatgpt' AND id IN (…)`,**按调用方给的顺序**重排结果,查不到的 id 收进 `missing_ids` 一并返回 |
| 当前筛选 | `all_filtered=True` + `email` / `status` / `plus_status` | 走筛选分支:`status` 精确等、`email` 用 `contains`(模糊)、**`plus_status` 在 SQL 之外用 Python 过**(`filter_accounts_by_plus_status`,因为结论藏在 `extra_json` 里) |
| 都没给 | — | 抛 `ValueError("请提供 account_ids，或指定 all_filtered=true")`,API 层转 400 |

**`account_ids` 优先于 `all_filtered`**:给了 id 就完全忽略筛选条件(`if ids: … elif all_filtered:`)。

其他统一约束:

- `normalize_account_ids()`(`:20`):去重、丢掉非整数与 `<=0`,**保留调用方顺序**;
- `keep` 回调是每个任务自己的二次筛选(补 RT 传 `account_missing_rt`,绑 2FA 传"没绑 2FA"),在范围解析**之后**执行;
- `MAX_BATCH_ACCOUNTS = 1000`,超出抛 `ValueError("单次最多处理 1000 个账号")`。

`services/chatgpt_rt_backfill.py:33` 的 `select_backfill_targets()` 只是这个函数的一层包装,把 `only_missing_rt` 翻译成 `keep=account_missing_rt`(为假时 `keep=None`)。`tests/test_backfill_rt_task_api.py` 与 `tests/test_chatgpt_rt_backfill.py` 印证了这套语义:未选未筛 → 400;已有 RT 的号在默认参数下被过滤掉;`missing_ids` 原样回传。

---

#### 五、付费与绑卡

> **本节只描述字段名与流程**,不含任何真实卡号、CVC、密钥或测试卡号。仓库中出现的卡片相关值一律以字段名指代。

##### 5.1 两代支付实现并存

| 代 | 模块 | 做什么 | 状态 |
|---|---|---|---|
| 旧 | `platforms/chatgpt/payment.py` | 只**生成支付链接**,让人拿浏览器去付 | 仍在用(`api/chatgpt.py:97`、plugin 动作 `payment_link`) |
| 新 | `platforms/chatgpt/payment_channels/direct/` | **纯协议全自动**绑卡 + 支付 + 订阅确认,零浏览器 | 在用(plugin 动作 `payment_channel_link` / `payment_channel_pay`、`api/payments.py`) |

##### 5.2 `payment.py` 的职责(旧路径)

| 函数 | 位置 | 请求 | 关键载荷 |
|---|---|---|---|
| `generate_plus_link()` | `:100` | `POST /backend-api/payments/checkout` | `plan_name="chatgptplusplan"`、`promo_campaign.promo_campaign_id="plus-1-month-free"`、`checkout_ui_mode="custom"` |
| `generate_team_link()` | `:146` | 同上 | `plan_name="chatgptteamplan"`、`team_plan_data{workspace_name, price_interval, seat_quantity}`、`promo_campaign_id="team-1-month-free"`、`is_coupon_from_query_param=True`、`cancel_url` |
| `open_url_incognito()` | `:201` | — | Playwright 无痕窗口打开链接并注入 cookie,**后台线程 + 最多等 5 分钟**;playwright 缺失时回退 `_open_url_system_browser()`(Win: chrome `--incognito`/edge `--inprivate`;mac: `open -a`;Linux: chrome/chromium) |
| `check_subscription_status()` | `:230` | `GET /backend-api/me` | 返回 `"team"/"plus"/"free"` 三值;`plan_type` 不含关键字时再看 `orgs.data[*].settings.workspace_plan_type ∈ {team, enterprise}` |

链接拼装:两个函数都从响应取 `checkout_session_id`,拼成 `https://chatgpt.com/checkout/openai_llc/{id}`(`TEAM_CHECKOUT_BASE_URL` 硬编码 `openai_llc`,新渠道则用响应里的 `processor_entity`)。

`_COUNTRY_CURRENCY_MAP`(`:28`)覆盖 SG/US/TR/JP/HK/GB/EU/AU/CA/IN/BR/MX,未命中回退 `USD`。请求头带 `Authorization`、`oai-language: zh-CN`,有 cookies 时补 `cookie` 与从 `oai-did=` 抠出的 `oai-device-id`。

`check_subscription_status()` 还被 `platforms/chatgpt/plugin.py:25-35` 的 `check_valid()` 使用:`status not in ("expired","invalid","banned",None)` 即算有效 —— 但这函数**只会返回 free/plus/team**,所以 `check_valid()` 实际上"只要 `/me` 不抛异常就算有效"。这也是 `core/scheduler.py:110` 要给 ChatGPT 开豁免的原因。

##### 5.3 契约层:`services/payment_channels/contracts.py`

渠道实现与 ORM / API 之间的稳定边界,**三个类型**:

| 类型 | 字段 / 成员 | 说明 |
|---|---|---|
| `PaymentAccount`(frozen dataclass) | `platform` / `account_id` / `email` / `access_token` / `session_token` / `user_id` / `cookies` | **不可变账号快照**。目的:渠道在跑几十秒网络请求时不占数据库连接 |
| `PaymentResult` | `ok` / `channel` / `operation` / `data: dict` / `error`,类方法 `success()` / `failure()`,`as_dict()` | 统一结果封装 |
| `PaymentChannel`(Protocol) | 属性 `name` / `display_name` / `operations` / `option_schema`;方法 `create_link(account, *, options)` / `pay(account, *, options)` | 渠道只需实现这四个属性 + 两个方法 |

配套的 `registry.py`(`PaymentChannelRegistry`,按 `name.lower()` 注册,`get()` 找不到时报出可用列表)与 `service.py`(门面:`account_context(AccountModel) → PaymentAccount`,再转调渠道)。`load_builtin_payment_channels()` **延迟导入** `direct_card_channel`,注释说明理由:避免导入 API 时就初始化卡片库或网络客户端。

`DirectCardChannel` 满足这个 Protocol:`name="direct"`、`display_name="直卡"`、`operations=["link","pay"]`、`option_schema` 声明了前端表单(见下表),因此**前端不需要写死渠道字段**,靠 `GET /payments/channels` 拿 schema 渲染。

| 操作 | option key | 控件 | 默认 |
|---|---|---|---|
| link | `country` | select(PH/US/SG/TR/HK/JP/GB/AU/CA) | `PH` |
| link | `currency` | select(PHP/USD/SGD/TRY/HKD/JPY/GBP/AUD/CAD) | `PHP` |
| link | `link_proxy` | text(advanced) | — |
| pay | `card_id` | card(必填) | — |
| pay | `taxfree_state` | select(DE/NH/MT/OR/AK) | `DE` |
| pay | `pay_proxy` | text(advanced) | — |
| pay | `timeout` | number(advanced) | `30` |

##### 5.4 `direct/` 各文件分工

| 文件 | 行数 | 职责 |
|---|---|---|
| `channel.py` | 412 | **Strategy 实现**:checkout 提链 → 压价 → 页面探活;`pay()` 编排取卡、造地址、调 `bind_and_pay` |
| `bind_card.py` | 942 | 纯协议绑卡 + 支付确认 + 订阅验证(Stripe 侧全部细节) |
| `card_store.py` | 170 | 卡片库(SQLite) |
| `taxfree_store.py` | 128 | 美国免税州账单地址生成 |
| `transport.py` | 123 | HTTP 传输层(session 构造、cookie 管理、SSL 降级重试) |

###### `transport.py`

- `APP_BASE = "https://chatgpt.com"`,`USER_AGENT` 为 Chrome/146,`make_session()` 用 `curl_cffi.Session(impersonate="chrome146")`。
- **`_req()`(`:36`)的 SSL 降级重试**:先按 `verify=True` 发,只有当异常信息里含 `ssl` 时才用 `verify=False` 重试一次;非 SSL 异常直接抛。同时把响应喂给 `core.traffic.record_response()` 做流量留痕。
- cookie 三件套:`parse_cookie_header()`(重名保留最后一个)、`cookie_header(device_id, session_token, cookie_jar)`(补 `oai-did`,有 session_token 时同时写 `__Secure-next-auth.session-token` 与 `next-auth.session-token`)、`merge_session_cookies()`(把 Set-Cookie 回灌 jar,**让后续阶段复用同一上下文**)。
- `chatgpt_session()` 统一装好 `Authorization` / `Origin` / `Referer` / `oai-device-id` / `oai-language` / `Cookie`。
- 文件头注释点明设计意图:业务策略不直接依赖 `curl_cffi`,后续渠道可替换传输实现或注入测试客户端。

###### `card_store.py`

SQLite 表 `cards`(默认落在 `platforms/chatgpt/payment_channels/cards.db`,可用 `MIN_CARDS_DB` 覆盖),线程锁保护。

| 列 | 用途 |
|---|---|
| `id` | 主键,前端 `card_id` 就是这个 |
| `number` / `exp_month` / `exp_year` / `cvc` | 卡数据字段(**本文不列具体值**) |
| `name` / `brand` / `source` / `note` | 持卡人名、品牌、来源标记、备注 |
| `max_uses` / `uses` | 每张卡的使用次数上限与已用数,默认上限 10 |
| `created_at` / `updated_at` | 时间戳 |

| 方法 | 行为 |
|---|---|
| `pickup_card()` `:118` | 取 `uses < max_uses` 的一张,按 `uses ASC, id ASC` 排序(**最少用优先**),取到即 `uses+1` |
| `get_card(id)` / `list_cards()` | 按 id / 全量查 |
| `add_card()` / `delete_card()` / `bulk_delete_cards()` / `reset_uses()` | 增删与用量清零 |

**内置测试卡默认禁用**:仅当环境变量 `MIN_ENABLE_TEST_CARD ∈ {1,true,yes,on}` 时才注入;否则 `_sync_builtin_test_card()` 会**主动删除**历史 `source='builtin_test'` 记录(`:88-94`),文件头注释写明理由"避免 live mode 误用"。测试卡的各字段值也全部走 `MIN_TEST_CARD_*` 环境变量注入,源码里只留占位。

`api/payments.py:92` 的 `_public_card()` 是对外脱敏投影:**只回 `last4`**,不回卡号/CVC/有效期。

###### `taxfree_store.py`

生成美国免税州账单地址,用于把订阅金额里的销售税压掉。

| 项 | 内容 |
|---|---|
| 支持州 | `DE`(首选)/ `NH` / `MT` / `OR` / `AK`,每州 5 组**真实城市+邮编**配对(`TAX_FREE_STATES` `:21`) |
| `generate_address(state, name)` `:62` | 返回 `{street, city, state, zip, phone, name, full}`;street = 随机门牌号 + 街道名模板;phone 随机格式化;非法州回退 `DE` |
| `pick_state(prefer)` `:80` | 显式指定优先;否则加权随机(DE 权重 60,其余各 10) |
| `list_states()` `:93` | 带中文说明的州列表,供前端下拉 |
| `fetch_usaddressgen()` `:105` | **预留**:从 usaddressgen.com 拉真实地址,失败回退本地模板 |

关键约束:`city / state / zip` **必须匹配**(注释强调),否则 Stripe 的地址校验会拒。

##### 5.5 `channel.py` 的提链链路(`create_link`)

`_CheckoutContext`(`:31`)贯穿全程:一个 `device_id`(优先复用 cookie 里的 `oai-did`,否则新 UUID)+ 一个 `cookie_jar`,每一步之后都 `merge_session_cookies()` 回灌 —— 保证 checkout 各阶段在 OpenAI 眼里是**同一个会话上下文**。

| 步 | 函数 | 请求 | 要点 |
|---|---|---|---|
| 0 | `_warmup()` `:60` | `GET https://chatgpt.com/` | 用 document 类 sec-fetch 头预热,**先种下 Cloudflare/OAI cookie** 再建 checkout;失败静默忽略 |
| 1 | `_checkout()` `:116` | `POST /backend-api/payments/checkout` | 载荷 `entry_point="all_plans_pricing_modal"`、`plan_name="chatgptplusplan"`、`billing_details{country,currency}`、`checkout_ui_mode="hosted"`;带 `x-openai-target-path` / `-route` 头。取 `checkout_session_id`(回退 `id`)与 `processor_entity`(缺省 `openai_llc`) |
| 2 | `_update_zero()` `:165` | `POST /backend-api/payments/checkout/update` | **压 0**:改成 `billing_details{country:"US", currency:"USD"}` + `promo_campaign_id="plus-1-month-free"` + `price_interval="month"` + `seat_quantity=1`。函数名 `_update_zero` 与 `bind_card.py` 头注释的"update(TR 压0)"呼应 —— 这一步的目的是把首月金额降到 0 |
| 3 | `_probe_hosted_page()` `:207` | `GET /checkout/{entity}/{cs}` | 探活:`status < 500` 且响应体**不含 `"Application Error"`** 才算 ok。防止返回一个打开就报错的死链 |
| 4 | 拼链 | — | `https://chatgpt.com/checkout/{processor_entity}/{checkout_session_id}` |

成功时 `PaymentResult.success(data={link, checkout_session_id, processor_entity, billing_country, hosted_status})`。

代理解析 `_proxy(options, kind)`(`:80`)是**分操作**的四级回退:`options["{kind}_proxy"]` → 配置 `payment_{kind}_proxy` → 配置 `payment_proxy`(老装机的共享设置)→ `proxy_pool.get_next("US")`。提链与支付可以走不同出口。

##### 5.6 `channel.pay()` 的编排

1. 校验 `access_token`;
2. 解析 `link_proxy` / `pay_proxy`(两者独立);
3. **取卡**:`options["card"]` 直接给了就用;否则 `card_store.get_card(card_id)`;都没有就 `card_store.pickup_card()`;取不到 → "卡片库无可用卡";
4. **拿 account_id**:`options["chatgpt_account_id"]` → 从 AT 的 JWT 解 `auth.chatgpt_account_id`(`_chatgpt_account_id()` `:105`)→ `account.account_id`;都没有 → "缺少 chatgpt_account_id";
5. **先提链**:内部复用 `self.create_link()`,失败原样返回;
6. **造账单地址**:`generate_address(taxfree_state, card.name)` → 组装 `billing{name, line1, city, state, postal_code, country:"US"}`(**country 强制 US**,因为免税州逻辑只对美国成立);
7. 调 `bind_and_pay(...)`,`fast_verify=True`;
8. 成功时返回 `{checkout, subscription_plan, card_last4, result}` —— **只回 last4**。

##### 5.7 `bind_card.py` 绑卡流程分步(942 行)

模块头注释点明核心突破:**Stripe 卡 tokenization 纯 HTTP 的关键不是 `POST /v1/payment_methods`**(该端点对 publishable key 直连返回 400 `integration surface unsupported`),**而是把卡数据内联到 SetupIntent confirm 调用里** —— 这正是 Stripe.js `confirmCardSetup` 内部做的事。全程零浏览器、零 CDP。

###### 常量与辅助

| 项 | 位置 | 说明 |
|---|---|---|
| `STRIPE_BASE` / `STRIPE_VERSION` / `STRIPE_BETAS` | `:40-51` | Stripe API 版本与 beta 头(`checkout_server_update_beta` / `checkout_manual_approval_preview`) |
| `KNOWN_PUBLISHABLE_KEYS` | `:62` | **双商户分片**的 Stripe publishable key(公开配置),按 client_secret 里的片段选(`_pick_pk()` `:74`、`_publishable_key_for_setup()` `:168`) |
| `STRIPE_HCAPTCHA_SITE_KEY` / `_URL` | `:54-58` | Stripe invisible hCaptcha,**可选**;有 token 时作为 `radar_options[hcaptcha_token]` 带上 |
| `_walk` / `_find_key` / `_find_identifier` / `_find_client_secret` / `_setup_intent_id` | `:102-167` | **递归**在嵌套响应里搜字段 / 按前缀搜标识符(`seti_` / `pm_` / `cus_` / `ctoken_`)—— 因为 OpenAI 与 Stripe 的响应结构会变,不硬编码路径 |
| `_card_fields()` | `:178` | 规范化:卡号与 CVC 去非数字、月份补 2 位、2 位年份补 `20` 前缀;字段长度不合就抛 `ValueError`(**报错只带长度,不带值**) |
| `_billing_fields()` | `:191` | 规范化账单字段,兼容 `address`→`line1`、`zip`→`postal_code` |
| `_mask()` | `:95` | 日志脱敏:头 8 尾 4,中间省略;`seti_` / `pm_` / `ctoken_` 打日志前都过这层 |
| `_stripe_headers()` | `:220` | **关键**:`Origin` / `Referer` 伪装成 `https://js.stripe.com`,`Authorization: Bearer {pk}` |
| `_require_setup_succeeded()` | `:264` | 统一要求 `status == "succeeded"`,否则抛出带阶段名的 `RuntimeError` |

###### 九步流程

| 步 | 函数 | 请求 | 产出 / 判据 |
|---|---|---|---|
| 1 | `bind_card` 内联 | `GET /backend-api/payments/checkout/{processor}/{cs}` | 建立 checkout context;>=400 即 `step="checkout_context"` 失败 |
| 2 | `_post_json`(等价 `create_setup_intent()` `:275`) | `POST /backend-api/payments/payment_method`,body `{account_id}` | 拿 `client_secret` + `setup_intent_id`(`seti_…`)+ 选定 `pk`;缺任一 → `step="setup_intent"` |
| 3 | `setup_confirm_inline()` `:316` | `POST {stripe}/v1/setup_intents/{seti}/confirm`(form) | **核心**。见下方载荷表。要求响应 `status == "succeeded"`,并从响应递归搜出 `pm_id` |
| 4 | `list_payment_methods()` `:429` | `GET /backend-api/payments/payment_methods?account_id=` | 列卡验证,提炼 `{id, brand, last4, exp, default}`;另可 `list_stripe_payment_methods()` `:470` 查 Stripe 侧(需 `cus_` customer) |
| 5 | `create_confirmation_token()` `:490` | `POST {stripe}/v1/confirmation_tokens` | 用 `pm_id` 换 `ctoken`;`client_context{currency, mode:"subscription", payment_method_types:[card, link]}`,`setup_future_usage="off_session"`,Stripe-Version 用 `STRIPE_BETAS` |
| 6 | `checkout_confirm()` `:533` | `POST /backend-api/payments/checkout/confirm`(空 body) | 拿 **final** SetupIntent 的 `client_secret` + id |
| 7 | `final_setup_confirm()` `:556` | `POST {stripe}/v1/setup_intents/{final}/confirm` | 用 `confirmation_token` 确认;`return_url` = `/checkout/verify?stripe_session_id=…&processor_entity=…&plan_type=plus`;要求 `status=="succeeded"` |
| 8 | 回访 checkout context(`fast_verify=False` 时另访 `/checkout/verify`、`/payments/success.data`、`/api/auth/session?reason=checkout_success`) | GET | 模拟浏览器付款后的回跳序列 |
| 9 | `verify_subscription()` `:590` | `GET /backend-api/subscriptions?account_id=` | **轮询 3 次、间隔 0.35s**,要求 `plan_type == "plus"`;否则 `error="plus not activated"` |

###### 第 3 步的载荷要点(`setup_confirm_inline`,`:340-425`)

| 分组 | 字段(仅名称) |
|---|---|
| 基础 | `set_as_default_payment_method` / `expected_payment_method_type=card` / `use_stripe_sdk` / `key`(pk)/ `_stripe_version` / `client_secret` |
| 内联卡(核心) | `payment_method_data[type]=card`、`payment_method_data[card][number]` / `[cvc]` / `[exp_month]` / `[exp_year]`、`payment_method_data[billing_details][name]`、`payment_method_data[allow_redisplay]` |
| 账单地址 | `payment_method_data[billing_details][address][line1/line2/city/state/postal_code/country]`(country 转大写)、`[email]`、`[phone]` —— **仅非空才发** |
| 反欺诈(伪造 Stripe.js 在浏览器里生成的值) | `payment_method_data[guid]` / `[muid]` / `[sid]`(各由两个 UUID 拼接)、`[pasted_fields]`(基础三项,有邮编时追加 `zip`)、`[payment_user_agent]`(伪装 `stripe.js/...; card-element`)、`[referrer]`、`[time_on_page]`(**随机 300000–750000 毫秒**,假装用户在页面上停留了几分钟) |
| 归因 | `client_attribution_metadata[client_session_id / merchant_integration_source=elements / _subtype=card-element / _version=2017]`,以及 `payment_method_data[client_attribution_metadata][…]` 同名一套 |
| 可选 | `radar_options[hcaptcha_token]`、`elements_session_id`、`wallet_config_id`(后两者从 `init_payload` 递归搜) |

###### 402 的一次重试

`platforms/chatgpt/payment_channels/direct/bind_card.py:712-723`:第 3 步返回 **402 时重试一次**(整个 `setup_confirm_inline` 重发),仍失败才判 `step="setup_confirm"`。这是唯一的重试点,其余步骤均一次定生死。

###### 流量隔离:`core.traffic` 的 BLOCK_PAY

`bind_card()` 与 `bind_and_pay()` 都在开头 `_traffic.set_block(_traffic.BLOCK_PAY)`、`finally` 里 `_traffic.clear_block()`(`:654`、`:809`、`:929`)。`bind_and_pay` 的 Phase 2 还要**重新 set 一次**,因为内部调的 `bind_card` 在自己的 finally 里已经清掉了(`:830` 有注释说明)。作用是把支付阶段的请求打上标记,和注册流量分开留痕。

###### 返回结构

- `bind_card()` → `{ok, step, setup_intent_id, pk, pm_id, setup_status, cards, customer, stripe_pm_status, stripe_js_id}`;失败时 `step` 精确指出卡在哪一步(`checkout_context / setup_intent / setup_confirm / exception`)。
- `bind_and_pay()` → 额外带 `bind`(整个绑卡子结果)、`ctoken`、`final_setup_intent_id`、`final_status`、`subscription`、`subscription_plan`、`card_last4`;失败 `step ∈ {bind, confirmation_token, checkout_confirm, final_confirm, verify, exception}`。

##### 5.8 Plus 试用 / 订阅链路对照

| 链路 | 促销 id | 谁用 | 结果 |
|---|---|---|---|
| 探测"能不能领" | `plus-1-month-free`(只读,出现在 `eligible_promo_campaigns.plus.id`) | `status_probe.probe_plus_trial_status()` | `trial_eligible` / `plus_active` / `free` / … |
| 旧提链 | `plus-1-month-free`(写进 checkout 载荷) | `payment.generate_plus_link()` | 一条 URL,人工去付 |
| 新提链 | `plus-1-month-free`(在 `_update_zero` 压价阶段带上) | `channel.create_link()` | hosted checkout 链接 + 页面探活 |
| 新支付 | 同上 | `channel.pay()` → `bind_and_pay()` | 直到 `GET /backend-api/subscriptions` 的 `plan_type == "plus"` 才算成功 |
| Team | `team-1-month-free` | `payment.generate_team_link()` | 只有提链,没有自动付 |

**这三处都把促销 id 写成字面量**,一旦 OpenAI 改活动 id:探测会误报"不能领",提链会失去免费首月。

##### 5.9 `cashier_url` 的来源与用途

`cashier_url` 与上面的 checkout 链接**不是同一个东西**,它是注册阶段的产物:

| 环节 | 位置 | 行为 |
|---|---|---|
| 产生 | 注册流程写进 `account.extra["cashier_url"]` | 平台注册时若拿到升级 / 收银台链接就塞进 extra |
| 落库 | `core/db.py:160` / `:175` | `save_account()` 把 `extra["cashier_url"]` **提升为独立列** `AccountModel.cashier_url`(`core/db.py:34`),列表页可直接排序 / 展示 |
| 任务日志 | `api/tasks.py:663-666` | 注册成功后若有 `cashier_url`,打一行 `[升级链接] …` 并 `_task_store.add_cashier_url()` 收进任务快照的 `cashier_urls` 数组 |
| 前端 | `frontend/src/pages/RegisterTaskPage.tsx:239` | 任务完成时把 `cashier_urls` **逐个 `window.open` 自动弹出** |
| 手工维护 | `api/accounts.py:28` / `:302`、`frontend/src/pages/Accounts.tsx:1761` | 支持创建 / 编辑时直接填,批量导入格式 `email password [cashier_url]` |
| 导出 | `services/account_export.py:91` | 导出字段中文名"试用链接" |
| 动作回显 | `api/actions.py:194` | 平台动作返回体里依次找 `message` / `detail` / `url` / `checkout_url` / `cashier_url` 作为展示文案 |

用途一句话:**"这个号去哪儿付钱升级"的持久化入口**,与实时提链(每次调用生成新的 checkout session)互补 —— 前者可长期保存在账号行上,后者有时效。

##### 5.10 支付相关 API(`api/payments.py`)

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/payments/channels` | 列渠道 + `option_schema`(前端据此渲染表单) |
| GET | `/payments/channels/{channel}/cards` | 列卡(经 `_public_card` 脱敏,**只回 last4**) |
| POST | `/payments/channels/{channel}/cards` | 加卡 |
| DELETE | `/payments/channels/{channel}/cards/{card_id}` | 删卡 |
| POST | `/payments/channels/{channel}/cards/reset-uses` | 用量清零 |
| POST | `/payments/{account_id}/link` | 单号提链(同步) |
| POST | `/payments/{account_id}/pay` | 单号支付(同步) |
| POST | `/payments/jobs` | **批量**提链 / 支付后台任务,复用 `_run_account_batch_task` 那套可停可跳 + SSE 日志 |

---

#### 六、`api/chatgpt.py` 接口清单

> ⚠️ **`api/chatgpt.py` 未挂载到应用**:`main.py` 既未 import 也未 `include_router` 该模块(15 个 `include_router` 里没有它),因此本节列出的 6 个端点**实际不可访问,是死代码**。同名能力的可用入口是 `api/actions.py` 的通用平台动作通道(`platforms/chatgpt/plugin.py` 声明的 `refresh_token` / `payment_link` 等 action)。下文保留其实现细节,是因为这些代码路径仍被 plugin 动作复用,阅读时不要按 HTTP 接口去调。

前置件:`_get_account()`(`:24`)要求 `platform == "chatgpt"`,否则 404;`_to_codex_account()`(`:31`)把 `AccountModel` duck-type 成协议层要的裸对象(`email` / `access_token` / `refresh_token` / `id_token` / `session_token` / `client_id` / `cookies` / `user_id`),`client_id` 缺省 `app_EMoamEEZ73f0CkXaXp7hrann`;`_persist_local_probe()`(`:50`)写 `extra["chatgpt_local"]` + 跑状态策略 + 刷 `updated_at`。

| 方法 | 路径 | 作用 | 对应后端能力 |
|---|---|---|---|
| POST | `/chatgpt/{account_id}/refresh-token` | 刷新 Token,回写 `extra.access_token` / `extra.refresh_token` / `token` 列;**响应只回 AT 前 40 字符 + `...`** | `token_refresh.TokenRefreshManager.refresh_account()` |
| POST | `/chatgpt/{account_id}/payment-link` | 生成 Plus / Team 支付链接(body:`plan` / `country` / `proxy` / `workspace_name` / `seat_quantity` / `price_interval`) | `payment.generate_plus_link()` / `generate_team_link()` |
| GET | `/chatgpt/{account_id}/subscription` | 查订阅:跑本地探测、落库、回 `{email, subscription, probe}` | `status_probe.probe_local_chatgpt_status()` + `apply_chatgpt_status_policy()` |
| POST | `/chatgpt/{account_id}/probe-local` | 显式本地状态探测并落库,回完整 probe | 同上 |
| POST | `/chatgpt/{account_id}/upload-cpa` | 上传到 CPA(body:`api_url` / `api_key`) | `cpa_upload.generate_token_json()` + `upload_to_cpa()` |
| POST | `/chatgpt/{account_id}/upload-sub2api` | 上传到 Sub2API | `sub2api_upload.upload_to_sub2api()` |

**几处需要注意的不一致**:

- 文件顶部定义了 `COUNTRIES` 列表(`:13`)与 `UploadRequest` 模型(`:16`,含 `account_ids` / `cpa_*` / `team_manager_*`)但**全文未使用**,是残留。
- `/subscription` 名字像是只读查询,实际执行的是**完整本地探测并会改账号 status**(可能把号标成 `invalid`)—— 一个有副作用的 GET。
- 这 6 个端点都是**单号同步**;批量能力在别处:`api/tasks.py`(补 RT)、`api/integrations.py`(补传 auth-file)、`api/payments.py`(批量支付)、`api/actions.py`(通用平台动作)。

##### 6.1 平台动作清单(`platforms/chatgpt/plugin.py:80-135`,列表页菜单)

| action_id | 中文标签 | 落到哪 |
|---|---|---|
| `probe_local_status` | 探测本地状态 | `probe_local_chatgpt_status`,回 `account_extra_patch{chatgpt_local}` |
| `check_plus_trial` | 检测 Plus 试用 | `probe_plus_trial_status`;**结论不确定时 patch 为空**(不污染"未检测") |
| `sync_cliproxyapi_status` | 同步 CLIProxyAPI 状态 | `cliproxyapi_sync.sync_chatgpt_cliproxyapi_status`,ok 判据 = `uploaded` 且 `remote_state ∉ {unreachable, not_found}` |
| `refresh_token` | 刷新 Token | `TokenRefreshManager` |
| `backfill_refresh_token` | 补 RT | `chatgpt_rt_backfill`,回 `build_extra_patch(result)` |
| `payment_link` | (旧)支付链接 | `generate_plus_link` / `generate_team_link` |
| `payment_channel_link` / `payment_channel_pay` | 渠道提链 / 渠道支付 | `payment_channels.service.create_link_for_context` / `pay_for_context` |
| `upload_cpa` / `upload_sub2api` / `upload_tm` / `upload_codex_proxy` | 四个上传目标 | 对应上传函数;`upload_codex_proxy` 按 `upload_type ∈ {rt, at}` 二选一走 `/api/admin/accounts` 或 `/…/at` |

---

#### 七、脆弱点

按"外部依赖"与"实现取舍"两类客观列出。

##### 7.1 强依赖 OpenAI 私有接口与响应结构

| 依赖 | 风险 | 涉及位置 |
|---|---|---|
| `/backend-api/me`、`/backend-api/wham/usage`、`/backend-api/accounts/check/v4-2023-04-27` 均为**未公开接口**,后者连版本日期都写在 URL 里 | 改路径 / 改版本即全盘失效 | `platforms/chatgpt/status_probe.py:18-20` |
| 判定逻辑**硬绑响应字段名**:`plan_type` / `orgs.data[*].settings.workspace_plan_type` / `accounts.default.entitlement.has_active_subscription` / `eligible_promo_campaigns.plus.id` / `account.is_deactivated` | 字段改名或层级调整会让状态**静默退化**为 `unknown` / `free` —— 不是报错,是错的结论 | `platforms/chatgpt/status_probe.py:311-341`、`:456-481` |
| 促销 id `plus-1-month-free` / `team-1-month-free` 三处字面量 | 活动更名 → 探测误报"不能领"、提链丢失免费首月 | `platforms/chatgpt/status_probe.py:30`、`platforms/chatgpt/payment.py:125` / `:179`、`platforms/chatgpt/payment_channels/direct/channel.py:187` |
| 封号识别靠**英文措辞子串匹配**(`deactivated` / `suspended` / `banned` / `violat` / `potential abuse` / `terminated`) | 文案改写或本地化即漏判;`violat` 这类宽泛词也可能把正常错误误判成封号 | `services/chatgpt_account_state.py:31-41` |
| UA 与指纹写死具体版本(`codex_cli_rs/0.116.0`、`Chrome/145`、`Chrome/146`、`impersonate="chrome110/120/146"`) | 版本停留过久本身成为识别特征 | `platforms/chatgpt/status_probe.py:21-26`、`platforms/chatgpt/payment_channels/direct/transport.py:20-29`、多处 `impersonate` |
| `refresh_token` 走 `auth.openai.com/oauth/token` 且 `client_id` 固定 | 该 client 被吊销则全库账号都刷不动 | `platforms/chatgpt/token_refresh.py:47-59` |
| Codex `wham/usage` 需要 `Chatgpt-Account-Id`,而该值靠**自行解 JWT**(不验签)取得 | claim 布局变化 → 探测降级为 `probe_failed` | `platforms/chatgpt/status_probe.py:81-103` |

##### 7.2 外部服务(CPA / Sub2API / CodexProxy / Team Manager)可用性

| 问题 | 说明 |
|---|---|
| **`verify=False` 全线关闭 TLS 校验** | 五个上传函数 + `test_cpa_connection` 全部 `verify=False`;`transport._req` 也在 SSL 异常时自动降级。对外部管理后台而言这等于放弃中间人防护,而载荷里正是明文 AT / RT |
| **不走代理是硬编码的** | 所有上传 `proxies=None`,无法为外部服务单独配出口 |
| **`uploaded` 是粘性布尔** | `_record_sync_result()` 里 `uploaded = 已有值 or 本次ok`,一次成功后永远为真。远端后来删了 auth-file,本地仍显示"已上传",只能靠 `remote_state` 纠正 |
| **`_remote_auth_missing` 的两个判据不完全等价** | `remote_state == "not_found"` 或 `uploaded` 为假都算缺;于是其他 state 若恰好 `uploaded=False` 也会触发重传 |
| **缓存复用可能基于过期信息** | `backfill_chatgpt_account_to_cpa` 只要缓存的 `remote_state != "unreachable"` 就跳过远端查询(`services/chatgpt_sync.py:314`),远端在这之后被删掉时不会被发现 |
| **`_build_compat_id_token` 造的是假 JWT** | 签名为固定字符串、`organization_id` / `project_id` 由 sha1 派生。只对"不验签只解 payload"的下游有效;而 Sub2API 的 `organization_id` 恰好只能从 id_token 解,自造版给出的是**派生假值** |
| **Sub2API 的若干值是魔法常量** | `expires_in=863999` / `concurrency=10` / `priority=1` / `group_ids=[2]`,与远端实际配额无关 |
| **错误信息只做粗提取** | 非 200 一律 `"上传失败: HTTP {code}"` + `message` 字段,拿不到结构化错误码,无法区分"重复账号"与"鉴权失败" |
| **无重试、无幂等键** | 上传函数一次定生死;重复上传同一 `{email}.json` 的去重完全依赖远端 |

##### 7.3 Token 与补 RT

| 问题 | 说明 |
|---|---|
| `refresh_account()` 的失效判定是"非 200 即失败" | **不区分 `invalid_grant`(RT 真的废了)与网络抖动**,无法据此决定"该重试"还是"该重新登录" |
| `expires_at` 算出来但**没落库** | 且 session 路径产出 aware datetime、OAuth 路径产出 naive `utcnow()`,两者混用会抛 `TypeError`(`platforms/chatgpt/token_refresh.py:118` vs `:193`) |
| **没有定时刷新**,全靠人点 | AT 过期后所有依赖它的能力(探测、上传、提链、支付)一起失效 |
| `platforms/chatgpt/token_refresh.py:281` / `:318` 是死代码 | 引用已被注释掉的 `get_db` / `crud` / `Account`,**一调用就 NameError**;新人容易照着调 |
| 补 RT 的"协议重登"需要**明文密码留在库里** | `_skip_reason` 直接说"库里没有密码,无法协议重登" |
| 补 RT 会连续打 OpenAI 授权链 | 已用"默认串行 + 5s 间隔 + `only_missing_rt`"三道闸缓解,但批量规模上去仍是风控暴露面 |
| `trial → expired` 的自动流转是**死路径** | `trial_end_time` 全仓无写入方;`subscription_active_until` 拿到了却没回填 |
| ChatGPT 的 status 只能被标成 `invalid`,**永不平反** | 号恢复后需要人工改状态 |

##### 7.4 支付链路的合规与风险(客观陈述)

| 项 | 客观事实 |
|---|---|
| **绕过 Stripe.js 的官方集成路径** | 卡数据以 `payment_method_data[card][…]` 内联到 SetupIntent confirm。模块注释自陈这是绕开 `POST /v1/payment_methods` 对 publishable key 的限制。这条路径不是 Stripe 面向服务端的受支持用法,随时可能被封 |
| **伪造 Stripe.js 的浏览器指纹** | `guid` / `muid` / `sid` 用 UUID 拼造、`payment_user_agent` 伪装成 `stripe.js/...`、`time_on_page` 随机编造 300–750 秒、`Origin` / `Referer` 伪装 `js.stripe.com`。这些字段本是 Stripe Radar 的反欺诈输入,伪造它们会直接影响风险评分,也明确属于规避风控 |
| **免税州地址是编造的** | `taxfree_store` 用真实城市 + 邮编配随机生成的街道 / 电话,`billing.country` 强制 `US`,目的是压掉销售税。这属于税务相关信息层面的失实 |
| **`_update_zero` 的"压 0"** | 先按 PH/PHP 建 checkout,再改成 US/USD + 免费促销 —— 属于对定价 / 地区策略的规避 |
| **卡片库明文存储** | `cards` 表以明文存卡号 / CVC / 有效期,SQLite 文件无加密。仅在 API 出口做 last4 脱敏,库文件本身一旦泄露即全量泄露。同时 `max_uses` 默认 10,单卡会反复用于不同账号 |
| **hCaptcha 靠外部传入 token** | `hcaptcha_token` 由调用方给,仓库不含求解逻辑;缺 token 时该字段不发,能否通过取决于 Stripe 当时是否强制 |
| **`verify_subscription` 只轮 3 次 × 0.35s** | 约 1 秒内就要求订阅生效。Stripe → OpenAI 的异步落账稍慢就会误报 `plus not activated`,而钱可能已经扣了 —— **失败结果与实际扣款状态不保证一致** |
| **402 只重试一次** | 银行侧软拒时重发同一张卡,可能触发发卡行风控 |
| **测试卡默认禁用是好设计** | `MIN_ENABLE_TEST_CARD` 未开时会主动删除内置测试卡记录,避免 live mode 误用;卡值本身也走环境变量注入,源码只留占位 |

##### 7.5 其他实现层面的脆弱点

- **两个同名 `AccountStatus` 枚举**(`core/base_platform.py:10` 与 `platforms/chatgpt/constants.py:15`)取值域完全不同,import 错了不会报错,只会写出脏 status。
- **`api/chatgpt.py:13` / `:16` 的 `COUNTRIES` 与 `UploadRequest` 是死代码**,容易误以为是可用接口。
- **`GET /chatgpt/{id}/subscription` 有写副作用**(可能把号标 `invalid`),与 HTTP 语义不符。
- **`_probe_hosted_page` 靠英文字面量 `"Application Error"` 判页面异常**,OpenAI 改错误页文案即失效。
- **`payment.open_url_incognito` 的窗口硬等 5 分钟**(`page.wait_for_timeout(300_000)`),线程在此期间不可回收。
- **`status_probe` 与 `token_refresh.validate_token` 功能重复**,判据粗细不一(后者不带 Codex UA、不读 `X-Error-Json`),同一个号可能得到两套结论。
- **`cliproxyapi_sync._probe_remote_auth` 把 AT 交给远端替换**(`Bearer $TOKEN$` 占位),等于信任 CLIProxyAPI 持有并使用凭证。
- **`plugin.check_valid()` 的判据实际恒真**:它比对 `check_subscription_status()` 的返回值与 `("expired","invalid","banned",None)`,而后者只会返回 `free`/`plus`/`team`。`core/scheduler.py:110` 因此对 ChatGPT 做了豁免,否则会用这个恒真判据覆盖状态策略的结论。

---

## 九、邮箱池子系统

> 面向新接手项目的开发者。涵盖临时邮箱 provider、邮件导入、OTP 收码三块。

#### 一、子系统定位与总体架构

注册流程对邮箱有两个硬需求：**领一个可收信地址**、**轮询收 OTP 验证码**。整个子系统围绕这两个动作展开，分三层：

| 层 | 位置 | 职责 |
|---|---|---|
| 抽象层 | `core/base_mailbox.py:23` `BaseMailbox` | 定义 `get_email` / `get_current_ids` / `wait_for_code` 三个抽象方法，外加轮询骨架、验证码正则、任务 checkpoint |
| Provider 层 | `core/base_mailbox.py`（4596 行单文件，15 个 provider 类） | 每家临时邮箱/收信服务一个类；`create_mailbox()` 工厂按配置字符串选型 |
| 号池/导入层 | `services/mail_imports/`、`core/applemail_pool.py`、`core/db.py:80` `OutlookAccountModel` | 「自带邮箱」模式：先批量导入账号进库/文件，运行时从池里领号 |

调用入口：

- 注册任务：`api/tasks.py:500` `_build_mailbox()` → `create_mailbox(provider=_base_extra.get("mail_provider","luckmail"), extra, proxy)`，实例注入 `PlatformCls(config, mailbox=...)`。
- ChatGPT 协议层：`platforms/chatgpt/protocol/mailbox_adapter.py` 把 `BaseMailbox` 适配成协议层的 `MailProvider`。
- 补 RT / 绑 2FA（已知地址反查通道）：`services/chatgpt_otp_mailbox.py:149` `resolve_otp_mail_provider()`。

---

#### 二、Provider 清单总表

##### 2.1 工厂可选的 provider（`core/base_mailbox.py:215` `create_mailbox`）

| 标识 | 实现类 file:line | 领址方式 | 收信方式 | 必填配置项 | 已知限制 |
|---|---|---|---|---|---|
| `luckmail`（默认值） | `LuckMailMailbox` `core/base_mailbox.py:2785` | API：ChatGPT 走「购买邮箱」`purchase_emails`，其他平台走「创建订单」`order/create` | 轮询 LuckMail API：购买模式 `GET /email/token/{token}/mails`，订单模式 `GET /order/{no}/code` | `luckmail_base_url`、`luckmail_api_key`；`luckmail_project_code` 由 `api/tasks.py:349` 按平台注入（chatgpt→`openai`） | 付费、按量扣余额；订单超时会被 `_cancel_order_silently` 自动取消（`:2853`）；两套模式由 `_use_purchase_mode()` `:2815` 隐式切换（token 前缀 `tok_` / project_code=="openai"） |
| `tempmail_lol` | `TempMailLolMailbox` `core/base_mailbox.py:843` | API `POST /v2/inbox/create` 自动生成 | 轮询 `GET /v2/inbox?token=` | 无（零配置兜底选项） | 公共免费服务，域名易被平台拉黑；无重试/错误上抛，异常全吞 |
| `skymail` | `SkyMailMailbox` `core/base_mailbox.py:937` | 本地随机前缀 + 配置域名，再调 `POST /api/public/addUser` 注册地址 | 轮询 `POST /api/public/emailList` | `skymail_api_base`、`skymail_token`、`skymail_domain`（三者缺一报错 `:953`） | 依赖自建服务；无邮件时间窗过滤（不支持 `otp_sent_at`） |
| `cloudmail` | `CloudMailMailbox` `core/base_mailbox.py:1086` | 纯本地拼装：随机前缀 + `_domain_candidates()` 随机域名 + 可选 subdomain（`:1279`） | 轮询 `POST /api/public/emailList`，凭 `genToken` 换来的 token | `cloudmail_api_base`、`cloudmail_admin_password`（`:1177`）；域名可从 api_base 推断 | token 类级缓存 1 小时（`:1223`）；`_seen_ids` 是**进程级类变量**（`:1092`），多实例共享，长跑会持续增长 |
| `duckmail` | `DuckMailMailbox` `core/base_mailbox.py:1419` | API：随机 `address` + 密码 → `POST /accounts` → `POST /token` 换 bearer | 轮询 `GET /messages?page=1` | 全部有默认值；`duckmail_api_key` 存在时直连，否则走前端代理（`:1440`） | **默认 bearer 硬编码 `kevin273945`**（`:259`）；第三方公共服务 |
| `freemail` | `FreemailMailbox` `core/base_mailbox.py:4421` | API `GET /api/generate`（带 `domainIndex`） | 轮询 `GET /api/emails?mailbox=` | `freemail_api_url` + (`freemail_admin_token` 或 `freemail_username`/`password`) | 自建 CF Worker（idinding/freemail）；指定域名只能按 index 命中，不匹配时仅告警不报错（`:4487`） |
| `moemail` | `MoeMailMailbox` `core/base_mailbox.py:2623` | **每次领址都新注册一个站点账号**（`_register_and_login` `:2640`），再 `POST /api/emails/generate` | 轮询 `GET /api/emails/{id}` | `moemail_api_url`（默认 `https://sall.cc`）、`moemail_api_key` | 每个邮箱一个新站点账号，开销大、易被风控；登录靠 cookie 里的 `session-token`，站点改版即失效；内部用 `print` 而非 `_log`（日志进不了任务面板） |
| `maliapi`（YYDS Mail） | `MaliAPIMailbox` `core/base_mailbox.py:1618` | API `POST /accounts`（可带 `domain` / `autoDomainStrategy`） | 轮询 `GET /messages?address=` + `GET /messages/{id}` 取详情 | `maliapi_api_key`；`maliapi_base_url` 默认 `https://maliapi.215.im/v1`；可选 `maliapi_domain`、`maliapi_auto_domain_strategy` | 唯一使用 `_yyds_*` 系列增强提取器的 provider（`:1801`）；不支持 `exclude_codes` |
| `gptmail` | `GPTMailMailbox` `core/base_mailbox.py:1816` | 双模式：配了 `gptmail_domain` → **本地拼装**；否则 `GET /api/generate-email` | 轮询 `GET` 列表 + 详情 | `gptmail_api_key`；`gptmail_base_url` 默认 `https://mail.chatgpt.org.uk` | 本地拼装模式下地址未在服务端注册，能否收信取决于该域是 catch-all |
| `opentrashmail` | `OpenTrashMailMailbox` `core/base_mailbox.py:2011` | 双模式：配了 `opentrashmail_domain` → 本地拼装；否则 `GET /api/random` 解析 HTML（`:2079`） | 轮询 `GET /json/<email>` + `/json/<email>/<id>` | `opentrashmail_api_url`；可选 `opentrashmail_domain`、`opentrashmail_password` | 自建服务；随机地址靠正则从网页抠，站点改版即失效 |
| `cfworker` | `CFWorkerMailbox` `core/base_mailbox.py:2277` | API `POST /admin/new_address`（本地生成 name + 组合域名） | 轮询 `GET /admin/mails?address=` | `cfworker_api_url`、`cfworker_admin_token`；域名相关一大票可选项 | 域名策略最复杂（`_compose_domain` `:2468`：多域随机、随机子域、真人名子域、按 `email_domain_level_count` 补齐级数）；`random_name_subdomain` 依赖可选包 `names`，缺失时静默降级；**唯一会被 `validate_email_domain_policy` 校验的 provider**（`api/tasks.py:617`） |
| `applemail`（小苹果） | `AppleMailMailbox` `core/base_mailbox.py:352` | **本地邮箱池文件轮转**：`take_next_applemail_record()`（`core/applemail_pool.py:240`） | 轮询第三方 `GET /api/mail-all`，用池里的 `client_id`+`refresh_token` 代取 INBOX/Junk | `applemail_pool_dir`(默认 `mail`)、`applemail_pool_file`、`applemail_mailboxes`(默认 `INBOX,Junk`)、`applemail_base_url` | 池游标是**进程内内存**（`core/applemail_pool.py:11-12`），重启即归零、多进程不共享；服务端返回 `new_refresh_token` 时只更新内存里的 `account.extra`，**不回写池文件**（`:574`） |
| `outlook` / `microsoft` | `OutlookMailbox` `core/base_mailbox.py:3615` | **数据库号池领号**：`_pop_account()` `:3713` 从 `outlook_accounts` 表取 `available` 行并置 `in_use` | 三后端策略：Graph（默认）/ IMAP / MailAPI URL，见第六章 | `outlook_backend`(默认 `graph`)、`mail_import_source`（决定筛哪类账号）；凭据来自导入 | 见第六章；池空/全部注册过时抛 RuntimeError 并区分原因 |
| `laoudo`（**兜底分支**） | `LaoudoMailbox` `core/base_mailbox.py:665` | **固定账号**：直接用配置里的 `laoudo_email` | 轮询 `GET https://laoudo.com/api/email/list`（curl_cffi 伪装 chrome131） | `laoudo_auth`、`laoudo_email`、`laoudo_account_id` | 全流程复用同一个地址（不适合批量注册）；`create_mailbox` 的 `else` 分支——**任何未知 provider 字符串都会落到这里**（`:344`），而非报错 |

##### 2.2 不在工厂里的类

| 类 | 位置 | 说明 |
|---|---|---|
| `AitreMailbox` | `core/base_mailbox.py:770` | mail.aitre.cc 临时邮箱。`create_mailbox` 里**没有任何分支创建它**，全仓库无引用 → **死代码**（约 75 行） |

##### 2.3 数量小结

- provider 实现类：**15 个**（14 个可通过配置选中 + 1 个死代码 `AitreMailbox`）
- 工厂可识别标识：14 个（`microsoft` 是 `outlook` 的别名）
- Outlook 收信后端策略类：3 个（`imap` / `graph` / `mailapi_url`）+ 1 个抽象基类
- 前端可选项：`frontend/src/pages/Settings.tsx:31` 的 `mail_provider` 下拉共 13 项；其中 `mail_import` 是虚拟值，由 `mail_import_source` 再决议成 `microsoft` 或 `applemail`（`frontend/src/lib/mailImport.ts:52`）

---

#### 三、统一抽象

##### 3.1 基类接口（`core/base_mailbox.py:23`）

| 成员 | 签名/位置 | 语义 |
|---|---|---|
| `MailboxAccount` | `:16` dataclass | `email` / `account_id` / `extra: dict`。`account_id` 语义**各 provider 不一致**：token（tempmail_lol、duckmail、cfworker、luckmail）、邮箱地址本身（skymail、gptmail）、数据库行 ID（outlook）、订单号（luckmail 订单模式） |
| `get_email()` | `:71` abstract | 领一个可用地址 |
| `get_current_ids(account)` | `:144` abstract | 返回当前邮件 ID 集合，作为「旧邮件基线」 |
| `wait_for_code(account, keyword, timeout, before_ids, code_pattern, **kwargs)` | `:76` abstract | 等码。`kwargs` 事实上的约定项：`otp_sent_at`（时间窗）、`exclude_codes`（已用过的码） |
| `_run_polling_wait(timeout, poll_interval, poll_once, timeout_message)` | `:46` | **轮询骨架**：单调时钟 deadline + 每轮 checkpoint + 碎步 sleep + 超时抛 `TimeoutError` |
| `_safe_extract` / `_decode_raw_content` | `:89` / `:117` | 标准版验证码提取 / 邮件原文清洗 |
| `_yyds_safe_extract` / `_yyds_decode_raw_content` | `:148` / `:184` | 增强版（剥 URL、加数字边界、条件切分邮件头），仅 MaliAPI 与 MailAPI URL 后端使用 |

**没有 `release()` / `close()` 抽象方法**——释放是隐式的：LuckMail 订单模式在超时路径上取消订单，Outlook 号池由外部调 `set_account_status()` 改状态，其余临时邮箱到期自然失效。

##### 3.2 注册与选型

不是注册表（dict/装饰器），而是 **`create_mailbox()` 里一条 if/elif 链**（`core/base_mailbox.py:215-349`）：

```python
def create_mailbox(provider, extra=None, proxy=None) -> BaseMailbox:
    if provider == "tempmail_lol": ...
    elif provider == "skymail": ...
    ...
    else:  # laoudo —— 未知 provider 全落这里
```

配置读取模式统一为 `extra.get("<provider>_<field>", default)`，`extra` 是 `config_store.get_all()` 与任务请求 `extra` 的合并结果（`api/tasks.py:468`）。可配置 key 白名单在 `api/config.py:15` `CONFIG_KEYS`。

选型链路：前端 `mail_provider` →（若为 `mail_import`）按 `mail_import_source` 决议 → 存 config → `_build_mailbox()` 读出 → `create_mailbox()`。

##### 3.3 代理链透传

| 环节 | 位置 | 做法 |
|---|---|---|
| 归一化 | `core/proxy_utils.py:148` `build_requests_proxy_config()` | 把任意形态代理串（`host:port`、`h:p:u:p`、URL、JSON）统一成 `{"http": url, "https": url}`；`socks5://` 被改写成 `socks5h://`（DNS 走代理侧） |
| 各 provider | 构造函数里 `self.proxy = build_requests_proxy_config(proxy)`，请求时 `proxies=self.proxy` | 除 `LaoudoMailbox`（**不接 proxy 参数**，`:668`）外全部支持 |
| Outlook | `core/base_mailbox.py:3634` `self._proxy` | Graph 与 OAuth token 请求带 `proxies`；**IMAP 连接不走代理**（`imaplib.IMAP4_SSL` 直连，`:4207`） |
| LuckMail 代理链 | `core/base_mailbox.py:2806` → `core/luckmail/client.py:91` → `core/luckmail/http_client.py:107` | 三段透传：`LuckMailMailbox(proxy=)` → `LuckMailClient(proxy_url=)` → `LuckMailHttpClient(proxy_url=)`；后者同时给 curl_cffi Session 传 `proxy=` **和** 设 `session.proxies`（`:122-136`），兼容不同 curl_cffi 版本 |

`tests/test_luckmail_proxy_chain.py` 专门锁这条链：第一个用例断言 client 把 `proxy_url` 原样转给 http_client；第二个断言 `socks5://127.0.0.1:7890` 最终变成 `socks5h://...` 并同时落在构造参数与 `session.proxies` 上。

---

#### 四、OTP 提取逻辑

##### 4.1 验证码正则（`core/base_mailbox.py:89` `_safe_extract`）

按优先级依次尝试，命中即返回（有捕获组取 `group(1)`，否则 `group(0)`）：

1. 调用方传入的 `code_pattern`（若有）
2. **语义前缀**：`verification code|one-time password|security code|login code|验证码|校验码|动态码|認證碼|驗證碼` 后 30 字符内的 6 位数字
3. `\bcode\b` 后 12 字符内的 6 位数字
4. 兜底：`(?<!#)(?<!\d)(\d{6})(?!\d)`（排除 hex 颜色与长数字串）

增强版 `_yyds_safe_extract`（`:148`）三处修正：① 先整体剥掉 `https?://\S+`，防 SendGrid 追踪链接里的数字被当成码；② 外部传入的粗糙 `\d{6}` 自动升级成带字母数字边界的版本；③ 兜底正则改成 `(?<![a-zA-Z0-9])(\d{6})(?![a-zA-Z0-9])`，防 `u20216706` 这类误匹配。

##### 4.2 HTML / 邮件原文解析

- `_decode_raw_content`（`:117`）：按首个空行切掉邮件头 → `quopri` 解 Quoted-Printable → `html.unescape` → 清 `Content-Type`/MIME boundary/`----=_Part_` → 剥标签 → 压空白。
- `_yyds_decode_raw_content`（`:184`）：**只有正文里真出现 `Return-Path|Received|Date|From|To|Subject|Content-Type:` 才做空行切分**。原因见 `core/base_mailbox.py:3555` 注释：MailAPI 返回的是网页，`<style>` 里就有空行，硬切会把验证码所在的后半截腰斩。`tests/test_mailapi_url_otp_backend.py:34` 正是这条回归。
- 各 provider 在提取前普遍再洗一轮：剥邮箱地址（防 `user123456@x.com`）、剥 `m=+\d+\.\d+` / `t=\d+` 时间戳（`core/base_mailbox.py:2596`）。
- 协议层另有一套独立实现 `platforms/chatgpt/protocol/mail_provider.py:44` `extract_otp()`，优先信 HTML `<span>` 包裹的码——与 `_safe_extract` 是**两套并行的提取逻辑**。

##### 4.3 去重三道闸

| 机制 | 作用 |
|---|---|
| `before_ids` / `get_current_ids()` 基线 | 领址后先记下已有邮件 ID，往后只认新 ID。基线在 `platforms/chatgpt/protocol/mailbox_adapter.py:88` `prime()` 打，复用同一 provider 跑第二段流程（注册完接着绑 2FA）时会**重新** prime |
| `otp_sent_at` 时间窗 | 只收「发码请求之后」到的信。IMAP 侧留 2 秒余量（`:3222`）、CFWorker 同样 -2s、iCloud 别名留 30 秒（Apple 转发慢，`services/chatgpt_otp_mailbox.py:81`） |
| `exclude_codes` | 已经试过并失败的码不再返回。支持者：AppleMail、CloudMail、DuckMail、GPTMail、OpenTrashMail、CFWorker、LuckMail、Freemail、Outlook 全后端；**不支持**：Laoudo、Aitre、TempMailLol、SkyMail、MoeMail、MaliAPI |

##### 4.4 轮询间隔与超时

| 项 | 值 |
|---|---|
| 默认轮询间隔 | 3 秒（13 处）；Laoudo 4 秒；Outlook IMAP / Graph 5 秒 |
| checkpoint 粒度 | 0.25 秒（`_sleep_with_checkpoint` `:38`） |
| `wait_for_code` 默认 timeout | 120 秒（签名默认值） |
| 实际业务超时 | 180 秒，可用 `mailbox_otp_timeout_seconds`（或 `email_otp_timeout_seconds` / `otp_timeout`）覆盖，见 `platforms/chatgpt/registration_engine.py:376`、`services/chatgpt_two_factor.py:229` |
| LuckMail 订单模式 | 外层自己切片：每片最多 6 秒调一次 SDK 的 `_sync_wait_for_code`（`:2992`），保证 checkpoint 及时生效 |

##### 4.5 与任务控制器的配合

`BaseMailbox._checkpoint()`（`:29`）调 `self._task_control.checkpoint(consume_skip=True, attempt_id=self._task_attempt_token)`，由 `core/task_runtime.py:110` 实现：`stop_requested` → 抛 `StopTaskRequested`；命中本 attempt 的跳过标记或有全局待跳过请求 → 抛 `SkipCurrentAttemptRequested`。

这三个属性是**外部鸭子注入**的，没有形式化接口：

- `api/tasks.py:600-601` 给 `_platform.mailbox` 塞 `_task_attempt_token` 和 `_log_fn`
- `platforms/chatgpt/protocol/mailbox_adapter.py:59` `bind_task_control()` 塞 `_task_control` / `_task_attempt_token` / `_log_fn`

该文件注释写明了漏塞的后果：不绑 `_task_control`，"停止任务"要等满整个 OTP 超时（默认三分钟）才生效；不绑 `_log_fn`，等码那几分钟任务日志一行不出。

##### 4.6 `services/chatgpt_otp_mailbox.py` 加了什么

它**不是在 provider 之上加过滤**，而是解决一个不同的问题：**给一个已知地址反查收件通道**（补 refresh_token、绑 2FA 时面对的是库里的老号，只有地址，没有当初的链路信息）。

`resolve_otp_mail_provider()`（`:149`）按可靠性从高到低试三个 builder，返回 `(provider, reason)`：

| 顺序 | builder | 判据 | 产出 |
|---|---|---|---|
| 1 | `_build_icloud_provider` `:212` | `ICloudAliasModel` 里有这个地址 | `ICloudAliasMailProvider`——走 iCloud 主号 IMAP 读隐私邮箱 |
| 2 | `_build_outlook_provider` `:228` | `outlook_accounts` 里还留着这个号 | `FixedAddressProviderAdapter(OutlookMailbox, account)`，凭据从库里取 |
| 3 | `_build_configured_provider` `:298` | 地址域名属于当前配置的邮箱服务（`_configured_domains` 收集 `<provider>_domain(s)/_enabled_domains/_domain_override`，`_domain_matches` 允许子域） | 用该服务的管理接口按地址收信 |

额外做的事：

- **别名回退查找**（`_find_outlook_row` `:259`）：`xxx+abc@outlook.com` 与 `xxx@outlook.com` 是同一信箱同一套凭据。精确匹配落空时按 `+` 前主地址再查，再不行按 `base+%@domain` LIKE 查。不做这层就会误判「读不到收件箱」，然后白等满一个 OTP 超时。
- **登录地址与收信地址分离**（`:249`）：IMAP/OAuth 认号池那条记录的地址，别名登不上去。
- 统一 `_bind_task_control` + `_prime`（`:197`/`:203`），让新建的 provider 也能被停止/跳过打断、也有旧邮件基线。
- 三条路都失败时返回 `(None, 原因串)`，由调用方决定硬试还是放弃（`services/chatgpt_two_factor.py:222` 只打日志继续）。

**没有**做的事：不按发件人/主题过滤（`keyword` 一路传空串），不做多地址并发（一次只解析一个地址），去重仍靠底层 provider 的 `_seen` / `before_ids`。

---

#### 五、LuckMail 专章

##### 5.1 分层

`core/luckmail/` 是一份**独立 SDK**（`__version__ = "1.2.1"`，`core/luckmail/__init__.py:36`），与本仓库其余代码风格明显不同（英文 docstring、示例代码、同异步双模式）。

| 文件 | 行数 | 职责 |
|---|---|---|
| `http_client.py` | 389 | 基于 **curl_cffi**（TLS 指纹模拟，`impersonate="chrome"`）的 HTTP 层。同异步双 Session 延迟初始化、API Key / HMAC-SHA256 双鉴权、统一响应解析（`code != 0` → `AuthError`/`APIError`）、代理注入 |
| `client.py` | 226 | 门面 `LuckMailClient`：持一个 http_client，挂 `.user` 与 `.supplier` 两个 API 面；另提供一站式 `create_and_wait()`（建单+轮询） |
| `user.py` | 1360 | 用户端全部 API（见下） |
| `supplier.py` | 463 | 供应商端 API，路径前缀 `/api/v1/openapi/supplier` |
| `models.py` | 251 | 21 个 dataclass 响应模型 |
| `exceptions.py` | 35 | 异常体系 |

**同异步双模式**的实现关键在 `_is_async_context()`（`core/http_client.py:24`）：检测事件循环是否在跑，决定返回协程还是直接结果。每个公开方法都是 `if _is_async_context(): return self._async_xxx(...) else: return self._sync_xxx(...)` 的三件套——这是 `user.py` 膨胀到 1360 行的**主因**。另有 `_SyncRunner`（`:47`）维护一个后台事件循环线程，供同步上下文跑协程（当前代码路径未实际使用）。

##### 5.2 `user.py` 1360 行在做什么

**不含**登录/签到/额度领邮箱——鉴权全靠 API Key，没有会话概念。按功能分 9 组，每组 1 个公开方法 + 2 个私有实现：

| 组 | 方法 | 端点 |
|---|---|---|
| 账户 | `get_user_info` `:218`、`get_balance` `:245` | `/user/info`、`/balance` |
| 元数据 | `get_email_types` `:270`、`get_projects` `:409` | `/email-types`、`/projects` |
| 邮箱库 | `get_emails` `:294`、`import_emails` `:336`、`export_emails` `:382` | `/emails`、`/emails/import`、`/emails/export` |
| **订单接码** | `create_order` `:440`、`get_order_code` `:488`、`cancel_order` `:519`、`get_orders` `:546`、`wait_for_code` `:589` | `/order/create`、`/order/{no}/code`、`/order/{no}/cancel` |
| **购买邮箱** | `purchase_emails` `:681`、`get_purchases` `:728` | `/email/purchase`、`/email/purchases` |
| **Token 收信** | `get_token_code` `:776`、`check_token_alive` `:807`、`wait_for_token_code` `:837`、`get_token_mails` `:906`、`get_token_mail_detail` `:938` | `/email/token/{token}/code|alive|mails|mails/{id}` |
| 申述 | `create_appeal` `:974` | `/appeal/create` |
| 购买项管理 | `set_purchase_disabled` `:1034`、`batch_set_purchase_disabled` `:1064`、`set_purchase_tag` `:1095`、`batch_set_purchase_tag` `:1136`、`api_get_purchases` `:1175` | `/email/purchases/...` |
| 标签 | `create_tag` `:1231`、`get_tags` `:1266`、`update_tag` `:1290`、`delete_tag` `:1332` | `/email/tags` CRUD |

前 200 行是 15 个 `_parse_*` 响应映射函数（`:35-198`）。

##### 5.3 两种业务模式

| 模式 | 触发条件 | 领址 | 收码 | 计费单位 |
|---|---|---|---|---|
| 购买邮箱（token） | `project_code == "openai"`（即 ChatGPT）或 account_id 以 `tok_` 开头（`core/base_mailbox.py:2815`） | `purchase_emails(project_code, quantity=1, email_type, domain)` → 返回 `email_address` + `token` + `warranty_until` | 轮询 `get_token_mails(token)`，本地 `_safe_extract` 抠码 | 买一个邮箱（带质保期），可反复收信 |
| 订单接码 | 其他平台 | `_sync_create_order({project_code, email_type})` → 返回 `order_no` + `email_address` + `expired_at` | SDK 端 `get_order_code(order_no)` 直接返回服务端已提取的 `verification_code` | 一单一码；超时/取消不计费 |

`_resolve_token()`（`:2826`）在只有地址没有 token 时，会用 `get_purchases(keyword=email)` 反查 token——用于「已注册账号补收码」场景。

##### 5.4 supplier 概念

LuckMail 是**双边平台**：供应商上架邮箱资源，用户消费。`SupplierAPI`（`core/luckmail/supplier.py:85`）覆盖供应商侧：`get_profile`（含 `frozen_balance`、`code_commission_rate`、`buy_commission_rate` 两档佣金率）、邮箱库 CRUD/导入导出、申述处理（`get_appeals`/`reply_appeal`/`batch_reply_appeals`）、`get_dashboard` 数据看板。

**本仓库完全不用它**——`LuckMailMailbox` 只调 `client.user.*`。它随 SDK 一起被搬进来，属于纯冗余的 463 行。

##### 5.5 异常体系（`core/luckmail/exceptions.py`）

```
LuckMailError (Exception)
├── AuthError        # 鉴权失败（HTTP 401 或 body code == 401）
├── APIError         # 业务码非 0，携带 code / message / data
├── NetworkError     # 请求层异常兜底
└── TimeoutError     # 错误信息含 "timeout" 时转成这个（注意：遮蔽内建 TimeoutError）
```

分类逻辑在 `core/http_client.py:197` `_parse_response` 与 `:250-257` 的 except 链。

**跨层丢失**：`LuckMailMailbox` 把所有 SDK 异常统一包成 `RuntimeError` / `TimeoutError`（`core/base_mailbox.py:2911`、`:3001`、`:3043`），上层拿不到 `APIError.code`，因此**无法区分「余额不足」和「网络抖动」**——两者都只能重试。

---

#### 六、邮件导入子系统

##### 6.1 架构（`services/mail_imports/`）

| 文件 | 行数 | 角色 |
|---|---|---|
| `base.py` | 34 | `BaseMailImportStrategy` 抽象：`descriptor` / `execute` / `get_snapshot` / `delete` / `batch_delete` |
| `registry.py` | 35 | `MailImportRegistry`：按 `descriptor.type` 建 dict；模块级单例 `mail_import_registry`；额外把 `"outlook"` 作为 `"microsoft"` 的别名（`:10`） |
| `providers.py` | 664 | 两个策略实现：`AppleMailImportStrategy` `:48`、`MicrosoftMailImportStrategy` `:278` |
| `schemas.py` | 118 | 8 个 Pydantic 模型 + `MailImportProviderType = Literal["applemail","microsoft"]` + `MailImportAccountType = Literal["microsoft_oauth","mailapi_url"]` |
| `microsoft_import_rules.py` | 238 | 行解析器 + 规则引擎（见 6.4） |
| `import_source.py` | 92 | **导入类型（视图）** 的归一化与决议（见 6.6） |

##### 6.2 支持的导入源与落库位置

| type | 标签 | 输入格式 | 数据落到哪 |
|---|---|---|---|
| `applemail` | AppleMail / 小苹果 | JSON 数组/对象（`email`/`clientId`/`refreshToken`/`folder`），或每行 `email----password----client_id----refresh_token` 文本 | **本地 JSON 文件**：`mail/applemail_*.json`（`core/applemail_pool.py:254` `save_applemail_pool_json`）；`bind_to_config` 为真时把 `applemail_pool_dir`/`applemail_pool_file` 写进 config |
| `microsoft` / `outlook` | 微软邮箱（Outlook / Hotmail） | 自动识别两种：4 段 `邮箱----密码----client_id----refresh_token`；2 段 `邮箱----mailapi_url` | **SQLite 表 `outlook_accounts`**（`core/db.py:80` `OutlookAccountModel`），一行一号。**iCloud 隐私邮箱也走这一行**：`POST /api/icloud/aliases/import-to-pool` 把别名拼成 2 段格式喂给本策略，落库为 `account_type=mailapi_url`（见 4.4.1） |

AppleMail 的解析在 `core/applemail_pool.py:147` `parse_applemail_pool_content`，兼容 JSON 数组、带 `data/items/accounts/list/emails/mails` 包裹的对象、`----`/Tab/空格分隔的文本行，3 段与 4 段（多一个 password）两种字段数。

##### 6.3 Microsoft 策略的完整流水线（`services/mail_imports/providers.py:426` `execute`）

1. 切行、跳过空行与 `#` 注释
2. `AutoDetectRowParser.parse()` 按段数派发（2 段→MailAPI、≥4 段→OAuth）
3. **批内查重**（同一次粘贴里重复的邮箱直接 fail）
4. 规则引擎 `evaluate()` 三条规则串联
5. **别名扩展**（可选）：`_expand_records_with_aliases` `:286`，按 `email+随机6小写@domain` 生成 1–5 个别名，最多 `20×count` 次尝试避开已占用地址
6. **OAuth 可用性并发探测**：`ThreadPoolExecutor`，worker 数 = `MAIL_IMPORT_OAUTH_WORKERS` 环境变量（默认 8，clamp 1–32）；MailAPI 账号跳过探测（`:352`）
7. 逐条 `session.add` + `commit`（一行一事务，失败 rollback 并计 failed）
8. 回传 `MailImportResponse{summary, snapshot, errors, meta}`

##### 6.4 `microsoft_import_rules.py` 在筛什么

| 规则类 | 位置 | 筛掉什么 | 为什么 |
|---|---|---|---|
| `DuplicateMicrosoftMailboxRule` | `:148` | `outlook_accounts` 里已存在的邮箱（小写比较） | 表有 unique 约束，重复导入会撞库 |
| `RegisteredMicrosoftMailboxRule` | `:162` | 已在 `accounts` 表注册成功过的邮箱 | 池内查重看不见「用过的」（取号只改状态不删行，但地址已被平台占用）。重导一份含旧别名的清单会把注册过的地址塞回池子，下次注册必撞「邮箱已被占用」 |
| `MailApiUrlFormatRule` | `:185` | `account_type == mailapi_url` 但 URL 不是合法 http/https | 空/畸形 URL 会让运行时反复 GET 一个取不到码的地址，白等满超时 |
| `MicrosoftMailboxAvailabilityRule` | `:201` | OAuth 号 refresh_token 换不出 access_token 的 | 提前剔死号。**注意：这条规则类未挂进 `execute()` 的引擎**（`services/mail_imports/providers.py:452` 只挂前三条），可用性检测是第 6 步单独并发跑的 |

规则引擎 `MicrosoftMailImportRuleEngine.evaluate()`（`:132`）短路返回第一条失败，`tests/test_mail_imports_service.py:102` 锁了这个语义。

`probe_oauth_availability()`（`core/base_mailbox.py:3978`）对 3 个 token endpoint × 4 组 scope 做笛卡尔积重试，并把 `invalid_grant` + `service abuse mode` 识别成专用 reason 立即返回。

##### 6.5 API 接口

`api/mail_imports.py`（通用，prefix `/mail-imports`）：

| 方法 路径 | 处理 |
|---|---|
| `GET /providers` | `registry.descriptors()`——返回每个 type 的 label/description/placeholder/helper_text，**前端表单是数据驱动的** |
| `GET /snapshot?type=&pool_dir=&pool_file=&preview_limit=` | 当前池内容预览（count + items + truncated） |
| `POST ""` | 执行导入 |
| `POST /delete` | 删单条 |
| `POST /batch-delete` | 批量删 |

`api/outlook.py`（旧版兼容，prefix `/outlook`）：只有 `POST /batch-import`，内部就是 `registry.get("microsoft").execute(...)` 的一层薄壳，把结果拍平成 `{total, success, failed, accounts, errors}`（`api/outlook.py:34`）。

`ValueError` → HTTP 400（不支持的类型），`RuntimeError` → HTTP 400（业务失败）。

##### 6.6 「注册取号按邮箱导入类型筛号池」（git `1ef41b9`）

**问题**：`outlook_accounts` 一张表里 OAuth 号和 MailAPI URL 号混着放，而取号只按 `id` 捞第一个 `available` 的。设置页明明选着 MailAPI URL，注册任务照样发下来一个 OAuth 号，日志停在 `has_mailapi_url=False` / 收信后端 `graph`，验证码永远等不到。

**实现分布在三处**：

| 位置 | 内容 |
|---|---|
| `services/mail_imports/import_source.py:34` `_SOURCE_POOL_ACCOUNT_TYPES` | 视图 → 该取哪类账号的映射：`outlook`/`hotmail` → `microsoft_oauth`，`mailapi` → `mailapi_url` |
| `services/mail_imports/import_source.py:67` `resolve_pool_account_type()` | 决议函数。**关键设计：不做互相兜底**，两类之间不顶替；没存过视图的老库返回空串，保持「整池随便取」的旧行为 |
| `core/base_mailbox.py:3633` `self._pool_account_type` + `:3747` | `OutlookMailbox.__init__` 用 `mail_import_source` 算出 wanted_type，`_pop_account()` 在 SQL 上加 `where account_type 匹配`。`_account_type_matches()` `:3696` 用 `lower(trim(coalesce(...)))` 把 NULL/空串也认成 OAuth（老库兼容） |

**四个视图**（`services/mail_imports/import_source.py:12-22`）：`applemail` / `outlook` / `hotmail` / `mailapi`。`mail_provider` 只记到号池粒度（microsoft vs applemail），视图单独存在 `mail_import_source` 配置项里——否则退出设置页再回来就只能按 provider 反推，永远反推成 Outlook。

配套函数：`normalize_mail_import_source()`（收敛成四个合法值，legacy `microsoft` → `outlook`）、`resolve_mail_provider_from_source()`（视图 → 实际 provider）、`align_source_with_provider()`（provider 与视图打架时以 provider 为准，避免存出「小苹果池 + MailAPI 视图」）。

服务端在 `api/config.py:153` 读取时归一化、`:201-208` 写入时对齐。前端镜像了同一套逻辑在 `frontend/src/lib/mailImport.ts`；注册任务提交必须带上 `mail_import_source`（`frontend/src/pages/RegisterTaskPage.tsx:140`），否则单独改导入类型不生效。

筛不到号时 `_pop_account()` 分四种情况报不同的话（`core/base_mailbox.py:3773-3789`）：同类型剩的都注册过了 / 选的类型池里一个都没有（并说明还剩多少其他类型、不会顶替）/ 整池都注册过了 / 池子是空的。

---

#### 七、Outlook / MailAPI URL 后端

##### 7.1 `OutlookAccountModel`（`core/db.py:80`，表 `outlook_accounts`）

| 字段 | 类型/默认 | 说明 |
|---|---|---|
| `email` | str, index, **unique** | 地址 |
| `password` | str | IMAP 密码登录兜底用 |
| `client_id` / `refresh_token` | str = "" | OAuth 凭据 |
| `account_type` | str = `"microsoft_oauth"` | `microsoft_oauth` 或 `mailapi_url` |
| `mailapi_url` | str = "" | MailAPI 取码地址 |
| `enabled` | bool = True | 软禁用 |
| `status` | str = `"available"`, index | 生命周期：`available` / `in_use` / `used` / `failed` |
| `created_at` / `updated_at` / `last_used` | datetime | 时间戳 |

##### 7.2 两种 account_type 的差异

| 维度 | `microsoft_oauth` | `mailapi_url` |
|---|---|---|
| 导入格式 | `邮箱----密码----client_id----refresh_token`（4 段） | `邮箱----https://mailapi.icu/key?...`（2 段）；**iCloud 隐私邮箱也走这一行**：`邮箱----<面板地址>/m/<share_token>` |
| 导入时校验 | 并发跑 `probe_oauth_availability()`，换不出 token 就拒 | 只校验 URL 是 http/https；**不做联网探测**（`tests/test_mail_imports_service.py:70` 断言 `OutlookMailbox` 根本没被实例化） |
| 运行时后端 | `graph`（默认）或 `imap` | `mailapi_url` |
| 取信方式 | refresh_token 换 access_token → Graph REST 或 IMAP XOAUTH2 | 反复 `GET mailapi_url`，从返回的网页/JSON 里抠码 |
| 去重键 | 邮件 ID（Graph `id` / IMAP `folder:uid`） | **验证码本身**（`f"mailapi_code:{code}"`，`core/base_mailbox.py:3533`）——因为 URL 返回的是渲染页，没有稳定 message id |

##### 7.3 refresh_token 换 access token 取信流程

```
_get_oauth_access_token(account, preferred_backend)   core/base_mailbox.py:4130
  └─ 先查 account.extra["_oauth_token_cache"][backend]，剩余 >60s 直接用
  └─ _fetch_oauth_token_bundle() :4090
       └─ probe_oauth_availability() :3978
            for endpoint in [consumers, live, common]:        # :3921
              for (label, scope) in scope_candidates:         # :3939
                POST {client_id, refresh_token, grant_type=refresh_token, scope?}
                → 200 且有 access_token 即返回 {access_token, scope_label, expires_in}
  └─ 顺手记 extra["_oauth_backend_capability"] = "graph" if scope_label=="graph_default" else "imap"
```

scope 候选顺序随 backend 变（`:3962`）：graph 优先 `graph_default` → `outlook_default` → `imap_new` → 空；imap 反过来。

**后端降级链**（这是本模块最容易踩的地方）：

1. `_resolve_backend()` `:4233`：`account_type == mailapi_url` 或 extra 里有 `mailapi_url` → MailAPI 后端；否则按配置的 backend；若选了 graph 但缺 `client_id`/`refresh_token` → **自动降 IMAP**
2. Graph 后端每次进入前再查 `_oauth_backend_capability`（`:3305`、`:3357`）：若拿到的 token 只支持 IMAP scope → **运行时再降 IMAP**

IMAP 侧（`OutlookImapMailboxBackend` `:3116`）：轮 `["INBOX","Junk","Deleted Items","Trash"]` 四个文件夹，先试 `EXAMINE`（readonly）失败再 `SELECT`（`:3119`），UID SEARCH ALL 取最后 50–100 封，`fetch RFC822` 后用 `email` 标准库解析多段正文。登录先试 XOAUTH2、失败退密码，两个 host（`outlook.live.com` / `outlook.office365.com`）依次试（`:4186`）。

Graph 侧（`OutlookGraphMailboxBackend` `:3302`）：轮 `["inbox","junkemail","deleteditems"]`，`GET /me/mailFolders/{f}/messages?$top=25&$orderby=receivedDateTime DESC`，请求头带 `Prefer: outlook.body-content-type="text"`（`:4253`）。

MailAPI 侧（`MailApiUrlOtpBackend` `:3529`）：`GET mailapi_url` → `_yyds_decode_raw_content` → `_yyds_safe_extract`。`tests/test_mailapi_url_otp_backend.py` 用一份真实的隐私邮箱分享页 fixture 锁了四条行为：iframe `srcdoc` 里转义过的正文能抠出码、空行前的内容不能丢、追踪链接里的数字不算码、`mailapi_url` 为空要报错而不是静默空轮询。

**这一条正是 iCloud 隐私邮箱的取码路径**：免登录页 `GET /m/{share_token}` 渲染的 HTML（含 iframe `srcdoc`）就是上面那份 fixture 的来源，「导入 MailAPI 号池」只是把 `地址----<origin>/m/<token>` 写进 `outlook_accounts`（见 4.4.1）。所以隐私邮箱地址在号池里长得像微软号，运行时却完全走 `mailapi_url` 分支——`_resolve_backend()` 先看 `account_type`，不看域名。

##### 7.4 状态流转

```
导入 ──→ available ──_pop_account()──→ in_use ──┬─→ used    (注册成功 / 流程走完)
  ↑         ↑                                    └─→ failed  (注册失败)
  │         └── requeue_account() 回退（存在则复位 available，不存在则新建行）
  └── 导入记录永久保留，取号不删行
```

- 领号：`_pop_account()` `:3713`，**类级 `_pop_lock`** 保证多线程互斥；SQL 条件 = `enabled=True` AND `status ∈ {available, NULL, ""}` AND `lower(email) NOT IN (select lower(email) from accounts)` AND（可选）account_type 匹配，按 `id` 升序取第一条，随即置 `in_use` + `last_used`
- 改状态：`set_account_status()` `:3894`，白名单 `{available, in_use, used, failed}`；调用方是 `platforms/chatgpt/registration_engine.py:139/144/169/176`（`"used" if registration.success else "failed"`）
- 回退：`requeue_account()` `:3846`，按 email upsert 回 `available`

##### 7.5 `api/outlook.py`

只有 `POST /outlook/batch-import` 一个接口（53 行），本质是 `mail_import_registry.get("microsoft").execute()` 的兼容壳。新代码应直接用 `/mail-imports`。

---

#### 八、域名策略

| 模块 | 作用 | 调用方 |
|---|---|---|
| `core/email_domain_policy.py:29` `validate_email_domain_policy(email, config)` | **注册后**的域名合规校验（事后否决，不参与生成）。`email_domain_rule_enabled` 关闭时直接返回；开启时校验：① 域名级数 ≥ `email_domain_level_count`（默认 2，小于 2 报错）；② 域名里至少 2 个英文字母 **且** 至少 2 个数字。不满足抛 `ValueError` | 唯一调用点 `api/tasks.py:617`，且**只在 `mail_provider == "cfworker"` 且地址含 `@` 时触发**。配置校验/默认值在 `api/config.py:209-221`、`:179-182` |
| `core/applemail_pool.py` | 小苹果本地邮箱池文件的**读写与轮转**（不是域名策略）：`parse_applemail_pool_content` `:147`（多格式解析）、`resolve_applemail_pool_path` `:169`（指定文件名 → `mail/` 下找 → 项目根兜底；未指定则按 mtime 取最新的 `*.json|txt|csv`）、`load_applemail_pool_snapshot` `:215`（预览）、`take_next_applemail_record` `:240`（游标轮转）、`save_applemail_pool_json` `:254`（落盘，文件名做 `[^A-Za-z0-9._-]` 清洗） | 运行时：`core/base_mailbox.py:581` `AppleMailMailbox.get_email()` 调 `take_next_applemail_record`。管理面：`services/mail_imports/providers.py:11` 导入其余三个函数 |

另一处「域名策略」其实在 provider 内部：`CFWorkerMailbox._compose_domain()`（`core/base_mailbox.py:2468`）**生成时**就按 `email_domain_level_count` 补齐子域级数——与 `email_domain_policy` 的事后校验是同一配置项的两端，这也解释了为什么只有 cfworker 会被校验。

`services/chatgpt_otp_mailbox.py:328` `_configured_domains()` / `:347` `_domain_matches()` 是第三套域名逻辑：按域名反查「这个地址是不是当前邮箱服务发出来的」，支持子域匹配。

---

#### 九、设计要点与问题

##### 9.1 值得肯定的设计

1. **轮询骨架统一**：`_run_polling_wait` 把 deadline/checkpoint/碎步 sleep 收成一处，15 个 provider 的「停止任务」响应延迟都是 0.25 秒级，不会各自跑偏。
2. **去重三道闸互补**：ID 基线（精确）+ 时间窗（ID 不稳定时兜底）+ `exclude_codes`（同一封信里的码试过就不再用），覆盖了「串号」的三种成因。注释里都写清了对应的真实事故。
3. **Outlook 双层后端降级**：先按凭据完整度降，再按实际拿到的 token scope 降。导入时的 scope 探测结果被复用到运行时（`_oauth_backend_capability`），避免每次都试错。
4. **号池筛选不做兜底**：`resolve_pool_account_type()` 明确拒绝「类型不匹配就将就一个」，把一个静默三分钟超时的故障转成一条说得清的报错。这是正确的取舍。
5. **导入规则引擎可组合**：`MicrosoftMailImportRuleEngine` + 独立规则类，新增筛选条件不用改 `execute()`。descriptor 驱动前端表单，加一个导入源不用改前端。
6. **测试锁的是行为而非实现**：`test_mailapi_url_otp_backend.py` 用真实分享页 fixture、`test_luckmail_proxy_chain.py` 锁 `socks5h` 改写、`test_mail_pool_account_type_filter.py` 锁「两类不互相顶替」——都是曾经出过事的点。

##### 9.2 问题与风险

**① `base_mailbox.py` 4596 行单文件**

15 个 provider 类 + 4 个后端策略类挤在一个文件里，最长的 `OutlookMailbox` 单类近 800 行（`:3615-4420`）。后果：任何 provider 的改动都在同一文件上产生冲突；`git blame` / code review 成本高；IDE 跳转与搜索噪声大。合理的拆法是 `core/mailboxes/{tempmail_lol,skymail,outlook,...}.py` + `registry.py`，`BaseMailbox` 留在 `base.py`。

**② provider 之间大量重复代码**

- `wait_for_code` 的骨架（建 seen 集合 → poll_once → 遍历新邮件 → 拼 search_text → keyword 过滤 → `_safe_extract` → `exclude_codes` 检查 → `_run_polling_wait`）在 15 个类里几乎逐行重复，只有取列表的 HTTP 调用不同。可抽成 `PollingMailboxMixin(list_messages, message_id, message_text)` 模板方法。
- 「剥邮箱地址」正则 `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` 在 **6 处**各写一遍（`:1600`、`:1794`、`:1987`、`:2253`、`:2591`、`:2769`）。
- 时间戳解析 `_parse_message_timestamp` 在 `CloudMailMailbox`（`:1289`，静态方法）和 `DuckMailMailbox`（`:1538`，函数内嵌套定义）里实现了两遍。
- `_normalize_domain` / `_generate_local_part` 在 GPTMail、OpenTrashMail、CFWorker 里各有一份近乎相同的实现。
- **`_safe_extract` 与 `_yyds_safe_extract` 是同一函数的两个版本**（`:89` vs `:148`），后者修了三个真实 bug（URL 数字、字母边界、误切正文），但只有 2 个调用方用了。剩下 13 个 provider 仍在用有已知缺陷的旧版。这是明确的技术债——应把 `_yyds_*` 的修正合并回 `_safe_extract` 并删掉分叉。
- 协议层另有第三套 `extract_otp()`（`platforms/chatgpt/protocol/mail_provider.py:44`），规则又不一样（优先 `<span>`）。三套并行意味着同一封邮件在不同路径下可能抠出不同结果。

**③ 异常处理过度宽松**

大量 `except Exception: pass` 出现在 `get_current_ids`（几乎全部 provider）和 `poll_once` 里——`base_mailbox.py` 单文件就有 **57 处**裸 `except Exception:`。后果：API Key 失效、域名配错、服务下线这些**配置性错误**，表现和「暂时没收到邮件」完全一样——用户只能看到「等待验证码超时 (180s)」。至少应该在首次失败时 `_log` 一次原因（MailAPI 后端做对了，`:3590`）。

**④ 外部服务可用性风险**

- 14 个可选 provider 里有 9 个依赖**他人运营的第三方服务**（tempmail.lol、laoudo.com、duckmail.sbs、sall.cc、maliapi.215.im、mail.chatgpt.org.uk、appleemail.top、mails.luckyous.com、mail.aitre.cc）。这些站点改个接口、换个域名、挂掉，对应 provider 立刻失效，且从日志上看只是「超时」。
- 多处**硬编码默认值**指向特定实例：`duckmail_bearer = "kevin273945"`（`:259`）、`moemail_api_url = "https://sall.cc"`、`luckmail_base_url = "https://mails.luckyous.com/"`、`applemail_base_url = "https://www.appleemail.top"`。这些既是可用性单点，也是隐性的信任边界。
- `MoeMailMailbox` 每领一个邮箱就在第三方站点注册一个新账号（`:2680`），最脆也最易触发风控。

**⑤ 状态与并发**

- 进程级可变类变量：`CloudMailMailbox._token_cache` / `_seen_ids`（`:1090-1092`）、`core/applemail_pool._POOL_CURSORS`（`:12`）。`_seen_ids` 按 email 累积且**永不清理**，长跑进程会缓慢泄漏；`_POOL_CURSORS` 重启归零、多 worker 不共享，会导致同一个小苹果邮箱被重复领。
- `OutlookMailbox._pop_lock` 是**类级**锁，只在单进程内有效。多 worker / 多容器部署时两个进程可能领到同一行（SQL 层没有 `SELECT ... FOR UPDATE`，SQLite 也不支持）。
- `MicrosoftMailImportStrategy.execute()` 逐条 `commit`（`services/mail_imports/providers.py:556`），一次导入 1000 个号就是 1000 个事务。

**⑥ 契约靠约定而非类型**

- `_task_control` / `_task_attempt_token` / `_log_fn` 三个属性由外部直接赋值，`BaseMailbox` 只用 `getattr` 取（`:25`、`:30`、`:35`）。漏塞不报错，只是行为静默退化——`platforms/chatgpt/protocol/mailbox_adapter.py:66` 的注释就是在描述这个坑。应改成基类的显式方法或构造参数。
- `MailboxAccount.account_id` 在不同 provider 里语义完全不同（token / 地址 / 行 ID / 订单号），`extra` 是无 schema 的 dict。`LuckMailMailbox._use_purchase_mode()` 靠 `account_id.startswith("tok_")` 猜模式（`:2819`），非常脆。
- `wait_for_code(**kwargs)` 里的 `otp_sent_at` / `exclude_codes` 是隐式约定，**6 个 provider 完全忽略它们**，调用方无法探知。

**⑦ 死代码与冗余**

- `AitreMailbox`（75 行）不可达。
- `core/luckmail/supplier.py`（463 行）+ `models.py` 里对应的 supplier 模型完全未被使用。
- `core/luckmail/client.py` 的 `create_and_wait()` / `_SyncRunner` 未被本仓库调用。
- `services/mail_imports/microsoft_import_rules.py:201` `MicrosoftMailboxAvailabilityRule` 定义了但没挂进引擎（同等逻辑在 `services/mail_imports/providers.py:351` 重新实现了一遍）；`:226` `parse_microsoft_import_record` 与 `:236` `parse_microsoft_import_line` 是两个只为测试保留的兼容入口。
- `create_mailbox` 的 `else → laoudo` 兜底（`:344`）会把**任何拼错的 provider 名**静默变成 Laoudo，然后报「Laoudo 邮箱未配置」——误导性极强。应改成 `raise ValueError(f"未知邮箱服务: {provider}")`。

**⑧ 前后端逻辑镜像**

`import_source.py` 的四个视图、legacy 别名、决议规则，在 `frontend/src/lib/mailImport.ts` 里用 TypeScript 重写了一遍。两边必须同步修改，`MAIL_IMPORT_SOURCES`、`LEGACY_SOURCE_ALIASES`、`resolveEffectiveMailProvider` 都是重复定义。可考虑由 `GET /mail-imports/providers` 一并下发。

---

## 十、iCloud 登录与凭据存储

### iCloud 平台:登录与凭据存储

> 覆盖范围:Apple ID 主号登录(SRP-6a 握手 + 双重认证)、Cookie 导入、AES-256-GCM 凭据落库、支撑模块(传输/构建号/常量)。
> 不含:Hide My Email 别名生成、IMAP 收件箱读取、分享页、API 路由层(见同目录其他文档)。

#### 模块地图

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `platforms/icloud/srp.py` | 139 | Apple 变体 SRP-6a 客户端(纯计算,无网络) |
| `platforms/icloud/login.py` | 697 | 登录状态机 + 会话管理器 + 限流 |
| `platforms/icloud/cookies.py` | 128 | 浏览器导出 Cookie 解析与 Cookie 头规范化 |
| `platforms/icloud/credentials.py` | 78 | 凭据数据类与加密/解密序列化 |
| `core/secret_box.py` | 107 | AES-256-GCM 密钥管理与封装 |
| `platforms/icloud/transport.py` | 255 | 会话头传递、重试、超时、JSON 取值 |
| `platforms/icloud/build_info.py` | 151 | iCloud 客户端构建号探测与缓存 |
| `platforms/icloud/constants.py` | 90 | 区域端点、UA、OAuth client_id、兜底构建号 |
| `platforms/icloud/errors.py` | 54 | 统一错误码模型 |
| `platforms/icloud/models.py` | 136 | `SessionImportRequest` / `TrustedPhone` 等领域模型 |

设计前提(`platforms/icloud/login.py:1-5`):**Apple ID 密码只参与本次 SRP 握手,绝不落盘**;登录会话仅存在于内存,成功后产出的是可导入的 iCloud Web Session。

---

#### 一、Apple SRP-6a 登录流程

##### 1.1 群参数与哈希

| 项 | 取值 | 位置 |
| --- | --- | --- |
| 群 | RFC 5054 2048 位组(`N` 十六进制常量) | `platforms/icloud/srp.py:18-28` |
| 生成元 `g` | 2 | `platforms/icloud/srp.py:29` |
| 哈希 | 全程 SHA-256(`_hash` / `_hash_int`) | `platforms/icloud/srp.py:34-42` |
| 填充宽度 | 256 字节(`_GROUP_WIDTH`),仅在算 `k`、`u` 时左侧零填充 | `platforms/icloud/srp.py:16,50-54` |
| 整数编码 | **最小长度大端**(`_to_bytes`),不是定长 | `platforms/icloud/srp.py:45-47` |
| 支持协议 | `s2k`、`s2k_fo` | `platforms/icloud/srp.py:31` |
| 迭代上限 | 1,000,000,超出即报 `invalid_response` | `platforms/icloud/srp.py:17,59-60` |

关键坑:同一份实现里混用两种整数编码。`k`、`u` 用 **256 字节零填充**,而 `A`、`B`、`S`、`N` 参与证明哈希时用**最小长度编码**。改动任一处都会让 M1 不被 Apple 接受。

##### 1.2 Apple 的 NoUsernameInX 变体

标准 SRP 的私钥是 `x = H(salt, H(username:password))`。Apple 把用户名留空但**保留冒号分隔符**:

```python
private_key = _hash_int(salt, _hash(b":" + derived_key))   # srp.py:112
```

即 `x = H(salt, H("" + ":" + derived))`。这解释了文件头注释里的"用空用户名,但保留 `用户名:密码` 的分隔符"(`platforms/icloud/srp.py:3-4`)。用户名本身只在 M1 里出现(`H(username)`,`platforms/icloud/srp.py:128`)。

##### 1.3 口令派生(PBKDF2 前的预哈希差异)

`derive_password(password, salt, iterations, protocol)`(`platforms/icloud/srp.py:57-66`):

1. `digest = SHA256(password.utf8)`;
2. 按协议选 PBKDF2 的输入材料:
   - `s2k` → 直接用 32 字节 `digest`;
   - `s2k_fo` → 用 `digest.hex()` 的 **ASCII 小写十六进制字符串**(64 字节);
3. `PBKDF2-HMAC-SHA256(material, salt, iterations, dklen=32)`。

已知答案向量(`tests/test_icloud_srp.py:9-19`)固定了这两条路径的输出,任何重构必须保持这两个向量不变。

##### 1.4 握手计算步骤(`process_challenge`)

| 步 | 计算 | 位置 | 校验/失败 |
| --- | --- | --- | --- |
| 0 | `a` 随机 32 字节,**首字节最高位强制置 1**(与 pysrp 对齐,保证私钥恒为 256 位);`A = g^a mod N` | `platforms/icloud/srp.py:78-86` | — |
| 1 | 解析服务端 `B`;要求 `0 < B < N` | `platforms/icloud/srp.py:102-104` | `Apple 返回的 SRP 公开值无效` |
| 2 | `k = H(pad(N) ‖ pad(g))` | `platforms/icloud/srp.py:106-107` | — |
| 3 | `u = H(pad(A) ‖ pad(B))`,要求 `u ≠ 0` | `platforms/icloud/srp.py:108-110` | `SRP 混淆参数无效` |
| 4 | `x = H(salt ‖ H(":" ‖ derived))` | `platforms/icloud/srp.py:112` | — |
| 5 | `v = g^x mod N` | `platforms/icloud/srp.py:113` | — |
| 6 | `base = (B - k·v) mod N`,要求 `≠ 0` | `platforms/icloud/srp.py:114-116` | `SRP 共享密钥基数无效` |
| 7 | `S = base^(u·x + a) mod N`,要求 `≠ 0` | `platforms/icloud/srp.py:118-120` | `SRP 共享密钥无效` |
| 8 | `K = H(S)`(会话密钥,**单次哈希,非 SHA_Interleave**) | `platforms/icloud/srp.py:121` | — |
| 9 | `group_hash = H(N) XOR H(pad(g))` | `platforms/icloud/srp.py:123-125` | 注意此处 `N` 用最小长度编码、`g` 用填充编码 |
| 10 | `M1 = H(group_hash ‖ H(username) ‖ salt ‖ A ‖ B ‖ K)` | `platforms/icloud/srp.py:126-133` | 客户端证明 |
| 11 | `M2 = H(A ‖ M1 ‖ K)` | `platforms/icloud/srp.py:134` | 服务端证明**由客户端预先算出**,一并上传 |

`salt` 原样参与哈希,不做归一化,因此**前导零字节有意义**——`tests/test_icloud_srp.py:45-53` 专门锁定了 `\x00\x01\x02\x03` 与 `\x01\x02\x03` 必须产生不同 M1。

服务端公开值的边界用例(空、`\x00`、等于 `N`、`N+1`)全部必须抛错,见 `tests/test_icloud_srp.py:56-68`。

`constant_time_equals`(`platforms/icloud/srp.py:138-139`)包装 `hmac.compare_digest`,供比对证明用。

##### 1.5 与 Apple 端点的字段对应

| 阶段 | HTTP | 端点 | 请求字段 | 响应字段 |
| --- | --- | --- | --- | --- |
| 初始化页面 | GET | `{auth}/authorize/signin` | `frame_id`/`client_id`/`redirect_uri`/`response_mode=web_message`/`authVersion=latest` 等 query | 只判 `response.ok`,作用是拿首批会话头 |
| 账号联邦校验 | POST | `{auth}/federate?isRememberMeEnabled=true` | `accountName`、`rememberMe` | 只判 `ok`(区分托管/联邦账号) |
| SRP init | POST | `{auth}/signin/init` | `a`=Base64(`A`)、`accountName`、`protocols=["s2k","s2k_fo"]` | `b`=Base64(`B`)、`salt`=Base64、`c`=challenge 串、`iteration`、`protocol` |
| SRP complete | POST | `{auth}/signin/complete?isRememberMeEnabled=true` | `accountName`、`c`(回传 challenge)、`m1`=Base64(M1)、`m2`=Base64(M2)、`rememberMe`、`trustTokens=[]` | 靠**状态码**分流,无正文语义 |

`_initialize_srp` 强制要求 `b`/`salt`/`c` 非空且 `iteration > 0`,否则 `invalid_response`(`platforms/icloud/login.py:318-319`)。Base64 解码用 `validate=True` 严格模式(`platforms/icloud/login.py:662-666`)。

`signin/complete` 的状态码分流(`platforms/icloud/login.py:335-349`):

| 状态码 | 含义 | 处理 |
| --- | --- | --- |
| 200 | 密码正确且**无需**双重认证 | 直接返回,后续走 `accountLogin` |
| 409 | 需要双重认证(HSA2) | 置 `_needs_2fa=True`;若此时 `scnt` 或 `X-Apple-ID-Session-Id` 缺失 → `invalid_response("Apple 双重认证响应缺少会话信息")` |
| 412 | 账号需先完成安全设置 | 自动 POST `{auth}/repair/complete`,仅接受 200/204 |
| 401 / 403 | 账号或密码错误 | `ICloudError("invalid_credentials")` |
| 其他 | — | `_status_error` 按码归类 |

##### 1.6 登录状态机主干

`AppleLoginSession` 的三段式(`platforms/icloud/login.py:168-273`):`sign_in()` → `prepare_verification()` → `verify()`。`LoginSessionManager.start()` 把前两段合成一次调用,任何异常都会 `cancel(session.id)` 清掉会话(`platforms/icloud/login.py:556-561`)。

```mermaid
flowchart TD
    A["start(LoginRequest)"] --> A1{"email/password 非空?"}
    A1 -- 否 --> AE["invalid_config"]
    A1 -- 是 --> B["_admit: 并发/频率准入"]
    B -->|超限| BE["provider_rate_limited"]
    B --> C["BuildInfoCache.get 探测构建号"]
    C --> D["GET authorize/signin 拿会话头"]
    D --> E["POST federate 校验账号"]
    E --> F["AppleSRPClient() 生成 a/A"]
    F --> G["POST signin/init"]
    G --> H["解析 salt/b/c/iteration/protocol"]
    H --> I["derive_password: SHA256 + PBKDF2"]
    I --> J["process_challenge: k,u,x,v,S,K,M1,M2"]
    J --> K["POST signin/complete (c,m1,m2)"]

    K -->|401/403| K1["invalid_credentials"]
    K -->|412| K2["POST repair/complete"] --> L
    K -->|200| L["needs_2fa = False"]
    K -->|409| M["needs_2fa = True"]

    L --> Z["_authenticate_with_token"]
    M --> N["GET {auth} 读双重认证状态"]
    N --> O["_trusted_phones 解析手机号"]
    O --> P{"noTrustedDevices?"}
    P -- 否 --> Q["delivery=trusted_devices<br/>PUT verify/trusteddevice/securitycode"]
    P -- 是,1 个号码 --> R["delivery=sms<br/>PUT verify/phone"]
    P -- 是,多个号码 --> S["delivery=sms_selection_required<br/>等 send_sms(phone_id)"]
    P -- 是,0 个号码 --> SE["invalid_config: 无可用 2FA 设备"]

    Q --> T["status=verification_required"]
    R --> T
    S --> S2["send_sms → PUT verify/phone"] --> T
    T --> U["verify(code): 6 位数字校验"]
    U --> V{"_sms_phone_id > 0?"}
    V -- 是 --> W["POST verify/phone/securitycode<br/>(code + phoneNumber.id + mode)"]
    V -- 否 --> X["POST verify/trusteddevice/securitycode<br/>(code)"]
    W --> Y{"ok 或 409+Session-Token?"}
    X --> Y
    Y -- 否 --> YE["invalid_verification_code<br/>(_code_failures++)"]
    Y -- 是 --> Y2["GET 2sv/trust 建立信任"]
    Y2 --> Z
    Z --> Z1["POST {setup}/accountLogin<br/>dsWebAuthToken + trustToken"]
    Z1 --> Z2{"dsInfo.termsUpdateNeeded?"}
    Z2 -- 是 --> Z3["upstream_rejected: 需接受服务条款"]
    Z2 -- 否 --> Z4["导出 cookie_header + validate_cookie_header"]
    Z4 --> Z5{"cookie_header 为空?"}
    Z5 -- 是 --> Z6["invalid_response: 无共享 Session Cookie"]
    Z5 -- 否 --> ZF["status=completed<br/>产出 SessionImportRequest"]
```

##### 1.7 会话头的隐式传递

`_absorb_headers`(`platforms/icloud/login.py:498-509`)在**每一次**响应后抓取并覆盖以下响应头,构成握手的隐式状态:

| 响应头 | 内部字段 | 用途 |
| --- | --- | --- |
| `scnt` | `_scnt` | 后续请求回填 `scnt` 头 |
| `X-Apple-ID-Session-Id` | `_session_id` | 后续回填同名头 |
| `X-Apple-Session-Token` | `_session_token` | `accountLogin` 的 `dsWebAuthToken` |
| `X-Apple-ID-Account-Country` | `_account_country` | `accountLogin` 的 `accountCountryCode` |
| `X-Apple-TwoSV-Trust-Token` | `_trust_token` | `accountLogin` 的 `trustToken`,免二次验证 |
| `X-Apple-Auth-Attributes` | `_auth_attributes` | 回填 `X-Apple-Auth-Attributes` 头 |

##### 1.8 会话生命周期与限流

| 常量 | 值 | 含义 | 位置 |
| --- | --- | --- | --- |
| `LOGIN_TTL_SECONDS` | 600 | 单次登录会话有效期 | `platforms/icloud/login.py:40` |
| `MAX_CONCURRENT_SESSIONS` | 32 | 同时进行中的登录会话上限 | `platforms/icloud/login.py:41` |
| `MAX_CODE_FAILURES` | 6 | 验证码错误次数上限,超出需重新登录 | `platforms/icloud/login.py:42` |
| `MAX_DELIVERIES` | 5 | 单会话验证码发送次数上限(推送与短信各自计数) | `platforms/icloud/login.py:43` |
| `DELIVERY_COOLDOWN_SECONDS` | 30 | 两次发送之间的冷却 | `platforms/icloud/login.py:44` |
| `ATTEMPT_WINDOW_SECONDS` / `MAX_ATTEMPTS_PER_EMAIL` | 600 / 10 | 单邮箱 10 分钟内最多 10 次登录尝试 | `platforms/icloud/login.py:45-46` |
| `GLOBAL_ATTEMPT_WINDOW_SECONDS` / `MAX_GLOBAL_ATTEMPTS` | 60 / 64 | 全局 1 分钟最多 64 次 | `platforms/icloud/login.py:47-48` |

- 每个会话一把 `threading.Lock`,管理器另有一把全局锁;`verify`/`resend`/`send_sms` 都在会话锁内执行(`platforms/icloud/login.py:577-596`)。
- `_purge_expired()` 在 `get()` 与 `_admit()` 时惰性清理过期会话,并调用 `discard_secrets()`(清空 `imap_password` + 关闭 transport)(`login.py:162-164,632-636`)。
- 已完成的会话再次调用 `verify` 会被跳过而非报错(`platforms/icloud/login.py:580-581`),保证前端重复提交幂等。

---

#### 二、双重认证分支

##### 2.1 投递方式判定

`prepare_verification()`(`platforms/icloud/login.py:190-212`)按 `GET {auth}` 返回的状态决策。若响应体没有 `authenticationType` 但有 `phoneNumberVerification` 子对象,则**下钻到该子对象**再解析(`platforms/icloud/login.py:356-358`)——这是 Apple 在不同流程里返回两种结构的兼容处理。

| 条件 | `delivery` 常量 | 动作 |
| --- | --- | --- |
| `_needs_2fa == False` | —(直接完成) | 立即 `_authenticate_with_token()`,状态置 `completed` |
| `noTrustedDevices` 为假 | `trusted_devices` | `PUT verify/trusteddevice/securitycode` 触发可信设备推送 |
| `noTrustedDevices` 为真 且 恰好 1 个手机号 | `sms` | 自动 `PUT verify/phone` 发短信 |
| `noTrustedDevices` 为真 且 多个手机号 | `sms_selection_required` | 不发送,等前端调 `send_sms(phone_id, mode)` |
| `noTrustedDevices` 为真 且 0 个手机号 | — | `invalid_config("Apple 账号没有可用的双重认证设备或手机号")` |

受信任手机号解析(`platforms/icloud/login.py:643-659`):优先 `trustedPhoneNumbers` 数组,退化到单对象 `trustedPhoneNumber`;号码展示优先 `obfuscatedNumber`,退化 `numberWithDialCode`;`pushMode` 缺失时默认 `"sms"`。

##### 2.2 各请求形态

| 操作 | 方法与端点 | 请求体 |
| --- | --- | --- |
| 可信设备推送 | `PUT {auth}/verify/trusteddevice/securitycode` | 无 |
| 发送短信 | `PUT {auth}/verify/phone` | `{"phoneNumber":{"id":<id>},"mode":<mode>}` |
| 校验短信码 | `POST {auth}/verify/phone/securitycode` | `{"securityCode":{"code":"<6位>"},"phoneNumber":{"id":<id>},"mode":<mode>}` |
| 校验设备码 | `POST {auth}/verify/trusteddevice/securitycode` | `{"securityCode":{"code":"<6位>"}}` |
| HSA2 建立信任 | `GET {auth}/2sv/trust` | 无 |
| 换取 Session Cookie | `POST {setup}/accountLogin` | `accountCountryCode`、`dsWebAuthToken`、`extended_login=true`、`trustToken` |

##### 2.3 验证码校验的成功判定

`_validate_code`(`platforms/icloud/login.py:377-386`)不只看 2xx:

1. **接受**:`response.ok`,**或者** `status == 409` 且响应带 `X-Apple-Session-Token` 头(Apple 在部分流程里用 409 携带成功令牌);
2. 408 / 429 / ≥500 → 走 `_status_error`,归类为可重试的上游错误,**不计入错误次数**;
3. 其余一切 → `ICloudError("invalid_verification_code")`,`_code_failures += 1`。

##### 2.4 幂等与重发

- `_code_verified`、`_trusted` 两个布尔位保证:验证码只校验一次、`2sv/trust` 只调一次,`verify()` 被重复调用时只会重跑 `_authenticate_with_token()`(`platforms/icloud/login.py:224-250`)。
- `resend_code()`(`platforms/icloud/login.py:252-264`):`sms_selection_required` 状态下拒绝重发(要求先选号);`_sms_phone_id > 0` 走短信重发,否则重发推送;两条路径都先过 `_check_delivery_limit`。
- `send_sms()` 会校验 `phone_id` 必须在已解析的 `phones` 列表内,防止越权指定号码(`platforms/icloud/login.py:266-273`)。

##### 2.5 错误码总表

`errors.py` 只定义**一个**异常类 `ICloudError`,靠 `code` 字段区分;工厂函数是语义包装。`retry_after` 参与 API 层的退避提示。

| 工厂 / code | 触发场景 | 位置 |
| --- | --- | --- |
| `invalid_config` | 邮箱/密码为空、无可用 2FA 设备、未选手机号、手机号非法 | `platforms/icloud/errors.py:33-34` |
| `invalid_response` | SRP 参数越界、Base64 无效、握手返回缺字段、缺会话令牌、无共享 Cookie | `platforms/icloud/errors.py:37-38` |
| `upstream_unavailable` | 网络异常(`requests.RequestException`)、408、5xx | `platforms/icloud/errors.py:41-42` |
| `upstream_rejected` | 其他非预期状态码;`termsUpdateNeeded` 需接受服务条款 | `platforms/icloud/errors.py:45-46` |
| `rate_limited` / `provider_rate_limited` | HTTP 429;并发会话超限;登录/发送/验证码频率超限 | `platforms/icloud/errors.py:49-50` |
| `session_expired` | 已保存的会话失效 | `platforms/icloud/errors.py:53-54` |
| `invalid_credentials`(直接构造) | `signin/complete` 401/403;`_status_error` 遇 401/403 | `login.py:348,679-680` |
| `invalid_verification_code`(直接构造) | 非 6 位数字;校验接口拒绝 | `login.py:216,386` |
| `login_session_expired`(直接构造) | `login_id` 不存在或已过期 | `platforms/icloud/login.py:569` |

`_status_error`(`platforms/icloud/login.py:677-697`)统一把 HTTP 状态映射成 code,并从响应体的 `errorMessage` / `reason` / `error.message` 三处按序取详情拼进消息。`ICloudError.__str__` 特意处理了 `cause` 为空字符串的情况(如 `cryptography` 的 `InvalidTag`),避免消息尾部出现空括号(`platforms/icloud/errors.py:25-30`)。

---

#### 三、Cookie 导入路径

##### 3.1 两条入口收敛到同一结构

SRP 登录成功与手工粘贴 Cookie 最终都产出同一个 `SessionImportRequest`(`platforms/icloud/models.py:89-103`),再统一交给 `ICloudWebClient.import_session()` 落成 `ICloudCredentials`。

| 入口 | 填充字段 | 位置 |
| --- | --- | --- |
| SRP 登录完成 | `cookie_header`、`validate_cookie_header`、`client_id`、`region` + IMAP 四项 | `platforms/icloud/login.py:149-159` |
| 手工/浏览器导入 | `cookie_header` 或 `cookies_json`,可选 `web_auth_token` + `web_auth_token_header` | `api/icloud.py:213-214` |

登录侧的两个 Cookie 头**作用域不同**(`platforms/icloud/login.py:434-438`):

1. `validate_cookie_header` — 按 `{setup}/validate` 这个 URL 取 Cookie,用于校验会话;
2. `cookie_header` — 按 `home` 去掉 `www.` 前缀后的**根域**取 Cookie,因为 HME 请求走 `icloud.com` 根域,带 `www` 前缀取不到共享 Cookie;为空即 `invalid_response`。

##### 3.2 `normalize_cookies` 接受的格式

`platforms/icloud/cookies.py:62-89`,签名 `normalize_cookies(header="", raw=None)`,返回 `(header, count)`。合并优先级:**`raw` 覆盖 `header`**。

| 写法 | 形态 | 处理 |
| --- | --- | --- |
| A. Cookie 头字符串 | `"a=1; b=2"` | `parse_cookie_header` 按 `;` 切分,`partition("=")` 取首个等号 |
| B. JSON 对象 | `{"name":"value", ...}` | 仅接受字符串值的项 |
| C. 浏览器导出数组 | `[{"name":"x","value":"y", ...}, ...]` | 只读 `name`/`value` 两个键,其余(`domain`/`path`/`expirationDate`/`httpOnly`/`sameSite`)**全部忽略** |
| D. 字符串形态的 JSON | `raw` 是 str | 先 `json.loads`;解析失败则**退化当作 Cookie 头**再解析一次;`""` 或 `"null"` 视为空 |

即 EditThisCookie / Cookie-Editor 这类扩展导出的 JSON 数组可直接粘贴,数组项不是 Mapping 的会被跳过。输出时按名称 `sorted()` 排序拼成 `k=v; k=v`(`platforms/icloud/cookies.py:49-51`),因此结果稳定可比对。

##### 3.3 名称与值的合法性

| 规则 | 实现 |
| --- | --- |
| Cookie 名不得为空,且不含空格、Tab、CR、LF、`;`、`=`、`,` | `is_valid_cookie_name`,`platforms/icloud/cookies.py:16-21` |
| 值过滤:只保留 `0x20 < ord(c) < 0x7F` 且不属于 `"`、`;`、`\` 的字符 | `_sanitize_value`,`platforms/icloud/cookies.py:24-30`(对齐 Go `net/http` 的拒绝规则) |
| 值两端成对的双引号先剥掉再处理 | `_unquote`,`platforms/icloud/cookies.py:42-46` |

##### 3.4 统一加引号(`quote_cookie_header`)

`platforms/icloud/cookies.py:54-59`:先解析、剥引号、过滤非法字节,再**无条件重新加上双引号**输出 `name="value"`。原因写在文件头(`platforms/icloud/cookies.py:3-4`):Apple Web 服务比对的是浏览器登录流程使用的"带引号"表示。这条路径同时修复历史上存进去的未加引号旧值——`tests/test_icloud_cookies.py:44-51` 锁定了 `session=legacy-value` → `session="legacy-value"`,且值里的 `v=2:t=token~~` 内部等号与波浪号必须原样保留。

##### 3.5 有效性校验

`import_session` 的校验链(`platforms/icloud/web_client.py:61-147`):

1. 归一化后 `cookie_header` 与 `web_auth_token` **同时为空**则 `invalid_config("需要 Cookie 或 Web 会话令牌")`;
2. `POST {setup}/validate`,请求体是字面量字符串 `"null"`,`Content-Type: text/plain;charset=UTF-8`,query 带 `clientBuildNumber` / `clientMasteringNumber` / `clientId`;
3. 网络异常 → `upstream_unavailable`;非 2xx → `http_error`;
4. `_ensure_terms_accepted(payload)` 检查是否待接受服务条款;
5. 从响应体取两个**必需**字段,缺任一即 `invalid_response`:
   - `dsInfo.dsid` → 账号 DSID;
   - `webservices.premiummailsettings.url` → HME 服务地址;
   - 可选 `webservices.mccgateway.url` → 旧版 Mail 网关;两个 URL 都过 `_normalize_service_url` 校验;
6. `merge_response_cookies` 把响应里 Apple 轮换的 Cookie 合并回来,再 `quote_cookie_header` 定形;
7. 账号邮箱从 `dsInfo.primaryEmail` → `appleId` → `appleID` 依次兜底取(`platforms/icloud/web_client.py:136-140`)。

`cookie_count` 只在导入摘要里回显,`ImportedSession.public_summary()` 只暴露 `masked_dsid` / `service_host` / `cookie_count`,不回传 Cookie 原文(`platforms/icloud/models.py:114-119`)。

##### 3.6 响应 Cookie 的合并规则(易错点)

`merge_response_cookies`(`platforms/icloud/cookies.py:98-110`)对每个响应 Cookie 判定:

1. 名称合法;
2. **必须是共享根 Cookie**:`path == "/"` 且 `domain` 去掉前导点后**恰好等于**区域根域(`icloud.com` 或 `icloud.com.cn`)(`_is_shared_icloud_cookie`,`platforms/icloud/cookies.py:92-95`);
3. `expires` 已过期 → 从集合里**删除**该 Cookie;
4. 否则覆盖写入(剥引号 + 过滤非法字节)。

被拒绝的三类:host-only(无 domain)、子路径(如 `/setup/ws/1/`)、跨区域域名(`global` 区域下的 `.icloud.com.cn`)。`tests/test_icloud_cookies.py:54-67` 逐条锁定了这些行为。设计意图是:扁平化的单一 Cookie 头无法表达域/路径维度,所以只允许对所有子域都成立的根 Cookie 参与更新,否则会把子域专用值污染到全局。

##### 3.7 关键 Cookie 名

代码不维护"必需 cookie 名"白名单——有效性由 `{setup}/validate` 的真实响应裁决。被显式按名引用的只有:

| Cookie 名 | 用途 | 位置 |
| --- | --- | --- |
| `X-APPLE-WEBAUTH-USER` | 解析 DSID(大小写不敏感匹配) | `platforms/icloud/cookies.py:113-127` |
| `X-APPLE-WEBAUTH-TOKEN` | 出现在加引号测试向量中,承载 `v=2:t=...~~` 会话令牌 | `tests/test_icloud_cookies.py:46` |

`dsid_from_cookie_header`(`platforms/icloud/cookies.py:113-127`)的解析顺序:先 `urllib.parse.unquote` 解百分号编码(浏览器导出常见 `v=1%3As=1%3Ad=...`),再剥引号,然后按 `rfind(":d=")` 定位(取**最后一次**出现,避免值里前面也有 `:d=`),否则接受 `d=` 前缀开头,再否则返回空;截到下一个 `:` 为止,**必须全是数字**才返回。`X-APPLE-WEBAUTH-USER="v=1:s=1"`(无 `d=` 段)返回空字符串,见 `tests/test_icloud_cookies.py:70-77`。DSID 也可从 `validate` 响应直接取,`platforms/icloud/web_client.py:346` 用 Cookie 里的值做交叉校验。

---

#### 四、凭据存储

##### 4.1 数据库落库模型

`core/db.py:97-112`,表名 `icloud_accounts`。**所有密文集中在一个字段**,其余列都是可明文查询的元数据。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int PK | — |
| `email` | str,index + **unique** | 主号 Apple ID,统一小写(`services/icloud_service.py:265`) |
| `display_name` | str | 界面备注名 |
| `region` | str,默认 `global` | `global` / `china`,决定端点与 Cookie 域 |
| `status` | str,默认 `active` | 导入成功时置 `active`(`services/icloud_service.py:282`) |
| `enabled` | bool,默认 True | 停用后 `resolve_account` 拒绝使用 |
| `credentials_cipher` | str | **AES-256-GCM 信封**,内容见下表 |
| `sync_error` | str | 最近一次同步的错误消息,导入成功时清空 |
| `last_sync_at` | datetime? | 最近同步时间 |
| `created_at` / `updated_at` | datetime | — |

##### 4.2 `credentials_cipher` 明文字段表

密文内容是 `ICloudCredentials.to_dict()` 的 JSON(`platforms/icloud/credentials.py:15-49`),即整个凭据对象都在密文里:

| 字段 | 默认 | 敏感度 | 来源与用途 |
| --- | --- | --- | --- |
| `region` | `global` | 低 | 区域,决定端点 |
| `dsid` | `""` | 中 | 账号数字 ID,取自 `validate` 的 `dsInfo.dsid` |
| `cookies` | `""` | **高** | 扁平化并加引号的 Web Session Cookie 头,**等同登录态** |
| `hme_service_url` | `""` | 低 | `webservices.premiummailsettings.url`,HME 请求基址 |
| `mail_gateway_url` | `""` | 低 | `webservices.mccgateway.url`,旧版 Mail 网关 |
| `client_id` | `""` | 低 | 会话 UUID,登录时生成并全程复用 |
| `client_build_number` / `client_mastering_number` | `""` | 低 | iCloud 主站构建号 |
| `mail_client_build_number` / `mail_client_mastering_number` | `""` | 低 | Mail 子站构建号 |
| `ckjs_build_version` | `""` | 低 | CloudKit JS 版本 |
| `web_auth_token` | `""` | **高** | 可替代 Cookie 的会话令牌(手工导入路径) |
| `web_auth_token_header` | `""` | 低 | 上述令牌要放进哪个请求头 |
| `imap_host` | `""` | 低 | 默认 `imap.mail.me.com`(`platforms/icloud/constants.py:28`) |
| `imap_port` | `0` | 低 | 默认 993 |
| `imap_username` | `""` | 中 | 通常即主号邮箱 |
| `imap_password` | `""` | **最高** | **Apple 应用专用密码**,长期有效、可读全部邮件 |
| `sync_limit` | 50 | 低 | 单次同步条数,上限 200(`platforms/icloud/constants.py:30-31`) |
| `extra_headers` | `{}` | 低 | 附加请求头 |

`from_dict` 用字段白名单过滤未知键,并强制 `imap_port` / `sync_limit` 为 int(`platforms/icloud/credentials.py:37-46`),因此新增字段向后兼容、删除字段不会因旧密文报错。

对外只暴露布尔状态,绝不回传原文——`public_state()` 返回 `has_session_cookies` / `has_dsid` / `has_hme_service_url` / `has_mail_gateway` / `has_web_auth_token` / `has_imap_credentials`(`platforms/icloud/credentials.py:69-78`)。`tests/test_icloud_service.py:101` 直接断言 `"app-specific" not in row.credentials_cipher`,即应用专用密码不得以明文出现在库里。

两个便捷判定:`has_web_session` 要求 `cookies`、`dsid`、`hme_service_url` 三者齐备;`has_imap` 只看 `imap_password` 非空白(`platforms/icloud/credentials.py:61-67`)。

##### 4.3 重新登录时的凭据合并

`merged_with`(`platforms/icloud/credentials.py:51-59`)语义:以**新会话为基准**,但若新请求没提交 `imap_password`,则把旧的 `imap_host` / `imap_port` / `imap_username` / `imap_password` 四项整体搬过来。落库处 `services/icloud_service.py:274-278` 据此实现"重新登录不丢 IMAP 配置"。注意判定只看 `imap_password` 一个字段——只改主机名不改密码的提交会被整组回滚成旧值。

##### 4.4 AES-256-GCM 的具体用法

`core/secret_box.py`:

| 参数 | 值 | 位置 |
| --- | --- | --- |
| 算法 | AES-256-GCM(`cryptography` 的 `AESGCM`) | `secret_box.py:18,81` |
| 密钥长度 | 32 字节 | `core/secret_box.py:24` |
| nonce | **每次加密 `secrets.token_bytes(12)` 随机生成** | `secret_box.py:25,85` |
| AAD | `None`(不使用附加认证数据) | `secret_box.py:86,96` |
| 认证标签 | 由 `AESGCM.encrypt` 附加在密文尾部(GCM 默认 16 字节) | — |
| 信封前缀 | 字面量 `v1:` | `core/secret_box.py:26` |

编码格式:

```
credentials_cipher = "v1:" + base64( nonce[12] || ciphertext || tag[16] )
```

`encrypt`(`core/secret_box.py:84-87`)把 nonce 与密文**拼接后整体 Base64**,而不是分两个字段。`decrypt`(`core/secret_box.py:89-96`)反向校验:前缀不是 `v1:` → `CredentialKeyError("凭据密文格式无法识别")`;解码后长度 ≤ 12 → `CredentialKeyError("凭据密文长度不足")`;随后切前 12 字节作 nonce。`encrypt_json` / `decrypt_json` 在外层套 JSON,`ensure_ascii=False` 保留中文原文;`decrypt_json` 对空串返回 `{}`,便于处理未初始化账号(`core/secret_box.py:98-104`)。

`SecretBox` 惰性初始化并用双重检查锁保护(`core/secret_box.py:73-82`),密钥只在首次加解密时读取一次并缓存到进程内。模块底部导出单例 `secret_box`(`core/secret_box.py:107`)。

**密钥轮换即数据不可读**:GCM 校验失败抛 `cryptography.exceptions.InvalidTag`,`tests/test_docker_credential_key_persistence.py:68-77` 显式固化了这个后果。

##### 4.5 密钥来源与优先级

`_load_or_create_key()`(`core/secret_box.py:52-67`)按顺序:

1. **环境变量 `CREDENTIAL_ENCRYPTION_KEY`**(`core/secret_box.py:20`)——非空则直接用,不再看文件。接受 **Base64 或 Hex** 编码,解码后必须**恰好 32 字节**,否则 `CredentialKeyError`;两种编码依次尝试(`_decode_key`,`core/secret_box.py:33-44`)。
2. **密钥文件**:路径取 `CREDENTIAL_ENCRYPTION_KEY_FILE` 环境变量,未设置则回落到 `Path.cwd() / ".secrets" / "credential_key"`(`secret_box.py:22,47-49`)。**注意是 `cwd()` 而非仓库根目录**,换工作目录启动会读到另一把密钥。
3. **自动生成**:文件不存在时 `secrets.token_bytes(32)`,创建父目录,再用 `os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` **先以 0600 建文件再写入**,避免密钥短暂对其他用户可读(`core/secret_box.py:61-67`);文件内容是 Base64 文本。

优先级带来的运维含义:一旦设过环境变量再取消,系统会转而读/生成密钥文件,已有密文全部失效。

##### 4.6 解密失败的用户可见处理

`load_credentials`(`services/icloud_service.py:79-90`)捕获一切解密异常,转成 `ICloudError("credentials_unreadable", "iCloud 主号 {email} 的凭据无法解密(加密密钥已变更),请重新登录该主号")`。裸抛 `InvalidTag` 只会得到一个无信息的 500。列表接口更宽容:`_account_to_dict` 单独兜住异常,把该账号的 `credential_state` 置为 `{"credentials_unreadable": True}`,保证一个坏账号不会让整个列表接口失败(`services/icloud_service.py:142-147`)。

##### 4.7 Docker 密钥持久化的坑

`tests/test_docker_credential_key_persistence.py:1-10` 把部署约束写成了测试。问题链条:默认密钥路径 `Path.cwd()/.secrets/credential_key` 在容器里是 `/app/.secrets/credential_key` —— **属于镜像层,不是挂载卷**。`docker compose up --build` 重建容器会生成新密钥,而数据库还在卷里,于是所有 `credentials_cipher` 变成解不开的密文,且不可恢复,用户只能重新登录全部主号。

四条强制约束(全部有测试守着):

| 约束 | 覆盖对象 | 测试 |
| --- | --- | --- |
| Dockerfile 必须设 `CREDENTIAL_ENCRYPTION_KEY_FILE`,且路径以 `/runtime/` 开头 | `Dockerfile`、`Dockerfile.server` | `:32-39` |
| compose 的 `services.app.environment` 必须含同一变量且指向 `/runtime/` | `docker-compose.yml`、`docker-compose.server.yml` | `:42-47` |
| compose 必须真的把 `/runtime` 挂成卷(否则指向它没意义) | 同上 | `:50-59` |
| entrypoint 必须准备密钥目录 | `docker/entrypoint.sh`、`docker/entrypoint.server.sh` | `:62-65` |

第三条测试特意用 `str(v).split(":")` 逐段匹配,因为宿主机一侧可能写成 `${APP_RUNTIME_BIND:-./data}`,冒号不能当字段分隔符(`:55-57`)。

---

#### 五、支撑模块解决的问题

##### 5.1 `constants.py` — 区域与身份常量

| 问题 | 解法 |
| --- | --- |
| 中国大陆与国际站是**两套独立域名** | `RegionEndpoints`(`platforms/icloud/constants.py:55-76`)按区域给出 `auth` / `setup` / `home` / `origin` 四个基址;`normalize_region` 把 `cn`、`china`、`icloud.com.cn` 都归一到 `china`(`:79-82`) |
| Cookie 域也随区域变 | `cookie_domain_for` 返回 `icloud.com.cn` 或 `icloud.com`(`:89-90`) |
| Apple 只认自家 Web 客户端 | 固定 macOS Safari 18.3.1 的 `USER_AGENT`(`:10-13`)与 iCloud Web 的 `OAUTH_CLIENT_ID`(`:14`) |
| 构建号会随发版过期 | 四个 `FALLBACK_*` 兜底常量,注释明确写了更新方法:从 `www.icloud.com` 页面的 `data-cw-private-build-number` 属性取新值(`:16-23`) |

##### 5.2 `build_info.py` — 客户端版本伪装

Apple Web 服务校验请求里的 `clientBuildNumber` / `clientMasteringNumber`,**过期即拒绝且不说明原因**(`platforms/icloud/build_info.py:3-4`)。

| 机制 | 实现 |
| --- | --- |
| 探测源 | 主站 `{origin}/` 取 cloud 构建号;`{origin}/applications/mail2/current/en-us/index.html?rootDomain=www` 取 mail 构建号(`:30,127-128`) |
| 解析方式 | `HTMLParser` 抓**第一个同时带** `data-cw-private-build-number` 与 `data-cw-private-mastering-number` 的元素(`:48-72`) |
| 页面体积上限 | 1 MiB(`MAX_PAGE_BYTES`,`:29,148`) |
| 重试 | 每个页面重试 2 次,理由是跨境访问 Apple 偶发超时(`FETCH_ATTEMPTS`,`:31,141-151`) |
| 缓存 | 按区域缓存,TTL 300 秒,`threading.Lock` 保护(`:28,84-101`) |
| 三级降级 | 探测成功 → 用新值;探测失败但**有过期缓存** → 用旧值 + WARNING;探测失败且无缓存 → 用内置常量 + WARNING,并在日志里点明"Apple 可能因构建号过期拒绝请求"(`:103-118`) |
| 真伪标记 | `BuildInfo.discovered` 布尔位,让调用方能区分真探测值与兜底值(`:43-45`) |
| 主动失效 | `invalidate(region)` 供上游报错后强制重探(`:120-123`) |

`is_official_service_url`(`:75-78`)校验 Apple 返回的服务地址主机名必须是 `icloud.com` / `icloud.com.cn` 或其子域,防止响应里的 URL 把请求引到第三方主机。

##### 5.3 `transport.py` — 会话、请求头与错误归一化

| 关注点 | 实现 |
| --- | --- |
| 不受环境变量代理干扰 | `session.trust_env = False`,代理只能由显式参数经 `build_requests_proxy_config` 注入(`platforms/icloud/transport.py:17-24`) |
| 超时 | 默认 30 秒(`DEFAULT_TIMEOUT_SECONDS`),`request()` 用 `setdefault` 保证每个请求都带 timeout(`:68-74`) |
| 浏览器指纹头 | `web_headers` 统一发 `Origin`、`Referer`、`Accept-Language: zh-CN,...`、`Sec-Fetch-Site/Mode/Dest`(`:35-55`) |
| Origin 推断 | 有 region 用 region;否则从 service_url 里嗅 `icloud.com.cn`(`web_origin`,`:27-32`) |
| 生命周期 | 实现 `__enter__` / `__exit__`,`client.web_client()` 用上下文管理器按请求创建并释放连接(`:82-86`,`platforms/icloud/client.py:33-39`) |
| `Retry-After` | 同时支持纯秒数与 HTTP-date 两种格式(`parse_retry_after`,`:89-101`) |
| HTTP 错误映射 | `http_error` 按状态码给稳定 code;**Mail 服务的 401/403 单独映射为 `mail_access_denied`** 并明确"不表示 HME 会话失效",避免误导用户重登(`:104-133`) |
| 诊断信息 | `_diagnostic` 附上 Host、Content-Type、Apple 请求 ID(四个候选头依次取)、响应体字节数,便于对账(`:136-157`) |
| 业务错误映射 | `envelope_error` 从 JSON 信封的 `code`/`errorCode` 与消息文本里按关键字识别限流("reached the limit of addresses" 等)与会话失效("session expired"/"invalid global session" 等)(`:160-196`) |
| JSON 取值工具 | `nested_value` / `nested_string` 按路径取;`find_string` 深度优先找首个非空字符串,应对 Apple 响应结构不稳定(`:219-255`) |

登录侧的请求头由 `platforms/icloud/login.py:465-496` 的 `_auth_headers()` 单独构造(不走 `web_headers`),固定发出 14 个 `X-Apple-*` 头,其中值得注意的:

| 头 | 值 | 作用 |
| --- | --- | --- |
| `X-Apple-Widget-Key` / `X-Apple-OAuth-Client-Id` | `OAUTH_CLIENT_ID` | 声明为 iCloud Web 首方客户端 |
| `X-Apple-OAuth-Client-Type` | `firstPartyAuth` | 走首方登录流程 |
| `X-Apple-OAuth-Response-Mode` / `-Type` | `web_message` / `code` | 与 `authorize/signin` 的 query 保持一致 |
| `X-Apple-OAuth-State` / `X-Apple-Frame-Id` | `auth-<uuid>` | 同一会话内两处必须相同(`platforms/icloud/login.py:108`) |
| `X-Apple-I-FD-Client-Info` | 紧凑 JSON:`{"U":<UA>,"L":"zh-CN","Z":"GMT+08:00","V":"1.1","F":""}` | **设备指纹伪装**;`separators=(",",":")` 去空格以匹配浏览器序列化格式;`F`(指纹)留空 |
| `X-Apple-I-Require-UE` / `X-Apple-Mandate-Security-Upgrade` | `true` / `0` | 声明客户端能力,规避强制升级流程 |
| `X-Requested-With` | `XMLHttpRequest` | 声明 AJAX 请求 |

`MAX_RESPONSE_BYTES = 2 MiB`(`platforms/icloud/transport.py:14`)界定了响应体的处理上限。

---

#### 六、风险与脆弱点

##### 6.1 完全依赖 Apple 未公开接口

| 风险点 | 具体表现 |
| --- | --- |
| 端点无契约 | `signin/init`、`signin/complete`、`verify/*`、`2sv/trust`、`accountLogin`、`setup/ws/1/validate` 全部是 iCloud Web 前端的私有接口,Apple 可随时改路径、字段或状态码语义,且无版本号、无变更公告 |
| 状态码承载语义 | `signin/complete` 靠 200/409/412/401 分流(`platforms/icloud/login.py:335-349`);`_validate_code` 甚至要把 **409 + `X-Apple-Session-Token`** 当成功(`platforms/icloud/login.py:379-381`)。这类约定极易随上游调整而静默失效 |
| 响应结构不稳定 | 代码里已有多处兼容分支:`phoneNumberVerification` 下钻(`platforms/icloud/login.py:356-358`)、`trustedPhoneNumbers` 与 `trustedPhoneNumber` 双形态(`platforms/icloud/login.py:644-647`)、账号邮箱三键兜底(`platforms/icloud/web_client.py:136-140`)、`find_string` 深度搜索(`platforms/icloud/transport.py:219`)。这些兼容层本身就是上游不稳定的证据 |
| 构建号是定时炸弹 | 兜底常量 `2630Build35` / `2630Hotfix39` 一定会过期;探测依赖 Apple 页面上 `data-cw-*` 属性继续存在。页面改版后会退化到兜底常量,表现为难以归因的"iCloud HME 拒绝了请求"(`platforms/icloud/build_info.py:110-117`) |
| 指纹伪装可被识别 | 固定 UA(Safari 18.3.1)与 `X-Apple-I-FD-Client-Info` 里空的 `F` 字段,与真实浏览器有差异;Apple 若加强风控可直接据此拦截 |

##### 6.2 SRP 自实现的正确性风险

| 风险点 | 说明 |
| --- | --- |
| 手写大整数运算 | `pow`、模减、编码全部自己实现(`platforms/icloud/srp.py:106-134`),没用经过审计的 SRP 库;Apple 的 NoUsernameInX 变体也无公开规范可核对 |
| 两种整数编码混用 | `k`/`u` 用 256 字节零填充,`A`/`B`/`S`/`N` 用最小长度编码。这是最容易在重构中改错的地方,改错的表现是 Apple 静默返回 401,难以定位 |
| 测试只锁自身输出 | `tests/test_icloud_srp.py` 的向量注明"与 Go 版实现保持一致"(`:1`),即它们验证的是**与另一份自研实现互一致**,不是与 Apple 官方规范一致。两份实现同时错会一起通过 |
| `M2` 由客户端计算并上传 | `signin/complete` 把客户端算出的 `m2` 一并提交(`platforms/icloud/login.py:326-333`),而 SRP 中 M2 本应由服务端产生供客户端校验。代码里 `constant_time_equals`(`platforms/icloud/srp.py:138`)在登录路径上**并未被调用**,因此没有对服务端身份做密码学验证 —— 服务端认证完全依赖 TLS |
| 缺少 `B mod N == 0` 之外的部分检查 | 已校验 `B ∈ (0, N)`、`u ≠ 0`、`base ≠ 0`、`S ≠ 0`(`platforms/icloud/srp.py:102-120`),覆盖较全;但 `salt` 长度、`iteration` 下界(仅 > 0)未设合理最小值,理论上 Apple 若返回 `iteration=1` 会被接受 |
| 时序侧信道 | `pow()` 与大整数运算非常数时间,本地进程内影响有限,但不是恒定时间实现 |

##### 6.3 凭据敏感度与密钥管理

| 风险点 | 说明 |
| --- | --- |
| IMAP 应用专用密码敏感度最高 | 它长期有效、可读取账号**全部邮件**,且撤销必须去 Apple 账号页手工操作。它与 Web Session 同存一个 `credentials_cipher` 字段,没有分级加密或独立作用域 |
| Web Session Cookie 等同登录态 | `cookies` 字段是完整的会话 Cookie 头,泄露即可直接操作 HME;代码没有做绑定 IP / 设备的额外约束 |
| 单一密钥、全库同密 | 所有账号共用一把进程级密钥,没有 per-record 密钥或 KMS 信封;密钥泄露即全库沦陷。无密钥轮换机制——换密钥等于让所有密文永久失效(`tests/test_docker_credential_key_persistence.py:68-77`) |
| GCM 未使用 AAD | 加密调用传 `None` 作为附加认证数据(`core/secret_box.py:86`),因此密文与它所属的 `account_id` / `email` **没有密码学绑定**。具备数据库写权限的攻击者可以把 A 账号的密文整段搬到 B 账号行上,解密仍会成功 |
| 密钥文件路径依赖 `cwd()` | 默认路径是 `Path.cwd()/.secrets/credential_key`(`core/secret_box.py:49`),不是仓库根目录。从不同工作目录启动进程会读到/生成不同密钥,静默导致凭据不可读 |
| 密钥文件权限只在创建时设定 | `os.open(..., 0o600)` 只保护**自己新建**的文件(`core/secret_box.py:64`);若文件已存在且权限被放宽(如 umask 异常、手工 `chmod`、容器卷挂载带来的宽松权限),代码不会检查也不会告警 |
| 环境变量密钥可被观测 | `CREDENTIAL_ENCRYPTION_KEY` 走环境变量,会出现在 `/proc/<pid>/environ`、`docker inspect`、compose 文件与 CI 日志里,泄露面比文件更宽 |
| 无静态强度校验 | `_decode_key` 只校验长度 32 字节,不检查熵;把 32 个相同字节当密钥也会被接受(`core/secret_box.py:33-44`) |

##### 6.4 登录流程本身的脆弱点

| 风险点 | 说明 |
| --- | --- |
| 明文密码在内存中传递 | 虽不落盘(`platforms/icloud/login.py:1-5`),但 `LoginRequest.password` 是普通 str,存活于 GC 堆上,无法主动擦除;`discard_secrets()` 只清 `imap_password` 并关闭 transport(`platforms/icloud/login.py:162-164`) |
| 登录会话是纯进程内状态 | `LoginSessionManager` 用内存字典(`platforms/icloud/login.py:546`);进程重启或多副本部署时,`login_id` 会失效或路由到没有该会话的副本。**该实现不支持水平扩展** |
| 限流同为进程内 | 全局与按邮箱的尝试计数都在内存(`platforms/icloud/login.py:547-548`),多副本部署下实际限流阈值被放大到副本数倍,增加触发 Apple 侧封禁的风险 |
| 会话上限可被占满 | `MAX_CONCURRENT_SESSIONS = 32`,过期靠惰性清理(`_purge_expired` 只在 `get`/`_admit` 时跑),空闲期不会主动回收,可被并发请求占满导致合法登录被拒 |
| 错误消息可能泄露上游细节 | `_status_error` 把 Apple 返回的 `errorMessage` 原文拼进错误消息(`platforms/icloud/login.py:687-696`),再由 API 层下发,存在把上游内部信息暴露给前端的可能 |
| 手工 Cookie 导入无来源校验 | `normalize_cookies` 只做格式与字符合法性检查,对 Cookie 的实际来源与新鲜度不做判断,完全依赖 `{setup}/validate` 的响应裁决 |
| DSID 解析靠字符串切割 | `dsid_from_cookie_header` 用 `rfind(":d=")` 手工切分 Apple 的私有 Cookie 值格式(`platforms/icloud/cookies.py:113-127`),格式一变即静默返回空字符串 |

##### 6.5 建议的加固方向(不改变现有行为)

1. 加密时把 `account_id` 或 `email` 作为 **AAD** 传入 `AESGCM.encrypt/decrypt`,消除密文跨行搬移的可能;需要一次带兼容分支的迁移。
2. 启动时**校验密钥文件权限**(非 0600 即告警或拒绝启动),并把默认路径从 `cwd()` 改为显式配置项。
3. 把 IMAP 应用专用密码与 Web Session 拆成两个密文字段,便于独立轮换与最小权限读取。
4. 在登录路径上真正使用 `constant_time_equals` 校验服务端返回的 M2,补上 SRP 的双向认证语义。
5. 给 `BuildInfo.discovered == False` 的情况加上可观测指标(而非仅 WARNING 日志),让构建号过期能被提前发现。
6. 若需多副本部署,先把登录会话与限流计数外移到共享存储(如 Redis)。

---

## 十一、iCloud 别名生成与收件箱

### iCloud 平台：Hide My Email 别名生成与收件箱

本文覆盖 iCloud 平台的**别名生成、别名收件箱、免登录分享页、服务层与 HTTP 接口、插件契约**五块。Apple ID 的 SRP 登录、双重认证、Cookie 导入、凭据加解密不在本文范围（见同系列的登录文档）。

#### 一、Hide My Email 别名生成

##### 1.1 Apple 内部接口清单

所有 HME 调用都由 `platforms/icloud/web_client.py:220` 的 `_hme_request` 统一出口，基址来自登录时 `setup/ws/1/validate` 返回的 `webservices.premiummailsettings.url`（`platforms/icloud/web_client.py:105`），不是硬编码域名——Apple 会按账号分配 `p1-maildomainws.icloud.com` 这类分片主机。

| 动作（action） | 方法 | 请求体 | 作用 | 调用点 |
|---|---|---|---|---|
| `setup/ws/1/validate` | POST | `null` | 校验 Cookie，取回 `dsInfo.dsid`、HME 服务地址、`mccgateway` 旧版 Mail 地址、主号邮箱 | `platforms/icloud/web_client.py:92` |
| `v2/hme/list` | GET | 无 | 拉取主号名下全部隐私邮箱（含历史、含已停用） | `platforms/icloud/web_client.py:152` |
| `v1/hme/generate` | POST | `{"langCode":"en-us"}` | 让 Apple **提议**一个地址，尚未占用 | `platforms/icloud/web_client.py:166` |
| `v1/hme/reserve` | POST | `{"hme","label","note"}` | **真正占用**该地址并写入 Apple 侧，返回 `anonymousId` | `platforms/icloud/web_client.py:173` |
| `v1/hme/deactivate` | POST | `{"anonymousId"}` | 停用（Apple 要求删除前先停用） | `platforms/icloud/web_client.py:212` |
| `v1/hme/delete` | POST | `{"anonymousId"}` | 从 Apple 侧彻底删除 | `platforms/icloud/web_client.py:216` |

URL 拼装见 `platforms/icloud/web_client.py:298` 的 `_hme_endpoint`：在服务基址路径后接 action，query 串固定带 `dsid`、`clientBuildNumber`、`clientMasteringNumber`，可选带 `clientId` / `ckjsBuildVersion`，并按 key 排序保证可复现。

请求头由 `platforms/icloud/transport.py` 的 `web_headers()` 生成，`Content-Type` 一律伪装成 `text/plain;charset=UTF-8`（Apple Web 前端就是这么发的，用 `application/json` 反而容易被拒）。Cookie 经 `quote_cookie_header()` 规整后放进 `Cookie` 头。

##### 1.2 构建号（clientBuildNumber）与自愈

- 兜底常量在 `platforms/icloud/constants.py:20`：`FALLBACK_CLOUD_BUILD = "2630Build35"` 等四个值。这组值**会随 iCloud 发版过期，过期后 Apple 直接拒绝且不说原因**。
- 正常路径由 `platforms/icloud/build_info.py` 的 `BuildInfoCache` 从 `www.icloud.com` 页面的 `data-cw-private-build-number` 属性动态探测；探测失败会打 WARNING 并把 `discovered` 标为 `False`（测试：`tests/test_icloud_hme_rejection_handling.py:157`）。
- `platforms/icloud/web_client.py:280` 的 `_with_current_builds`：只有当服务地址是 Apple 官方域名（`is_official_service_url`）时才强制覆盖为最新探测值；自建反代沿用凭据里保存的旧值，避免把用户的私有部署改坏。

##### 1.3 生成 → 保留 → 落库完整步骤

1. `POST /api/icloud/aliases`（`api/icloud.py`）→ `services.icloud_service.generate_alias(account_id, label, note)`。
2. **取主号级互斥锁**：`services/icloud_service.py:324` `with _account_lock(account_id)`。同一主号的生成串行化，防止并发注册互相挤占小时额度。
3. 校验主号存在且 `enabled`，否则 `account_disabled`（`services/icloud_service.py:326`）。
4. **本地配额预检**：`alias_quota()` 算滚动一小时窗口，`remaining <= 0` 直接抛 `provider_rate_limited`，**不打 Apple 接口**（`services/icloud_service.py:328`）。
5. 解密凭据：`load_credentials(row)` → `secret_box.decrypt_json`。解密失败映射成 `credentials_unreadable`，提示“重新登录该主号”（`services/icloud_service.py:79`）。
6. **标签兜底**：`label.strip() or DEFAULT_ALIAS_LABEL`（默认 `"隐私邮箱"`）。Apple 的 reserve 不接受空标签，会回 `{"errorCode":"400","errorMessage":"invalid Label"}`，界面上标签是选填的，所以在 `platforms/icloud/web_client.py:164` 兜住。
7. `POST v1/hme/generate` → 从响应里用 `find_string(generated, "hme","address","email")` 取地址（Apple 字段名不稳定，三个 key 都试）。取不到抛 `invalid_response`。
8. `POST v1/hme/reserve`，带 `{hme,label,note}`。响应里若带地址则以响应为准（`address = find_string(reserved,...) or address`）。
9. 组装 `PrivateEmail`：地址 `strip().lower()`、`status=active`、`provider_id = find_string(reserved,"anonymousId","id","identifier","hmeId")`、`created_at=utcnow()`。
10. `_upsert_alias()` 落库（`services/icloud_service.py:626`），**按 address 全表唯一**去重，首次落库时补 `share_token`。

```mermaid
flowchart TD
    A["POST /api/icloud/aliases"] --> B["generate_alias()"]
    B --> C{"取主号锁<br/>_account_lock(account_id)"}
    C --> D{"主号 enabled?"}
    D -- 否 --> E["account_disabled"]
    D -- 是 --> F{"alias_quota().remaining > 0?"}
    F -- 否 --> G["provider_rate_limited<br/>不请求 Apple"]
    F -- 是 --> H["load_credentials() 解密"]
    H -- 解密失败 --> I["credentials_unreadable"]
    H --> J["label 为空则填 '隐私邮箱'"]
    J --> K["POST v1/hme/generate<br/>Apple 提议地址"]
    K -- success:false --> K2["重试一次 + 失效构建号缓存"]
    K2 --> K
    K --> L{"响应含地址?"}
    L -- 否 --> M["invalid_response"]
    L -- 是 --> N["POST v1/hme/reserve<br/>{hme,label,note} 真正占用"]
    N -- success:false --> O["绝不重试<br/>直接抛 upstream_rejected"]
    N --> P["解析 anonymousId → provider_id"]
    P --> Q["_upsert_alias() 按 address 去重落库"]
    Q --> R["首次落库补 share_token<br/>secrets.token_urlsafe(16)"]
    R --> S["返回别名 JSON"]
```

##### 1.4 每小时 5 个的限流实现位置

限流是**本地按主号维度自己算的**，不依赖 Apple 回错：

| 要素 | 实现 |
|---|---|
| 上限常量 | `services/icloud_service.py:43` `HOURLY_ALIAS_LIMIT = 5` |
| 计数函数 | `services/icloud_service.py:164` `alias_quota(account_id)` |
| 窗口 | **滚动一小时**：`since = _utcnow() - timedelta(hours=1)`，不是自然小时对齐 |
| 计数口径 | `ICloudAliasModel` 中 `account_id` 匹配且 `created_at >= since` 的**行数**（`len(recent)`） |
| 剩余 | `max(HOURLY_ALIAS_LIMIT - used, 0)` |
| 重置时刻 | 窗口内**最早**一条的 `created_at + 1 小时` |
| 并发保护 | `services/icloud_service.py:57` 进程内 `_ACCOUNT_LOCKS: dict[int, threading.Lock]`，`_ACCOUNT_LOCKS_GUARD` 保护字典本身 |
| 拒绝点 | `services/icloud_service.py:329`，错误码 `provider_rate_limited` → HTTP 429（`api/icloud.py:44`） |
| 对外暴露 | `_account_to_dict()` 里把 `quota` 挂在主号 JSON 上（`services/icloud_service.py:143`），前端可直接显示剩余额度 |

两个要注意的口径问题：

- 时区：查询用 `since.replace(tzinfo=None)`，即把 UTC 时间去掉 tzinfo 后与库里的 naive datetime 比较。库里必须存 UTC naive，否则窗口会偏。
- **`sync_aliases` 拉回来的历史别名也会计入配额**。同步刚落库的远端别名 `created_at` 由 `_upsert_alias` 走 `ICloudAliasModel` 默认值（本地当前时间），一次大批量同步会把本小时额度瞬间"用完"，产生假限流。

##### 1.5 被 Apple 拒绝时的处理

Apple 的拒绝分两层，处理策略完全不同。

**第一层：HTTP 状态码**（`platforms/icloud/transport.py:104` `http_error`）

| 状态 | 业务码 | 备注 |
|---|---|---|
| 401 / 403 | `session_expired`（Mail 服务则为 `mail_access_denied`） | Mail 的 403 特意区分：只表示 Mail 未开通/子会话未授权，**不代表 HME 会话失效** |
| 429 | `provider_rate_limited` | 带 `Retry-After` 解析 |
| 408 / 5xx | `upstream_unavailable` | |
| 400 / 422 | `upstream_rejected` | |
| 其它 | `upstream_error` | |

所有错误都会附 `_diagnostic()`：Host、Content-Type、**Apple Request ID**（`X-Apple-Request-UUID` 等四个头轮询）、响应正文字节数——这是向 Apple/社区排查的唯一线索。

**第二层：HTTP 200 但信封 `success:false`**（`platforms/icloud/web_client.py:271`、`platforms/icloud/transport.py:160` `envelope_error`）

- 先**无条件把 Apple 原始信封记 ERROR 日志**（截断 600 字节）：`logger.error("iCloud HME 拒绝了 %s，原始响应: %s", ...)`。原因是面向用户的文案刻意收敛（限流不回显上游原文），排查只能靠日志。测试：`tests/test_icloud_hme_rejection_handling.py:143` 断言 `ZONE_NOT_ENABLED` 出现在日志里。
- 再按关键词映射业务码：`reached the limit of addresses` / `too many requests` / `rate limit` → `provider_rate_limited`；`session expired` / `invalid session` / `invalid global session` / `authentication failed` → `session_expired`；`cannot connect to icloud` → `upstream_rejected` 且提示"确认区域正确、已开通 iCloud+"；上游码 408/5xx → `upstream_unavailable`；兜底 `upstream_rejected`。

**幂等性驱动的重试白名单**（`platforms/icloud/web_client.py:51`）

```python
_RETRIABLE_HME_ACTIONS = frozenset({"v2/hme/list", "v1/hme/generate"})
```

只有这两个动作在 `upstream_rejected` 时重试**一次**，因为 `list` 是纯读、`generate` 只是让 Apple 提议地址；`reserve`/`deactivate`/`delete` 有副作用，重试会凭空多出或多删一个别名。重试前顺带 `self._builds.invalidate(region)` 强制重新探测构建号——万一真是构建号过期，这一步能自愈。

对应测试（`tests/test_icloud_hme_rejection_handling.py`）：

| 测试 | 断言 |
|---|---|
| `test_transient_rejection_of_generate_is_retried_once`:90 | generate 第一次 `success:false`、第二次成功；共 2 次请求；`cache.invalidated == 1` |
| `test_reserve_is_never_retried`:109 | reserve 只发 1 次请求，`invalidated == 0` |
| `test_delete_is_never_retried`:122 | delete 只发 1 次请求 |
| `test_persistent_rejection_still_surfaces_after_the_retry`:132 | 重试后仍失败则抛出，总请求 2 次 |
| `test_rejection_logs_apples_raw_envelope`:143 | 原始 error code 进日志 |
| `test_discovery_failure_is_logged_and_marked_as_fallback`:157 | 构建号探测失败打 WARNING、`discovered=False` |

此外 `_ensure_terms_accepted`（`platforms/icloud/web_client.py:478`）单独拦一种情况：`dsInfo.termsUpdateNeeded == True` 时抛 `upstream_rejected`，提示"先在浏览器接受最新服务条款"。这个错不在 HME 阶段暴露，而在 validate 阶段就拦住，否则后面所有 HME 调用都会莫名失败。

##### 1.6 别名字段用途

`core/db.py` 的 `ICloudAliasModel`：

| 字段 | 来源 | 用途 |
|---|---|---|
| `account_id` | 生成时传入 | 归属主号；配额统计、删除主号级联、IMAP 收件时定位主号凭据 |
| `address` | Apple `hme`/`address`/`email` 字段，`strip().lower()` | **本地去重主键**（`_upsert_alias` 按 address 全表查找，不带 account_id 条件）；IMAP 过滤依据 |
| `label` | 用户填或 `DEFAULT_ALIAS_LABEL` | Apple 侧的备注标签，reserve 必填；本地只作展示 |
| `note` | 用户填，可空 | Apple 侧备注，纯展示 |
| `status` | Apple 的 `isActive`/`active`/`enabled` 布尔或 `status` 文本推导 | `active` / `disabled`。删除时决定是否先调 `deactivate`（已 disabled 就跳过） |
| `provider_id` | Apple `anonymousId`（回退 `id`/`identifier`/`hmeId`） | **删除/停用的唯一凭证**。为空时 `delete_private_email` 会先 `list` 一遍反查；仍找不到就抛 `upstream_rejected`，提示"请先同步后重试" |
| `share_token` | `new_alias_share_token()` = `secrets.token_urlsafe(16)` | 免登录分享页的唯一凭证，见第三节 |

字段解析全部走大小写不敏感 + 多 key 回退（`_scalar`、`find_string`），因为 Apple 未公开接口的字段名在不同区域/版本下不一致。

##### 1.7 同步远端已有别名：去重与回写

`services/icloud_service.py:341` `sync_aliases(account_id)`：

1. `get_account` + `load_credentials`。
2. `client.list_private_emails(credentials)` → `GET v2/hme/list`。
3. **上游侧去重**在 `platforms/icloud/web_client.py:361` `_parse_private_email_list`：先从 `hmeEmails` 或 `hmeList`（大小写不敏感）里取数组，用 `seen: set[str]` 按规整后的地址跳过重复条目；地址为空/条目非 Mapping 直接抛 `invalid_response`（宁可整体失败也不落半份脏数据）。
4. **本地侧去重与回写**在 `_upsert_alias`：按 `address` 查已有行，有则更新、无则新建。回写策略是**保守合并**：
   - `label` / `note` / `provider_id`：`str(payload.get(x) or "") or row.<x>`——上游为空时**保留本地旧值**，不会被空值覆盖。
   - `status`：无条件以上游为准（`str(payload.get("status") or ALIAS_STATUS_ACTIVE)`）。
   - `share_token`：`row.share_token or new_alias_share_token()`——**已有 token 永不轮换**，分享链接稳定。
   - `account_id`：无条件覆写为本次同步的主号（同一地址换主号时会跟着搬家）。
5. 统计：`created`（新建数）/ `updated`（更新数）/ `fetched`（上游总数），返回带 `synced_at`。
6. **成功则清空 `sync_error`，失败则记录**：`_record_sync_error(account_id, str(exc))` 后 `raise`（`services/icloud_service.py:348`）。

注意 `_upsert_alias` 的 address 查询**不带 account_id 过滤**，所以同一个隐私邮箱地址在整库只可能有一行；多主号场景下同步会把行的 `account_id` 改成最后一次同步的主号。

#### 二、别名收件箱：主号 IMAP 拉取

##### 2.1 总体思路

Apple 把发往 Hide My Email 地址的邮件**转发到主号收件箱**，Hide My Email 本身不是一个可登录的信箱。所以收件不走任何 Web 接口，而是**用主号的 App 专用密码登录 `imap.mail.me.com:993`，拉取主号 INBOX，再按投递头把邮件归到某个别名上**（`platforms/icloud/mailbox.py:1` 文件头注释明确了这个分工：Web Session 只负责 HME 管理，收件全走 IMAP）。

##### 2.2 IMAP 连接参数

`resolve_imap_target()`（`platforms/icloud/mailbox.py:47`）先把参数校验成一个 `IMAPTarget`：

| 参数 | 默认值 / 来源 | 校验 |
|---|---|---|
| host | `credentials.imap_host` 或 `DEFAULT_IMAP_HOST = "imap.mail.me.com"` | 不允许含 `://` 或 `/?#\r\n\t` 空格；允许 `host:port` 内联形式（自动拆出端口）；去掉 IPv6 的 `[]` |
| port | `credentials.imap_port` 或内联端口 或 `DEFAULT_IMAP_PORT = 993` | 必须 1–65535 |
| username | `credentials.imap_username` 或**回退主号邮箱** | 不能为空 |
| password | `credentials.imap_password`（App 专用密码，非 Apple ID 密码） | 不能为空 |
| timeout | `DEFAULT_TIMEOUT_SECONDS = 30` | |
| limit | 入参 → `credentials.sync_limit` → `DEFAULT_SYNC_LIMIT = 50`，再夹到 `[1, MAX_SYNC_LIMIT=200]` | |

连接固定用 `imaplib.IMAP4_SSL`（隐式 TLS，`platforms/icloud/mailbox.py:137`），没有 STARTTLS 分支。错误映射：

- 建连失败（`OSError` / `IMAP4.error`）→ `upstream_unavailable`
- 登录失败（`IMAP4.error`）→ **`invalid_credentials`**，文案"用户名或 App 专用密码错误"；登录失败也会先 `logout()` 再抛
- `_Connection` 是个上下文管理器，`__exit__` 里静默 `logout()`，保证连接不泄漏

##### 2.3 检索与搜索条件

关键点：**没有用 IMAP `SEARCH`**。流程是（`platforms/icloud/mailbox.py:87`）：

1. `client.select('"INBOX"', readonly=True)` —— **只读打开**，不会把邮件标记成已读。
2. 从 `SELECT` 响应里取 `UIDVALIDITY`，为 0 就抛 `invalid_response`（`platforms/icloud/mailbox.py:167`）；消息总数取自 `SELECT` 返回的 `EXISTS`。
3. `message_count == 0` 直接返回空列表。
4. **按序号区间取最近 N 封**：`first = max(message_count - limit + 1, 1)`，然后一次 `FETCH first:last (UID FLAGS INTERNALDATE BODY.PEEK[])`。
   - 用 `BODY.PEEK[]` 而不是 `BODY[]`，同样是为了不置 `\Seen`。
   - 用**序号区间**而非 UID 区间，因此这是"最近 limit 封"的语义，不是增量同步。每次调用都全量重拉这 N 封。
5. `_iter_fetch_items()` 从 FETCH 描述符里用正则 `rb"UID\s+(\d+)"` 抠 UID，`INTERNALDATE "..."` 抠内部时间（`imaplib.Internaldate2tuple` + `calendar.timegm` → UTC）。
6. 邮件唯一标识：`provider_message_id = f"{mailbox}:{uid_validity}:{uid}"`。带上目录名和 UIDVALIDITY 是为了目录不同或服务端重置 UIDVALIDITY 时不会撞 ID。
7. 结果排序：`(received_at, uid)` **倒序**，最新在前。

代价：过滤在**客户端**做。要找某个别名的邮件，必须先把主号最近 50（或 limit）封全下载解析再筛。如果主号收件箱噪声大，某个冷门别名的邮件很容易被挤出窗口。

##### 2.4 按别名地址过滤：看哪些头

优先级由 `platforms/icloud/constants.py:45` 的 `DELIVERY_HEADER_NAMES` 定义，**不是只看 `To`**：

```python
DELIVERY_HEADER_NAMES = (
    "Delivered-To", "X-Original-To", "Original-Recipient",
    "Envelope-To", "X-Envelope-To", "X-Forwarded-To",
)
```

`platforms/icloud/delivery.py:36` 的 `delivery_addresses()` 按 **`已知别名` → 上面 6 个投递头（按序） → `To` 头里的可见收件人** 收集候选地址，去重且保序。

为什么必须这样：Apple 转发时 `To` 头里往往还是隐私邮箱地址，但也有发信方直接写主号、或收件人只出现在信封（envelope）里的情况；`Delivered-To` 才是转发链路留下的真实信封收件人。所以真实收件人**优先从投递头取，`To` 只是最后的回退**。

两个解析细节：

- `_addresses_in()`（`platforms/icloud/delivery.py:15`）先用标准 `parse_address_list`；解析不出来时，按 `,` 切分并对每段取 `;` 之后的部分——因为 `Original-Recipient` 这类头常写成 `rfc822;user@example.com`，标准解析器识别不了。
- `delivered_to()`（`platforms/icloud/delivery.py:52`）双向都规整成小写后做集合交集判断；`recipient` 本身也过一遍 `_addresses_in`，所以传 `"Foo <a@b.c>"` 这种形态也能匹配。

匹配成功后会**把 `parsed.alias_address` 强制改写成请求的 `recipient`**（`platforms/icloud/mailbox.py:114`），保证返回给前端的归属地址是调用方要的那个，而不是候选列表里第一个。

`_parse_message` 里存进 `MailMessage.headers` 的只有这 6 个投递头加 `Message-Id`（`platforms/icloud/mailbox.py:231`），其余头一律丢弃——既省内存，也避免把主号相关的敏感头带到免登录分享页。

##### 2.5 正文解析

`_walk_parts()`（`platforms/icloud/mailbox.py:261`）：

1. 遍历 `message.walk()`，跳过 multipart 容器节点。
2. 有 `filename` 或 `Content-Disposition: attachment` → 标记 `has_attachments=True` 并跳过，**附件内容不下载不解析**。
3. 只收 `text/plain` 与 `text/html`，其余 content-type 忽略。
4. `_decode_part()` 用 `get_payload(decode=True)` + `get_content_charset()`（默认 utf-8）解码，`errors="replace"`；charset 不认识（`LookupError`）时退回 utf-8。
5. 同类型多段用 `"\n\n"` 拼接。
6. `snippet`：优先纯文本，纯文本为空则 `strip_html(html_body)`，再 `truncate_text(..., 240)`。
7. 主题走 `make_header(decode_header(...))` 解 MIME 编码字（`=?utf-8?B?...?=`），异常时原样返回。
8. 时间：`received_at = INTERNALDATE → Date 头 → 当前时间`；`sent_at` 只来自 `Date` 头。`Date` 缺时区时按 UTC 补齐（`platforms/icloud/mailbox.py:315`）。
9. `is_read = "\\Seen" in flags`。
10. `message_from_bytes` 抛任何异常时该封直接返回 `None` 被跳过——单封坏邮件不会毁掉整次拉取。

**注意：`mailbox.py` 里没有 OTP 提取逻辑。** 它只负责把邮件解析成结构化的 `MailMessage`。OTP 提取在上层（见 2.6）。

##### 2.6 与 ChatGPT 注册流程的对接：iCloud 别名可以当 ChatGPT 注册邮箱池

对接层是 `services/chatgpt_otp_mailbox.py`，其中 `ICloudAliasMailProvider`（`services/chatgpt_otp_mailbox.py:30`）把 iCloud 别名适配成 ChatGPT 侧的 `MailProvider` 协议（`platforms/chatgpt/protocol.MailProvider`）。

| 契约点 | 实现 |
|---|---|
| `kind` / `display_name` | `"icloud_alias"` / `"iCloud 隐私邮箱"` |
| `accepts_existing_account` | `True`——**支持给库里的老号补 RT**，不只用于新注册 |
| `create_mailbox()` | 直接返回已有的别名地址，**不新建**（别名是预先生成好的） |
| 取信 | `_fetch_messages()` → `services.icloud_service.fetch_alias_messages(alias_id, limit=20)`，即走 2.1–2.4 那条 IMAP 链路 |
| OTP 提取 | `extract_otp(self._searchable_text(item))`，`extract_otp` 来自 `platforms/chatgpt/protocol`。搜索文本是 `subject + text_body + html_body + snippet` 拼接（`services/chatgpt_otp_mailbox.py:138`） |
| 去重 | `self._seen` 存 `provider_message_id`；`prime()` 在等码前先把现有邮件全记进基线，避免把上一轮旧码当本轮 |
| 轮询 | `POLL_INTERVAL_SECONDS = 5.0`，`wait_for_otp(timeout=120)` 到点抛 `TimeoutError` |
| 时间窗 | `cutoff = issued_after - 30`——**Apple 转发有延迟，窗口刻意放宽 30 秒**，否则常把唯一那封码信误判成旧信（`services/chatgpt_otp_mailbox.py:81`） |
| 可中断 | `_checkpoint()` 调 `task_control.checkpoint()`；`_sleep()` 按 0.25 秒碎步睡，让"停止任务"几百毫秒内生效而不是等满一个 5 秒轮询 |

**路由入口**：`resolve_otp_mail_provider(email, ...)`（`services/chatgpt_otp_mailbox.py:149`）按可靠性从高到低试三个 builder：

1. `_build_icloud_provider` —— 拿地址去 `ICloudAliasModel` 里查（`services/chatgpt_otp_mailbox.py:212`），命中就返回 `ICloudAliasMailProvider`。**iCloud 别名是第一优先**。
2. `_build_outlook_provider` —— 微软号池里的 OAuth/IMAP 凭据。
3. `_build_configured_provider` —— 按域名匹配当前配置的邮箱服务。

三条路都返回 `(provider, reason)`，全落空时把 reason 用 `；` 拼起来返回 `None`，由调用方决定硬试还是放弃。命中后统一 `_bind_task_control()` + `_prime()`。

结论：**iCloud 隐私邮箱可以直接作为 ChatGPT 注册与补 RT 的邮箱池**，适配层就是 `ICloudAliasMailProvider`，唯一前提是主号配了 IMAP App 专用密码。

##### 2.7 另一条用法：把隐私邮箱导进 MailAPI URL 号池

上面的 `ICloudAliasMailProvider` 走的是 **IMAP**（主号 App 专用密码 → `imap.mail.me.com`）；同一批别名还有第二条取信路径——「导入 MailAPI 号池」（`POST /api/icloud/aliases/import-to-pool`，见 4.4.1）把免登录页链接写进微软号池，注册时由 `MailApiUrlOtpBackend` 轮询该 URL 抠码。

| 维度 | `ICloudAliasMailProvider`（2.6） | MailAPI URL 号池（本节） |
|---|---|---|
| 凭据 | 主号 IMAP App 专用密码 | 免登录链接里的 `share_token`（无需密码） |
| 取信 | IMAP 直连 Apple | HTTP GET 面板自己的 `/m/<token>` 页 |
| 落库位置 | `icloud_aliases`（本来就在） | `outlook_accounts`，`account_type=mailapi_url` |
| 选号 | `resolve_otp_mail_provider` 按地址反查，**第一优先** | 走微软号池，受 `mail_import_source` 视图筛号约束 |
| 何时用 | 主号配好了 IMAP、按地址补信 | 只有 Web 会话、没有 IMAP 密码；或想统一让注册任务从号池领号 |

两条路不冲突，都依赖同一个 `share_token` / 主号凭据，选哪条看主号有没有 IMAP 密码与任务侧想怎么取号。

#### 三、免登录分享页（`api/shared_mail.py`）

##### 3.1 为什么不带 `/api` 前缀

`main.py:84` 的 `auth_middleware` 判定逻辑是：

```python
if path.startswith("/api/auth/") or not path.startswith("/api/"):
    # 放行
```

即**鉴权中间件只拦 `/api/*`**。`main.py:131` 单独一行 `app.include_router(shared_mail_router)`——不带 `prefix="/api"`，与其他 14 个路由全部带前缀形成对比，注释也写明"不带 /api 前缀就绕开了鉴权中间件"。

路由最终是 `GET /m/{share_token}`（`api/shared_mail.py:149`），并且 `include_in_schema=False`，不出现在 OpenAPI 文档里。

**这条链接有第二个消费者**：「导入 MailAPI 号池」（`POST /api/icloud/aliases/import-to-pool`）把 `http(s)://<面板地址>/m/<token>` 当成取码地址写进 `outlook_accounts.mailapi_url`，注册任务运行时由 `MailApiUrlOtpBackend` 反复 GET 它抠验证码。也就是说免登录页从"人点开看的分享页"变成了"机器轮询的取码端点"——3.4 节列的那几个缺失（无过期、无撤销、无限流）在这里的后果更直接：每个取号中的注册任务都会按 OTP 超时窗口反复打这个地址。链接必须从**面板自己的对外地址**拼，所以前端传 `window.location.origin`、服务端退回配置项 `public_base_url`（`services/icloud_service.py:436`）。

##### 3.2 share_token 的生成与安全边界

| 项 | 内容 |
|---|---|
| 生成 | `core/db.py` 的 `new_alias_share_token()` = `secrets.token_urlsafe(16)` |
| 熵 | 16 字节 = **128 位**随机（url-safe base64 后 22 个字符） |
| 模型 | `ICloudAliasModel.share_token`，`default_factory` + `index=True`（`core/db.py:127`） |
| 补写 | `_upsert_alias` 里 `row.share_token = row.share_token or new_alias_share_token()`——只在为空时补，**永不轮换** |
| 老数据迁移 | `_migrate_icloud_aliases_schema()`（`core/db.py:220`）：SQLite 下先 `ALTER TABLE ... ADD COLUMN share_token TEXT DEFAULT ''`，再**逐行**补随机值。注释点明不能用一条 UPDATE，因为每行必须是不同的随机值 |
| 查询 | `fetch_latest_shared_message()`（`services/icloud_service.py:692`）按 `share_token` 精确查一行 |

安全模型是**"链接即权限"（capability URL）**：不用自增 id（注释：用 id 谁都能从 1 数到 100），128 位随机不可枚举。但也意味着：

- **拿到链接的任何人都能读该别名的最新邮件**，包括收到过的 OTP。
- 返回信息刻意收窄：只回**一封**最新邮件 + 别名地址，拿不到主号邮箱、其它别名、历史邮件（`services/icloud_service.py:695` 注释"只认 token、只回一封"）。虽然内部 `fetch_account_messages` 取了 50 封，但只把 `messages[0]` 交出去。

##### 3.3 页面渲染方式

**纯服务端渲染的自包含 HTML**，没有前端框架、没有 XHR：

- `_page()`（`api/shared_mail.py:58`）拼一整个 `<!doctype html>` 文档，样式 `_PAGE_STYLE` 内联在 `<style>` 里（配色跟面板暗色主题同一套值），返回 `HTMLResponse`。
- 响应头：`Cache-Control: no-store`（链接会被转发，不能让中间层缓存别人的邮件）、`X-Robots-Tag: noindex, nofollow`，页面里也有 `<meta name="robots" content="noindex, nofollow">`。
- 三种页面状态：正常邮件（主题/发件人/发往地址/时间 + 正文 + "刷新"按钮）、`还没有邮件`、错误页 `_error_page()`。
- 所有插值都过 `html.escape()`：地址、主题、发件人、时间、错误详情。

**邮件正文的 XSS 隔离**（`api/shared_mail.py:72` `_body_frame`）—— 这是全文安全设计最讲究的一处：

1. HTML 正文包进一个完整子文档，子文档头里带 `<meta http-equiv="Content-Security-Policy" content="script-src 'none'">`。
2. 整段再 `html.escape(..., quote=True)` 后塞进 `<iframe srcdoc="...">`。
3. iframe 的 `sandbox="allow-same-origin"` —— **刻意不给 `allow-scripts`**，框里的 HTML 只能当静态内容渲染。给 `allow-same-origin` 仅为让外层脚本量高度；脚本已被禁用，框内拿不到任何东西。
4. 外层一小段 `fit()` 脚本按 `contentDocument.body.scrollHeight` 调 iframe 高度（立即 + 300ms 后各量一次）。
5. 没有 HTML 正文时退回 `text_body`/`snippet`，走 `html.escape()` + `white-space:pre-wrap` 纯文本渲染；都为空则显示"这封邮件没有正文"。

错误处理分三档（`api/shared_mail.py:151`）：`alias_not_found` → 404「链接无效」；其它 `ICloudError` → 502「暂时读不到邮件」并把 `str(error)` 显示出来 + WARNING 日志；裸 `Exception` → 500 通用文案 + `logger.exception`，注释明确"免登录页面不能把栈打给外面"。

##### 3.4 速率限制 / 过期 / 撤销：都没有（明确风险）

逐条核对代码，结论如下：

| 机制 | 现状 | 风险 |
|---|---|---|
| **速率限制** | **无**。`/m/{share_token}` 不经鉴权中间件，`api/shared_mail.py` 里也没有任何计数或限流 | 可被**无限制暴力枚举**（虽然 128 位熵下枚举不现实），更现实的是**放大攻击**：每次请求都会触发一次真实的 IMAP 登录 + FETCH 最近 50 封（`fetch_latest_shared_message` 默认 `limit=DEFAULT_MESSAGE_LIMIT=50`）。反复刷同一个合法链接就能把主号打到 Apple 的 IMAP 连接/频率限制，甚至触发风控 |
| **过期时间** | **无**。`share_token` 没有 `expires_at` 字段，页面也不检查任何时间 | 链接**永久有效**。一次分享等于永久授予该别名的读信权 |
| **撤销 / 轮换** | **无**。全仓没有重置 token 的接口；`_upsert_alias` 的 `row.share_token or ...` 保证已有 token 不会被覆盖 | **泄露后唯一的止损方式是删除整个别名**（`DELETE /api/icloud/aliases/{id}`），代价是连带删掉 Apple 侧的隐私邮箱 |
| **访问审计** | **无**。只在出错时打日志，成功访问不记录 | 无法发现链接是否被第三方读过 |
| 已有的缓解 | `no-store`、`noindex`、只回一封、只回地址、iframe 无脚本沙箱 | 这些降低了泄露后的**范围**，但不改变"链接永久有权限"这个事实 |

这是本模块最需要注意的设计取舍：**为了免登录分享的便利，接受了一个无过期、无撤销、无限流的 capability URL**。若要收紧，最小改动是给 `ICloudAliasModel` 加 `share_expires_at` 与一个"重置 token"接口，并在 `/m/` 路由上按 token + IP 做简单计数。

#### 四、服务层与 HTTP 接口

##### 4.1 `services/icloud_service.py` 的职责划分

它是 `api/` 与 `platforms/icloud/` 之间的**唯一**中间层。分工边界很清楚：

| 只在 service 层做的事 | 说明 |
|---|---|
| **凭据加解密** | `load_credentials()` 走 `secret_box.decrypt_json`，`import_session()` 走 `encrypt_json`。**上层 api 与下层 platforms 都拿不到明文**（模块 docstring 明确"调用方永远拿不到原文"） |
| **数据库读写** | 所有 `Session(engine)` 都在这里；`api/icloud.py` 里没有一处 DB 访问，`platforms/icloud/` 也完全不碰 DB（web_client 只认传进来的 `ICloudCredentials`） |
| **配额与并发控制** | `HOURLY_ALIAS_LIMIT`、`alias_quota()`、`_ACCOUNT_LOCKS` |
| **主号解析** | `resolve_account(email)`：给了邮箱按邮箱找并校验 `enabled`；没给就取 **id 最小的 enabled 主号**（`services/icloud_service.py:111`） |
| **DTO 组装** | `_account_to_dict()` / `_alias_to_dict()`，决定哪些字段能出网 |
| **隐私邮箱入池** | `import_aliases_to_mailapi_pool()` `:529`：把别名拼成 `隐私邮箱----邮件 URL` 交给 `MicrosoftMailImportStrategy`（见 4.4.1），并顺带把 `mail_import_source` 切到 `mailapi`。拼行、选行、判重口径的前置逻辑拆在 `normalize_public_base_url()` `:421` / `resolve_public_base_url()` `:436` / `build_alias_mailapi_lines()` `:448` / `_list_pool_emails()` `:470` / `_resolve_pool_alias_ids()` `:479` / `_apply_mail_import_source()` `:509`，都是为了能脱离 HTTP 层单测 |
| **登录会话的薄转发** | `start_login`/`login_state`/`verify_login`/`resend_login_code`/`send_login_sms`/`cancel_login` 全是一行转发给 `login_manager()`，不含逻辑 |

##### 4.2 主号会话的缓存与失效重登

这里的设计比较特殊，**没有"会话对象缓存"**：

| 层次 | 生命周期 |
|---|---|
| `BuildInfoCache` / `LoginSessionManager` | **进程级单例**（`platforms/icloud/client.py:21`），构建号缓存与进行中的登录会话在这里 |
| `ICloudWebClient` + `WebTransport` | **每次调用现建现销**：`with web_client(proxy=...) as client`（contextmanager，退出即释放连接池）。`generate_alias`/`sync_aliases`/`delete_alias`/`check_valid` 各自建一个 |
| Web Session 本体（Cookie/DSID/服务地址） | **只存在数据库密文里**，每次操作都 `load_credentials()` 解密一次 |
| IMAP 连接 | 每次 `fetch_inbox` 建连 + `logout`，无连接复用 |

后果与含义：

- 没有内存会话可"失效"，Cookie 失效表现为 Apple 回 401/403 → `session_expired`（HTTP 409）。**没有自动重登**——`ICloudCredentials` 里存的是 Cookie，不是密码，服务层没有能力静默重新走 SRP。用户必须重新走 `POST /icloud/login-sessions` 或 `POST /icloud/accounts/import-cookie`。
- 重新登录时 `import_session()` 会 `load_credentials(row).merged_with(credentials)`（`services/icloud_service.py:278`）：新会话覆盖旧 Cookie，但**本次没显式提交 IMAP 密码时保留旧的 IMAP 配置**（`platforms/icloud/credentials.py:51`），用户不必每次重登都重填 App 专用密码。
- 每次操作都解密 + 建连，换来的是"不会用到过期的内存副本"和"多进程/多 worker 下天然一致"，代价是每次生成别名都有一次解密 + 一次 TLS 握手开销。

##### 4.3 `sync_error` 与 `last_sync_at` 的回写

统一走 `_record_sync_error(account_id, message)`（`services/icloud_service.py:655`），一次写四个字段：

| 字段 | 写入规则 |
|---|---|
| `sync_error` | 即传入的 message（成功时传 `""` 清空） |
| `status` | `"error" if message else "active"` |
| `last_sync_at` | **无条件**写当前 UTC 时间（成功失败都写，语义是"最后一次尝试同步的时刻"） |
| `updated_at` | 当前 UTC 时间 |

调用点只有两处，都在 `sync_aliases()` 里：拉列表抛 `ICloudError` → 记错误后 `raise`（`services/icloud_service.py:349`）；全部 upsert 完成 → `_record_sync_error(account_id, "")`（`services/icloud_service.py:358`）。

另外 `import_session()` 会直接把 `row.sync_error = ""`、`row.status = "active"`（`services/icloud_service.py:282`），即**重新登录成功等于清除同步错误状态**。

`_account_to_dict()` 把 `sync_error`、`last_sync_at`（ISO 格式）、`credential_state`、`quota` 一起返回；`load_credentials` 失败时 `credential_state` 退化为 `{"credentials_unreadable": True}` 而不是抛异常（`services/icloud_service.py:144`），保证列表页不会因为一个坏主号整体 500。

##### 4.4 `api/icloud.py` 完整接口清单（18 个）

路由前缀 `/icloud`，`main.py:127` 挂在 `/api` 下，所以实际路径都带 `/api/icloud`。下表路径列为 router 内相对路径。

| # | 方法 | 路径 | 处理函数 | 作用 |
|---|---|---|---|---|
| 1 | POST | `/login-sessions` | `start_login` `api/icloud.py:144` | 用 Apple ID + 密码发起 SRP 登录；返回登录状态（可能是 `verification_required`）。完成时**顺带落库主号** |
| 2 | GET | `/login-sessions/{login_id}` | `get_login` `api/icloud.py:165` | 轮询登录会话状态 |
| 3 | POST | `/login-sessions/{login_id}/verify` | `verify_login` `api/icloud.py:173` | 提交双重认证 6 位验证码；成功即落库主号 |
| 4 | POST | `/login-sessions/{login_id}/resend` | `resend_login_code` `api/icloud.py:181` | 重发验证码到受信设备 |
| 5 | POST | `/login-sessions/{login_id}/sms` | `send_login_sms` `api/icloud.py:189` | 改用短信下发验证码（指定 `phone_id`） |
| 6 | DELETE | `/login-sessions/{login_id}` | `cancel_login` `api/icloud.py:197` | 取消登录会话、销毁内存态。**唯一不做 ICloudError 捕获的接口** |
| 7 | GET | `/accounts` | `list_accounts` `api/icloud.py:206` | 主号列表，含 `alias_count`、`quota`、`credential_state`、`sync_error` |
| 8 | POST | `/accounts/import-cookie` | `import_cookie` `api/icloud.py:211` | 手工粘 Cookie（header 或 JSON）导入主号，同时可带 IMAP 配置 |
| 9 | PATCH | `/accounts/{account_id}` | `update_account` `api/icloud.py:231` | 启用/停用主号（`{"enabled": bool}`） |
| 10 | DELETE | `/accounts/{account_id}` | `delete_account` `api/icloud.py:239` | 删除主号，**级联删除其名下所有本地别名行**（不动 Apple 侧） |
| 11 | POST | `/accounts/{account_id}/sync` | `sync_account` `api/icloud.py:248` | 从 Apple 拉全量别名合并到本地，返回 `fetched/created/updated/synced_at` |
| 12 | GET | `/accounts/{account_id}/messages` | `account_messages` `api/icloud.py:256` | 主号 IMAP 收件，可选 `limit`（默认 50）与 `recipient`（按别名过滤） |
| 13 | GET | `/aliases` | `list_aliases` `api/icloud.py:270` | 别名列表，可选 `account_id` 过滤；按 id 倒序；**返回 `share_token`** |
| 14 | POST | `/aliases` | `generate_aliases` `api/icloud.py:275` | 批量生成 1–5 个别名（`count` 被 `Field(ge=1, le=5)` 卡住），逐个串行调 `generate_alias` |
| 15 | POST | `/aliases/batch-delete` | `batch_delete_aliases` `api/icloud.py:290` | 批量删除；空 `ids` 直接 400；返回 `{ok, deleted, failed}` |
| 16 | POST | `/aliases/import-to-pool` | `import_aliases_to_pool` `api/icloud.py:298` | 把隐私邮箱导进 MailAPI URL 号池，等价于「导出 `mail_url` 再手工导入」。`ids` 为空则按 `account_id`（再缺省则全部主号）全量导；`origin` 是前端传来的浏览器地址栏 origin，用来拼免登录链接（见 4.4.1） |
| 17 | DELETE | `/aliases/{alias_id}` | `delete_alias` `api/icloud.py:312` | 删除单个别名，`remote=true`（默认）时同时注销 Apple 侧 |
| 18 | GET | `/aliases/{alias_id}/messages` | `alias_messages` `api/icloud.py:321` | 取该别名收到的邮件（内部转成主号 IMAP + `recipient` 过滤） |

（第 19 个对外入口是免登录页 `GET /m/{share_token}`，在 `api/shared_mail.py:150`，不在本 router 内。）

##### 4.4.1 16 号接口：隐私邮箱直接导进 MailAPI 号池

免登录链接就是 `隐私邮箱----<面板地址>/m/<share_token>` 这一行，和前端导出 `mail_url` 格式逐字一致（`frontend/src/lib/icloud.ts:37` `formatAliasExport`），所以后端不需要另写一套落库逻辑，直接把拼好的文本喂给现成的导入器：

```python
strategy = mail_import_registry.get("microsoft")          # services/icloud_service.py:583
response = strategy.execute(MailImportExecuteRequest(
    type="microsoft", content="\n".join(lines),
    enabled=bool(enabled), bind_to_config=False,          # 微软策略不读 bind_to_config，传 False 只为表意
))
```

于是查重规则（`DuplicateMicrosoftMailboxRule` / `RegisteredMicrosoftMailboxRule`）、`account_type=mailapi_url` 落库、错误文案全部与手工导入**逐字节相同**，差别只在内容由后端按 `share_token` 现拼。关键取舍：

| 点 | 做法 | 为什么 |
|---|---|---|
| 面板地址从哪来 | 前端 POST `window.location.origin` → `normalize_public_base_url()` 只留 `scheme://host[:port]`；不合法或没传则退回配置项 `public_base_url`（`services/icloud_service.py:421/436`） | 反代、内网穿透、换端口部署下服务端自己猜不出对外地址；导出的链接是要贴进号池、之后由注册任务去轮询的，存一个拼不起来的串比不存更糟 |
| 没有 `share_token` 的老别名 | 整行跳过，进 `skipped_aliases`（与前端 `countExportableAliases` 同一口径） | 写成末尾空着的 `邮箱----` 只会让导入器报格式错 |
| 勾选行与主号筛选 | `_resolve_pool_alias_ids()` **先按 `account_id` 缩范围再按 id 过滤** | 勾选的行可能是「全部主号」之外筛选出来的，不能拿 id 越界取别的主号的别名 |
| 导入完成后切视图 | 只在 `summary.success > 0` 时把 `mail_import_source` 写成 `mailapi`（`_apply_mail_import_source()` `:509`） | 取号按视图筛 `account_type`（见 6.6），视图停在 Outlook 的话刚导进去的地址一个都取不到；但一条都没进池（全被查重挡下）时不该动用户的设置 |
| 已在池里的怎么报 | 不算失败，但追加一句「N 个隐私邮箱早就在号池里了」（`:602`） | 否则用户点了没反应会以为是坏了 |

**批量生成的标签编号**：`_alias_label(label, index, count)`（`api/icloud.py:100`）在 `count > 1` 时给标签加序号（`隐私邮箱 1`、`隐私邮箱 2`…），避免 Apple 侧一串同名地址分不清。

**注意 14 号接口与限流的交互**：`count` 上限 5 恰好等于 `HOURLY_ALIAS_LIMIT`，但它是**逐个调用**且每次都重新查配额。若本小时已用掉 3 个，请求 `count=5` 会成功生成 2 个后在第 3 个抛 429，整个请求返回 429，**前 2 个已落库的别名不会回滚**。

##### 4.5 错误码 → HTTP 状态映射

`api/icloud.py:32` 的 `_ERROR_STATUS`，未列出的一律 502。两个刻意的决定值得记住：

- **一律不用 401**。401 是面板自身的登录态语义，前端见到就清 token 跳登录页。iCloud 主号凭据问题是"Apple 那边不认"，不是"你没登录本面板"，用 401 表达会让用户在验证弹窗里点一下保存就被踢出去。所以 `session_expired`/`credentials_unreadable` 都映射成 **409**。
- **`upstream_rejected` → 422 而不是 5xx**。Apple 收到了并明确拒绝，属于请求语义问题；更关键的是 **Cloudflare 会把 5xx 的响应体整个换成自己的错误页**，Apple 到底说了什么就全丢了（测试 `tests/test_icloud_api.py:175` 就叫 `test_apple_rejection_stays_in_4xx_so_the_reason_survives`）。

| 错误码 | 状态 | 错误码 | 状态 |
|---|---|---|---|
| `account_not_found` / `alias_not_found` | 404 | `mail_access_denied` | 403 |
| `account_disabled` / `login_incomplete` | 409 | `provider_rate_limited` | 429 |
| `login_session_expired` | 410 | `upstream_rejected` | 422 |
| `invalid_config` / `invalid_verification_code` / `invalid_credentials` | 400 | `upstream_error` / `invalid_response` | 502 |
| `session_expired` / `credentials_unreadable` | 409 | `upstream_unavailable` | 503 |

`_http_error()` 在 `status >= 500` 时额外打一条 ERROR 日志，理由同上：反代会吃掉 5xx 响应体，运维侧只能靠日志。

#### 五、插件层：`platforms/icloud/plugin.py` 如何实现 `BasePlatform`

```python
@register
class ICloudPlatform(BasePlatform):
    name = "icloud"
    display_name = "iCloud 隐私邮箱"
    supported_executors = ["protocol"]
```

`core/registry.py:12` 的 `SUPPORTED_PLATFORMS = ("chatgpt", "icloud")`，通过 `@register` 装饰器自动注册。只支持 `protocol` 执行器——全程走 Apple Web 协议，没有浏览器自动化路径。

##### 5.1 `register()` 到底干了什么

iCloud 不是传统"注册"。这里的"注册"**= 从已登录主号生成一个 Hide My Email 地址**（文件 docstring：不需要外部临时邮箱，也不需要验证码）。

`register(email=None, password=None)`（`platforms/icloud/plugin.py:31`）**完全忽略这两个入参**，实际步骤：

1. `resolve_account(extra.get("icloud_account_email"))` 定位主号（不填就取第一个可用主号）。
2. 查 `alias_quota()` 并**打一条日志**告知本小时剩余额度 `remaining/limit`。
3. `generate_alias(account_id, label=extra["icloud_alias_label"], note=extra["icloud_alias_note"], proxy=config.proxy)`。
4. 返回 `Account`：`email = 别名地址`、**`password = ""`**（隐私邮箱没有密码这个概念）、`user_id = provider_id`（Apple 的 `anonymousId`）、`region` 取主号区域、`status = REGISTERED`。
5. `extra` 里回填溯源信息：`icloud_account_id`、`icloud_account_email`、`alias_id`、`label`、`note`、`status`。这几个字段是后续 `check_valid` / `execute_action` 找回主号与本地行的**唯一依据**。

`__init__` 里显式 `self.mailbox = None`（`platforms/icloud/plugin.py:29`）：iCloud 自带隐私邮箱，**注册流程不消耗外部邮箱池**。测试 `tests/test_icloud_plugin.py:92` `test_register_does_not_consume_a_mailbox` 就是守这一条。

##### 5.2 `check_valid()`

"隐私邮箱可用" 定义为：**它仍在主号的上游 `v2/hme/list` 列表里且 `status == active`**（`platforms/icloud/plugin.py:69`）。找不到主号、解密失败、任何 `ICloudError` 都返回 `False`（不抛）。注意这是一次真实的 Apple 调用，不看本地库。

##### 5.3 `get_platform_actions()` 声明的动作

只有两个（`platforms/icloud/plugin.py:90`），都不需要参数：

| id | label | `execute_action` 行为 |
|---|---|---|
| `fetch_inbox` | 实时收件 | 由 `extra` 反查主号 id → `fetch_account_messages(account_id, limit=params.limit or 20, recipient=account.email)`。缺主号信息时返回 `{"ok": False, "error": "该隐私邮箱缺少所属主号信息"}` |
| `delete_alias` | 删除隐私邮箱 | 需要 `extra["alias_id"]`；缺失返回 `{"ok": False, "error": "该隐私邮箱缺少本地记录，无法删除"}`（测试 `tests/test_icloud_plugin.py:133`）。存在则 `delete_alias(alias_id)`（默认 `remote=True`，连 Apple 侧一起删） |

`_owner_account_id()`（`platforms/icloud/plugin.py:117`）两级回退：先看 `extra["icloud_account_id"]`，再用 `extra["icloud_account_email"]` 走 `find_account_by_email`；都没有返回 `None`。未知 action 交回 `super().execute_action()`。

#### 六、风险与脆弱点

按严重度排列，客观陈述。

##### 6.1 依赖 Apple 未公开接口

- `v1/hme/generate` / `reserve` / `deactivate` / `delete`、`v2/hme/list` 都是 iCloud 网页版的内部接口，**Apple 没有承诺兼容性**，字段名、路径、鉴权方式随时可变。代码已经用 `find_string` / `_scalar` 的多 key 大小写不敏感回退来吸收字段名波动，但路径或语义变更只能改代码。
- **构建号（clientBuildNumber）是硬依赖**。`FALLBACK_CLOUD_BUILD = "2630Build35"` 这类常量会随 iCloud 发版过期，过期后 **Apple 直接拒绝且不说原因**。虽有 `BuildInfoCache` 从页面属性动态探测，但探测本身依赖 `www.icloud.com` 页面里 `data-cw-private-build-number` 属性继续存在；探测失败会静默降级到常量（有 WARNING 日志，但不阻断）。`platforms/icloud/constants.py:16` 的注释给了人工更新办法。
- `success:false` 的**无原因拒绝**已在线上出现过（`tests/test_icloud_hme_rejection_handling.py` 的整个存在理由）。目前对策是"无副作用动作重试一次 + 记原始信封 + 失效构建号缓存"，属于经验性缓解而非根治。

##### 6.2 限流的三重不确定性

| 问题 | 说明 |
|---|---|
| **上限是猜的** | `HOURLY_ALIAS_LIMIT = 5` 是本地常量，Apple 从未公开真实配额。Apple 若调整，本地不会跟随；本地放宽会撞上游 429，本地收紧会白白浪费额度 |
| **锁只在进程内** | `_ACCOUNT_LOCKS` 是 `dict[int, threading.Lock]`。**多 worker / 多容器部署下完全失效**，并发生成会双双通过配额预检然后一起撞 Apple 限流 |
| **同步会污染配额** | `sync_aliases` 落库的历史别名 `created_at` 取本地当前时间，一次大批量同步会把当前滚动小时的额度瞬间打满，产生假限流。配额口径是"库里最近一小时新增的行数"，不是"真正向 Apple 申请成功的次数" |
| **批量生成非原子** | `POST /aliases` 的 `count>1` 逐个生成，中途 429 时前面已成功的不回滚，客户端只看到一个 429（第 4.4 节） |

##### 6.3 分享页（最需要主动决策的一处）

- `share_token` **无过期、无撤销、无轮换、无速率限制**（详见 3.4）。链接一旦泄露就是永久读信权，唯一止损是删掉整个别名。
- 每次访问 `/m/{token}` 都触发一次**真实 IMAP 登录 + FETCH 最近 50 封**。这是个无鉴权的放大入口：反复刷合法链接就能把主号打到 Apple 的 IMAP 频率限制，还会拖慢面板内其它收件请求。
- 别名列表接口（13 号）**直接返回 `share_token`**，任何能读面板 API 的人都能一次性导出全部分享链接。

##### 6.4 IMAP 凭据敏感度

- 存的是**主号的 App 专用密码**。它的权限是**整个主号收件箱**，不是单个别名——泄露等于全部邮件可读。虽然以 AES-256-GCM 密文落库（`credentials_cipher`）、`public_state()` 只回布尔位、日志里 DSID 走 `mask_secret`，但解密密钥（`core/secret_box`）与库同机时，拿到磁盘即拿到明文。
- 密钥丢失/变更的表现已被专门处理成 `credentials_unreadable`（"加密密钥已变更，请重新登录该主号"），注释指出**最常见原因是密钥没放在挂载卷里、重建容器时被重新生成**——部署时必须把密钥文件放进持久卷。
- 收件全靠 IMAP，等于**主号一旦开了"高级数据保护"或撤销了 App 专用密码，所有别名收件同时失效**，且报错是 `invalid_credentials`（400），前端不一定引导得清楚。

##### 6.5 数据模型与并发

- `ICloudAliasModel.address` 是**全表唯一**，`_upsert_alias` 查询不带 `account_id`。多主号下同一地址只有一行，同步会把 `account_id` 改成最后同步的主号，历史归属被静默覆盖。
- 配额查询用 `since.replace(tzinfo=None)` 与库里 naive datetime 比较，**依赖"库里存的一定是 UTC naive"**这个未被约束强制的约定。若某处写入本地时间，窗口会整体偏移。
- `delete_alias` 先读快照 → 调 Apple → 再开新 session 删本地行，中间没有事务保护。Apple 侧删成功但本地删失败会留下**指向已不存在地址的孤儿行**（下次同步不会清理它，因为同步只做 upsert，不做"本地有、远端没有 → 删除"的对账）。
- `fetch_inbox` 按**序号区间**取最近 N 封，不是增量拉取。主号收件箱噪声大时，冷门别名的邮件会被挤出窗口，表现为"明明收到了却查不到"。ChatGPT 对接层的 `ICloudAliasMailProvider` 用 `limit=20`，窗口更窄，这一风险更明显。

##### 6.6 相关测试对照

| 测试文件 | 守住的行为 |
|---|---|
| `tests/test_icloud_web_client.py` | validate 解析、HME 各动作的请求构造与响应解析 |
| `tests/test_icloud_hme_rejection_handling.py` | 重试白名单、原始信封入日志、构建号降级可见 |
| `tests/test_icloud_mailbox.py` | IMAP 参数校验、投递头过滤、正文与时间解析 |
| `tests/test_icloud_service.py` | 配额窗口、upsert 去重与回写、sync_error 记录；**导入 MailAPI 号池**：只留 `scheme://host` 的地址归一化、没有 `share_token` 的别名整行跳过、落库为 `mailapi_url` 且视图切到 `mailapi`、缺面板地址时给出人话原因、已在池里的不重复导、选不到别名时带原因、**不越界取别的主号的别名**、地址不合法时退回配置项 `public_base_url` |
| `tests/test_icloud_api.py` | 18 个接口的状态码映射（429、503、**422 保留 Apple 原因**）、批量删除的"一条失败不拖累其余"；`POST /aliases/import-to-pool` 的成功路径与缺地址路径 |
| `tests/test_icloud_plugin.py` | `register` 生成别名并记录归属、**不消耗邮箱池**、`check_valid` 要求上游仍 active |
| `tests/test_shared_mail_page.py` | token 不可猜、**面板设了密码也能免登录打开**、不可信 HTML 无法执行、空收件箱不报错、未知 token 是 404 页、老数据迁移补 token |
| `tests/fixtures/shared_mail_chatgpt_otp.html` | 分享页渲染 ChatGPT OTP 邮件的黄金样本（含 `noindex`、暗色样式、iframe 沙箱结构） |

---

## 十二、外部集成服务层与部署

### 外部插件管理(services/external_apps.py)

#### 支持的插件清单

当前只注册了一个插件,全部元数据硬编码在 `_SERVICE_META` 字典里:

| 字段 | 值 | 位置 |
| --- | --- | --- |
| key | `cliproxyapi` | `services/external_apps.py:28` |
| label | `CLIProxyAPI` | `services/external_apps.py:29` |
| repo_name | `CLIProxyAPI` | `services/external_apps.py:30` |
| **remote(git 地址)** | `https://github.com/router-for-me/CLIProxyAPI.git` | `services/external_apps.py:31` |
| url | `http://127.0.0.1:8317` | `services/external_apps.py:32` |
| health | `http://127.0.0.1:8317/` | `services/external_apps.py:33` |
| management_url | `http://127.0.0.1:8317/management.html` | `services/external_apps.py:34` |
| management_key_setting | `cliproxyapi_management_key` | `services/external_apps.py:35` |
| port | `8317` | `services/external_apps.py:36` |

要新增插件,只需往 `_SERVICE_META` 加一项;但 `_build_command()` 里写死了 `if name != "cliproxyapi": raise KeyError`(`services/external_apps.py:331`),所以新插件必须同时补一条启动命令分支,否则只能装不能启。

#### 目录布局

| 路径 | 计算位置 | 说明 |
| --- | --- | --- |
| `_EXT_ROOT` = `<repo_parent_parent>/_ext_targets` | `services/external_apps.py:17-18` | `Path(__file__).parents[2]`,即仓库**上一级目录**,不是仓库内 |
| 仓库克隆目录 | `_EXT_ROOT / repo_name` | `services/external_apps.py:90-91` |
| 日志目录 | `services/external_logs/`(import 时 mkdir) | `services/external_apps.py:19-20` |
| 单插件日志 | `services/external_logs/<name>.log` | `services/external_apps.py:94-95` |

`parents[2]` 这个相对定位是个坑:仓库放在 `/opt/app/any-auto-register` 时 `_ext_targets` 落在 `/opt/app/_ext_targets`。Docker 完整版镜像里对应挂载 `/_ext_targets`(`Dockerfile:71,76`、`docker-compose.yml:32`),因为 WORKDIR 是 `/app`,`parents[2]` 正好是 `/`。

#### 安装/更新策略:semver tag 还是分支 HEAD

`install(name)`(`services/external_apps.py:340`)加锁后调 `_sync_repo_to_latest`(`services/external_apps.py:227`):

1. 目录不存在 → `git clone <remote>`(`:231`)
2. 一律先 `git fetch --all --tags --prune`(`:233`)
3. **切换点在 `services/external_apps.py:234`**:
   ```python
   if _update_mode() == "branch" or not _sync_repo_to_latest_semver_tag(repo):
       _sync_repo_to_branch_head(repo)
   ```

| 模式 | 来源 | 行为 |
| --- | --- | --- |
| `tag`(默认) | config 键 `external_apps_update_mode`,`_update_mode()` 归一化,非 `branch` 一律当 `tag`(`services/external_apps.py:155-157`);默认值兜底在 `api/config.py:177-178` | 取 `git for-each-ref refs/tags --sort=-version:refname` 第一个匹配 semver 正则的 tag(`:206-214`,正则在 `:22`),然后 `checkout --force <tag>` + `reset --hard` + `clean -fd`(`:217-224`) |
| `branch` | 同一配置键设成 `branch` | 直接走分支 HEAD |
| tag 模式但仓库无 semver tag | `_latest_semver_tag()` 返回空 → `_sync_repo_to_latest_semver_tag` 返回 False | **自动降级**到分支 HEAD |

分支候选顺序(`_branch_candidates`,`services/external_apps.py:180-192`):传入的 preferred → 当前本地分支 → `origin/HEAD` 指向的默认分支 → `main` → `master`;逐个检查 `refs/remotes/origin/<branch>` 是否存在,命中就 `checkout -B` + `reset --hard origin/<b>` + `clean -fd`(`:195-203`);全部不存在则抛 `未找到可用远端分支`。

注意 `reset --hard` + `clean -fd` 会**无条件丢弃插件目录里的本地改动**,包括生成的 `config.local.yaml` 之外的任何手工文件。

#### 卸载流程:先停服务再删目录

`uninstall(name)`(`services/external_apps.py:347`)顺序固定:

1. `stop(name)`,异常被吞掉(`:349-352`)——停不掉也要继续删
2. 持锁后调 `_remove_repo_tree`(`:357`)
3. 清理内存态:`_PROCS.pop`、`_LAST_ERROR.pop`、`_close_log`(`:358-360`)

`_remove_repo_tree`(`services/external_apps.py:364-377`)是 Windows 文件锁的对抗逻辑:最多 12 轮循环,每轮先 `_kill_processes_touching_path`(仅 Windows,用 PowerShell `Get-CimInstance Win32_Process` 匹配 CommandLine/ExecutablePath 后 `Stop-Process -Force`,`:131-152`),再 `_make_tree_writable`(逐个 chmod 加 `S_IWRITE`,应对 `.git` 只读对象,`:115-128`),再 `shutil.rmtree`;失败 sleep 0.5s 重试。12 轮都失败则把原因写进 `_LAST_ERROR` 并抛 `RuntimeError`。

#### 进程启停与状态检测

| 能力 | 实现 | 位置 |
| --- | --- | --- |
| 启动命令 | `go run ./cmd/server -config <config.local.yaml>`,cwd = 插件仓库 | `services/external_apps.py:329-337` |
| Go 定位 | `shutil.which("go")` → `C:\Program Files\Go\bin\go.exe` → `/usr/local/go/bin/go`,都找不到抛"未找到 go" | `services/external_apps.py:305-307` |
| 运行时配置生成 | 无 `config.local.yaml` 就从 `config.example.yaml` 复制,然后把所有 `secret-key:` 行改成配置里的 `cliproxyapi_management_key`(默认 `cliproxyapi`);一行都没匹配到就在文件末尾 append `  secret-key: "..."`(缩进硬编码两空格) | `services/external_apps.py:310-326` |
| 启动等待 | 每秒探一次健康,最多 90 次(`_START_TIMEOUT_SECONDS=90`);进程提前退出则记 `启动失败，退出码=N`;超时记 `启动超时` | `services/external_apps.py:405-414`,常量 `:24` |
| 健康检测 | `GET http://127.0.0.1:8317/`,timeout 2s,`status_code < 500` 即算活 | `services/external_apps.py:238-245`,常量 `:23` |
| PID 补充 | 健康则用 `netstat -ano -p tcp` 解析 LISTENING 行拿端口占用 PID | `services/external_apps.py:248-269` |
| 停止 | Windows 走 `taskkill /PID /T /F`;POSIX 走 `proc.terminate()` → `wait(8)` → `kill()`;再额外杀掉端口占用的陌生 PID;最多等 10s 直到健康检查失败 | `services/external_apps.py:417-455`,常量 `:25` |
| 状态快照 | `_status_one` 返回 name/label/repo_path/repo_exists/url/management_url/management_key/running/pid/log_path/last_error/kind | `services/external_apps.py:277-298` |
| 批量 | `start_all()` 跳过未安装项并把 `last_error` 写成"未安装；如需使用请先手动安装";`stop_all()` 无条件遍历 | `services/external_apps.py:458-474` |

进程句柄存在模块级全局 `_PROCS`/`_LOG_FILES`/`_LAST_ERROR` + 一把 `threading.Lock`(`services/external_apps.py:40-43`),**进程内存态**:后端重启后 `_PROCS` 清空,但 `_health_ok` 仍能通过端口探活把 `running=true` 报出来,只是 `pid` 只能靠 netstat 反查。

#### 跨平台假设与限制

| 假设 | 位置 | 在什么环境下不成立 |
| --- | --- | --- |
| 宿主机装了 `git` 且在 PATH | `_run_git`,`services/external_apps.py:66-76` | `Dockerfile.server` 没装 git(仅完整版 `Dockerfile:68` 装了),无头镜像里插件安装必然失败 |
| 宿主机装了 Go 工具链 | `_find_go`,`services/external_apps.py:305-307` | 完整版 `Dockerfile:44` 装了 go1.24.2;`Dockerfile.server` 没有 |
| `go run` 每次启动都现场编译 | `services/external_apps.py:337` | 首次启动很慢,90s 超时可能不够;还要求 Go module 代理可达 |
| `netstat -ano -p tcp` 存在 | `services/external_apps.py:252-256` | 这是 Windows 语法;Linux 的 net-tools netstat 不认 `-ano`,`_find_pid_by_port` 直接返回 None(异常被吞),Linux 上端口 PID 永远查不到 |
| `creationflags=CREATE_NO_WINDOW` | `_creationflags`,`services/external_apps.py:62-63` | 非 Windows 用 `getattr(..., 0)` 兜底为 0,安全 |
| PowerShell 可用 | `_kill_processes_touching_path`,`:131-152` | 函数开头 `if os.name != "nt": return`,非 Windows 直接跳过,所以 Linux 上 rmtree 失败没有兜底手段 |
| 与后端同机同网 | health/url 全部写死 `127.0.0.1:8317` | 插件跑在别的容器/主机时状态永远是"未运行";此时应改用 `cliproxyapi_base_url` 配置项走同步链路,而不是插件页 |
| conda 环境 | `main.py:28` 期望 `APP_CONDA_ENV`(默认 `any-auto-register`),Windows 启动脚本用 `conda run` 解析 python 路径 | Docker 里靠 `APP_CONDA_ENV=docker` 跳过告警(`main.py:48-49`) |

#### 对外 API(api/integrations.py)

| 方法 | 路径 | 实现 |
| --- | --- | --- |
| GET | `/api/integrations/services` | `list_status()`,`api/integrations.py:26-28` |
| POST | `/api/integrations/services/start-all` | `api/integrations.py:31-33` |
| POST | `/api/integrations/services/stop-all` | `api/integrations.py:36-38` |
| POST | `/api/integrations/services/{name}/start` | `api/integrations.py:41-43` |
| POST | `/api/integrations/services/{name}/install` | `api/integrations.py:46-48` |
| POST | `/api/integrations/services/{name}/uninstall` | `api/integrations.py:51-53` |
| POST | `/api/integrations/services/{name}/stop` | `api/integrations.py:56-58` |
| POST | `/api/integrations/backfill` | 批量补传,见下一节 |

前端入口在 `frontend/src/pages/Settings.tsx:886`(读列表)、`1029-1113`(启停/安装/卸载/回填按钮)。安装模式切换写的是 `external_apps_update_mode` 配置(`frontend/src/pages/Settings.tsx:936`)。


### CLIProxyAPI / CPA 同步

三个文件职责完全不同,名字容易混:

| 文件 | 职责 | 方向 |
| --- | --- | --- |
| `services/cliproxyapi_sync.py` | **只读**拉取远端 auth-file 列表 + 借远端通道探测账号额度状态 | 远端 → 本地 |
| `services/chatgpt_sync.py` | 上传/回填编排 + 把同步结果写进 `extra_json.sync_statuses` | 双向 |
| `services/cpa_manager.py` | 定时维护:清理 `status=error` 的凭证 + 低于阈值时自动补注册 | 远端 → 触发本地任务 |
| `services/external_sync.py` | 注册成功后的自动上报分发(CPA / Sub2API / CodexProxy / 贡献服务器) | 本地 → 远端 |

#### auth-file 概念

CLIProxyAPI 用"一个 JSON 文件 = 一个可用凭证"的模型,管理接口 `/v0/management/auth-files`。文件名约定为 `<email>.json`(`platforms/chatgpt/cpa_upload.py:212`),内容由 `generate_token_json()` 生成(`platforms/chatgpt/cpa_upload.py:157-192`):

| 字段 | 来源 |
| --- | --- |
| `type` | 固定 `"codex"` |
| `email` | 账号邮箱 |
| `expired` | 从 access_token JWT 的 `exp` 解出,格式化成 `+08:00` 时区串 |
| `id_token` | extra 里的 id_token;缺失时用 `_build_compat_id_token` 由 access_token 合成 |
| `account_id` | access_token JWT 里 `https://api.openai.com/auth.chatgpt_account_id` |
| `access_token` / `refresh_token` | extra 优先,回退 `account.token` |
| `last_refresh` | 上传当时的时间戳 |

上传走 `multipart` 表单 PUT/POST 到 `{api_url}/v0/management/auth-files`,用 `curl_cffi` 的 `CurlMime` + `impersonate="chrome110"`、`verify=False`(`platforms/chatgpt/cpa_upload.py:210-237`)。

#### 同步什么数据、双向还是单向

**方向 A(远端 → 本地,只读状态同步)** — `sync_chatgpt_cliproxyapi_status()`(`services/cliproxyapi_sync.py:333`):

1. `GET /v0/management/auth-files` 拿全量文件列表(`:165-168`)
2. `_match_auth_file`(`:182-204`)按邮箱匹配:只看 `provider/type == "codex"`,命中条件是 `item.email == account.email` **或** `item.name == "<email>.json"`;多条候选按状态优先级排序(`active < refreshing < pending < error < disabled`,`_status_rank` `:171-179`),取最优的一条
3. 命中后组装远端快照(`_build_remote_sync_result`,`:273-330`):`auth_index`、`name`、`provider`、`status`、`status_message`、`unavailable`、`disabled`、`last_refresh`、`next_retry_after`、`remote_plan_type`、`chatgpt_subscription_active_until`(后两个从 `id_token` 子对象里取)
4. 再用 `_probe_remote_auth`(`:207-270`)借 CLIProxyAPI 的 `POST /v0/management/api-call` 通道,让**远端拿着自己保存的 token** 去请求 `https://chatgpt.com/backend-api/wham/usage`,请求头里 `Authorization: Bearer $TOKEN$` 是占位符由 CLIProxyAPI 替换,另带 `Chatgpt-Account-Id` 和 Codex UA

远端状态判定表(`services/cliproxyapi_sync.py:252-262`):

| 上游 status_code / 条件 | `remote_state` |
| --- | --- |
| 200 | `usable` |
| 401 且 error_code == `token_invalidated` | `access_token_invalidated` |
| 401 其它 | `unauthorized` |
| `is_account_deactivated_message(code, msg)` 命中 | `account_deactivated` |
| 402 / 403 | `payment_required` |
| 429 | `quota_exhausted` |
| 其它 | `probe_failed` |
| 缺 auth_index 或缺 account_id | `probe_skipped`(不发请求) |
| 列 auth-files 或探测抛异常 | `unreachable` |
| 邮箱没匹配到任何 codex auth-file | `not_found` |

错误信息提取做了三层兜底:响应头 `X-Openai-Ide-Error-Code`、body JSON 的 `error.code`、以及 base64 编码的 `X-Error-Json` 头解出来的 `error.code/message`(`services/cliproxyapi_sync.py:64-105`)。

**方向 B(本地 → 远端,补传)** — `backfill_chatgpt_account_to_cpa()`(`services/chatgpt_sync.py:299-373`)。

#### 目标地址与凭据解析

| 层 | 键 | 位置 |
| --- | --- | --- |
| 只读同步 base_url | `cliproxyapi_base_url`,默认 `http://127.0.0.1:8317` | `services/cliproxyapi_sync.py:15,37-38` |
| 只读同步 key | `cliproxyapi_management_key`,默认字面量 `cliproxyapi` | `services/cliproxyapi_sync.py:41-42` |
| 回填目标(带回退链) | `cliproxyapi_base_url` → `cpa_api_url`;key 同理 `cliproxyapi_management_key` → `cpa_api_key` | `services/chatgpt_sync.py:36-49` |
| 上传/清理用 | `cpa_api_url` / `cpa_api_key`,Bearer 头 | `services/cpa_manager.py:92-106`、`platforms/chatgpt/cpa_upload.py:203-217` |

所有出站请求都是 `verify=False`(`services/cliproxyapi_sync.py:121`、`services/cpa_manager.py:116`),`cliproxyapi_sync` 还显式 `urllib3.disable_warnings`(`:112`)。

#### 两类批量操作的实现

##### 1)"同步所选账号 CLIProxyAPI 状态"(只读)

- 触发:`POST /api/actions/chatgpt/sync_cliproxyapi_status/batch`,请求体 `BatchActionRequest`(`api/actions.py:24-30`),支持 `account_ids` 或 `all_filtered=true` + `email/status/plus_status` 筛选,单次上限 1000(`api/actions.py:148-188`)
- 特化分流:`api/actions.py:280-296` 对 `platform == "chatgpt" && action_id == "sync_cliproxyapi_status"` 走专用批量路径,不走逐账号 `execute_action`
- 批量实现 `_execute_batch_cliproxy_sync`(`api/actions.py:204-255`):内部临时类 `SyncAccount` 把 ORM 拍平成 duck-typed 快照(带 `client_id` 默认 `app_EMoamEEZ73f0CkXaXp7hrann`),调 `sync_chatgpt_cliproxyapi_status_batch`
- 批量函数 `sync_chatgpt_cliproxyapi_status_batch`(`services/cliproxyapi_sync.py:354-400`):**auth-files 只拉一次**然后本地匹配,逐个账号做远端探测,匹配到的相邻两次之间 sleep `BATCH_PROBE_DELAY_SECONDS = 0.12`(`:18`);列表拉取失败则给所有账号统一填 `unreachable` 兜底并 `logger.warning`;结束打一条 info 汇总 `accounts / unreachable / not_found / base_url`
- 落库:每个账号 `update_account_model_cliproxy_sync(commit=False)`,最后统一 `session.commit()`(`api/actions.py:230,295`)。这个函数同时会调 `apply_chatgpt_status_policy(account, remote_sync=...)` 把远端状态映射成账号状态(`services/chatgpt_sync.py:213`)
- 成功判定:`uploaded == True` 且 `remote_state not in {unreachable, not_found}`(`api/actions.py:232`)
- 单账号版同路径在插件里:`platforms/chatgpt/plugin.py:194-210`(action `sync_cliproxyapi_status`)
- 前端:`frontend/src/pages/Accounts.tsx:1019-1030`,scope 支持"所选账号"/"当前筛选账号"

##### 2)"补传远端未发现的 auth-file"(写)

- 触发:`POST /api/integrations/backfill`,请求体 `BackfillRequest`(`api/integrations.py:17-23`):`platforms`(默认 `["chatgpt"]`)、`account_ids`、`pending_only`、`status`、`email`、`plus_status`
- 选账号:给了 `account_ids` 就按 ID + 平台过滤,否则按平台全量;再叠加 `status`/`email` 的 SQL 过滤和 `plus_status` 的内存过滤(`api/integrations.py:66-84`)
- **`pending_only=true` 的语义**:只保留 `get_cliproxy_sync_state(row).remote_state == "not_found"` 的账号,也就是"上一次同步时远端确实没找到 auth-file 的"(`api/integrations.py:85-90`)。注意它读的是**缓存状态**,没跑就是空,所以通常要先做一次批量状态同步
- 逐账号执行 `backfill_chatgpt_account_to_cpa`(`services/chatgpt_sync.py:299`),步骤:
  1. 先看 `extra.sync_statuses.cliproxyapi` 缓存;缓存存在且不是 `unreachable` 就复用,否则现场同步一次(`:312-319`)
  2. `remote_state == "unreachable"` → 直接失败返回(`:321-328`)
  3. `_remote_auth_missing()` 为假(远端已有且 uploaded)→ 返回 `skipped=True`,消息"远端已存在 (状态),跳过上传"(`:330-336`)
  4. 本地探测 `probe_local_chatgpt_status`,要求 `auth.state == "access_token_valid"` 才允许上传(`_local_probe_uploadable`,`:289-291`);不合格直接失败,消息带本地状态(`:341-348`)
  5. 上传 `upload_account_model_to_cpa`(`:350`)
  6. **上传后复核**:再同步一次,若远端依旧 missing 则整体判失败,消息"上传后远端仍未发现 auth-file"(`:358-366`)
  7. 全程 `commit=False`,只在每个分支出口按需 `session.commit()` + `refresh`
- 汇总:`api/integrations.py:63,92-121` 统计 `total/success/failed/skipped` 和逐条 `items[].results`;单账号抛异常时 `session.rollback()` 并记 `{"name":"error"}`
- 前端只传 `{platforms:["chatgpt"]}`,不带筛选(`frontend/src/pages/Settings.tsx:919-934`)

#### 同步状态如何落库

统一写在账号 `extra_json` 的 `sync_statuses` 子对象里,按渠道名分桶(`services/chatgpt_sync.py:13-15`):

| 桶名 | 写入函数 | 结构 |
| --- | --- | --- |
| `cpa` | `record_cpa_sync_result` | `last_attempt_ok` / `last_message` / `last_attempt_at` / `uploaded`(粘性 OR) / `uploaded_at`(`:95-114`) |
| `sub2api` | `record_sub2api_sync_result` | 同上 |
| `cliproxyapi` | `record_cliproxy_sync_result` | **整体覆盖**为最新一次同步结果 dict(`:125-131`) |

`uploaded` 字段对 cpa/sub2api 是"一旦成功就永久为真"的粘性标记(`:108`),对 cliproxyapi 则每次被覆盖,含义是"这一次是否匹配到远端 auth-file"。

#### 错误与重试

| 机制 | 细节 | 位置 |
| --- | --- | --- |
| 可重试判定 | 错误文本包含 `无法连接` / `请求超时` / `connection` / `timeout` / `timed out` 才重试,其它一律直接抛 | `services/cliproxyapi_sync.py:136-147` |
| 重试次数/间隔 | 3 次,固定 0.4s(无指数退避) | `:16-17,150-162` |
| 连接异常翻译 | `ConnectionError` → "CLIProxyAPI 无法连接,请确认服务已启动或 API URL 是否正确:<base>";`Timeout` → "CLIProxyAPI 请求超时" | `:123-126` |
| HTTP 超时 | 统一 30s | `:120`、`services/cpa_manager.py:115` |
| 覆盖范围 | 只包住 `list_auth_files` 和 `_probe_remote_auth` 两处;上传(`upload_to_cpa`)**没有任何重试** | `:310-312,341,366` |

#### CPA 定时维护(services/cpa_manager.py)

配置全部来自 config_store,`get_cpa_maintenance_config()`(`services/cpa_manager.py:56-80`):

| 配置键 | 默认 | 作用 |
| --- | --- | --- |
| `cpa_cleanup_enabled` | `false` | 总开关 |
| `cpa_cleanup_interval_minutes` | 60(下限 1) | 巡检间隔 |
| `cpa_cleanup_threshold` | 5(下限 1) | 可用凭证低水位 |
| `cpa_cleanup_concurrency` | 1(下限 1) | 补注册并发 |
| `cpa_cleanup_register_delay_seconds` | 0.0 | 补注册间隔 |
| `cpa_api_url` | 空 | 空则 `get_cpa_maintenance_interval_seconds()` 返回 0,调度器跳过(`:83-89`) |

调度接入:`core/scheduler.py:47-60` 每轮循环问一次间隔,到点调 `maintain_cpa_credentials()`(`core/scheduler.py:125-127`)。

`maintain_cpa_credentials()`(`services/cpa_manager.py:219-253`)流程:
1. 未启用 → `{"ok": False, "reason": "disabled"}`
2. 列 auth-files,挑出 `status == "error"` 的名字(`_error_names`,`:154-161`),`DELETE /v0/management/auth-files` 带 `{"names": [...]}` 批量删(`:133-143`),然后重新拉一遍列表
3. 数剩余可用(名字非空且 status != error,`_count_remaining` `:146-151`)
4. 够阈值 → 不注册;不够 → `_trigger_register(missing)`(`:178-216`):先用 `has_active_register_task(platform="chatgpt", source="cpa_replenish")` 去重,再 `enqueue_register_task`,executor 归一化到 `protocol|headless|headed`(默认 `protocol`),solver 归一化到 `yescaptcha|local_solver|manual`(默认 `yescaptcha`),`source = "cpa_replenish"`(`:15`),meta 带 remaining/threshold/missing

#### 注册后自动上报(services/external_sync.py)

`sync_account(account)`(`services/external_sync.py:35`)由注册任务在后台线程调用(`api/tasks.py:422-435`,异常只写日志不影响注册)。ChatGPT 分支优先级:

1. **贡献模式最高优先级**(`:59-61`):`contribution_enabled` 为真时**只**上报贡献服务器,直接 return,不再走 CPA/Sub2API/CodexProxy
   - `contribution_mode == "custom"`:`POST {custom_contribution_url}/api/upload`,Bearer 用 `custom_contribution_token`,body 含 `email` / `refresh_token` / `access_token` / 完整 `token_json`(`:119-129`);缺 URL 或 token 直接失败;缺 refresh_token 也失败(`:113-117`)
   - 否则 `codex` 模式:复用 `upload_chatgpt_account_to_cpa(api_url=contribution_server_url, api_key=contribution_key)`(`:156-160`)
2. `cpa_enabled`(未显式设置时以 `cpa_api_url` 非空为默认)且有 URL → 上传 CPA(`:165-173`)
3. `codex_proxy_url` 非空 → 按 `codex_proxy_upload_type`(`at`/`rt`,默认 `at`)调 `upload_at_to_codex_proxy` 或 `upload_to_codex_proxy`(`:175-194`)
4. `sub2api_enabled`(默认取决于 URL+KEY 都非空)→ `upload_to_sub2api`,与 CPA **互不覆盖、分别记结果**(`:196-212`)

已知瑕疵:custom 模式里留了三行 `print(f"[DEBUG] ...")`,会把 `extra` 的 key 列表和 refresh_token 前 20 字符打进后端日志(`services/external_sync.py:90-91,111-112`)。


### 账号导出(services/account_export.py)

#### 两层数据结构

| 层 | 定义 | 说明 |
| --- | --- | --- |
| `ExportField` | `services/account_export.py:68-73` | 一个可导出字段:`id` / `label`(中文列名) / `read(account, extra) -> str` |
| `EXPORT_FIELDS` | `services/account_export.py:75-94` | 15 个字段的注册表 |
| `ExportFormat` | `services/account_export.py:97-113` | 一个导出格式:`id` / `label` / `description` / `columns` / `extension`(默认 `txt`) / `skip_empty_rows` / 可选 `renderer` |
| `EXPORT_FORMATS` | `services/account_export.py:249` | 由 `_FORMAT_LIST`(`:163-247`)推导出的 `id -> ExportFormat` 字典 |
| `DEFAULT_EXPORT_FORMAT` | `email_pw_2fa`,`services/account_export.py:250` | 前端下拉框默认项 |

字段取值表(`services/account_export.py:78-93`):

| 字段 id | 列名 | 取值来源 |
| --- | --- | --- |
| `platform` | 平台 | `account.platform` |
| `email` | 邮箱 | `account.email` |
| `password` | 密码 | `account.password` |
| `totp_secret` | 2FA 密钥 | `extra["totp_secret"]` |
| `access_token` | AccessToken | `extra["access_token"]`,回退 `account.token`(`:64-65`) |
| `refresh_token` | RefreshToken | `extra["refresh_token"]` 或 `extra["refreshToken"]` |
| `id_token` | IdToken | `extra["id_token"]` |
| `session_token` | SessionToken | `extra["session_token"]` |
| `phone_number` | 手机号 | `extra["phone_number"]` |
| `bound_email` | 绑定邮箱 | `extra["bound_email"]` |
| `user_id` | UID | `account.user_id` |
| `status` | 状态 | `account.status` |
| `region` | 地区 | `account.region` |
| `cashier_url` | 试用链接 | `account.cashier_url` |
| `created_at` | 注册时间 | `account.created_at`,datetime 统一格式化 `%Y-%m-%d %H:%M:%S`(`:44-45`) |

`extra` 读取有三重兜底:优先 `account.get_extra()`,其次解析 `extra_json` 字符串,最后 `account.extra` dict;脏 JSON 只让这一个账号的 extra 变空,不会让整次导出失败(`services/account_export.py:23-38`)。

#### EXPORT_FORMATS 完整格式表

分隔符统一 `----`(`SEPARATOR`,`services/account_export.py:20`)。样例行用列的**中文 label** 拼出来(`ExportFormat.sample`,`:109-113`),前端下拉里直接展示。

| 格式 id | label / 一行样例 | 包含字段 | 扩展名 | 空行处理 |
| --- | --- | --- | --- | --- |
| `email_pw` | `邮箱----密码` | email, password | txt | 保留占位 |
| `email_pw_2fa` **(默认)** | `邮箱----密码----2FA 密钥` | email, password, totp_secret | txt | 保留占位 |
| `email_pw_2fa_at` | `邮箱----密码----2FA 密钥----AccessToken` | email, password, totp_secret, access_token | txt | 保留占位 |
| `email_pw_2fa_rt` | `邮箱----密码----2FA 密钥----RefreshToken` | email, password, totp_secret, refresh_token | txt | 保留占位 |
| `email_pw_2fa_at_rt` | `邮箱----密码----2FA 密钥----AccessToken----RefreshToken` | email, password, totp_secret, access_token, refresh_token | txt | 保留占位 |
| `email_pw_2fa_phone` | `邮箱----密码----2FA 密钥----手机号` | email, password, totp_secret, phone_number | txt | 保留占位 |
| `email_pw_rt` | `邮箱----密码----RefreshToken` | email, password, refresh_token | txt | 保留占位 |
| `email_2fa` | `邮箱----2FA 密钥` | email, totp_secret | txt | 保留占位 |
| `at` | `AccessToken`(一行一个) | access_token | txt | **跳过空行** |
| `rt` | `RefreshToken`(一行一个) | refresh_token | txt | **跳过空行** |
| `totp` | `2FA 密钥`(一行一个) | totp_secret | txt | **跳过空行** |
| `csv` | 带表头的全字段表格,无 `sample` | 见下 | csv | renderer 自定义 |
| `json` | 全字段 JSON 数组,无 `sample` | 见下 | json | renderer 自定义 |

注意:表里 label 列写的是 `EXPORT_FORMATS` 中人类可读的 `label`(如 `邮箱----密码----2FA`,`services/account_export.py:172`),而 `sample` 属性生成的是字段 label 拼接(`2FA 密钥` 而不是 `2FA`),两者略有差异,前端同时展示 label 和 sample。

`csv` 列顺序(`_render_csv`,`services/account_export.py:116-136`,11 列,`csv.writer` + `lineterminator="\n"`,首行表头是字段 **id** 不是中文 label):
`platform, email, password, status, region, user_id, totp_secret, access_token, refresh_token, cashier_url, created_at`

`json` 字段顺序(`_render_json`,`services/account_export.py:139-160`,10 键,`ensure_ascii=False, indent=2`):
`platform, email, password, totp_secret, access_token, refresh_token, id_token, session_token, status, created_at`

两者都不含 `phone_number` / `bound_email`;`csv` 有 `region`/`cashier_url` 而 `json` 没有,`json` 有 `id_token`/`session_token` 而 `csv` 没有。

#### 空字段占位规则与 `----` 约定

- **多列格式**:空字段必须留位,`a@b.com----pw----` 这种尾随分隔符不能省,否则按列切的脚本会错位(模块 docstring `services/account_export.py:7-8`)。实现上就是 `SEPARATOR.join(values)`,不做 rstrip(`render_account_row`,`:275-281`)
- **单列格式**(`at`/`rt`/`totp`,`skip_empty_rows=True`):空行没有意义,`render_account_row` 返回 `None`,`render_accounts` 把它丢掉(`:279-280,288-293`)。判定是 `not any(values)`,所以多列格式即使全空也**不会**被跳过(只有单列格式设了这个标志)
- 所有值都过 `_text()` 归一化:`None` 和 `False` → 空串,datetime → 固定格式串,其它 `str()`(`services/account_export.py:41-46`)

#### 导出范围语义与后端接收

请求模型 `ExportTextRequest`(`api/accounts.py:46-56`):

| 字段 | 默认 | 语义 |
| --- | --- | --- |
| `format` | `DEFAULT_EXPORT_FORMAT` | 格式 id |
| `platform` | `""` | 平台过滤,同时用于生成文件名 |
| `account_ids` | `[]` | **非空即"只导这些"** |
| `status` / `email` / `plus_status` | `""` | 筛选条件 |
| `created_at_start` / `created_at_end` | `None` | 时间区间 |

后端分流在 `api/accounts.py:179-194`:

- `account_ids` 非空:`WHERE id IN (...)`,然后**按前端给的 ID 顺序回填**(`row_map` + `dict.fromkeys(ids)` 去重保序,`:183-184`)——勾选顺序就是导出顺序
- `account_ids` 为空:走 `_filtered_accounts()`(`api/accounts.py:59-83`),SQL 过滤 platform/status/email(`contains` 模糊)/created_at 区间,再在内存里做 `plus_status` 过滤(Plus 试用状态存在 `extra_json` 里,SQL 筛不动)

响应体(`api/accounts.py:201-207`):`format` / `total`(账号数) / `lines`(非空行数) / `content`(整段文本) / `filename`。文件名格式 `{platform或accounts}_{format_id}_{YYYYmmdd_HHMMSS}.{ext}`(`export_filename`,`services/account_export.py:296-299`)。未知格式 → `KeyError` → HTTP 400 `未知的导出格式: X`(`api/accounts.py:198-199`)。

前端 `frontend/src/components/AccountExportModal.tsx`:
- scope 默认值:勾了行默认 `selected`,没勾默认 `filtered`,用户改过以用户为准(`:59-65`)
- `selected` 只传 `account_ids`;`filtered` 只传非空的 email/status/plus_status/created_at 区间(`:95-104`)
- 格式列表来自 `GET /accounts/export-formats`(`:81-86`)

#### 相关接口清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/accounts/export-formats` | 返回 `{formats: [...], default}`,元信息含 id/label/description/extension/columns(中文)/sample(`api/accounts.py:171-174`,`services/account_export.py:253-265`) |
| POST | `/api/accounts/export-text` | 多格式文本导出,返回 JSON 内容体由前端负责下载/复制(`api/accounts.py:177-207`) |
| GET | `/api/accounts/export` | **遗留** CSV 流式下载,固定 8 列(platform/email/password/user_id/region/status/cashier_url/created_at),只支持 platform+status 过滤,不走 `EXPORT_FORMATS`(`api/accounts.py:142-168`) |

#### 扩展点:新增格式只改一处

往 `_FORMAT_LIST`(`services/account_export.py:163-247`)加一个 `ExportFormat(...)` 就够了:

```python
ExportFormat(
    id="email_pw_uid",
    label="邮箱----密码----UID",
    description="带 UID 的三列",
    columns=("email", "password", "user_id"),
),
```

`EXPORT_FORMATS` 字典、`/export-formats` 接口返回值、前端下拉框、`render_accounts` 分发全部自动跟上,**不需要改 API、不需要改前端**。只有两种情况要多写代码:

1. 需要的字段还不在 `EXPORT_FIELDS` 里 → 先往 `EXPORT_FIELDS`(`:75-94`)加一个 `ExportField`
2. 输出不是"分隔符拼列"结构(比如另一种表格/YAML)→ 写一个 `renderer(accounts) -> str` 并设置 `extension`,参照 `_render_csv` / `_render_json`

唯一遗留的重复点是 `GET /api/accounts/export` 那个老 CSV 接口,它的列表写死在 `api/accounts.py:157-162`,新增字段不会同步。


### 付费渠道抽象(services/payment_channels/)

> 本节只描述流程与字段名,不含任何真实卡号、CVC 或密钥。

#### 设计模式

Registry + Strategy,三个文件各管一件事:

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `contracts.py` | 62 | 稳定边界:数据契约 + 渠道协议,不 import 任何平台代码 |
| `registry.py` | 51 | 渠道注册/查找/列举 + 内置渠道延迟加载 |
| `service.py` | 64 | 门面:ORM → 不可变快照 → 调策略 |
| `__init__.py` | 12 | 只再导出 `PaymentAccount` / `PaymentChannel` / `PaymentResult` / `payment_channels` / `register_payment_channel` |

#### contracts.py 定义的契约

**`PaymentAccount`**(frozen dataclass,`services/payment_channels/contracts.py:13-21`):脱离 ORM 的账号快照,避免网络请求期间占用数据库连接。

| 字段 | 类型 | 必填 |
| --- | --- | --- |
| `platform` | str | 是 |
| `account_id` | str | 是(本地 DB 主键的字符串形式) |
| `email` | str | 是 |
| `access_token` | str | 是 |
| `session_token` | str | 否,默认 `""` |
| `user_id` | str | 否 |
| `cookies` | str | 否(Cookie 头原文) |

**`PaymentResult`**(`services/payment_channels/contracts.py:24-47`):

| 字段 | 说明 |
| --- | --- |
| `ok` | 成功标志 |
| `channel` | 渠道 name |
| `operation` | `link` / `pay` / `create_link` 等操作名 |
| `data` | dict,渠道自定义负载 |
| `error` | 失败原因 |

配套三个便捷方法:`PaymentResult.success(channel, operation, **data)`、`PaymentResult.failure(channel, operation, error, **data)`、`as_dict()`(API 直接返回这个)。

**`PaymentChannel`**(Protocol,`services/payment_channels/contracts.py:50-62`):渠道必须提供四个类属性 + 两个方法:

| 成员 | 用途 |
| --- | --- |
| `name: str` | 注册键(registry 里统一 lower) |
| `display_name: str` | 前端展示名 |
| `operations: list[str]` | 支持的操作,如 `["link", "pay"]` |
| `option_schema: Mapping` | 前端表单描述,按操作分组 |
| `create_link(account, *, options)` | 返回 `PaymentResult` |
| `pay(account, *, options)` | 返回 `PaymentResult` |

用 `Protocol` 而非基类,渠道实现不需要继承,只要结构匹配。

#### registry.py 的注册与查找

`PaymentChannelRegistry`(`services/payment_channels/registry.py:10-37`):

| 方法 | 行为 |
| --- | --- |
| `register(channel)` | key = `name.strip().lower()`,空名抛 `ValueError("支付渠道必须提供 name")`;同名**直接覆盖**,无冲突检测(`:14-19`) |
| `get(name)` | 找不到抛 `KeyError`,错误消息里带可用渠道列表(`:21-26`) |
| `list()` | 按 key 排序返回 `[{name, display_name, operations, option_schema}]`,`operations` 缺省 `["link","pay"]`(`:28-37`) |

模块级单例 `payment_channels`(`:40`)+ 便捷函数 `register_payment_channel`(`:43-44`)。

`load_builtin_payment_channels()`(`services/payment_channels/registry.py:47-50`)是**延迟加载**入口:函数体内才 `from platforms.chatgpt.payment_channels.direct import direct_card_channel` 并注册,注释说明是为了"避免导入 API 时初始化卡片或网络客户端"。它可被反复调用(register 幂等覆盖),所以每个入口都直接先调一次。

#### service.py 的编排职责

| 函数 | 职责 | 位置 |
| --- | --- | --- |
| `account_context(account: AccountModel)` | ORM → `PaymentAccount`:`access_token` 取 `extra["access_token"]` 回退 `account.token`;`session_token`/`cookies` 从 extra 取;`account_id` 用本地 DB id | `services/payment_channels/service.py:13-23` |
| `_channel(name)` | 先 `load_builtin_payment_channels()` 再 `payment_channels.get(name)` | `:26-28` |
| `create_link(account, channel, *, options)` | ORM 入口 | `:31-37` |
| `create_link_for_context(ctx, channel, *, options)` | 已有快照入口(批量任务用,不再持有 session) | `:40-46` |
| `pay(account, channel, *, options)` | ORM 入口 | `:49-55` |
| `pay_for_context(ctx, channel, *, options)` | 快照入口 | `:58-64` |

`*_for_context` 这对变体是为了让批量任务在开 session 拿完快照后立刻关掉连接,再去跑几十秒的网络流程。

#### 与 platforms/chatgpt/payment_channels/direct/ 的关系

调用方向是单向的:**services 层调 platforms 层**,platforms 层只反向 import `contracts`(数据契约),不 import `registry`/`service`。

```
api/payments.py ──► services/payment_channels/service.py
                        └─► registry.load_builtin_payment_channels()
                                └─► platforms/chatgpt/payment_channels/direct/__init__.py
                                        └─► channel.DirectCardChannel  (import services/.../contracts.py)
```

`direct` 包内部分工:

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `channel.py` | 412 | `DirectCardChannel` 策略本体:checkout 创建、update、托管页探活、串联绑卡 |
| `bind_card.py` | 942 | 绑卡 + 扣款 + 订阅确认的完整流程(`bind_and_pay`) |
| `card_store.py` | 170 | 卡片库,SQLite 持久化 + 轮询取卡 |
| `taxfree_store.py` | 128 | 免税州地址生成(`generate_address` / `pick_state` / `list_states`) |
| `transport.py` | 123 | `curl_cffi` 会话构建、Cookie 头拼装、统一请求封装 |

`DirectCardChannel` 的声明(`platforms/chatgpt/payment_channels/direct/channel.py:243-279`):

| 属性 | 值 |
| --- | --- |
| `name` | `direct` |
| `display_name` | `直卡` |
| `operations` | `["link", "pay"]` |

`option_schema` 表单项:

| 操作 | key | 控件 | 必填 | 默认 | 可选值 |
| --- | --- | --- | --- | --- | --- |
| link | `country` | select | 是 | `PH` | PH/US/SG/TR/HK/JP/GB/AU/CA |
| link | `currency` | select | 是 | `PHP` | PHP/USD/SGD/TRY/HKD/JPY/GBP/AUD/CAD |
| link | `link_proxy` | text | 否(advanced) | — | 提链代理 |
| pay | `card_id` | card | 是 | — | 卡片库选卡 |
| pay | `taxfree_state` | select | 否 | `DE` | DE/NH/MT/OR/AK |
| pay | `pay_proxy` | text | 否(advanced) | — | 支付代理 |
| pay | `timeout` | number | 否(advanced) | 30 | 秒 |

`create_link` 流程(`platforms/chatgpt/payment_channels/direct/channel.py:281-348`):校验 access_token → 生成 checkout 上下文(复用或新建 `oai-did` 设备 ID,`:37-41`)→ `_checkout` 创建 checkout session → `_update_zero` 更新 → `_probe_hosted_page` 探活托管页 → 拼出 `{APP_BASE}/checkout/{processor_entity}/{checkout_session_id}` 作为 `link` 返回。失败时把上游 status/body 放进 `PaymentResult.data.response`。

`pay` 流程(`platforms/chatgpt/payment_channels/direct/channel.py:350-412`):校验 access_token → 解析 link/pay 两套代理 → 取卡(`options["card"]` 显式传入 > `card_id` 查库 > `card_store.pickup_card()` 轮询)→ 从 access_token JWT 解 `chatgpt_account_id`(`_chatgpt_account_id`,`:105-113`)→ 先跑一次 `create_link` → `generate_address(taxfree_state, card.name)` 生成账单地址(`country` 固定 `US`)→ `bind_and_pay(...)` 完成绑卡扣款 → 成功时返回 `subscription_plan` 和 `card_last4`(只取后 4 位)。

代理解析优先级(`_proxy`,`platforms/chatgpt/payment_channels/direct/channel.py:80-102`,与 `api/payments.py:52-64` 对齐):
`options["{link|pay}_proxy"]` → config `payment_{link|pay}_proxy` → config `payment_proxy`(旧版兼容) → `proxy_pool.get_next("US")`。

卡片库 `card_store`(`platforms/chatgpt/payment_channels/direct/card_store.py`):

| 项 | 说明 |
| --- | --- |
| 存储 | SQLite,路径 `MIN_CARDS_DB` 环境变量,缺省 `platforms/chatgpt/cards.db`(`:21-23`) |
| 表结构 | `cards(id, number, exp_month, exp_year, cvc, name, brand, source, max_uses, uses, note, created_at, updated_at)`(`:45-60`) |
| 取卡 | `pickup_card()` 在 `uses < max_uses` 的卡里轮询/随机取(`:118`) |
| 内置测试卡 | **默认禁用**,仅 `MIN_ENABLE_TEST_CARD=1` 时注入,卡号等字段从 `MIN_TEST_CARD_*` 环境变量读,仓库里只留占位值,避免 live mode 误用(`:8,25-43,88-106`) |
| 其它方法 | `list_cards` / `get_card` / `add_card` / `delete_card` / `bulk_delete_cards` / `reset_uses` |

**敏感数据只落在这张表里**,不进账号 `extra_json`,也不回显完整卡号。

#### api/payments.py 接口清单

| 方法 | 路径 | 请求体 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/payments/channels` | — | 先 `load_builtin_payment_channels()`,返回 `{channels: registry.list()}`(`api/payments.py:108-111`) |
| GET | `/api/payments/channels/{channel}/cards` | — | 仅 `direct` 支持,其它渠道 404 `该渠道没有卡片资源`;返回 `_public_card` 脱敏视图(`:114-119`) |
| POST | `/api/payments/channels/{channel}/cards` | `CardCreateRequest` | 新增卡片,返回脱敏视图(`:122-129`) |
| DELETE | `/api/payments/channels/{channel}/cards/{card_id}` | — | 删卡,不存在 404(`:132-139`) |
| POST | `/api/payments/channels/{channel}/cards/reset-uses` | — | 重置全部卡片使用计数(`:142-147`) |
| POST | `/api/payments/jobs` | `PaymentTaskRequest` | 批量异步任务,返回 `{task_id, total}`(`:210-251`) |
| POST | `/api/payments/{account_id}/link` | `PaymentRequest` | 单账号提链,返回 `PaymentResult.as_dict()`(`:254-257`) |
| POST | `/api/payments/{account_id}/pay` | `PaymentRequest` | 单账号支付(`:260-262`) |

请求模型:

| 模型 | 字段与约束 | 位置 |
| --- | --- | --- |
| `PaymentRequest` | `channel`(默认 `direct`)、`options: dict` | `api/payments.py:21-23` |
| `PaymentTaskRequest` | 继承上者 + `account_ids`(1~1000)、`operation`(默认 `link`)、`concurrency`(1~10)、`delay_seconds`(0~3600) | `:26-30` |
| `CardCreateRequest` | `number`(12~24 字符)、`exp_month`(1~2)、`exp_year`(2~4)、`cvc`(3~4)、`name`、`brand`、`source`(默认 `manual`)、`max_uses`(1~100000,默认 10)、`note` | `:33-42` |

#### 敏感数据处理(值得照抄的几处)

| 位置 | 做法 |
| --- | --- |
| `api/payments.py:92-105` `_public_card` | 卡号只返回 `last4`(先 `re.sub(r"\D","")` 清洗),不返回 number/cvc/expiry |
| `api/payments.py:233` | 建任务时把 options 里的 `card`、`number`、`cvc` 三个键**剔除**后才写进 task meta(task 快照会持久化) |
| `api/payments.py:150-164` `_safe_result` | 只把白名单字段(link / checkout_session_id / billing_country / subscription_plan / card_last4 / error)写进任务结果 |
| `platforms/chatgpt/payment_channels/direct/channel.py:406` | 成功结果里只放 `card_last4` |

#### 批量任务执行

`_run_payment_task`(`api/payments.py:167-207`)把每个账号包成 `handle_account`:开 session 取 ORM → `account_context()` 拿快照 → **立刻关 session** → 调 `create_link_for_context` / `pay_for_context` → `_safe_result` 追加到 task meta 的 `results` 列表并落快照 → 返回 `AttemptResult.success/failed/skipped`。外层复用通用的 `_run_account_batch_task`(`api/tasks.py`)提供并发、延迟、代理和日志。

账号校验:`_account()` 要求存在且 `platform == "chatgpt"`,否则 404(`api/payments.py:45-49`);任务创建时逐个预校验(`:217-219`)。`operation` 只允许 `link` / `pay`(`:212-214`)。task_id 形如 `payment_{operation}_{毫秒时间戳}`(`:220`)。

#### 新增渠道的步骤

1. 实现一个类,满足 `PaymentChannel` 协议(name / display_name / operations / option_schema / create_link / pay),只 import `services/payment_channels/contracts.py`
2. 在 `load_builtin_payment_channels()`(`services/payment_channels/registry.py:47-50`)里 import 并 `payment_channels.register(...)`
3. 不需要改账号模型,不需要改 `api/payments.py` 的 link/pay/jobs 路由

唯一需要注意的硬编码:卡片资源那四个接口写死了 `channel.lower() != "direct"` 就 404(`api/payments.py:117,125,135,145`),新渠道若也有卡片类资源需要另想办法。另外 `docs/PAYMENT_CHANNELS.md` 的接口清单没提 `/channels/{channel}/cards*` 和 `/jobs`,以代码为准。


### 贡献榜 / 贡献模式(api/contribution.py + services/external_sync.py)

#### 这是什么

一个把本机注册出来的 ChatGPT 账号凭证上传到**第三方共享服务器**(codex2api 形态),换取额度/提现额的机制。`api/contribution.py` 只是一个**反向代理层**:后端代前端去调贡献服务器的 public 接口,前端不直连。真正的凭证上传发生在 `services/external_sync.py`。

#### 默认服务器地址(硬编码)

| 位置 | 值 |
| --- | --- |
| `api/contribution.py:14` | `DEFAULT_CONTRIBUTION_SERVER_URL = "http://new.xem8k5.top:7317/"` |
| `api/config.py:171-172` | 配置项 `contribution_server_url` 缺省也填同一个地址 |
| `api/config.py:175-176` | `custom_contribution_url` 缺省 `http://127.0.0.1:5000` |

注意这是一个**明文 HTTP 的第三方域名**,写在两处代码里。`_resolve_server_url`(`api/contribution.py:47-53`)在用户没配时无声地落到这个默认值,而且给不带 scheme 的输入自动补 `http://`。

#### 数据来源与接口清单

后端不查本地数据库,所有榜单/额度数据都来自贡献服务器。四个接口都是 `POST`(即使下游是 GET):

| 方法 | 路径 | 下游候选端点(按序试,首个成功即返回) | 位置 |
| --- | --- | --- | --- |
| POST | `/api/contribution/quota-stats` | `GET /public/quota-stats` → `GET /public/quota/stats` → `POST /public/quota-stats` → `POST /public/quota/stats`;再额外拉一次 key-info | `api/contribution.py:124-200`,候选表 `:15-20` |
| POST | `/api/contribution/key-info` | `GET /public/key-info` → `GET /public/key/info` → `POST /public/key-info` → `POST /public/key/info` | `:203-234`,候选表 `:21-26` |
| POST | `/api/contribution/redeem` | `POST /public/redeem` → `POST /api/contribution/redeem`,body `{amount_usd}` | `:237-284`,候选表 `:27-30` |
| POST | `/api/contribution/generate-key` | `POST /public/generate`,body 可带 `{name}` | `:287-305` |

请求体统一 `ContributionProxyRequest`(`server_url?`, `key?`,`:33-35`),`redeem` 多一个 `amount_usd: float > 0`(`:38-39`),`generate-key` 用 `{server_url?, name?}`(`:42-44`)。

鉴权头同时发两份:`X-Public-Key: <key>` 和 `Authorization: Bearer <key>`(`api/contribution.py:74-77`)。超时 15s(`:82`)。`quota-stats` 的 key 是可选的(没配就只返回服务器总额度,并在结果里放 `key_error: "未配置 key，仅返回服务器额度统计"`,`:198-199`);`key-info` 和 `redeem` 的 key 是必需的,没配抛 400 `请先配置贡献 key`(`:56-60`)。

端点探测失败时会把每次尝试的 method/endpoint/status_code/detail 收集进 `attempts` 数组,最终抛 502 并把 attempts 带回前端(`:149-156,228-234,266-273`),消息形如"请确认 codex2api 是否已启用该接口"。

#### 上报的内容与目标(隐私影响,如实说明)

**开启贡献模式后会把完整可用的 ChatGPT 账号凭证发到外部服务器。** 这是本仓库中隐私影响最大的一处集成。

触发时机:每次注册成功后,注册任务在后台线程调 `services/external_sync.py:sync_account()`(`api/tasks.py:422-435`)。

| 模式 | 目标地址 | 上报内容 | 位置 |
| --- | --- | --- | --- |
| `contribution_mode = "codex"`(默认) | `contribution_server_url`(默认 `http://new.xem8k5.top:7317/`)的 `/v0/management/auth-files` | 完整 auth-file JSON:`email` / `access_token` / `refresh_token` / `id_token` / `account_id` / `expired` / `last_refresh` / `type=codex` | `services/external_sync.py:146-163` → `platforms/chatgpt/cpa_upload.py:157-253` |
| `contribution_mode = "custom"` | `{custom_contribution_url}/api/upload` | JSON body:`email`、`refresh_token`、`access_token`,**外加完整 `token_json`**(即上面那整个对象) | `services/external_sync.py:119-129` |

也就是说上报的不是"匿名统计",而是**账号邮箱 + 可直接调用 API 的 access_token + 可长期续期的 refresh_token + id_token**。拿到 refresh_token 的一方可以持续换取新的 access_token,等价于长期占有该账号的 API 使用权。

其它需要留意的点:

- 贡献模式**抢占式**:`contribution_enabled` 为真时,CPA / Sub2API / CodexProxy 全部被跳过(`services/external_sync.py:59-63` 注释明说"仅上传到贡献服务器,避免重复上报到其它平台"),上报只去贡献服务器一处
- `custom` 模式的调试日志会把 `extra` 的 key 列表和 `refresh_token` 前 20 字符打进后端标准输出(`services/external_sync.py:90-91,111-112`),日志文件也就成了凭证泄露面
- 上报结果被记进账号 `extra_json.sync_statuses.cpa`(复用 CPA 那个桶,`persist_cpa_sync_result`),名字容易误读
- `quota-stats` / `key-info` / `redeem` / `generate-key` 这四个代理接口本身只发 key,不发账号凭证
- 所有贡献相关 HTTP 都是明文(默认地址是 `http://`),token 在网络上裸奔

#### 能否关闭

**能,而且默认就是关的。**

| 事实 | 依据 |
| --- | --- |
| 开关键 `contribution_enabled`,默认 `"0"` | `api/config.py:169-170`(读配置时兜底写 `0`)、`services/external_sync.py:60`(`config_store.get("contribution_enabled", "0")`) |
| 布尔解析口径 | `_is_config_enabled` 只认 `1/true/yes/on/enabled`,空串走 `default=False`(`services/external_sync.py:15-19`) |
| 关闭后行为 | `sync_account()` 跳过整个贡献分支,继续按 CPA / CodexProxy / Sub2API 各自开关走(`services/external_sync.py:61,165+`) |
| 关闭方式 | `PUT /api/config` 把 `contribution_enabled` 设为 `0`(该键在白名单 `CONFIG_KEYS` 里,`api/config.py:126`);或直接不配 `contribution_server_url`,那样 codex 模式会 fail-fast 报"Contribution 服务器地址未配置"(`services/external_sync.py:150-154`) |
| 代理接口无法"关" | `/api/contribution/*` 四个路由无条件注册(`main.py:126`),但它们是拉取型接口,需要前端主动调,不会自动外发凭证 |

相关配置键一览(全部在 `api/config.py:126-131` 白名单内):

| 键 | 默认 | 作用 |
| --- | --- | --- |
| `contribution_enabled` | `0` | 总开关 |
| `contribution_server_url` | `http://new.xem8k5.top:7317/` | codex 模式目标 + 代理接口目标 |
| `contribution_key` | 空 | public key,用于 key-info / redeem |
| `contribution_mode` | `codex` | `codex` 或 `custom` |
| `custom_contribution_url` | `http://127.0.0.1:5000` | custom 模式目标 |
| `custom_contribution_token` | 空 | custom 模式 Bearer token("请先绑定邮箱"才能拿到) |

前端当前没有贡献相关 UI(`frontend/src/` 里 grep 不到 `contribution`/`贡献`),也就是说这些开关只能通过 `/api/config` 接口操作 —— 反过来说,新接手项目的人容易完全不知道这套机制存在。


### 部署形态对比

仓库提供两套完全独立的镜像定义,**不是同一个 Dockerfile 的 target**。

#### 总览对比表

| 维度 | 完整版 | 无头服务器版 |
| --- | --- | --- |
| Dockerfile | `Dockerfile`(78 行) | `Dockerfile.server`(65 行) |
| compose | `docker-compose.yml` | `docker-compose.server.yml` |
| entrypoint | `docker/entrypoint.sh` | `docker/entrypoint.server.sh` |
| 镜像体积 | 约 5 GB | 约 1.1~1.2 GB(`Dockerfile.server:6`、`docs/SERVER_DEPLOY.md:14`) |
| 前端构建 | `node:20-bookworm-slim` 多阶段 `npm ci && npm run build`(`Dockerfile:3-11`) | 同样(`Dockerfile.server:16-24`) |
| 运行时基础镜像 | `python:3.12-slim`(`Dockerfile:14`) | `python:3.12-slim`(`Dockerfile.server:27`) |
| apt 包 | `curl ca-certificates nodejs libgtk-3-0 libx11-xcb1 libasound2 xvfb xauth`,之后再补 `dos2unix git iproute2 procps`(`Dockerfile:41-46,68`) | 只 `ca-certificates curl nodejs`,构建期临时装 `dos2unix` 并在同层 purge(`Dockerfile.server:45-49,54-59`) |
| Go 工具链 | 装 go1.24.2 到 `/usr/local/go`,加进 PATH(`Dockerfile:44,48`) | **不装** |
| uv | 装 astral uv(`Dockerfile:45`) | 不装 |
| 浏览器 | `playwright install --with-deps chromium firefox`,带 3 次重试;再跑 `scripts/install_camoufox.py` 装 Camoufox(`Dockerfile:50-63`) | **不装任何浏览器** |
| Camoufox 版本参数 | build args `CAMOUFOX_VERSION=135.0.1` / `CAMOUFOX_RELEASE=beta.24`(`Dockerfile:16-17`,compose 里可覆盖) | 无 |
| Turnstile Solver | 开启,`APP_ENABLE_SOLVER=1` | 关闭,`APP_ENABLE_SOLVER=0`(`Dockerfile.server:37`) |
| EXPOSE | `8000 8889`(`Dockerfile:74`) | `8000`(`Dockerfile.server:61`) |
| VOLUME | `/runtime`, `/_ext_targets`(`Dockerfile:76`) | `/runtime`(`Dockerfile.server:63`) |
| entrypoint 命令 | `xvfb-run -a --server-args="-screen 0 1920x1080x24" python main.py`(`docker/entrypoint.sh:22`) | `python main.py`(`docker/entrypoint.server.sh:26`) |
| 适用场景 | 需要有头/无头浏览器、Turnstile Solver、CLIProxyAPI 插件(要 git+go)的桌面或大机器 | 只跑 Web UI + ChatGPT/iCloud 纯协议注册的小 VPS(2 核 2G 也能一两分钟构建完) |

#### 为什么无头版能砍掉浏览器栈

`Dockerfile.server:8-12` 和 `docs/SERVER_DEPLOY.md:9-15` 给的理由:ChatGPT 注册已整体切到 `platforms/chatgpt/protocol` 的纯协议实现,iCloud 声明 `supported_executors = ["protocol"]`,两个平台都不碰浏览器;唯一用 Playwright 的 `payment.open_url_incognito` 是"在本机弹个付款页"的桌面便利功能,无头服务器上没意义,缺浏览器时会自行回退到系统浏览器分支。

**Node 是运行时硬依赖,两个镜像都装。** ChatGPT 的 Sentinel PoW 必须起 node 子进程跑 OpenAI 的 `sdk.js`;缺它算出来的 token 过不了服务端复核,验证码邮件会被**静默丢弃**——链路看着正常但码永远收不到(`Dockerfile:39-40`、`Dockerfile.server:43-44`)。无头版 entrypoint 额外加了一道 `command -v node` 检查,缺失时打警告(`docker/entrypoint.server.sh:21-23`)。

#### compose 差异

| 项 | `docker-compose.yml` | `docker-compose.server.yml` |
| --- | --- | --- |
| 端口 | `8000:8000`(**绑 0.0.0.0**)、`127.0.0.1:8889:8889`、`${CLIPROXYAPI_PORT_BIND:-8317}:8317`(`:26-29`) | 只 `${APP_PORT_BIND:-127.0.0.1:8000}:8000`(`:26-27`) |
| 卷 | `${APP_RUNTIME_BIND:-./data}:/runtime`、`${APP_EXT_TARGETS_BIND:-./_ext_targets}:/_ext_targets`、`${APP_EXTERNAL_LOGS_BIND:-./external_logs}:/app/services/external_logs`(`:30-33`) | 只 `${APP_RUNTIME_BIND:-./data}:/runtime`(`:28-29`) |
| `shm_size` | `1gb`(浏览器需要)(`:34`) | 不设 |
| `DATABASE_URL` | 不设(靠 entrypoint 做符号链接到 `/app/account_manager.db`) | 显式 `sqlite:////runtime/account_manager.db`(`:23`) |
| 其它 env | `APP_CONDA_ENV=docker`、solver 四项、`SOLVER_BROWSER_TYPE`、`PLAYWRIGHT_HEADLESS` 可覆盖 | `APP_ENABLE_SOLVER=0`,无 solver/conda 项 |
| image 名 | 不指定 | `any-auto-register:server`(`:13`) |
| 共同点 | `init: true`、`restart: unless-stopped`、`container_name: any-auto-register`、`CREDENTIAL_ENCRYPTION_KEY_FILE=/runtime/.secrets/credential_key` | 同 |

无头版注释解释了两条取舍(`docker-compose.server.yml:3-6`):不映射 8317 是为了不和宿主机已有的 CLIProxyAPI 抢端口;端口默认只绑 `127.0.0.1`,由宿主机反向代理决定是否对外。

#### entrypoint 做什么

两个脚本都是 `#!/bin/sh` + `set -eu`,构建期用 `dos2unix` 处理过换行(`Dockerfile:69`、`Dockerfile.server:55`):

| 步骤 | `entrypoint.sh` | `entrypoint.server.sh` |
| --- | --- | --- |
| 建目录 | `mkdir -p $RUNTIME_DIR $RUNTIME_DIR/logs`(`:7`) | 同(`:10`) |
| touch 占位 | `account_manager.db` + `logs/solver.log`(`:8-10`) | 只 `account_manager.db`(`:11`) |
| 符号链接 | db → `/app/account_manager.db`;solver.log → `/app/services/turnstile_solver/solver.log`(`:12-13`) | 只 db(`:19`) |
| 密钥目录 | `mkdir -p $(dirname $CREDENTIAL_ENCRYPTION_KEY_FILE)` + `chmod 700`(`:18-19`) | 同(`:16-17`) |
| node 检查 | 无 | 有,缺失打警告(`:21-23`) |
| 启动 | `exec xvfb-run -a --server-args="-screen 0 1920x1080x24" python main.py` | `exec python main.py` |

两个脚本都带同一段关键注释:凭据加密密钥**必须**和数据库一起留在挂载卷里,放镜像内默认位置(`/app/.secrets`)会在每次重建容器时重新生成,导致库里已加密的 iCloud/ChatGPT 凭据全部解不开(decrypt 抛 `InvalidTag`),**没有任何补救手段**(`docker/entrypoint.sh:15-17`、`docker/entrypoint.server.sh:13-15`)。有专门的测试守护这一点:`tests/test_docker_credential_key_persistence.py` 断言两个 compose 文件都设了该变量且值以 `/runtime/` 开头。

#### CI:.github/workflows/docker-image.yml

| 项 | 值 |
| --- | --- |
| 触发 | push 到 `main`/`master`、push tag `v*`、`workflow_dispatch`(`:3-10`) |
| 权限 | `contents: read`, `packages: write`(`:12-14`) |
| 步骤 | checkout → setup-qemu → setup-buildx → 登录 GHCR(用 `GITHUB_TOKEN`)→ docker/metadata-action → build-push-action(`:24-62`) |
| 镜像名 | `ghcr.io/<owner>/any-auto-register`(`:17,45`) |
| tag 策略 | `latest`(仅默认分支)、分支名、tag 名、`sha-<短 sha>`(`:46-50`) |
| 平台 | 只 `linux/amd64`(`:58`) |
| **只构建 `./Dockerfile`** | `file: ./Dockerfile`(`:56`)——`Dockerfile.server` **不在 CI 里**,无头版镜像必须本地 `docker compose -f docker-compose.server.yml up -d --build` 自建 |
| 缓存 | `type=gha`,`mode=max`(`:61-62`) |

设了 QEMU 但 platforms 只写 amd64,多架构其实没生效。

#### ClawCloud 部署要点(docs/CLAWCLOUD_DEPLOY.md)

用的是完整版 GHCR 镜像。必须挂 `/runtime`(必选)、可选挂 `/_ext_targets` 和 `/app/services/external_logs`;Exposed port `8000`,8889 不建议公网暴露;环境变量照抄 compose 那套;验证用 `GET /api/solver/status` 期望 `{"running": true}`。

#### 安全前置(两套都适用)

没设面板密码时,`main.py` 的鉴权中间件会**直接放行所有 `/api/` 请求**(`main.py:90-91`:`if not _cs.get("auth_password_hash", ""): return await call_next(request)`)。这个面板管着账号、Token、接码 API Key、邮箱和代理凭据,对外暴露前必须先在「全局配置 → 安全」设密码(`docs/SERVER_DEPLOY.md` 专门开了一节强调)。另外 CORS 是 `allow_origins=["*"]` + 全 method/header 放开(`main.py:109-114`)。

#### 反向代理注意点

任务日志走 SSE,nginx 必须对 `/api/tasks/` 关掉响应缓冲(`proxy_buffering off; proxy_cache off; chunked_transfer_encoding off; proxy_read_timeout 3600s;`),否则前端看不到实时日志(`docs/SERVER_DEPLOY.md` 给了完整 server 块)。

#### Windows 本地启停脚本

| 脚本 | 行为 |
| --- | --- |
| `start_backend.ps1` | 参数 `-EnvName`(默认 `any-auto-register`)、`-BindHost`(默认 `0.0.0.0`)、`-Port`(默认 8000)、`-RestartExisting`(默认真);检查 conda 存在 → 先调 `stop_backend.ps1 -FullStop 0` 清理旧进程 → `conda run -n <env> python -c "import sys; print(sys.executable)"` 解析解释器 → 设 `$env:HOST/$env:PORT` → `& $pythonExe main.py` |
| `start_backend.bat` | 同逻辑的 cmd 版,env 名取 `%APP_CONDA_ENV%` 回退 `any-auto-register`,`HOST`/`PORT`/`RESTART_EXISTING` 都可用环境变量覆盖 |
| `stop_backend.ps1` | 参数 `-BackendPort 8000 -SolverPort 8889 -CLIProxyAPIPort 8317 -FullStop 1`;`FullStop=0` 时**不动 8317**(启动前清理不杀插件);用 `Get-NetTCPConnection -State Listen` 找 PID,三级升级停止:`taskkill /PID /T` → `taskkill /T /F` → `Stop-Process -Force`,每级等 6s |
| `stop_backend.bat` | 薄封装,转发到 ps1,端口默认值同上 |

两个 start 脚本都**硬依赖 conda**,没有 conda 直接报错退出;`main.py:28-59` 也会在 conda 环境名不等于 `APP_CONDA_ENV` 时打告警(`APP_CONDA_ENV=docker` 时跳过检查)。


### 环境变量总表

以下是 `grep -rn 'getenv\|environ' --include=*.py` 全仓扫描后筛出的真实配置项(排除测试与脚本内部临时变量)。

#### 服务与运行时

| 变量名 | 默认值 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `HOST` | `0.0.0.0` | uvicorn 监听地址 | `main.py:160` |
| `PORT` | `8000` | uvicorn 端口 | `main.py:161` |
| `APP_RELOAD` | `0` | 是否开 uvicorn 热重载(认 `1/true/yes`) | `main.py:162` |
| `APP_CONDA_ENV` | `any-auto-register` | 期望的 conda 环境名;设为 `docker` 时跳过环境告警 | `main.py:28`(判断在 `:48-54`) |
| `CONDA_DEFAULT_ENV` | — | conda 注入,用于检测当前环境;缺失时从 `sys.prefix` 里找 `envs/<name>` | `main.py:32,36-40` |
| `DATABASE_URL` | `sqlite:///account_manager.db`(相对 cwd) | SQLModel engine 连接串 | `core/db.py:18` |
| `APP_RUNTIME_DIR` | `/runtime` | 容器内持久化根目录(仅 entrypoint 读,Python 侧不读) | `docker/entrypoint.sh:5`、`docker/entrypoint.server.sh:8` |

#### Turnstile Solver

| 变量名 | 默认值 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `APP_ENABLE_SOLVER` | `1` | 后端启动时是否自动拉起 solver;`0/false/no` 关闭 | `services/solver_manager.py:15` |
| `SOLVER_PORT` | `8889` | solver 监听端口 | `services/solver_manager.py:19`;另在 `core/base_captcha.py:7`、`core/base_platform.py:152`、`services/turnstile_solver/api_solver.py:1108` 各自读一次 |
| `LOCAL_SOLVER_URL` | `http://127.0.0.1:{SOLVER_PORT}` | 调用 solver 的完整 URL(优先于端口拼接) | `services/solver_manager.py:23`、`core/base_captcha.py:7`、`core/base_platform.py:151` |
| `SOLVER_BIND_HOST` | `0.0.0.0` | solver 绑定网卡 | `services/solver_manager.py:27` |
| `SOLVER_BROWSER_TYPE` | `camoufox` | solver 用哪种浏览器(camoufox / patchright 系) | `services/solver_manager.py:31` |

`core/base_platform.py:150-152` 的解析顺序是"配置项 → `LOCAL_SOLVER_URL` → 端口拼接",所以 config_store 里的值优先于环境变量。

#### 浏览器行为

| 变量名 | 默认值 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `PLAYWRIGHT_HEADLESS` | 未设 | 强制覆盖有头/无头,优先级最高 | `core/browser_runtime.py:38`(经 `parse_env_bool` `:17-31`) |
| `REGISTER_HEADLESS` | 未设 | 同上,次优先 | `core/browser_runtime.py:38` |
| `DISPLAY` | — | Linux 有头模式检查;缺失时抛"未检测到 DISPLAY,Docker 内请启用 Xvfb" | `core/browser_runtime.py:58-64` |
| `LD_LIBRARY_PATH` | — | camoufox 缓存目录被前插到这个变量(非 Windows) | `services/turnstile_solver/start.py:11-29`(写入) |

布尔解析口径:`1/true/yes/on` 为真,`0/false/no/off` 为假,其它值打 warning 并当未设置(`core/browser_runtime.py:13-14,30-31`)。

#### 凭据加密

| 变量名 | 默认值 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `CREDENTIAL_ENCRYPTION_KEY` | 空 | 直接给密钥材料,base64 或 hex 编码的 **32 字节**;非法长度抛 `CredentialKeyError` | `core/secret_box.py:53`(常量名 `:20`,解码 `:33-44`) |
| `CREDENTIAL_ENCRYPTION_KEY_FILE` | `<cwd>/.secrets/credential_key` | 密钥文件路径;文件不存在则用 `secrets.token_bytes(32)` 生成,以 **0600** 权限创建后写入 | `core/secret_box.py:48-49`(常量名 `:21`,默认 `:22`,生成 `:61-67`) |

算法是 AES-256-GCM 信封加密,密文前缀 `v1:`(`core/secret_box.py:24-26`)。**两个 Docker compose 都把 KEY_FILE 指到 `/runtime/.secrets/credential_key`**,因为默认位置在镜像内、每次重建会换密钥导致旧数据永久解不开。

#### 鉴权

| 变量名 | 默认值 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `APP_JWT_SECRET` | 空 | 面板 JWT(HS256)签名密钥;为空则用 config_store 里的 `auth_jwt_secret`,还没有就生成 `token_hex(32)` 并落库 | `api/auth.py:36-43` |

#### ChatGPT Sentinel PoW

| 变量名 | 默认值 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `OPENAI_SENTINEL_NODE_PATH` | `node`(靠 PATH 解析) | node 可执行文件的绝对路径;PoW 求解要起 node 子进程跑 OpenAI 的 `sdk.js`,解析不到会导致验证码收不到 | `platforms/chatgpt/protocol/sentinel_quickjs.py:39-40` |

子进程继承 `**os.environ`(`platforms/chatgpt/protocol/sentinel_quickjs.py:104`)。历史上注册流程曾直接写 `os.environ` 再在 finally 还原,现在改成 `AuthFlow(env_overrides=...)` 显式传参,不再污染全局(`platforms/chatgpt/protocol/auth_flow.py:94,562,567`)。

#### 卡片库(直卡渠道)

| 变量名 | 默认值 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `MIN_CARDS_DB` | `platforms/chatgpt/cards.db` | 卡片库 SQLite 路径 | `platforms/chatgpt/payment_channels/direct/card_store.py:21-23` |
| `MIN_ENABLE_TEST_CARD` | 未设(禁用) | 设为 `1/true/yes/on` 才注入内置测试卡,避免 live mode 误用 | `platforms/chatgpt/payment_channels/direct/card_store.py:25-27` |
| `MIN_TEST_CARD_NUMBER` / `_EXP_MONTH` / `_EXP_YEAR` / `_CVC` | 仓库内占位值 | 测试卡各字段,运行时注入 | `platforms/chatgpt/payment_channels/direct/card_store.py:32-35` |

#### 邮箱导入

| 变量名 | 默认值 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `MAIL_IMPORT_OAUTH_WORKERS` | `8`(clamp 到 1~32,再不超过记录数) | OAuth 邮箱可用性检查的并发线程数 | `services/mail_imports/providers.py:340-348` |

#### 构建期(仅 Dockerfile / 脚本)

| 变量名 | 默认值 | 作用 | 读取位置 |
| --- | --- | --- | --- |
| `CAMOUFOX_VERSION` | `135.0.1` | Camoufox 版本,build arg | `Dockerfile:16`,消费于 `scripts/install_camoufox.py:13` |
| `CAMOUFOX_RELEASE` | `beta.24` | Camoufox release 后缀 | `Dockerfile:17`,消费于 `scripts/install_camoufox.py:14` |
| `PYTHONDONTWRITEBYTECODE` / `PYTHONUNBUFFERED` / `PIP_NO_CACHE_DIR` | `1` | 标准 Python 容器优化 | `Dockerfile:19-21`、`Dockerfile.server:29-31` |

compose 层还有几个只用于 bind 路径/端口拼装的变量,Python 侧不读:`APP_RUNTIME_BIND`(默认 `./data`)、`APP_EXT_TARGETS_BIND`(默认 `./_ext_targets`)、`APP_EXTERNAL_LOGS_BIND`(默认 `./external_logs`)、`CLIPROXYAPI_PORT_BIND`(默认 `8317`)、`APP_PORT_BIND`(默认 `127.0.0.1:8000`)。

#### 环境变量 → config_store 的隐式回退(重要机制)

`core/config_store.py` 有一层容易被忽略的桥接:**任何 config 键都能用环境变量或 `.env` 文件兜底**。

| 环节 | 实现 |
| --- | --- |
| `.env` 位置 | 仓库根目录 `<repo>/.env`(`core/config_store.py:12`) |
| `.env` 解析 | 跳过空行/`#` 注释,支持 `export ` 前缀,去掉成对引号(`:52-76`,`_normalize_config_value` `:15-19`) |
| 优先级 | `os.environ` **覆盖** `.env`(`_runtime_env_values`,`:79-89`) |
| 键名模糊匹配 | 一个 config 键会生成候选:原样、全小写、全大写、非字母数字换 `_` 后的三种变体(`_config_key_candidates`,`:29-49`) |

也就是说 `cpa_api_url` 这个 config 键可以用环境变量 `CPA_API_URL` 提供,`cliproxyapi_management_key` 可以用 `CLIPROXYAPI_MANAGEMENT_KEY` 提供 —— 数据库里没配就自动回退。所有出现在 `api/config.py:CONFIG_KEYS` 里的键(接码 key、邮箱 token、代理、CPA/Sub2API/贡献服务器地址等)都吃这个规则,部署时可以完全用环境变量注入而不碰面板。反面是:宿主机上任何同名环境变量都会静默影响配置。

### 依赖清单要点

#### requirements.txt(20 项)

| 依赖 | 版本约束 | 用途 | 证据 |
| --- | --- | --- | --- |
| `fastapi` | >=0.110.0 | Web 框架,全部 `api/*.py` 路由 | `main.py:5` |
| `uvicorn` | >=0.29.0 | ASGI server,`python main.py` 里直接起 | `main.py:158-163` |
| `quart` | >=0.19.4 | **只**给 Turnstile Solver 用的独立异步 Web 框架(solver 是单独进程) | `services/turnstile_solver/api_solver.py:10` |
| `sqlmodel` | >=0.0.16 | ORM + 模型定义,SQLite | `core/db.py:6,19` |
| `curl-cffi` | >=0.16.2,<0.17 | **核心**:带浏览器 TLS/JA3 指纹的 HTTP 客户端(`impersonate="chrome110"`),ChatGPT 协议注册、状态探测、CPA 上传、直卡支付全靠它 | `platforms/chatgpt/status_probe.py`、`platforms/chatgpt/payment.py`、`platforms/chatgpt/sub2api_upload.py`、`platforms/chatgpt/payment_channels/direct/transport.py`、`platforms/chatgpt/cpa_upload.py:229-237` |
| `requests` | >=2.31.0 | 普通内部/管理接口调用(CLIProxyAPI 同步、贡献服务器、插件健康检查) | `services/external_apps.py:15`、`services/cliproxyapi_sync.py:109`、`services/cpa_manager.py:8`、`api/contribution.py:6` |
| `pysocks` | >=1.7.1 | SOCKS5 代理支持(`socks5h://`) | `platforms/chatgpt/constants.py`、`platforms/chatgpt/protocol/http_client.py` |
| `playwright` | >=1.43.0 | 浏览器自动化执行器 + 付款页弹窗 | `core/executors/playwright.py`、`platforms/chatgpt/payment.py` |
| `patchright` | >=1.52.5 | 反检测版 Playwright,solver 的一种 browser_type | `services/turnstile_solver/api_solver.py:18,174` |
| `camoufox` | >=0.4.0 | 反指纹 Firefox,solver 默认 browser_type | `services/turnstile_solver/api_solver.py:13,177` |
| `pydantic` | >=2.0.0 | 全部请求/响应模型 | 各 `api/*.py` |
| `jwcrypto` | >=1.5.0 | JOSE/JWT 处理(未在主链路 grep 到直接 import,可能是间接或历史依赖) | — |
| `cbor2` | >=5.4.0 | CBOR 编解码(疑似 WebAuthn/passkey 相关,主链路无直接 import) | — |
| `aiofiles` | >=23.0.0 | 异步文件 IO(主链路无直接 import,可能被 quart/playwright 间接需要) | — |
| `rich` | >=13.7.1 | 终端彩色输出/日志 | `services/turnstile_solver/api_solver.py`、`platforms/chatgpt/protocol/fingerprint.py` |
| `pyinstaller` | >=6.0.0 | 打包后端成单文件可执行(Electron 用) | `electron/build-backend.sh:11-20` |
| `httpx` | >=0.27.0 | 流量记录兼容层同时支持 httpx/curl_cffi Response | `core/traffic.py:64,91` |
| `selectolax` | >=0.3.21 | 快速 HTML 解析(主链路无直接 import) | — |
| `names` | >=0.3.0 | 随机英文姓名生成(注册时造用户名) | `core/base_mailbox.py:2479` |
| `cryptography` | >=41.0.0 | AES-256-GCM 凭据加密 | `core/secret_box.py:18` |

标 "—" 的四个(`jwcrypto` / `cbor2` / `aiofiles` / `selectolax`)在全仓 `.py` 里 grep 不到直接 import,属于**可疑冗余或间接依赖**,精简镜像时值得逐个验证。

#### 非 Python 运行时依赖

| 运行时 | 必需性 | 用途 | 安装位置 |
| --- | --- | --- | --- |
| **Node.js** | **硬依赖(两套镜像都装)** | ChatGPT Sentinel PoW:起 node 子进程跑 OpenAI `sdk.js`;缺失会导致注册收不到验证码且无报错 | `Dockerfile:42`、`Dockerfile.server:46`;路径可用 `OPENAI_SENTINEL_NODE_PATH` 指定 |
| **Node 20 + npm** | 构建期 | 前端 `npm ci && npm run build`,产物 `/app/static` | `Dockerfile:3-11`、`Dockerfile.server:16-24` |
| **Go 工具链** | 可选(仅完整版) | CLIProxyAPI 插件用 `go run ./cmd/server` 启动 | `Dockerfile:44`(go1.24.2);本机需 PATH 里有 `go` 或落在 `_find_go` 的三个候选路径 |
| **git** | 可选(仅完整版) | 插件安装/更新全靠 `git clone/fetch/checkout` | `Dockerfile:68`;`Dockerfile.server` **没装** |
| **Xvfb + xauth** | 仅完整版 | 无显示环境下跑有头浏览器 | `Dockerfile:43`,entrypoint 用 `xvfb-run` |
| **GTK/X11/ALSA 库** | 仅完整版 | `libgtk-3-0 libx11-xcb1 libasound2`,浏览器运行库 | `Dockerfile:43` |
| **uv** | 构建期辅助 | astral uv,装了但主流程仍用 pip | `Dockerfile:45` |
| **Playwright 浏览器二进制** | 仅完整版 | chromium + firefox,`--with-deps` | `Dockerfile:54` |
| **Camoufox 二进制** | 仅完整版 | 反指纹 Firefox,由 `scripts/install_camoufox.py` 下载 | `Dockerfile:63` |
| **conda** | 仅 Windows 本机启动 | `start_backend.ps1/.bat` 用 `conda run` 解析解释器,没有就报错退出 | `start_backend.ps1:12-16` |
| **PowerShell** | 仅 Windows | 停止脚本 + 插件卸载时的进程清理 | `stop_backend.ps1`、`services/external_apps.py:137` |
| **Electron 33 + electron-builder 25** | 仅打包桌面版 | 见下一节 | `electron/package.json:14-17` |


### Electron 桌面壳

一个非常薄的壳,总共 101 行 JS。目录 `electron/`,文件:`main.js`(101 行)、`package.json`、`electron-builder.yml`、`build-backend.sh`、`package-lock.json`。**没有 preload、没有 IPC、没有渲染进程代码** —— UI 完全是后端 `/` 返回的那套 SPA。

#### main.js 做什么

| 环节 | 实现 | 位置 |
| --- | --- | --- |
| 端口 | 硬编码 `const PORT = 8000`,不读环境变量 | `electron/main.js:6` |
| 模式判定 | `isDev = !app.isPackaged` | `:7` |
| 后端路径 | 生产模式取 `resources/backend/backend/backend[.exe]` | `:12-19` |
| **拉起后端** | **仅生产模式**;`isDev` 时只打印 `[dev] 请先在项目根目录运行 start_backend.bat 或 start_backend.ps1` 并 return | `:21-25` |
| 子进程 | `spawn(backendPath, [], {cwd: resources/backend/backend, env: {...process.env, PORT: "8000"}, stdio: ['ignore','pipe','pipe']})`,stdout/stderr 转发到 console | `:30-41` |
| 健康等待 | 轮询 `GET http://localhost:8000/api/platforms`,`statusCode < 500` 算通;最多 30 次,每次间隔 1s;超时 reject `后端启动超时` | `:44-58` |
| 窗口 | `BrowserWindow` 1280x800,title `any-auto-register`,`contextIsolation: true`,直接 `loadURL('http://localhost:8000')` | `:60-72` |
| 启动序列 | `whenReady` → `startBackend()` → `await waitForBackend()`;失败弹 `dialog.showErrorBox('启动失败', ...)` 然后 `app.quit()` | `:74-90` |
| macOS activate | 无窗口时重建 | `:87-89` |
| 退出 | 非 darwin 时 `window-all-closed` 即 quit;`will-quit` 里 `backendProcess.kill()` | `:92-101` |

#### 和 Python 后端的关系(已知行为)

**开发模式下 Electron 不会自动拉起后端。** `startBackend()` 开头就 `if (isDev) { console.log(...); return }`(`electron/main.js:22-25`),开发者必须先手动跑 `start_backend.bat` / `start_backend.ps1`(或 `python main.py`)。如果忘了,窗口不会出现:`waitForBackend()` 轮询 30 秒后 reject,弹一个"启动失败 / 后端启动超时"错误框然后整个 app 退出。

其它耦合点:

- 端口写死 8000,两边都不可配。后端若因 `PORT` 环境变量或占用改了端口,壳就连不上
- 生产模式给子进程注入 `PORT=8000`,但**不注入** `DATABASE_URL` / `CREDENTIAL_ENCRYPTION_KEY_FILE` / `APP_CONDA_ENV`,所以 PyInstaller 后端会用相对 cwd 的默认值:数据库落在 `resources/backend/backend/account_manager.db`,密钥落在同目录 `.secrets/credential_key`。应用更新覆盖 resources 目录时这两样都可能丢
- 健康检查打的是 `/api/platforms`。这个路径在有面板密码时会被鉴权中间件拦成 401 —— 但 401 < 500,所以仍算"通"(`electron/main.js:48`),逻辑上没问题
- `backendProcess.kill()` 只发默认 SIGTERM 给 PyInstaller 主进程,solver 子进程和 CLIProxyAPI 插件进程未必跟着退出;后端自己的 lifespan 关闭钩子会调 `solver_manager.stop()`(`main.py:75-78`),但被强杀时不保证执行

#### 打包流程

两步,先 Python 后 Electron:

**第 1 步:`npm run build:backend` → `bash electron/build-backend.sh`**

| 步骤 | 命令 | 位置 |
| --- | --- | --- |
| 切到仓库根 | `cd "$SCRIPT_DIR/../"` | `electron/build-backend.sh:5-8` |
| 装 PyInstaller | `pip install pyinstaller --quiet` | `:11` |
| 打包 | `pyinstaller --onefile --name backend --add-data "platforms:platforms" --add-data "core:core" --add-data "api:api" --add-data "services:services" --add-data "static:static" main.py` | `:14-20` |
| 复制产物 | `mkdir -p electron/backend && cp dist/backend* electron/backend/` | `:23-24` |

注意 `--add-data` 用的是 **POSIX 冒号分隔符**,在 Windows 上 PyInstaller 要求分号,所以这个脚本只能在 macOS/Linux(或 WSL/Git Bash)跑。而且 `--add-data` 只带了这 5 个目录,`requirements.txt`、`.env`、`docker/` 等不在内;`platforms` 目录下的 `.js`(如 `openai_sentinel_quickjs.js`)靠 `--add-data platforms` 一并带上。

**第 2 步:`npm run build:win|mac|linux|all` → electron-builder**

| 配置 | 值 | 位置 |
| --- | --- | --- |
| appId | `com.anyautoregister.app` | `electron/electron-builder.yml:1` |
| productName | `Any Auto Register` | `:2` |
| 输出 | `dist/` | `:6` |
| extraResources | `backend/` → `resources/backend/`(filter `**/*`) | `:10-14` |
| mac | dmg + zip,`build/icon.icns`,category `public.app-category.utilities` | `:16-21` |
| win | nsis,`build/icon.ico` | `:23-26` |
| linux | AppImage,`build/icon.png`,category Utility | `:28-32` |
| nsis | `oneClick: false`,允许改安装目录 | `:34-36` |

`package.json` 脚本:`dev`(`electron .`)、`build:backend`、`build:mac` / `build:win` / `build:linux` / `build:all`;devDependencies 只有 `electron ^33.0.0` 和 `electron-builder ^25.0.0`(`electron/package.json:6-17`)。

**打包版的能力缺口**:PyInstaller 产物里没有 Playwright/Camoufox 浏览器二进制、没有 Go、没有 git、也没有捆绑 Node。所以桌面版能跑纯协议注册(前提是宿主机 PATH 里有 node),但 Turnstile Solver、有头浏览器执行器和 CLIProxyAPI 插件安装在干净机器上都会失败。

### 运维风险清单

按影响面从高到低,只陈述事实与依据。

#### 1. 贡献模式会把完整凭证发到硬编码的第三方明文 HTTP 地址

`http://new.xem8k5.top:7317/` 同时硬编码在 `api/contribution.py:14` 和 `api/config.py:172`。开启 `contribution_enabled` 后,每个注册成功的账号的 email + access_token + refresh_token + id_token 会被上传(`services/external_sync.py:119-163`)。默认关闭,且前端没有对应 UI —— 但这也意味着接手者可能既不知道它存在,也不知道它已被某个 `.env` / 环境变量打开(config_store 有环境变量回退,见环境变量章节)。**明文 HTTP,凭证在网络上无加密传输。**

#### 2. `.env` / 环境变量能静默改写任意业务配置

`core/config_store.py:79-89` 让 `os.environ` 覆盖 `.env` 再覆盖"未在库里配置的键",而 `_config_key_candidates`(`:29-49`)做全大写/全小写/下划线归一的模糊匹配。后果:宿主机上一个恰好同名的环境变量(比如 CI runner 注入的)会改掉贡献服务器地址、CPA 地址、接码 key 等。排查配置问题时必须同时看库、`.env` 和进程环境。

#### 3. 凭据密钥文件的位置与权限

`core/secret_box.py:52-67`:密钥文件不存在就现场生成。默认路径是 **`Path.cwd() / ".secrets/credential_key"`**,跟着工作目录跑 —— 从不同 cwd 启动后端会各自生成不同密钥,旧数据全部解不开(`InvalidTag`,无补救)。生成时用 `os.open(..., 0o600)` 先建再写,权限处理是正确的;但**已存在的文件不校验权限**,从别处拷来的 644 文件会被照用。两个 compose 都把它钉在 `/runtime/.secrets/credential_key` 并有测试守护(`tests/test_docker_credential_key_persistence.py`),裸机部署没有同等保护。

#### 4. 插件系统的硬编码与宿主机假设

| 风险 | 依据 |
| --- | --- |
| git 远端硬编码,不可配置 | `services/external_apps.py:31`;上游改名/转移/被墙都要改代码 |
| `_ext_targets` 落在**仓库上一级目录** | `services/external_apps.py:17-18`(`parents[2]`);裸机部署时会在项目外面创建目录,备份容易漏 |
| 依赖宿主机 git + Go | `_run_git`(`:66`)、`_find_go`(`:305-307`);`Dockerfile.server` 两者都没装,插件页在无头镜像里必然报错 |
| `go run` 每次启动现场编译 | `:337`;首启可能超过 90s 超时(`:24`),且要求 Go module 代理可达 |
| `netstat -ano -p tcp` 是 Windows 语法 | `:252-256`;Linux 上 `_find_pid_by_port` 恒返回 None(异常被吞),端口占用检测失效,`stop()` 里"杀掉端口上的陌生进程"这条兜底在 Linux 完全不生效 |
| Linux 上删不掉目录没有兜底 | `_kill_processes_touching_path` 开头 `if os.name != "nt": return`(`:132-133`);12 轮 rmtree 失败就只能报错 |
| `reset --hard` + `clean -fd` | `:200-201,222-223`;插件目录里的任何手工文件(除被重新生成的 `config.local.yaml`)会被无声删除 |
| 管理密钥默认值是字面量 | `cliproxyapi_management_key` 缺省 `"cliproxyapi"`(`services/external_apps.py:314`、`services/cliproxyapi_sync.py:42`);未改默认值又把 8317 暴露出去等于管理接口敞开 |
| 生成配置的缩进硬编码 | `:324` append 时写死两空格缩进,上游 YAML 结构变化会静默产出错误配置 |

#### 5. 网络调用一律 `verify=False`

`services/cliproxyapi_sync.py:121`、`services/cpa_manager.py:116`、`platforms/chatgpt/cpa_upload.py:234` 全部关掉 TLS 校验,`cliproxyapi_sync` 还主动 `urllib3.disable_warnings`(`:112`)。对本机 127.0.0.1 无所谓,但这些 URL 都是可配置的远端地址,配成公网 HTTPS 时中间人无法被发现。

#### 6. 上传路径没有重试,状态同步有

`_retry_sync_call` 只包住 `list_auth_files` 和 `_probe_remote_auth`(`services/cliproxyapi_sync.py:310-312,341,366`),而 `upload_to_cpa` / `upload_to_sub2api` / 贡献上传都是一次性调用。网络抖动时凭证上传直接失败,只能靠"补传远端未发现的 auth-file"批量操作人工重跑。重试也没有指数退避(固定 0.4s × 3,`:16-17`)。

#### 7. 镜像体积与 CI 覆盖

完整版约 5GB(Playwright chromium+firefox + Camoufox + Go + GTK/Xvfb),CI 每次 push 到默认分支都构建并推 GHCR,只有 `linux/amd64`(`.github/workflows/docker-image.yml:56-58`)。`Dockerfile.server` **完全不在 CI 里**,只能本地构建,也就没有任何自动化验证——它坏了要等到有人手动部署时才发现。设了 QEMU 但没用上多架构。

#### 8. 面板默认无鉴权 + CORS 全开

`main.py:90-91` 未设 `auth_password_hash` 时放行全部 `/api/`;`main.py:109-114` CORS `allow_origins=["*"]` 且 method/header 全放开。完整版 `docker-compose.yml:27` 把 8000 绑在 `0.0.0.0`(无头版才默认绑 127.0.0.1)。这个面板管着账号密码、token、接码 key、邮箱和代理凭据。

#### 9. 日志里的凭证碎片

`services/external_sync.py:90-91,111-112` 的三行 `[DEBUG] print` 会把 extra 的 key 列表和 refresh_token 前 20 字符写进后端 stdout;容器日志/`external_logs` 都会留存。

#### 10. 导出功能是完整凭证的明文出口

`EXPORT_FORMATS` 里的 `email_pw_2fa_at_rt`、`rt`、`json` 等格式会把 access_token / refresh_token / TOTP 密钥以明文文本返回给前端(`services/account_export.py:189-246`)。接口 `POST /api/accounts/export-text` 单次可导出当前筛选下的**全部**账号,没有条数上限(对比批量操作有 1000 上限)。库里凭据是加密存储的(`core/secret_box.py`),但导出等于一键解密全量落地。

#### 11. 状态缓存导致的语义陷阱

`pending_only=true`(`api/integrations.py:85-90`)和 `backfill_chatgpt_account_to_cpa` 的缓存复用(`services/chatgpt_sync.py:312-319`)都读 `extra.sync_statuses.cliproxyapi` 的**历史值**。从没同步过的账号该字段为空,`pending_only` 会把它们全部过滤掉;而缓存里是陈旧的 `not_found` 时又会触发不必要的重复上传。正确用法是先跑一次批量状态同步再补传。

#### 12. Electron 桌面版的路径与运行时缺口

端口硬编码 8000(`electron/main.js:6`);开发模式不拉后端是**已知行为**(`:22-25`);生产模式不给子进程注入 `DATABASE_URL` / `CREDENTIAL_ENCRYPTION_KEY_FILE`,数据库和密钥落在 `resources/backend/backend/` 下,应用升级覆盖 resources 时有丢数据风险;打包产物不含浏览器/Go/git/Node,solver 与插件功能在干净机器上不可用;`build-backend.sh` 的 `--add-data` 用冒号分隔,Windows 原生环境跑不了。

---

## 十三、前端基础架构与注册任务页

### 07a 前端核心:基础架构 / 路由鉴权 / 注册任务页 / 实时日志

> 本文覆盖 any-auto-register 前端的**工程骨架、主题、路由与鉴权、注册任务页 `RegisterTaskPage.tsx`、实时日志 `TaskLogPanel.tsx`、仪表盘与任务/代理页**。
> Accounts.tsx / Settings.tsx / ICloud.tsx / Payments.tsx 等大页面由另一份文档负责。
> 所有位置引用格式为 `文件:行号`,行号对应仓库当前状态。

#### 一、技术栈与构建

##### 1.1 依赖清单(frontend/package.json)

| 类别 | 包 | 版本约束 | 说明 |
|---|---|---|---|
| 运行时 | `react` / `react-dom` | `^19.2.4` | **React 19**,不是 18 |
| 运行时 | `antd` | `^5.24.6` | **Ant Design 5**,唯一 UI 组件库(全站无 MUI / Tailwind) |
| 运行时 | `@ant-design/icons` | `^5.6.1` | 图标 |
| 运行时 | `@ant-design/v5-patch-for-react-19` | `^1.0.3` | **必需补丁**,见 1.2 |
| 运行时 | `react-router-dom` | `^7.13.1` | 路由 v7,用的是 `BrowserRouter` 组件式写法(非 data router) |
| 构建 | `vite` | `^8.0.0` | **Vite 8** |
| 构建 | `@vitejs/plugin-react` | `^6.0.0` | React 插件(Babel 版) |
| 语言 | `typescript` | `~5.9.3` | TS 5.9 |
| 类型 | `@types/react` `^19.2.14`、`@types/react-dom` `^19.2.3`、`@types/node` `^24.12.0` | | |
| Lint | `eslint` `^9.39.4`、`typescript-eslint` `^8.56.1`、`eslint-plugin-react-hooks` `^7.0.1`、`eslint-plugin-react-refresh` `^0.5.2`、`globals` `^17.4.0` | | ESLint 9 flat config |

关键结论:**没有任何数据请求库(无 axios / react-query / SWR)、没有状态管理库(无 redux / zustand / jotai)、没有表单库(只用 antd `Form`)、没有测试框架**。全部靠原生 `fetch` + React 内置 hooks。

##### 1.2 React 19 兼容补丁

`frontend/src/main.tsx:3` 在**任何其它 import 之前**引入 `@ant-design/v5-patch-for-react-19`。原因写在同文件 `:1-2` 的注释里:antd 5 的静态方法(`message` / `notification` / `Modal.confirm`)内部依赖 React 18 时代的 `ReactDOM.render`,React 19 已移除该 API,不打补丁则**全站提示会静默失效**(不报错、也不显示)。新增依赖或调整 import 顺序时不要把这一行挪到后面。

##### 1.3 npm scripts

| script | 命令 | 说明 |
|---|---|---|
| `dev` | `vite` | 开发服务器,默认 5173,`/api` 走代理(见 1.5) |
| `build` | `tsc -b && vite build` | **先做 TS 项目引用构建校验,再打包**;类型错误会直接阻断构建 |
| `lint` | `eslint .` | flat config,无 `--fix` |
| `preview` | `vite preview` | 预览 `../static` 产物 |

##### 1.4 入口与挂载

`frontend/index.html:5` 的 favicon 指向 `/logo.png`(由后端 static 目录提供),`:7` title 仍是脚手架默认的 `frontend`(未定制)。挂载点 `#root`。

`frontend/src/main.tsx:13-17` 用 `createRoot` + `<StrictMode>` 渲染 `<App />`。注意 StrictMode 下开发环境 effect 会双跑,`TaskLogPanel` 的流式连接对此有依赖(见第五章)。

`frontend/src/main.tsx:11` 在挂载前先执行 `applyThemeVars(localStorage.getItem('theme') || 'dark')`,原因:登录页不在 `AppContent` 树里,拿不到 `ConfigProvider`,必须让 CSS 变量在首屏之前就落到 `<html>` 上。

##### 1.5 vite.config.ts

`frontend/vite.config.ts` 只有 21 行,三件事:

| 配置 | 值 | 意义 |
|---|---|---|
| `resolve.alias` | `'@' -> ./src` | 全站 import 一律写 `@/pages/...`、`@/lib/utils` |
| `build.outDir` | `../static` | **构建产物直接落到后端 FastAPI 的 `static/` 目录**,由后端托管,不做独立部署 |
| `build.emptyOutDir` | `true` | 每次构建清空 `../static`(注意:手工放进 static 的文件会被清掉) |
| `server.proxy` | `'/api' -> http://localhost:8000` | 开发期把 `/api/*` 转发到本地 FastAPI |

因为产物同源托管,前端所有请求都用**相对路径 `/api/...`**,不存在 baseURL / CORS 配置。

##### 1.6 tsconfig 项目引用结构

三份配置,标准 Vite 模板的 solution-style 布局:

| 文件 | include | 作用 |
|---|---|---|
| `tsconfig.json` | `files: []` + `references` | 只做入口,引用下面两份;`tsc -b` 从这里开始 |
| `tsconfig.app.json` | `["src"]` | 浏览器侧代码,`types: ["vite/client"]`,`jsx: react-jsx` |
| `tsconfig.node.json` | `["vite.config.ts"]` | Node 侧配置文件,`types: ["node"]` |

两份子配置共同开启的严格项(`frontend/tsconfig.app.json:20-25`):`strict`、`noUnusedLocals`、`noUnusedParameters`、`erasableSyntaxOnly`、`noFallthroughCasesInSwitch`、`noUncheckedSideEffectImports`、`verbatimModuleSyntax`、`moduleDetection: force`、`noEmit`。

值得注意的两点:

- `verbatimModuleSyntax: true` ⇒ 只导入类型时**必须写 `import type`**,否则报错。
- `noUnusedLocals` / `noUnusedParameters` 打开 ⇒ 留一个没用到的变量就会让 `npm run build` 失败。
- **但 `noImplicitAny` 之外没有禁 `any`**,加上 ESLint 未开 `no-explicit-any` 报错级规则,所以代码里散落大量 `any`(见 1.7)。
- `frontend/tsconfig.app.json:26-29` 里的 `baseUrl` + `paths` 与 vite alias 一一对应,改别名要两边同步。

##### 1.7 eslint.config.js 要点

`frontend/eslint.config.js` 23 行,ESLint 9 flat config:

```js
globalIgnores(['dist']),
{ files: ['**/*.{ts,tsx}'], extends: [js.recommended, tseslint.recommended,
  reactHooks.configs.flat.recommended, reactRefresh.configs.vite] }
```

要点与坑:

- `globalIgnores(['dist'])` —— 但真实产物目录是 `../static`(见 1.5),**忽略名和实际 outDir 不一致**,好在 `static` 在 `src` 之外、`eslint .` 从 frontend 根跑时仍可能扫到,属于配置遗留。
- 只 `extends` recommended,**没有任何自定义 `rules` 覆盖**,因此 `@typescript-eslint/no-explicit-any` 停留在 recommended 的 warn 级别,不阻断。
- `languageOptions.ecmaVersion: 2020` 与 `tsconfig` 的 `target: ES2023` 不一致(仅影响 lint 解析,不影响产物)。
- 没有开 `tseslint.configs.recommendedTypeChecked`,即**不做类型感知的 lint**。

`any` 的实际分布(全量统计):

| 文件 | `any` 出现次数 |
|---|---|
| `src/pages/Accounts.tsx` | 28 |
| `src/pages/Settings.tsx` | 14 |
| `src/pages/Proxies.tsx` | 7 |
| `src/pages/Dashboard.tsx` | 3 |
| `src/pages/Login.tsx` | 2 |
| `src/pages/RunningTasks.tsx` | 1 |
| `src/pages/RegisterTaskPage.tsx` | 1 |
| `src/App.tsx` | 1 |

集中在几个大页面的**接口响应类型**上(`catch (e: any)`、`(p: any) =>`、`useState<any[]>`),`src/lib/*` 和 `src/hooks/*` 基本是干净的强类型。想收紧的话优先给 `/api` 响应补 interface。

##### 1.8 主题与样式方案(theme.ts + index.css)

方案是**「一份调色板,双通道输出」**:同一个 `Palette` 对象既喂给 antd `ConfigProvider` 的 token,又写成 CSS 变量挂到 `<html>`,这样组件内联样式统一写 `var(--x)`,换主题不用改组件。

`frontend/src/theme.ts` 结构:

| 位置 | 内容 |
|---|---|
| `frontend/src/theme.ts:9-37` | `interface Palette`,27 个语义色位(布局背景/边框/文字/强调/语义色/**日志专用 7 色**/选中/tooltip) |
| `frontend/src/theme.ts:39-67` | `darkPalette`(默认,`#161922` 底) |
| `frontend/src/theme.ts:69-97` | `lightPalette` |
| `frontend/src/theme.ts:99-167` | `buildTheme(palette, algorithm)`,产出 antd ThemeConfig |
| `frontend/src/theme.ts:169-170` | `darkTheme = buildTheme(darkPalette, theme.darkAlgorithm)`、`lightTheme = buildTheme(lightPalette, theme.defaultAlgorithm)` |
| `frontend/src/theme.ts:172-200` | `CSS_VAR_NAMES`:`Palette` 字段名 → CSS 变量名的映射表 |
| `frontend/src/theme.ts:203-212` | `applyThemeVars(mode)`,遍历映射表 `root.style.setProperty`,并 `classList.toggle('light')` + 设 `colorScheme` |

`buildTheme` 里除全局 token(`colorPrimary`、`borderRadius: 10` 等)还逐组件覆写了 `Layout` / `Menu` / `Card` / `Table` / `Modal` / `Tag` / `Button` / `Segmented`(`frontend/src/theme.ts:121-164`),例如 Button 三种 shadow 全设 `none`、Table 头部用 `bgSubtle`。

**暗色模式**:默认暗色。`darkAlgorithm` / `defaultAlgorithm` 二选一,状态在 `frontend/src/App.tsx:62-64` 用 `useState` 从 `localStorage.getItem('theme')` 初始化(缺省 `'dark'`),`frontend/src/App.tsx:71-74` 的 effect 在切换时同时 `applyThemeVars` + 写回 localStorage。切换入口是侧边栏底部的按钮(`frontend/src/App.tsx:193-204`)。**localStorage key 为 `theme`,取值 `'dark' | 'light'`。**

`frontend/src/index.css`(186 行)分工:

| 区块 | 行 | 内容 |
|---|---|---|
| `:root` 兜底变量 | `index.css:2-30` | 复制一份**暗色**默认值,防首屏闪白(JS 注入前生效) |
| reset | `:32-48` | `* { margin/padding: 0; box-sizing: border-box }`、body 字体栈、`#root { min-height: 100vh }` |
| 动画 | `:54-95` | `fadeIn` / `slideInUp` / `slideInRight` / `pulse` / `spin` 关键帧 + `.animate-*` / `.page-enter` 工具类 |
| 交互 | `:97-130` | `.hover-lift`、webkit 滚动条、`::selection` |
| antd 微调 | `:132-150` | Modal/Drawer 遮罩透明度与投影、`.light` 变体、Modal header 分隔线 |
| **日志面板** | `:152-186` | `.log-panel`、`.log-line--success/danger/warning`、`.log-placeholder`、`.log-error`、`.log-stat--success/info` —— 注册与补 RT 任务共用 |

注意 `index.css:2-30` 这份兜底变量是**手抄的 darkPalette 副本**,改 `theme.ts` 的暗色值需要同步这里,否则首屏与切换后颜色会不一致(缺 `--spotlight-bg`,该变量只在 JS 注入时才有)。

#### 二、路由与布局

##### 2.1 双层路由结构

路由全部在 `frontend/src/App.tsx`,分两层:

**外层(`frontend/src/App.tsx:248-257`)** —— `BrowserRouter` 下只有两条:

| 路径 | 元素 | 说明 |
|---|---|---|
| `/login` | `<Login />` | 独立页,不带侧边栏,自带 `ConfigProvider theme={darkTheme}` |
| `/*` | `<ProtectedLayout />` | 其余全部走鉴权 + 布局壳 |

**内层(`frontend/src/App.tsx:229-240`,在 `<Content>` 内)**:

| 路径 | 页面组件 | 职责 |
|---|---|---|
| `/` | `Dashboard` | 仪表盘,统计卡 + 最近任务 |
| `/accounts` | `Accounts` | 账号列表(不带平台参数) |
| `/accounts/:platform` | `Accounts` | 按平台过滤的账号列表 |
| `/register` | `RegisterTaskPage` | **注册任务表单 + 实时日志**,注意菜单里没有入口(见 2.3) |
| `/icloud` | `ICloudPage` | iCloud 主号 + 隐私邮箱专属控制台 |
| `/running-tasks` | `RunningTasks` | 运行中任务列表 |
| `/payments` | `Payments` | 支付中心 |
| `/history` | `TaskHistory` | 任务历史 |
| `/proxies` | `Proxies` | 代理管理 |
| `/settings` | `Settings` | 全局配置 |

**没有 `*` 兜底路由**:输入不存在的路径会渲染带侧边栏的空白内容区,不会 404 提示。

##### 2.2 布局壳

`frontend/src/App.tsx:145-245` 的 `AppContent`:

- 最外层 `ConfigProvider theme={currentTheme} locale={zhCN}`(`frontend/src/App.tsx:146`,`zhCN` 来自 `antd/es/locale/zh_CN`),内层再套 `<AntdApp>`(即 antd 的 `App` 组件,给 `App.useApp()` 的 message/modal 提供上下文)。
- `Layout` = `Sider`(可折叠,`width={220}`) + `Content`(`padding: 24`,`overflow: auto`)。没有 Header,页面标题由各页自己画。
- Sider 顶部是一个 64px 高的 Logo 区(只有一个 `DashboardOutlined` 图标);底部绝对定位区(`frontend/src/App.tsx:181-220`)放两个按钮:**主题切换**、**退出登录**(仅 `hasPassword` 为真时渲染)。
- 所有背景/边框颜色写 `var(--bg-container)`、`var(--border)` 等 CSS 变量,不写死颜色。

##### 2.3 菜单结构与平台菜单的驱动来源

`menuItems` 定义在 `frontend/src/App.tsx:102-143`:

| 顺序 | key | 图标 | 文案 | 备注 |
|---|---|---|---|---|
| 1 | `/` | `DashboardOutlined` | 仪表盘 | |
| 2 | `/running-tasks` | `PlayCircleOutlined` | 任务运行 | |
| 3 | `/payments` | `CreditCardOutlined` | 支付中心 | |
| 4 | `/accounts` | `UserOutlined` | 平台管理 | **含动态子菜单**,`defaultOpenKeys={['/accounts']}` 默认展开 |
| 5 | `/history` | `HistoryOutlined` | 任务历史 | |
| 6 | `/proxies` | `GlobalOutlined` | 代理管理 | |
| 7 | `/settings` | `SettingOutlined` | 全局配置 | |

点击行为极简:`onClick={({ key }) => navigate(key)}`(`frontend/src/App.tsx:175`),即 **menu key 就是路由路径**。选中态由 `getSelectedKey()`(`frontend/src/App.tsx:89-100`)按 `location.pathname` 前缀逐条 if 判断,未匹配则回落到 `['/']`。

**关键结论 —— 平台菜单不由 `lib/platforms.ts` 驱动**:

- 平台子菜单来自后端接口。`frontend/src/App.tsx:80-84` 用 `apiFetch('/platforms')` 拉取,映射成 `{ key: p.name, label: p.display_name }`(`frontend/src/App.tsx:82`),存入 `platforms` state,再在 `frontend/src/App.tsx:122-126` 展开为 children。
- 子菜单 key 有一处特判:`p.key === 'icloud'` 时指向 `/icloud`,其余指向 `/accounts/${p.key}`(`frontend/src/App.tsx:124`),注释说明 iCloud 有主号 + 隐私邮箱专属控制台,不走通用账号列表。
- `frontend/src/lib/platforms.ts` 是**前端侧的平台元数据静态表**,服务的是**表单与展示**而非菜单:

| 导出 | 位置 | 用途 |
|---|---|---|
| `PLATFORMS` | `frontend/src/lib/platforms.ts:22-39` | `chatgpt` / `icloud` 两条元数据:`label`、`color`、`executors`、`usesMailbox`、`usesCaptcha` |
| `PLATFORM_OPTIONS` | `:41-44` | 注册页平台下拉的 options |
| `PLATFORM_FILTER_OPTIONS` | `:46` | 列表页筛选(前置「全部平台」) |
| `getPlatformLabel` / `getPlatformColor` | `:52-58` | 表格里渲染平台 Tag |
| `getSupportedExecutors` / `getExecutorOptions` | `:60-66` | 按平台裁剪执行器下拉(chatgpt 三种,icloud 只有 `protocol`) |
| `normalizeExecutorForPlatform` | `:68-71` | 切平台时把非法执行器纠正为该平台第一个合法值 |
| `EXECUTOR_LABELS` | `:16-20` | `protocol`=纯协议 / `headless`=无头浏览器 / `headed`=有头浏览器 |

即:**菜单靠后端 `/api/platforms`,业务行为靠本地 `platforms.ts`,两处各写一份平台清单**。新增平台需要同时改后端和 `PLATFORMS`,否则菜单出现了但表单不认。

##### 2.4 路由守卫

守卫只有一处:`frontend/src/App.tsx:32-59` 的 `ProtectedLayout`。

流程:

1. 组件挂载时裸 `fetch('/api/auth/status')`(`frontend/src/App.tsx:37`,不带 token,这是公开接口)。
2. 拿到 `{ has_password }`,与本地 `getToken()` 组合判断(`frontend/src/App.tsx:41`):`has_password && !token` ⇒ `navigate('/login', { replace: true })`;否则 `setReady(true)` 渲染 `<AppContent />`。
3. `ready` 为 false 时全屏 `<Spin size="large" />`(`frontend/src/App.tsx:50-56`)。
4. `.catch(() => setReady(true))`(`frontend/src/App.tsx:47`)—— **接口挂了直接放行**,属于「fail-open」设计:后端不可达时不把人锁在登录页。

需要注意的弱点:

- 守卫只校验「本地有没有 token」,**不校验 token 是否有效**。过期 token 会让守卫通过,真正的拦截发生在后续业务请求的 401 上(见 3.4)。
- `useEffect` 依赖数组为 `[]`(`frontend/src/App.tsx:48`)且用了 `navigate`,ESLint 的 exhaustive-deps 会警告,但配置里不阻断。
- `has_password === false` 时(未设置面板密码)全站免登录,`hasPassword` state 也控制着「退出登录」按钮是否出现(`frontend/src/App.tsx:205`)。
- `AppContent` 里又独立请求了一次 `/api/auth/status`(`frontend/src/App.tsx:76-78`),即**同一份状态被拉两遍**,可以合并。

#### 三、鉴权与请求层

##### 3.1 统一请求封装的位置

**有统一封装,就在 `frontend/src/lib/utils.ts`(46 行)**,导出 `apiFetch(path, opts?)`(`frontend/src/lib/utils.ts:20-45`)。这是全站唯一的请求入口约定,不存在 axios 实例、也没有 `src/api/client.ts` 之类的中间层(`src/api/icloud.ts`、`src/api/payments.ts` 只是业务函数集合,内部同样调用 `apiFetch`)。

`utils.ts` 全量导出:

| 导出 | 行 | 说明 |
|---|---|---|
| `API` / `API_BASE` | `:1-2` | 都等于 `'/api'`(两个同值别名,`API_BASE` 供 `TaskLogPanel` 拼流地址用) |
| `getToken()` | `:4-6` | `localStorage.getItem('auth_token') \|\| ''` |
| `setToken(token)` | `:8-10` | 写 localStorage |
| `clearToken()` | `:12-14` | 删 localStorage |
| `PANEL_AUTH_HEADER` | `:18` | 常量 `'X-Panel-Auth-Required'` |
| `apiFetch(path, opts?)` | `:20-45` | 主封装 |

##### 3.2 token 存储

| 项 | 值 |
|---|---|
| 存储位置 | `localStorage` |
| **key** | **`auth_token`** |
| 值格式 | 裸 JWT/token 字符串(不带 `Bearer ` 前缀) |
| 写入点 | `frontend/src/pages/Login.tsx:29`(密码直登)、`frontend/src/pages/Login.tsx:49`(2FA 通过后),均调 `setToken(data.access_token)` |
| 清除点 | `frontend/src/lib/utils.ts:29`(401 时)、`frontend/src/App.tsx:210`(点「退出登录」) |

另有一个与鉴权无关的 key:`theme`(`'dark' \| 'light'`),以及三个 ChatGPT 开关 key(见 4.3)。

##### 3.3 请求如何带 Authorization

`apiFetch` 每次调用**实时读取** token(`frontend/src/lib/utils.ts:21`,不缓存),拼装:

```ts
const baseHeaders = { 'Content-Type': 'application/json' }
if (token) baseHeaders['Authorization'] = `Bearer ${token}`
const res = await fetch(API + path, { ...opts, headers: { ...baseHeaders, ...opts?.headers } })
```

要点:

- 路径参数是**不带 `/api` 前缀的相对路径**,如 `apiFetch('/tasks/register')` → `/api/tasks/register`。
- `Content-Type` 恒为 `application/json`,调用方自己 `JSON.stringify(body)`。**上传文件要显式覆盖 headers**,否则 boundary 会错。
- 调用方传的 `headers` 会覆盖默认值(展开顺序在后)。
- 响应恒 `res.json()`(`frontend/src/lib/utils.ts:45`),因此 **204 / 空响应体的接口会抛 JSON 解析错**。

##### 3.4 401 处理与跳转

这里有一个刻意设计的**双重条件**(`frontend/src/lib/utils.ts:28-34`),注释在 `frontend/src/lib/utils.ts:16-17`:

```ts
if (res.status === 401 && res.headers.get(PANEL_AUTH_HEADER)) { ... }
```

即**只有同时满足「401」且「响应带 `X-Panel-Auth-Required` 头」时才判定为面板登录态失效**,才执行 `clearToken()` → `window.location.href = '/login'`(仅当当前不在 `/login` 时,避免死循环)→ 抛 `Error('未认证,请重新登录')`。

原因:业务接口本身也可能回 401(例如上游服务不认用户提交的凭据),那种 401 只应把错误显示出来,**不能把人踢回登录页**。这条约定要求后端在面板鉴权失败时必须带上该响应头,漏带会导致用户停留在页面上看一条无意义的报错。

其余非 2xx 走 `frontend/src/lib/utils.ts:35-44`:读 text,尝试 `JSON.parse` 取 `detail` 字段作为 message,解析失败则用原始 text 抛出。所以页面里 `catch (e) { message.error(e.message) }` 能直接拿到 FastAPI 的 `detail`。

跳转方式是 `window.location.href`(**整页刷新**),不是 react-router `navigate`,因为 `apiFetch` 在组件外无法拿到 router 上下文。

##### 3.5 裸 fetch 清单

全站只有 **5 处** 绕过 `apiFetch` 的裸 `fetch`(`frontend/src/lib/utils.ts:24` 自身那处不计),而且都有正当理由:

| 位置 | 请求 | 为什么不走 apiFetch |
|---|---|---|
| `frontend/src/App.tsx:37` | `GET /api/auth/status` | 守卫探测,公开接口,且不希望 401 逻辑介入 |
| `frontend/src/App.tsx:77` | `GET /api/auth/status` | 同上(取 `has_password` 决定是否显示退出按钮) |
| `frontend/src/pages/Login.tsx:18` | `POST /api/auth/login` | 登录时还没有 token;且要读 `res.ok` 之外的 `requires_2fa` 字段 |
| `frontend/src/pages/Login.tsx:42` | `POST /api/auth/verify-totp` | 同上 |
| `frontend/src/components/TaskLogPanel.tsx:191` | `GET /api/tasks/{id}/logs/stream` | **流式响应**,需要 `response.body.getReader()` 与 `AbortSignal`,`apiFetch` 恒 `res.json()` 无法用 |

结论:请求层是**收敛的**,不存在「各页面裸 fetch」的问题。`apiFetch` 被引用的文件分布(按引用次数):`Settings.tsx` 24、`Accounts.tsx` 15、`api/payments.ts` 10、`api/icloud.ts` 9、`Proxies.tsx` 8、`MailImportPanel.tsx` 8、`RegisterTaskPage.tsx` 4、`TaskLogPanel.tsx` 4、`TaskHistory.tsx` 3、`RunningTasks.tsx` 3、`Payments.tsx` 3、`AccountExportModal.tsx` 3、`Dashboard.tsx` 2、`smsCountries.ts` 2、`App.tsx` 2。

##### 3.6 Login.tsx 登录流程

`frontend/src/pages/Login.tsx` 158 行,两步式表单,用 `step: 'password' | '2fa'` 切换(`frontend/src/pages/Login.tsx:7`、`:11`)。

| 步骤 | 处理函数 | 接口 | 请求体 | 成功后 |
|---|---|---|---|---|
| 1 密码 | `handleLogin`(`:15-37`) | `POST /api/auth/login` | `{ password }` | 若 `data.requires_2fa` ⇒ 存 `data.temp_token` 到 state 并切到 `'2fa'`;否则 `setToken(data.access_token)` + `window.location.href = '/'` |
| 2 TOTP | `handleTotp`(`:39-56`) | `POST /api/auth/verify-totp` | `{ temp_token, code }` | `setToken(data.access_token)` + `window.location.href = '/'` |

其它细节:

- `temp_token` 只存在 React state(`frontend/src/pages/Login.tsx:12`),**不落 localStorage**,刷新即失效,需重新输密码。2FA 页有「返回密码登录」按钮(`:110-112`)。
- 验证码校验规则:必填 + `len: 6`(`frontend/src/pages/Login.tsx:91-94`),输入框 `maxLength={6}`、`letterSpacing: 6` 居中。
- 登录成功用 `window.location.href = '/'` 而非 `navigate`,目的是**整页重载**,让 `ProtectedLayout` 重新跑一遍守卫、`AppContent` 重新拉平台列表。
- 错误统一 `message.error(e.message)`,`message` 来自 `App.useApp()`(`frontend/src/pages/Login.tsx:10`),所以 `Login` 默认导出(`:150-157`)必须把 `LoginContent` 包在 `<ConfigProvider theme={darkTheme}><App>` 里 —— **登录页恒暗色**,不跟随 `theme` 偏好。
- `catch (e: any)` 两处,是 `Login.tsx` 里那 2 个 `any` 的来源。

#### 四、注册任务页 RegisterTaskPage.tsx

`frontend/src/pages/RegisterTaskPage.tsx`,738 行,单文件组件,内部无子组件拆分。结构:一个 antd `Form`(`layout="vertical"`,`maxWidth: 800`)+ 提交后出现的「任务状态」`Card`(内嵌 `TaskLogPanel`)。

##### 4.1 页面骨架与卡片可见性

表单被切成若干 `Card`,可见性由 `Form.useWatch` 的联动值决定(`frontend/src/pages/RegisterTaskPage.tsx:246-254`):

| Card | 标题 | 显示条件 | 行 |
|---|---|---|---|
| 1 | 基本配置 | 恒显示 | `:291-358` |
| 2 | iCloud 隐私邮箱配置 | `!usesMailbox`(即 platform=icloud) | `:360-387` |
| 3 | 邮箱配置 | `usesMailbox`(即 platform=chatgpt) | `:389-645` |
| 4 | ChatGPT 手机接码 | `platform === 'chatgpt'` | `:647-664` |
| 5 | 验证码配置 | `usesCaptcha && captchaSolver === 'yescaptcha'` | `:666-672` |
| 6 | 本地 Solver 配置 | `captchaSolver === 'local_solver'` | `:674-683` |

`usesMailbox` / `usesCaptcha` 取自 `getPlatformMeta(platform)`(`:252-254`),默认 `true`。邮箱配置卡内部还有 **12 个按 `mailProvider` 分支的子块**,每个邮件服务商一组字段。

`:256-262` 有一个纠偏 effect:平台变化时用 `normalizeExecutorForPlatform` 把 `executor_type` 拉回该平台合法值(chatgpt→icloud 时 `headless` 会被改成 `protocol`)。

##### 4.2 表单字段全清单

**基本配置(Card 1)**

| 字段 | 控件 | 默认值 | 提交到后端 |
|---|---|---|---|
| `platform` | `Select`(`PLATFORM_OPTIONS`),必填 | `'chatgpt'` | 顶层 `platform` |
| `executor_type` | `Select`(`getExecutorOptions(platform)`),必填 | `'protocol'`,被 `/config` 的 `default_executor` 覆盖 | 顶层 `executor_type` |
| `captcha_solver` | `Select`:yescaptcha / local_solver / manual,必填,`hidden={!usesCaptcha}` | `'yescaptcha'`,被 `default_captcha_solver` 覆盖 | 顶层 `captcha_solver` |
| `count` | `Input type=number` min=1 | `1` | 顶层 `count` |
| `concurrency` | `Input type=number` min=1 | `1` | 顶层 `concurrency` |
| `register_retry_times` | `InputNumber` 0-10 precision=0,带长 tooltip | `DEFAULT_REGISTER_RETRY_TIMES`=1,被 `cfg.register_retry_times` 覆盖 | 顶层 `register_retry_times`(过 `normalizeRegisterRetryTimes`) |
| `register_delay_seconds` | `InputNumber` min=0 step=0.5 | `0` | 顶层 `register_delay_seconds`(`\|\| 0`) |
| `proxy` | `Input`,placeholder `http://user:pass@host:port` | 空 | 顶层 `proxy`(空 ⇒ `null`) |

三个 ChatGPT 开关(`:336-357`)**不是 Form 字段**,它们只有 `<Form.Item label>` 外壳,值由 hook 管理(见 4.3)。

顶层还有两个字段 —— `email`、`password`(`:215-216` 提交,空 ⇒ `null`)—— **表单里没有对应的 `Form.Item`**,永远提交 `null`,属于遗留的指定账号入口。

**iCloud 卡(Card 2,`:366-385`)**:`icloud_account_email`(Select,来源见 4.6)、`icloud_alias_label`、`icloud_alias_note`。注意这三个字段**没有出现在 `submit()` 的请求体里**,即当前提交逻辑并未把它们发出去。

**邮箱配置(Card 3)**

| 字段 | 控件 | 默认值 |
|---|---|---|
| `mail_provider` | `Select` 13 项:luckmail / mail_import / moemail / tempmail_lol / skymail / cloudmail / maliapi / gptmail / opentrashmail / duckmail / freemail / laoudo / cfworker,必填 | `'luckmail'` |
| `mail_import_source` | `Select`(`MAIL_IMPORT_SOURCE_OPTIONS`),仅 `mail_provider==='mail_import'` 时显示,必填 | `'outlook'` |
| `outlook_backend` | `Select` graph/imap,仅 `mailProvider==='microsoft'` | `'graph'` |
| skymail 组 | `skymail_api_base` / `skymail_token`(Password) / `skymail_domain` | `https://api.skymail.ink` / 空 / 空 |
| cloudmail 组 | `cloudmail_api_base`(必填) / `cloudmail_admin_email` / `cloudmail_admin_password`(必填) / `cloudmail_domain` / `cloudmail_subdomain` / `cloudmail_timeout`(5-120) | 空 / 空 / 空 / 空 / 空 / `30` |
| laoudo 组 | `laoudo_email` / `laoudo_account_id` / `laoudo_auth` | 空 |
| maliapi 组 | `maliapi_base_url` / `maliapi_api_key` / `maliapi_domain` / `maliapi_auto_domain_strategy`(balanced/prefer_owned/prefer_public) | `https://maliapi.215.im/v1` / 空 / 空 / `balanced` |
| applemail 组 | `applemail_base_url` / `applemail_pool_dir` / `applemail_pool_file` / `applemail_mailboxes` | `https://www.appleemail.top` / `mail` / 空 / `INBOX,Junk` |
| gptmail 组 | `gptmail_base_url` / `gptmail_api_key` / `gptmail_domain` | `https://mail.chatgpt.org.uk` / 空 / 空 |
| opentrashmail 组 | `opentrashmail_api_url`(必填) / `opentrashmail_domain` / `opentrashmail_password` | 空 |
| cfworker 组 | `cfworker_api_url` / `cfworker_admin_token` / `cfworker_custom_auth` / `cfworker_domain_override` / `cfworker_subdomain` / `cfworker_random_subdomain`(Checkbox) / `cfworker_random_name_subdomain`(Checkbox) / `cfworker_fingerprint` | 空;两个 Checkbox 由 `parseBooleanConfigValue(cfg.*)` 初始化 |
| freemail 组 | `freemail_api_url`(必填) / `freemail_admin_token` / `freemail_username` / `freemail_password` / `freemail_domain` | 空 |
| luckmail 组 | `luckmail_base_url` / `luckmail_api_key` / `luckmail_email_type`(''/ms_graph/ms_imap/self_built) / `luckmail_domain` | `https://mails.luckyous.com/` / 空 / 空 / 空 |
| moemail 组 | `moemail_api_url` / `moemail_api_key` | 空(仅在 `/config` 回填,**UI 里没有对应 Form.Item**) |
| duckmail 组 | `duckmail_api_url` / `duckmail_provider_url` / `duckmail_bearer` | 空(同样只在 `/config` 回填,UI 无字段) |

**手机接码卡(Card 4,`:654-662`)**:`sms_country`(`SmsCountrySelect`)、`sms_per_phone_timeout`(Input,placeholder 80)、`sms_max_phone_attempts`(Input,placeholder 3),全部可选,留空用设置页默认。

**验证码 / Solver(Card 5-6)**:`yescaptcha_key`(Input)、`solver_url`(默认 `http://localhost:8889`)。

##### 4.3 默认值的三层来源

1. `Form` 的 `initialValues`(`:271-290`)—— 硬编码兜底,含 `platform: 'chatgpt'`、`count: 1`、`concurrency: 1`、`register_retry_times: DEFAULT_REGISTER_RETRY_TIMES`、`solver_url: 'http://localhost:8889'` 等 18 项。
2. 挂载后 `apiFetch('/config')`(`:70-136`)一次性 `form.setFieldsValue`,用**服务端全局配置覆盖**约 60 个字段,每项都写了 `|| 默认值` 兜底。`mail_provider` 有转换逻辑:若配置值属于 `MAIL_IMPORT_PROVIDERS`(`microsoft`/`outlook`/`applemail`)则显示成 `'mail_import'`(`:74`、`:79`),并用 `normalizeMailImportSource` 反推出导入视图。
3. 用户手改。

`resolveEffectiveMailProvider`(`frontend/src/lib/mailImport.ts:52-55`)负责把 UI 上的 `'mail_import'` 还原成后端要的真实 provider:导入视图为 `applemail` ⇒ `'applemail'`,否则 ⇒ `'microsoft'`。提交时(`:140`)和联动判断时(`:248`)都要过这一层。

##### 4.4 三个 ChatGPT 开关的状态持久化

三者不进 Form,而是各自一个 `usePersistent*` hook(`:57-62`),模式完全一致:`useState(() => load())` + `useEffect(() => save(mode), [mode])`,**即改即存 localStorage,跨刷新与跨页面保持**。

| UI 组件 | Hook | lib | localStorage key | 取值 | 默认值 |
|---|---|---|---|---|---|
| `ChatGPTRegisterFlowSelect`(注册方式) | `usePersistentChatGPTRegisterFlow` | `lib/chatgptRegisterFlow.ts` | `chatgpt-register-flow` | `email` / `phone` / `phone_with_email` | `email` |
| `ChatGPTRegistrationModeSwitch`(Token 方案) | `usePersistentChatGPTRegistrationMode` | `lib/chatgptRegistrationMode.ts` | `chatgpt-registration-mode` | `refresh_token`(有 RT) / `access_token_only`(无 RT) | `refresh_token` |
| `ChatGPTBind2faSwitch`(绑定 2FA) | `usePersistentChatGPTBind2fa` | `lib/chatgptBind2fa.ts` | `chatgpt-bind-2fa` | 字符串 `'1'` / `'0'` | `false` |

lib 层的职责划分:

- **常量 + 类型**:每个 lib 顶部导出字面量常量与 union 类型(如 `frontend/src/lib/chatgptRegisterFlow.ts:1-9`),避免各处写魔法字符串。
- **校验/归一**:`normalizeChatGPTRegistrationMode`(`frontend/src/lib/chatgptRegistrationMode.ts:12-19`)、`normalizeChatGPTRegisterFlow`(`frontend/src/lib/chatgptRegisterFlow.ts:36-44`)—— 采用**白名单匹配,任何非法值一律回落到默认值**,不抛错。`chatgptBind2fa.ts` 没有 normalize,直接用 `=== '1'` 判定(`:8`),所以任何非 `'1'` 值都是 false。
- **读写**:`loadX` / `saveX`,均带 `typeof window === 'undefined'` 的 SSR 守卫(项目并未 SSR,属防御性写法)。
- **派生**:`registerFlowUsesPhone(flow)`(`frontend/src/lib/chatgptRegisterFlow.ts:46-48`)= `flow !== 'email'`,用于提示「需要接码」;hook 把它作为 `usesPhone` 返出(`frontend/src/hooks/usePersistentChatGPTRegisterFlow.ts:22`)。`usePersistentChatGPTRegistrationMode` 同理返出 `hasRefreshTokenSolution`(`:22-23`)。
- **展示文案**:`CHATGPT_REGISTER_FLOW_OPTIONS`(`frontend/src/lib/chatgptRegisterFlow.ts:14-34`)把 label 与 hint 一起放在 lib 里,组件只渲染。

三个组件都是 40-50 行的纯受控展示组件(`props: 值 + onChange`),无内部状态:`frontend/src/components/ChatGPTRegistrationModeSwitch.tsx:26-37` 是一个 `Switch`(checkedChildren「有 RT」/ unCheckedChildren「无 RT」)+ 状态 Tag + 一段说明文字;`frontend/src/components/ChatGPTBind2faSwitch.tsx:32-36` 在开启时额外渲染一条 **warning**:密钥只在绑定那一刻下发一次,服务端取不回。

注意 `RegisterTaskPage` 里 `usePersistent*` 的派生字段(`usesPhone` / `hasRefreshTokenSolution`)**未被使用**,只取了值与 setter。

##### 4.5 chatgptRegistrationRequestAdapter.ts:UI 状态 → 请求体

`lib/chatgptRegistrationRequestAdapter.ts`(70 行)用的是**策略模式**:一个抽象基类 + 两个子类,工厂函数按 mode 选择。

工厂 `buildChatGPTRegistrationRequestAdapter(platform, mode, registerFlow?, bind2fa?)`(`:56-70`):

| 输入 | 返回 |
|---|---|
| `platform !== 'chatgpt'` | `null`(**非 ChatGPT 平台不注入任何字段**) |
| `mode === 'access_token_only'` | `AccessTokenOnlyChatGPTRegistrationRequestAdapter`(`hasRefreshTokenSolution = false`) |
| 其它(含非法值) | `RefreshTokenChatGPTRegistrationRequestAdapter`(`hasRefreshTokenSolution = true`) |

`registerFlow` 进构造前先过 `normalizeChatGPTRegisterFlow`(`:64`);`bind2fa` 直接透传,默认 `DEFAULT_CHATGPT_BIND_2FA`。

唯一方法 `extendExtra(extra)`(`:35-43`)—— 在原 `extra` 上**追加 4 个字段**:

| 输出字段 | 来源 | 类型 | 取值 |
|---|---|---|---|
| `chatgpt_registration_mode` | 子类 `mode` | string | `'refresh_token'` / `'access_token_only'` |
| `chatgpt_has_refresh_token_solution` | 子类常量 | boolean | `true` / `false`(与上一字段冗余,便于后端直接判 bool) |
| `chatgpt_register_flow` | 归一后的 `registerFlow` | string | `'email'` / `'phone'` / `'phone_with_email'` |
| `chatgpt_bind_2fa` | `bind2fa` | boolean | `true` / `false` |

调用点 `frontend/src/pages/RegisterTaskPage.tsx:200-209`:先拼出 60 余项的 `registerExtra`(`:141-199`,把所有邮箱/接码/验证码字段平铺),再 `adapter ? adapter.extendExtra(registerExtra) : registerExtra`。

最终请求 `POST /api/tasks/register`(`:211-226`),body 形状:

```
{ platform, email, password, count, concurrency, register_retry_times,
  register_delay_seconds, proxy, executor_type, captcha_solver, extra: {...} }
```

即**顶层只有 10 个通用字段,其余全塞进 `extra`**。`extra` 的 key 与 `/api/config` 的配置项同名,便于后端按任务级覆盖全局配置。

##### 4.6 registerRetry.ts 的前端重试语义

`lib/registerRetry.ts` 只有 10 行,**是纯参数校验,前端不做任何重试**:

| 导出 | 值/行为 |
|---|---|
| `DEFAULT_REGISTER_RETRY_TIMES` | `1` |
| `MAX_REGISTER_RETRY_TIMES` | `10` |
| `normalizeRegisterRetryTimes(value)` | 解析成整数;`NaN` 或 `< 0` ⇒ 回落到 `1`;否则 `Math.min(trunc(v), 10)` |

语义(注释 `frontend/src/lib/registerRetry.ts:1` + 表单 tooltip `frontend/src/pages/RegisterTaskPage.tsx:322`):**「整条注册流程失败后再开几轮,每轮换新邮箱 / 新号码 / 新会话」**。`0` 表示失败即止。要与「接码里的最多换号次数(`sms_max_phone_attempts`)」区分 —— 后者是同一轮内换手机号。tooltip 还提到后端的提前收手规则:手机注册连续两轮都「建出了号却一条短信都没收到」时不再重试,避免造孤号。

`normalizeRegisterRetryTimes` 被调用两次:回填时(`:78`)与提交时(`:219`),保证脏配置和手改越界都被夹到 0-10。

##### 4.7 SmsCountrySelect 与 smsCountries.ts

数据来源:**后端接口 `GET /api/sms/country-options`**,由 `useSmsCountryOptions(enabled = true)` hook 拉取(`frontend/src/lib/smsCountries.ts:33-62`)。前端不内置国家表。

| 环节 | 实现 |
|---|---|
| 拉取时机 | mount 时一次;`options.length > 0` 即跳过(`:37`),**天然只请求一次、无缓存共享**(每个组件实例各拉一次) |
| 排序 | `openai_sms_whitelisted` 为真的置顶,其余按 `localeCompare(..., 'zh-Hans-CN')` 中文名排序(`:44-49`)。注释说明:表里 190+ 国家,泰国因 OpenAI 只对它走纯短信而必须排最前 |
| 失败处理 | `.catch()` 静默吞掉(`:52-54`),退化成手填数字,不弹错 |
| 值格式 | 存的是**接码平台的数字 ID 字符串**,UI 只显示中文名(`:12` 注释) |
| ID 解析 | `parseCountryIdList(value)`(`:13-21`)兼容数组与 `,` `,` `;` `;` 空白 混合分隔的字符串 |
| ID 序列化 | `formatCountryIdList`(`:23-25`)统一输出逗号连接 |
| 未知值兜底 | `withUnknownCountries(options, selected)`(`:65-74`)给不在清单里的已存值补一个 `未知国家 (ID)` 占位项,**防止下拉把历史值吞掉** |

`components/SmsCountrySelect.tsx`(58 行)是薄壳:`multiple` 属性切换单选/多选(`:27-42` vs `:44-55`),均 `allowClear showSearch optionFilterProp="label"`,多选额外 `maxTagCount="responsive"`。白名单国家的 label 会拼上后缀 `· OpenAI 纯短信`(`:24`)。注册页只用单选(`sms_country`),多选模式供设置页使用。

##### 4.8 提交后的状态跟踪:轮询 + 日志流并存

`submit()` 成功后(`:227-229`):`setTask(res)` → `setPolling(true)` → `pollTask(res.task_id)`。

`pollTask(id)`(`:232-244`)是一个 **2 秒 `setInterval` 轮询 `GET /api/tasks/{id}`**,把整个 task 对象塞进 state,直到 `status` 为 `done`/`failed`/`stopped` 才 `clearInterval`。结束时若 `t.cashier_urls` 非空,会 `forEach` 逐个 `window.open(url, '_blank')`(`:239-241`,支付链接自动弹窗,可能被浏览器拦截)。

因此**同一个任务同时存在两条数据通道**:页面级 2s 轮询(驱动「任务状态」Card 的进度/成功数/错误列表)+ `TaskLogPanel` 内部的 SSE 流(驱动日志与计数 Tag)。

已知问题:

- `pollTask` 里的 `setInterval` **没有在组件卸载时清理**,离开页面后仍会继续请求直到任务终态。
- 轮询启动用的是 `res.task_id`,而渲染任务卡与日志面板用的是 `task.id`(`:705`、`:731`)——**两个字段名不同**,依赖后端 `POST /tasks/register` 与 `GET /tasks/{id}` 各自返回其一。
- `task` 是 `useState<any>(null)`(`:54`),该页唯一的 `any`。

任务状态卡(`:690-735`)展示:状态 Tag(done→success / stopped→warning / failed→error / 其余→processing)、任务 ID(`Text copyable` 等宽字体)、`progress`、`skipped ?? 0`、成功数、`errors[]` 列表、`error` 单条,最后挂 `<TaskLogPanel taskId={task.id} />`(未传 `kind`,走默认 `'register'` 文案)。提交按钮在 `polling` 期间 `disabled` 并显示「注册中...」(`:685-687`)。

#### 五、实时日志 TaskLogPanel.tsx

`frontend/src/components/TaskLogPanel.tsx`,373 行,被注册 / 补 RT / 绑 2FA / 支付四类任务共用。

##### 5.1 结论:用的是 SSE,但不用 EventSource

**是 SSE(`text/event-stream`),但用 `fetch` + `ReadableStream` 手工解析,不是 `EventSource`**。

后端对应 `api/tasks.py:1237` 的 `@router.get("/{task_id}/logs/stream")` → `stream_logs(task_id, since)`,返回 `StreamingResponse(media_type="text/event-stream")`,带 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`,内部 0.5s 一轮把新日志逐行 `yield data: {...}`,终态时发一条 `{done: true, status}` 后 break。

前端为什么不用 `EventSource`:**`EventSource` 无法自定义请求头**,而本项目鉴权靠 `Authorization: Bearer`。`frontend/src/components/TaskLogPanel.tsx:186-194` 显式取 `getToken()` 拼 header,再 `fetch(..., { headers, signal: controller.signal })`。这也是全站 5 处裸 fetch 之一(`:191`)。

解析逻辑(`:207-251`):`response.body.getReader()` + `TextDecoder`,累积 `buffer`,按 **`\n\n`** 切帧,`parts.pop()` 保留不完整尾帧,对每帧用 `/^data:\s*(.+)$/m` 提取后 `JSON.parse`,解析失败静默 `continue`(`:247-249`)。

单帧 payload 字段:`line?`、`done?`、`status?`、`success?`、`registered?`、`total?`。

##### 5.2 生命周期:快照 + 流 + 指数退避重连

整体在一个 `useEffect(..., [taskId])`(`:130-284`)里,用 `cancelled` 标志 + `AbortController` 双保险清理(`:280-283`)。

**第一步 `initSnapshot()`(`:146-182`)** —— `apiFetch('/tasks/{id}')` 拉一次全量快照:

- `setLines(snapshot.logs)` 铺满历史日志;
- `nextSinceRef.current = snapshotLines.length`(`:167`)—— **游标初始化为历史行数**;
- `setStopRequested(snapshot.control?.stop_requested)`;
- 若 `status` 已是 `done`/`failed`/`stopped` ⇒ 设终态、调 `onDone()`、**返回 true 表示不必连流**(`:170-174`);
- 失败只 `setError(detail)`,仍继续连流。

**第二步 `connectStream()`(`:262-276`)** —— `while (!cancelled)` 循环调 `connectStreamOnce()`,返回 false(非正常结束)则重连:

```
retryMs = min(1000 * 2 ** (retryCount - 1), 8000)
```

即 **1s → 2s → 4s → 8s → 8s …**(`baseRetryMs = 1000`、`maxRetryMs = 8000`,`:134-135`、`:272`),并把 `日志流连接中断,Ns 后重试(第 N 次)` 写进 `error` 展示给用户(`:273`)。**重试次数无上限**,只要不卸载就一直重连。

##### 5.3 since 游标

| 环节 | 行为 |
|---|---|
| 存储 | `nextSinceRef = useRef(0)`(`:77`),用 ref 而非 state,避免触发重渲染 |
| 初始化 | effect 开头置 0(`:136`),快照后置为历史日志条数(`:167`) |
| 递增 | 每收到一条含 `line` 的 payload `+= 1`(`:239`) |
| 使用 | 每次(含重连)拼 URL:`/tasks/{id}/logs/stream?since=${nextSinceRef.current}`(`:190-191`) |

效果:**重连时只补拉断点之后的日志,不重复、不丢行**。后端 `stream_logs` 的 `sent = since` 正好对应这一语义。

##### 5.4 `connectStreamOnce` 的返回值语义

返回 `true` = 不要重连,`false` = 应重连:

| 情况 | 返回 | 副作用 |
|---|---|---|
| `!response.ok` | `true` | `setError('日志流连接失败 (状态码)')`,**放弃重连**(鉴权/404 类错误重试无意义) |
| `!response.body` | `false` | `setError('日志流未返回可读数据')`,会重连 |
| 收到 `payload.done` | `true` | 设 `terminalStatus = payload.status \|\| 'done'`、调 `onDoneRef.current?.()` |
| reader 自然 `done`(流被断开但没收到 done 帧) | `false` | 走重连 |
| 抛 `AbortError`(组件卸载) | `true` | 不重连 |
| 其它异常 | `false` | 重连 |

##### 5.5 任务结束判定与关流

终态状态机 `TaskTerminalStatus = 'idle' \| 'done' \| 'failed' \| 'stopped'`(`:24`),判定入口有两处 —— 快照里 `status` 已是终态(`:170`),或流里收到 `done: true`(`:242`),两者都会调用 `onDone` 回调并让循环 `return`,**函数返回后 while 循环退出,不再发起新连接,即自动关流**。

`isFinished = terminalStatus !== 'idle' || stopRequested`(`:79`)驱动「跳过当前账号」「停止任务」两个按钮的 `disabled`。

底部 footer 文案由 `terminalStatus` 映射(`:291-298`):`done` → `text.done`(绿)、`stopped` → 「任务已停止」(黄)、`failed` → 「任务失败」(红)、`idle` → 不渲染。

`onDone` 用 `onDoneRef` 保存并单独 effect 同步(`:126-128`、`:76`),这样父组件每次重渲染传新函数**不会重启日志流**(effect 依赖只有 `taskId`)。

##### 5.6 按钮与调用的后端接口

| 按钮 | 图标 | 接口 | 行 |
|---|---|---|---|
| 跳过当前账号 | `FastForwardOutlined` | `POST /api/tasks/{taskId}/skip-current` | `:94` |
| 停止任务 | `StopOutlined`,`danger` | `POST /api/tasks/{taskId}/stop` | `:115` |
| 复制日志 | `CopyOutlined` | 无(`navigator.clipboard.writeText(lines.join('\n'))`) | `:83` |

细节:

- 跳过成功后读响应 `control.targeted_skip_attempts`,>1 时提示「已发送跳过 N 个进行中账号请求」,否则「已发送跳过当前账号请求」(`:97-102`)。
- 停止成功后 `setStopRequested(true)`(`:116`),立即让两个按钮变灰,提示「已发送停止任务请求,正在停止进行中的线程」。**注意这不改 `terminalStatus`**,真正的终态仍等流里的 done 帧。
- 两个操作各有独立 loading state(`skipLoading` / `stopLoading`),且入口都有 `if (isFinished) return` 守卫。
- 复制按钮在 `lines.length === 0` 时禁用。

##### 5.7 渲染、着色与自动滚动

**自动滚动**(`:286-289`):一个依赖 `[lines]` 的 effect,直接 `panelRef.current.scrollTop = scrollHeight`。**无「用户已上滚则不抢滚动条」判断** —— 只要有新日志就强制拉到底,人工翻看历史时会被打断。

**行着色** `lineToneClass(line)`(`:45-50`),按**文本关键字**判断而非结构化 level:

| 命中 | class | 颜色变量 |
|---|---|---|
| 含 `✓` 或 `成功` | `log-line--success` | `--log-success` |
| 含 `✗` / `失败` / `错误` | `log-line--danger` | `--log-danger` |
| 含 `停止` / `跳过` | `log-line--warning` | `--log-warning` |
| 其它 | 无 | `--log-text` |

判断按上述顺序短路,所以「获取失败次数成功清零」这类混合文本会被判成 success。

**容器样式**(`:335-354`):`className="log-panel"`(样式见 `index.css:152-186`),等宽字体栈,`fontSize: 12`,`minHeight: 320`、`maxHeight: '65vh'`,`whiteSpace: 'pre-wrap'` + `wordBreak: 'break-word'`,并显式开 `userSelect: 'text'` 与 `cursor: 'text'` 便于框选复制。每行 `<div key={index}>`(用索引作 key,日志只追加故安全)。空态显示 `等待日志...`,`error` 单独一行 `.log-error`。

**顶部计数 Tag**(`:302-306`):三枚,文案随 `kind` 变化。

`kind` 与文案映射(`KIND_TEXT`,`:17-22`):

| kind | success | registered | total | done |
|---|---|---|---|---|
| `register` | 注册成功 | 已注册 | 总共注册 | 注册完成 |
| `backfill_rt` | 补 RT 成功 | 已处理 | 总共账号 | 补 RT 完成 |
| `bind_2fa` | 绑定成功 | 已处理 | 总共账号 | 绑定 2FA 完成 |
| `payment` | 支付成功 | 已处理 | 总共账号 | 支付任务完成 |

`kind === 'payment'` 时还会按 `operation` 二次细分(`:61-67`):`'link'` ⇒「提链成功 / 提链任务完成」,`'pay'` ⇒「支付成功」,未传 ⇒「操作成功」。**`kind` 只影响文案,日志流对所有后台任务是同一套**(`:10` 注释)。

**计数的单调性保护**:`parseCounter`(`:32-36`)把非有限数/负数夹成 0 并 `Math.floor`;`normalizeSummary`(`:38-43`)强制 `total >= registered >= success`;`mergeSummary`(`:52-58`)只覆盖 payload 里实际存在的字段(`incoming.x ?? previous.x`)。这样即使后端某帧漏带某个计数,UI 上的数字也不会突然归零或倒退。

#### 六、仪表盘与任务 / 代理页

##### 6.1 Dashboard.tsx(148 行)

**唯一数据源:`GET /api/accounts/stats`**(`frontend/src/pages/Dashboard.tsx:28`),一次请求撑满整页;无自动刷新,只有右上角手动「刷新」按钮(`:73-75`)。

响应结构(由用法反推):`{ total, by_platform: { [platform]: count }, by_status: { [status]: count } }`。

**四张统计卡**(`:39-64`,`Row gutter` + `Col xs=24 sm=12 lg=6` 响应式):

| 卡片 | 取值 | 图标 | 颜色 |
|---|---|---|---|
| 总账号数 | `stats.total` | `UserOutlined` | `var(--accent)` |
| 试用中 | `by_status.trial` | `ClockCircleOutlined` | `var(--warning)` |
| 已订阅 | `by_status.subscribed` | `CheckCircleOutlined` | `var(--success)` |
| 已失效 | `by_status.expired + by_status.invalid` **求和** | `CloseCircleOutlined` | `var(--danger)` |

**两块分布卡**(`:91-145`,各占 `lg=12`):

- **平台分布**(`:93-115`):遍历 `by_platform`,每项一个平台 Tag(颜色/文案取 `getPlatformColor` / `getPlatformLabel`,来自 `lib/platforms.ts`)+ 计数 + `Progress` 条,百分比 = `count / total`,`showInfo={false}`。
- **状态分布**(`:119-143`):遍历 `by_status`,Tag 颜色查本地表 `STATUS_COLORS`(`:13-19`,`registered`=default、`trial`/`subscribed`=success、`expired`=warning、`invalid`=error),**文案直接显示英文 status 原值**,未做中文映射。

页面的 3 个 `any` 分别是 `useState<any>(null)`(`:22`)和两处 `Object.entries(...).map(([k, v]: any) => ...)`(`:99`、`:125`)。`load()` 只有 `try/finally` 没有 `catch`(`:25-33`),接口报错会产生未捕获的 promise rejection,页面停在空态。

##### 6.2 RunningTasks.tsx vs TaskHistory.tsx:职责差异

两个页面看起来都是「任务列表」,但**数据源、粒度、生命周期完全不同**:

| 维度 | RunningTasks.tsx(338 行) | TaskHistory.tsx(166 行) |
|---|---|---|
| 菜单入口 | 任务运行 `/running-tasks` | 任务历史 `/history` |
| 接口 | `GET /api/tasks` | `GET /api/tasks/logs?page=1&page_size=50` |
| 记录粒度 | **一条 = 一个任务(批次)** | **一条 = 一个账号的注册结果** |
| 主键 | `id: string`(任务 UUID) | `id: number`(数据库自增) |
| 布局 | 卡片流,分「进行中 / 已完成」两组 | 单个 antd `Table` |
| 实时性 | **2.5s 轮询**列表 + **1s tick** 刷新耗时显示(`:121-130`) | 无轮询,手动刷新 |
| 状态维度 | `pending`/`running`/`done`/`failed`/`stopped` 五态 | 仅 `success`/`failed` 两态 |
| 日志 | 有,`Drawer` 内嵌 `TaskLogPanel` | 无 |
| 删除 | **单条删除**(仅非活跃任务) | **批量删除**(多选) |
| 筛选 | 无(前端按状态分组 + 排序) | 平台下拉 `PLATFORM_FILTER_OPTIONS` |

**RunningTasks 细节**:

- 列表排序在前端做(`:104-112`):先按 `running(0) < pending(1) < done(2) < failed(3) < stopped(4)`,同组内按 `created_at` 倒序。
- `toUnixSeconds`(`:63-79`)兼容 ISO 字符串 / 秒级 / 毫秒级时间戳(`> 1e12` 判定为毫秒);`formatDuration`(`:81-91`)输出 `Ns` / `Nm Ns` / `Nh Nm`。活跃任务用 `now` 做终点(实时走秒),完成任务用 `updated_at`(`:161-163`)。
- 每张任务卡(`renderTask`,`:150-265`)一行展示:任务 ID(`Text code`)、平台 Tag、来源标签(`SOURCE_LABELS`,`:46-53`:manual=手动 / api=API / schedule=调度 / backfill_rt=补 RT / bind_2fa=绑 2FA / payment=支付)、状态 `Badge`、耗时、`Progress`(百分比 = `registered/total`,`format` 显示 `done/total`,状态映射 failed/stopped→exception、done→success、其余→active)、成功/失败/跳过三个计数、按钮组。
- 所有数字都过 `Number.isFinite` 校验并 `Math.floor`(`:153-159`),百分比夹在 0-100。
- **删除按钮只在 `!isActive(task)` 时渲染**(`:247`),即运行中任务不能删;删除走 `DELETE /api/tasks/{id}`(`:140`),成功后本地 `filter` 移除并同步关掉日志抽屉。
- 日志抽屉(`:316-335`):`width={720}`、`destroyOnClose`(**关闭即销毁,断开 SSE 连接**),`kind` 直接把 `task.source` 强转成 `TaskKind`(`:334`)—— 注意 `source` 里的 `manual`/`api`/`schedule` **不在 `TaskKind` 联合类型内**,`KIND_TEXT[kind]` 会取到 `undefined` 并在读 `.success` 时抛错。实际不炸是因为这类任务 source 通常是 `register`,属于类型不严的隐患。
- 空态用 `<Empty description="暂无任务记录" />`(`:311-313`)。
- 该页唯一的 `any` 在 `:192`:`<Badge status={cfg.color as any} />`。

**TaskHistory 细节**:

- `load` 用 `useCallback([platform])`(`:41-53`),`useEffect([load])` ⇒ **切平台自动重载**。请求固定 `page=1&page_size=50`,**服务端分页只取前 50 条**,而表格 `pagination={{ pageSize: 20 }}` 是纯前端分页 —— 所以第 3 页之后的数据看不到,总数 `total` 显示的却是全量。
- 表格 5 列:时间(`toLocaleString('zh-CN')`)、平台(Tag)、邮箱(等宽)、状态(成功/失败 Tag)、错误信息。
- **批量删除**(`handleBatchDelete`,`:59-73`):`POST /api/tasks/logs/batch-delete`,body `{ ids: number[] }`;响应 `{ deleted, not_found: number[], total_requested }`;成功提示「已删除 N 条任务历史」,`not_found` 非空时额外一条 `message.warning`;之后清空选中并重载。入口包在 `Popconfirm` 里,且**仅在有选中时才渲染按钮**(`:125-137`)。
- 选中态维护有两处防护:重载后过滤掉已不存在的 key(`:49`),切换平台筛选时清空选中(`:142`)。
- 该文件类型定义完整(`TaskLogItem` / `TaskLogListResponse` / `TaskLogBatchDeleteResponse`,`:14-32`),**零 `any`**,是本仓库前端类型规范的正面样板。
- `load` 同样只有 `try/finally` 无 `catch`。

##### 6.3 Proxies.tsx(244 行)代理录入与检测

**接口一览**:

| 操作 | 接口 | 位置 |
|---|---|---|
| 列表 | `GET /api/proxies` | `:25` |
| 单条新增 | `POST /api/proxies` body `{ url, region }` | `:46-49` |
| 批量新增 | `POST /api/proxies/bulk` body `{ proxies: string[], region }` | `:41-44` |
| 单条删除 | `DELETE /api/proxies/{id}` | `:62` |
| 批量删除 | `POST /api/proxies/batch-delete` body `{ ids }` | `:75-78` |
| 启用/禁用 | `PATCH /api/proxies/{id}/toggle` | `:105` |
| 检测全部 | `POST /api/proxies/check` | `:111` |

**录入交互**(`add`,`:36-58`):`Input.TextArea rows={3}` 支持**每行一个代理**,提交时 `split('\n')` + `trim` + 去空(`:38`);**行数 > 1 走 `/bulk`,= 1 走单条接口**(`:40-50`)。旁边一个可选的「地区标签」`Input`(如 US、SG),对整批共用。成功后清空两个输入并重载。**前端不做 URL 格式校验**,合法性完全交给后端。

**检测交互**(`check`,`:109-116`):`POST /api/proxies/check` 是**触发式**接口(立即返回,后端异步跑),前端**无从得知何时跑完**,做法是硬编码 `setTimeout(3000)` 后 `load()` 并解除 loading(`:112-115`)。检测慢于 3 秒时看到的是旧数据,需再手动刷新 —— 这是本页最明显的粗糙处。按钮上的 `ReloadOutlined spin={checking}` 提供转动反馈。

**表格**(`:118-175`、`:230-240`):`pagination={false}`(全量渲染,代理多时会卡)。列为 代理地址(等宽)、地区、成功/失败(两个 Tag 并列显示 `success_count` / `fail_count`)、状态(活跃/禁用 Tag 带图标)、操作。操作列两个 `type="text"` 小按钮:切换启用(图标随状态在 `SwapLeftOutlined` / `SwapRightOutlined` 间变化)、删除(带 `Popconfirm`)。

**批量删除**(`batchDel`,`:71-102`)与 TaskHistory 的差别:结果不用 `message` 而是弹 `Modal.success`,列出「请求删除 / 成功删除 / 未找到」三个数字,`not_found` 非空时把 ID 列表渲染在一个可滚动的等宽区块里(`:91-95`)。头部常驻显示「已选中 N 条」,按钮在无选中时 `disabled`。

**已知问题**:该页 7 个 `any` 集中在 `useState<any[]>([])`(`:15`)、`columns: any[]`(`:118`)与各列 `render(_: any, record: any)`;`toggle`(`:104-107`)与 `check`(`:109-116`)**完全没有 try/catch**,失败静默无提示。

#### 七、给新接手者的注意事项

| # | 事项 |
|---|---|
| 1 | **不要移动 `frontend/src/main.tsx:3` 的 React 19 补丁 import**,否则全站 message/notification/Modal.confirm 静默失效。 |
| 2 | 改颜色要同时改 `theme.ts` 的 palette **和** `index.css:2-30` 的兜底变量,否则首屏与切换后不一致。 |
| 3 | 新增页面 = 加 `App.tsx` 内层 `<Route>` + `menuItems` 一项 + `getSelectedKey()` 一条 if,**三处都要改**(菜单 key 必须等于路由路径)。 |
| 4 | 新增平台要同时改后端 `/api/platforms` 与前端 `lib/platforms.ts` 的 `PLATFORMS`,前者管菜单、后者管表单行为。 |
| 5 | 业务请求一律用 `apiFetch`;需要流式或登录前的请求才裸 `fetch`。后端在面板鉴权失败时**必须回 `X-Panel-Auth-Required` 头**,否则前端不会跳登录页。 |
| 6 | `apiFetch` 恒 `res.json()`,新增返回 204 / 空 body 的接口会在前端抛解析错。 |
| 7 | 注册表单新增字段:多半只需加进 `Form.Item` + `submit()` 的 `registerExtra`(`frontend/src/pages/RegisterTaskPage.tsx:141-199`),`extra` 的 key 与 `/api/config` 同名。 |
| 8 | ChatGPT 三开关的持久化 key 是 `chatgpt-register-flow` / `chatgpt-registration-mode` / `chatgpt-bind-2fa`,改名会让老用户的偏好丢失(会静默回落到默认值)。 |
| 9 | 日志流是**手写 SSE 解析**(按 `\n\n` 切帧 + `data:` 正则),后端改帧格式或换成多行 `event:` 字段会直接解析不出来。 |
| 10 | `TaskLogPanel` 的重连**无次数上限**,只在收到 `!response.ok` 或 done 帧时才停;后端长时间 502 会一直以 8s 间隔重试。 |
| 11 | `RegisterTaskPage.pollTask` 的 `setInterval` 未在卸载时清理,离开页面仍会请求;修 bug 时优先处理。 |
| 12 | 待收紧的类型债:`Accounts.tsx`(28)、`Settings.tsx`(14)、`Proxies.tsx`(7)的 `any` 主要是接口响应缺 interface,可参照 `frontend/src/pages/TaskHistory.tsx:14-32` 的写法补齐。 |

---

## 十四、前端账号页与导出

### 前端四大业务页与共享组件

> 适用范围：`frontend/src/pages/{Accounts,Settings,ICloud,Payments}.tsx` 及其专属组件、API 封装、lib 工具。
> 基础架构（入口 / 路由 / 布局 / 注册任务页 / 日志面板）见另一份文档。
>
> 技术栈基线：React **19.2** + TypeScript 5.9 + Vite **8** + **Ant Design 5**（`antd@^5.24.6`、`@ant-design/icons`、`@ant-design/v5-patch-for-react-19`）+ react-router-dom 7。后端 FastAPI，所有接口挂在 `/api/*` 下，前端统一经 `apiFetch()`（`frontend/src/lib/utils.ts:20`）发起，路径里不写 `/api` 前缀。

---

#### 一、账号页 Accounts.tsx（1919 行，最大文件）

##### 1.1 文件结构总览

单文件里塞了 **9 个模块级函数 + 4 个内部组件 + 1 个巨型默认导出组件**，没有拆分子目录。

| 行号范围 | 区块 | 职责 |
| --- | --- | --- |
| `1:53` | import | antd 组件 21 个、图标 9 个、自研组件 7 个、lib 5 个 |
| `56:83` | 常量表 | `STATUS_COLORS` 状态色映射、`COPY_TOTP_ACTION_ID` 纯前端伪动作、`TASK_BACKED_ACTIONS`（把长耗时动作改走后台任务的白名单） |
| `85:132` | 数据归一化 | `parseExtraJson` / `normalizeAccount` / `formatSyncTime` / `formatCreatedAt` |
| `134:221` | 状态文案映射 | `authStateMeta`、`codexStateMeta`、`plusTrialMeta`、`planMeta`、`PLUS_TRIAL_FILTERS` |
| `223:332` | 详情展示组件 | `formatStructuredText`、`SummaryField`、`DetailSection`、`LocalProbeSummary` |
| `334:434` | CLIProxy/上传状态 | `cliproxyStateMeta`、`uploadSyncMeta`、`uploadSyncTitle`、`CliproxySyncSummary` |
| `436:457` | `TotpSecretAlert` | TOTP 密钥一次性下发提示 + 可复制 |
| `459:721` | **`ActionMenu`** | 行级操作下拉菜单，含 13 个 useState、结果弹窗、任务日志弹窗、付费抽屉 |
| `723:1919` | **`Accounts` 默认导出** | 页面主体（约 1200 行） |

`Accounts` 组件内部再分：

| 行号范围 | 功能区 |
| --- | --- |
| `724:767` | 状态声明：**29 个 useState/useForm**（列表、分页、6 个筛选、7 个弹窗开关、5 个 loading 标志…） |
| `769:813` | 副作用：URL 参数同步平台、详情表单回填、`load()` 拉列表、拉平台动作清单 |
| `815:836` | 剪贴板与 `extra_json` 取值助手（`copyText` / `copySecret` / `getRefreshToken`） |
| `838:984` | 单条 CRUD：删除、批量删除、新增、批量导入、发起注册、详情保存 |
| `986:1015` | `showBatchActionResult` — 批量结果只把失败项拼成 `pre` 弹窗 |
| `1017:1126` | 批量状态同步 / 批量上传 CPA（**作用范围逻辑核心**） |
| `1128:1171` | 补 RT（backfill refresh_token）任务发起与弹窗 |
| `1173:1221` | 作用范围计算函数 + 一堆内联 `CSSProperties` 样式常量 |
| `1223:1443` | **列定义**（基础 4 列 → 平台分支追加 → 通用尾部 2 列） |
| `1445:1470` | `statusSyncMenuItems` 状态同步下拉菜单项 |
| `1472:1597` | 工具栏 JSX：筛选器组 + 操作按钮组 |
| `1599:1617` | `<Table>` 主体（rowSelection / pagination / 双击开详情） |
| `1619:1740` | 注册弹窗、补 RT 弹窗 |
| `1742:1791` | 新增弹窗、`AccountExportModal` 挂载点 |
| `1793:1916` | 批量导入弹窗、账号详情弹窗 |

##### 1.2 后端接口映射

| 前端位置 | 接口 | 说明 |
| --- | --- | --- |
| `frontend/src/pages/Accounts.tsx:797` | `GET /accounts?platform&page&page_size&email&status&plus_status&created_at_start&created_at_end` | 列表主查询，全部筛选走 query string |
| `frontend/src/pages/Accounts.tsx:810` | `GET /actions/{platform}` | 拉平台动作清单，喂给 `ActionMenu` |
| `frontend/src/pages/Accounts.tsx:839` | `DELETE /accounts/{id}` | 单条删除 |
| `frontend/src/pages/Accounts.tsx:846` | `POST /accounts/batch-delete` `{ids}` | 批量删除（仅勾选，不支持筛选全部） |
| `frontend/src/pages/Accounts.tsx:857` | `POST /accounts` | 手动新增 |
| `frontend/src/pages/Accounts.tsx:872` | `POST /accounts/import` `{platform, lines}` | 文本按行导入 |
| `frontend/src/pages/Accounts.tsx:891` | `GET /config` | **注册前现取全量配置**，前端组装 `extra` |
| `frontend/src/pages/Accounts.tsx:955` | `POST /tasks/register` | 发起注册任务 |
| `frontend/src/pages/Accounts.tsx:977` | `PATCH /accounts/{id}` | 详情保存（只提交 `status` + `token`） |
| `frontend/src/pages/Accounts.tsx:1052` | `POST /actions/{platform}/{actionId}/batch` | 批量：`probe_local_status` / `check_plus_trial` / `sync_cliproxyapi_status` |
| `frontend/src/pages/Accounts.tsx:1104` | `POST /actions/{platform}/upload_cpa/batch` | 批量导入 CPA |
| `frontend/src/pages/Accounts.tsx:1154` | `POST /tasks/backfill-rt` | 批量补 RT，返回 `task_id` |
| `frontend/src/pages/Accounts.tsx:507` | `POST /tasks/backfill-rt` / `POST /tasks/bind-2fa` | 行级动作走后台任务（见 `TASK_BACKED_ACTIONS`） |
| `frontend/src/pages/Accounts.tsx:551` | `POST /actions/{platform}/{accountId}/{actionId}` `{params:{}}` | 行级同步动作通用入口 |

##### 1.3 列表列定义（`1223:1443`）

列表是 `const columns: any[]` **命令式 push** 出来的，不是声明式数组常量。

| 阶段 | 列 | 备注 |
| --- | --- | --- |
| 基础（`1223:1284`） | 邮箱、密码、RT、状态 | 密码/RT 用 `filter: 'blur(4px)'` 打码（`:1210`），复制按钮紧贴密文右侧 |
| `isChatgptPlatform` 分支（`1286:1358`） | 本地状态、Plus 试用 | 「本地状态」单格塞 5 个 Tag：认证 / 套餐 / Codex / CPA / Sub2API，外加可点复制的「2FA 已绑」Tag（`:1318`） |
| 非 chatgpt 分支（`1359:1400`） | CPA（仅当平台动作含 `upload_cpa`）、地区、试用链接 | CPA 列的出现由 `hasUploadCpaAction`（`:1184`）决定 |
| 通用尾部（`1402:1443`） | 注册时间、操作 | 操作列 chatgpt 下 `fixed:'right'`；内含「详情」「删除」+ `ActionMenu` |

横向滚动宽度按平台切换：`scroll={{ x: isChatgptPlatform ? 1300 : 980 }}`（`:1610`）。

##### 1.4 筛选器、分页、批量选择

| 控件 | 位置 | 绑定 state | 传参 |
| --- | --- | --- | --- |
| 邮箱搜索 `Input.Search` | `:1476` | `search` | `email` |
| 状态下拉 | `:1482` | `filterStatus` | `status` |
| Plus 试用下拉（仅 chatgpt） | `:1496` | `filterPlusStatus` | `plus_status` |
| 起止时间 `DatePicker showTime` ×2 | `:1504` `:1510` | `createdAtStart/End`（ISO 字符串） | `created_at_start/end` |

- 每个筛选 `onChange` 都手动 `setPage(1)` 复位分页——**没有集中处理**，四处重复。
- `load()` 用 `useCallback` 把 8 个依赖列进依赖数组（`:803`），`useEffect(() => load(), [load])`（`:805`）实现"任一筛选变化即重查"。
- 时间区间合法性在 `load()` 开头前端自检（`:782`），非法时直接清空列表并 `message.warning`，不发请求。
- 分页：受控 `pagination`，`pageSizeOptions: ['20','50','100']`，默认 `pageSize=50`（`:731`）。
- 批量选择：`rowSelection` 受控于 `selectedRowKeys: React.Key[]`（`:738`）。**翻页不清空选择**，也没有跨页"全选所有页"能力——这正是需要"筛选全部"作用范围的原因。

##### 1.5 「当前筛选全部」vs「勾选账号」两种作用范围 ⭐

这是账号页最重要的一条约定。前端**不把筛选出的 ID 列表算出来传给后端**，而是把"筛选条件本身"重发一遍，让后端自己重算集合。

###### 前端表达方式

作用范围由一个纯函数从 `selectedRowKeys` 推导，**勾了就是"所选"，没勾就是"当前筛选"**：

```ts
const getStatusSyncScope = (): 'selected' | 'all' => (selectedRowKeys.length > 0 ? 'selected' : 'all')
const getUploadCpaScope  = (): 'selected' | 'all' => (selectedRowKeys.length > 0 ? 'selected' : 'all')
```
（`frontend/src/pages/Accounts.tsx:1173` / `:1175`，两个函数体完全相同 —— 明显的可合并点）

按钮/菜单文案随之切换，把数量摊在标签上让用户先看清范围：

| 位置 | 「所选」文案 | 「筛选」文案 |
| --- | --- | --- |
| 状态同步菜单 `:1445` | `同步所选本地状态 (n)` | `同步当前筛选本地状态 (total)` |
| 导入 CPA 按钮 `:1177` | `导入所选 CPA (n)` | `导入筛选 CPA (total)` |
| 补 RT 按钮 `:1551` | `补 RT (n)` | `补 RT` |
| 导出按钮 `:1591` | `导出 (n)` | `导出` |
| 补 RT 弹窗 Alert `:1693` | `处理所选 n 个账号` | `处理当前筛选的 total 个（本页缺 RT m 个）` |

###### 请求体差异

三处批量入口（`handleBatchStatusSync:1017`、`handleBatchUploadCpa:1076`、`handleBackfillRt:1130`）**各自写了一遍几乎相同的 body 组装逻辑**：

| scope | 请求体字段 |
| --- | --- |
| `selected` | `account_ids: number[]`（`Array.from(selectedRowKeys).map(Number).filter(Number.isInteger && >0)`，为空则 `message.warning` 后 return） |
| `all` | `all_filtered: true` + 按需带 `email` / `status` / `plus_status` |

后端 `api/actions.py:149` 优先吃 `account_ids`（上限 1000 条，`:161`），否则要求 `all_filtered=true`，两者都没有则 400（`api/actions.py:174`）。

**已知不一致**：`all` 分支只回传 `email/status/plus_status` 三个筛选，**漏掉了 `created_at_start` / `created_at_end`**（对比 `load()` 的 `:795`）。也就是说按注册时间筛完再点"当前筛选全部"，后端处理的集合会比界面上看到的更大。只有 `AccountExportModal` 完整传了时间区间。

##### 1.6 平台特有操作菜单如何被后端驱动 ⭐

行级操作菜单**完全由后端下发**，前端不硬编码任何平台动作。

链路：`GET /actions/{platform}` → `api/actions.py:263` 返回 `{"actions": instance.get_platform_actions()}` → 各平台插件实现（`platforms/chatgpt/plugin.py:78`、`platforms/icloud/plugin.py:90`，基类默认 `core/base_platform.py:81`）→ 前端存进 `platformActions`（`frontend/src/pages/Accounts.tsx:728`）→ 作为 prop 传给每一行的 `<ActionMenu actions={platformActions}>`（`:1439`）。

菜单项由 `actions.map(a => ({key: a.id, label: a.label}))` 生成（`:607`），前端只认 `id`/`label`。

后端动作示例：

| 平台 | 动作 id |
| --- | --- |
| chatgpt | `probe_local_status`、`check_plus_trial`、`sync_cliproxyapi_status`、`refresh_token`、`backfill_refresh_token`、`bind_2fa`、`payment_channel_link`、`payment_channel_pay`、`upload_cpa`、`upload_sub2api`、`upload_tm`、`upload_codex_proxy` |
| icloud | `fetch_inbox`、`delete_alias` |

`ActionMenu.handleAction`（`:522`）按 **4 条分派规则**处理点击：

| 优先级 | 判定 | 行为 |
| --- | --- | --- |
| 1 | `actionId === COPY_TOTP_ACTION_ID` | 纯前端复制 TOTP，不发请求（`:527`）。该项由前端在 `acc.totpSecret` 存在时插到菜单最前面（`:604`），用 `__copy_totp_secret` 命名以免和后端 id 撞车 |
| 2 | `payment_channel_link` / `payment_channel_pay` | 不直接 POST，改开 `PaymentOperationDrawer`（`:537`） |
| 3 | 命中 `TASK_BACKED_ACTIONS`（`backfill_refresh_token` / `bind_2fa`） | 改调 `/tasks/*` 起后台任务，用 `TaskLogPanel` 弹窗显示逐步日志（`:503`）。原因写在 `:67` 注释：这类动作要跑几十秒还可能停下来等验证码，同步等只能看见一个转圈 |
| 4 | 其余 | `POST /actions/{platform}/{id}/{actionId}`，body 恒为 `{params:{}}`（`:551`） |

**注意**：后端动作定义里带了 `params` 描述（如 `payment_channel_pay` 声明了 `channel`/`card_id`/`taxfree_state`），但**前端从不渲染这些参数表单**，一律提交空 `params`。需要参数的动作只能靠后端读配置兜底，或者走专门的抽屉/弹窗（付费类就是这么绕过去的）。

响应处理按 payload 形态分支（`:555:593`）：`totp_secret` → 弹 `TotpSecretAlert`；`url|checkout_url|cashier_url` → 弹"复制/打开链接"；`probe` → 渲染 `LocalProbeSummary`；`sync` → 渲染 `CliproxySyncSummary`；否则 `JSON.stringify` 塞进 `pre`。

##### 1.7 状态同步与 `extra_json` 解析

后端把大量运行时状态塞进账号的 `extra_json`（字符串），前端在 `normalizeAccount`（`frontend/src/pages/Accounts.tsx:95`）里 `JSON.parse` 后铺成一级字段：

| 归一化字段 | `extra_json` 路径 | 消费位置 |
| --- | --- | --- |
| `cpaSync` | `sync_statuses.cpa` | 列表 Tag、`uploadSyncMeta` |
| `sub2apiSync` | `sync_statuses.sub2api` | 列表 Tag |
| `cliproxySync` | `sync_statuses.cliproxyapi` | 详情 `CliproxySyncSummary` |
| `chatgptLocal` | `chatgpt_local`（含 `auth`/`subscription`/`codex`） | 列表本地状态列、详情 `LocalProbeSummary` |
| `plusCheck` | `plus_check` | Plus 试用列 |
| `totpSecret` | `totp_secret` | 「2FA 已绑」Tag、详情、复制动作 |

`getRefreshToken`（`:829`）**又独立 parse 了一次** `extra_json` 取 `refresh_token`/`refreshToken`，没复用 `normalizeAccount` 的结果 —— 每次渲染 RT 列都重新 parse 一遍 JSON。

三种同步的语义区分：

| kind | 动作 id | 含义 |
| --- | --- | --- |
| `probe` | `probe_local_status` | 拿本地凭证直接打上游，判断 AT 是否有效、套餐、Codex 可用性 |
| `plus` | `check_plus_trial` | 判断能否领首月免费（`trial_eligible` / `plus_active` / `free` / `banned` / `token_invalid` / `unchecked`） |
| `remote` | `sync_cliproxyapi_status` | 查 CLIProxyAPI 远端对这个 auth-file 的认定状态 |

批量结果**只展示失败项**：`showBatchActionResult`（`:986`）过滤 `!item.ok` 拼成多行文本，全成功时直接不弹窗。toast 按 `success/failed/total` 三态分别用 success/warning/error（`:1057:1065`）。

##### 1.8 补 RT（auth-file 补传）

入口只在 chatgpt 平台出现（`:1545`）。弹窗 `:1679:1740` 分两态：未起任务显示表单，起了任务换成 `TaskLogPanel`。

| 表单项 | 默认值 | 含义 |
| --- | --- | --- |
| `only_missing_rt` Switch | `true` | 只补缺 RT 的号 |
| `allow_login` Switch | `true` | 会话失效时用邮箱密码重登（可能要收验证码） |
| `concurrency` | 1（max 10） | 并发 |
| `delay_seconds` | 5 | 每号间隔，注释说明连续打授权链易触发风控（`:1726`） |

`missingRtCount`（`:1128`）只统计**当前页**缺 RT 的数量，文案里也如实写成「本页缺 RT n 个」。

##### 1.9 注册弹窗的配置搬运

`handleRegister`（`:887`）里有一段值得注意的反模式：先 `GET /config` 拿全量配置，然后**在前端手抄 40+ 个邮箱服务字段**（`:893:943`，覆盖 applemail / laoudo / gptmail / maliapi / moemail / skymail / cloudmail / duckmail / freemail / cfworker / luckmail）拼成 `extra` 再发给 `/tasks/register`。后端加一个邮箱 provider 字段，前端这份清单就得同步改，否则静默丢参。

ChatGPT 专属三个开关（注册方式 / Token 方案 / 绑定 2FA）不入表单，走三个 `usePersistentChatGPT*` hook 存本地，再由 `buildChatGPTRegistrationRequestAdapter`（`:944`）以 `extendExtra()` 方式织进 `extra`。

##### 1.10 账号详情弹窗（`1793:1916`）

- 可编辑字段**只有 2 个**：`status`、`token`（`PATCH /accounts/{id}`）。
- 只读展示：Refresh Token（`antd` `Text copyable`）、TOTP 2FA 密钥（附「验证器里选手动输入密钥」提示）、chatgpt 专属两个 `DetailSection`（本地真实状态 / CLIProxyAPI 状态）。
- 双击表格任意行也能打开（`:1611`）。
- 无数据时提示去操作菜单点对应动作，属于把"下一步操作"写进空态文案的好做法。

---

#### 二、导出弹窗 AccountExportModal.tsx（224 行）

`frontend/src/components/AccountExportModal.tsx`，被 `frontend/src/pages/Accounts.tsx:1776` 挂载。

##### 2.1 Props 契约

| prop | 类型 | 来源 |
| --- | --- | --- |
| `filters` | `AccountExportFilters`（`platform` + 5 个可选筛选） | 账号页当前筛选，**含 `created_at_start/end`** |
| `selectedIds` | `number[]` | `selectedRowKeys` 清洗后的正整数 |
| `filteredTotal` | `number` | 账号页 `total` |

##### 2.2 范围选择

`Radio.Group` 两个按钮（`:174`）：`所选 n 个`（`selectedIds.length===0` 时 disabled）/ `当前筛选 total 个`。

默认值逻辑与账号页一致但实现方式不同——用 `scopeChoice ?? (selectedIds.length > 0 ? 'selected' : 'filtered')`（`:65`），即"**用户没手动改过就按勾选情况推断，改过之后以用户为准**"。关闭弹窗时 `setScopeChoice(null)` 复位（`:126`）。

注意枚举值与账号页不一致：这里是 `'selected' | 'filtered'`，账号页是 `'selected' | 'all'`。

##### 2.3 格式清单来自后端

**不是硬编码**。`GET /accounts/export-formats`（`:81`）返回 `{formats: [...], default: "email_pw_2fa"}`，由 `services/account_export.py:253 list_export_formats()` 生成，后端定义在 `_FORMAT_LIST`（`services/account_export.py:163`）。

只在弹窗首次打开且 `formats` 为空时拉一次（`:80` 的 `if (!open || formats.length > 0) return`），之后缓存在组件 state 里。默认选中项优先用后端 `default`，兜底取第一项（`:84`）。

后端现有 13 种格式（列分隔符为 `----`）：

| id | label | 备注 |
| --- | --- | --- |
| `email_pw` | 邮箱----密码 | |
| `email_pw_2fa` | 邮箱----密码----2FA | **后端默认** |
| `email_pw_2fa_at` | +AT | |
| `email_pw_2fa_rt` | +RT | |
| `email_pw_2fa_at_rt` | 全凭证 | |
| `email_pw_2fa_phone` | +手机号 | 无号码留空占位 |
| `email_pw_rt` | 精简版 | |
| `email_2fa` | 只补验证器 | |
| `at` / `rt` / `totp` | 单列，一行一个 | `skip_empty_rows`，缺字段的账号直接跳过 |
| `csv` | 全字段表格 | 带表头，扩展名 `.csv` |
| `json` | 全字段 | 扩展名 `.json` |

下拉只显示 `label`，`description` 挂 `title` 悬浮（`:192`），选中后在下方以 `secondary` 小字展示 `description` + `sample`（`:195`）。

##### 2.4 预览生成

`POST /accounts/export-text`（`:107`），body 按 scope 组装：

| scope | body |
| --- | --- |
| `selected` | `{format, platform, account_ids}` |
| `filtered` | `{format, platform}` + 非空的 `email` / `status` / `plus_status` / `created_at_start` / `created_at_end` |

返回 `ExportPreview = {format, total, lines, content, filename}`。

两个值得学的实现细节：

1. **依赖数组防死循环**（`:67` 注释）：父组件每次渲染都新建 `filters`/`selectedIds` 对象，直接进 `useEffect` 依赖会把预览请求打成死循环。做法是先 `JSON.stringify(filters)` / `selectedIds.join(',')` 压成字符串，再用 `useMemo` 还原成 `request`（`:69:77`）。
2. **用请求 key 代替 loading 状态**（`:60` 注释）：`result` 存成 `{key, data}`，`requestKey = scope|formatId|filtersKey|selectionKey`（`:90`）。展示时 `result?.key === requestKey ? result.data : null`（`:122`），`loading` 由 key 不匹配推导（`:123`）。选项一变旧结果立刻失效，不需要额外 loading state，也不会出现旧响应覆盖新请求。另配 `cancelled` 闭包标志防竞态（`:106`）。

##### 2.5 预览裁剪、复制、下载

| 能力 | 实现 |
| --- | --- |
| 预览裁剪 | `PREVIEW_LINE_LIMIT = 200`（`:42`），超出部分替换成「… 其余 n 行已省略，复制和下载不受影响」（`:44`）；`content` 本体不裁，复制/下载仍是全量 |
| 复制 | `navigator.clipboard.writeText(shown.content)`，成功提示带行数；失败提示"请手动选中预览框内容"（`:130`） |
| 下载 | `Blob` + **前置 `\uFEFF` BOM**（`:142`，保证 Excel 打开 CSV 不乱码），`type: text/plain;charset=utf-8;`，文件名用后端下发的 `filename`，`click()` 后立刻 `revokeObjectURL` |
| 命中提示 | `Alert` 显示「命中 n 个账号，导出 m 行」，并说明"空字段照样占位，按 `----` 切列不会错位；单列格式会跳过没有该字段的账号"（`:201`） |

`复制`/`下载` 按钮在 `shown.content` 为空时 disabled（`:152`）。

---

## 十五、前端设置页、iCloud 页与付费页

### 三、全局配置页 Settings.tsx(1653 行)

#### 3.1 整体形态:声明式配置表 + 命令式面板

这一页有两套并存的机制,理解它是读懂 1653 行的关键:

| 机制 | 适用范围 | 数据流 |
| --- | --- | --- |
| **声明式配置表**(`TAB_ITEMS`,`frontend/src/pages/Settings.tsx:93`) | 7 个 Tab 里的普通配置项 | 一个静态数组描述所有字段 → `ConfigSection`/`ConfigField` 通用渲染 → 一个大 `Form` 统一收集 → 整页 `PUT /api/config` 一次落库 |
| **命令式面板**(自定义组件) | `插件`、`安全`、CF Worker 域名池、SMS 自检、Solver 状态 | 组件自持 state,自己 `apiFetch`,**不参与那个大 Form 的保存** |

后者的 4 个组件是:

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| `CFWorkerDomainPoolSection` | `frontend/src/pages/Settings.tsx:607` | CF Worker 多域名池的增删与启停(数组型配置,通用字段渲染表达不了) |
| `SmsProbePanel` | `frontend/src/pages/Settings.tsx:744` | 「测试余额」「查询国家排名」两个即时探测动作 |
| `SolverStatus` | `frontend/src/pages/Settings.tsx:805` | 本地 Turnstile Solver 运行状态与重启 |
| `IntegrationsPanel` | `frontend/src/pages/Settings.tsx:860` | 外部插件安装/更新/启停/卸载 |
| `SecurityPanel` | `frontend/src/pages/Settings.tsx:1133` | 面板访问密码与后台 2FA |

#### 3.2 九个 Tab 与配置分区

`TAB_ITEMS` 共 9 个 Tab(`frontend/src/pages/Settings.tsx:94-430`),其中 2 个是纯自定义面板(`sections: []`):

| Tab key | 标签 | 分区 | 关键配置项 |
| --- | --- | --- | --- |
| `register` | 注册设置 | 默认注册方式 | `default_executor`(执行器类型)、`register_retry_times`(整流程失败重试轮数) |
| `mailbox` | 邮箱服务 | **15 个 provider 分区** | `mail_provider` 选服务 + `mailbox_otp_timeout_seconds` 等待秒数;下面是 Laoudo / Freemail / MoeMail / SkyMail / CloudMail / YYDS Mail(MaliAPI) / 邮箱导入(微软) / 邮箱导入(AppleMail) / GPTMail / OpenTrashMail / TempMail.lol / DuckMail / CF Worker / LuckMail 各自的凭据分区 |
| `captcha` | 验证码 | 验证码服务 | `default_captcha_solver`、`yescaptcha_key` |
| `chatgpt` | ChatGPT | CPA 面板 / Sub2API 面板 / CPA 自动维护 | `cpa_*`、`sub2api_*`、`cpa_cleanup_*`(自动维护含阈值、并发、延迟) |
| `sms` | 手机接码 | 接码平台 / 国家选择 / 租号与重试 | 见下表 |
| `cliproxyapi` | CLIProxyAPI | 管理面板 | `cliproxyapi_base_url`、`cliproxyapi_management_key` |
| `icloud` | iCloud | 隐私邮箱默认参数 | `icloud_region`、`icloud_alias_label`、`icloud_alias_note`、`public_base_url`(面板访问地址,留空则用浏览器地址栏的地址;只在服务端拿不到前端 origin 时才用到,见 4.4.1) |
| `integrations` | 插件 | — (纯 `IntegrationsPanel`) | 不走 Form |
| `security` | 安全 | — (纯 `SecurityPanel`) | 不走 Form |

`sms` Tab 的 15 个配置项是全页最密的一块:

| 分区 | 配置 key | 标签 | 类型 |
| --- | --- | --- | --- |
| 接码平台 | `sms_enabled` | 启用手机接码 | boolean |
| | `sms_provider` | 接码平台 | select |
| | `sms_api_key` | API Key | input |
| | `sms_service` | 服务代码 | input |
| 国家选择 | `sms_country` | 默认国家 | **country**(自定义类型,渲染 `SmsCountrySelect`) |
| | `sms_auto_country` | 自动选最优国家 | boolean |
| | `sms_auto_min_stock` | 自动选号最低库存 | input |
| | `sms_auto_max_price` | 自动选号价格上限 | input |
| | `sms_max_price` | 单号价格上限 | input |
| | `sms_fixed_price` | 固定价格 | input |
| 租号与重试 | `sms_reuse_phone` | 复用同一号码 | boolean |
| | `sms_phone_success_max` | 单号复用上限 | input |
| | `sms_per_phone_timeout` | 单号等待秒数 | input |
| | `sms_max_phone_attempts` | 最多换号次数 | input |
| | `sms_code_retries_per_phone` | 单号内验证重试次数 | input |

字段描述符支持 `type`(`select`/`boolean`/`country`,缺省为文本框)、`placeholder`、`secret: true`(渲染密码框)。加一个新配置项**只需往 `TAB_ITEMS` 里加一行**——这是这一页设计得最好的地方,声明式表驱动省掉了大量重复 JSX。

#### 3.3 保存机制:整页一次提交

保存是**整页级**的,不是逐项。`save()`(`frontend/src/pages/Settings.tsx:1478`)一次性把整个 Form 的值 `PUT /api/config`:

```ts
await apiFetch('/config', { method: 'PUT', body: JSON.stringify({ data: values }) })
```

`save()` 里在提交前做了一批**规范化与前端校验**,这是配置页最容易出错的部分:

| 处理 | 位置 | 做什么 |
| --- | --- | --- |
| 邮箱导入视图落库 | `:1479` | `mail_import_source` 必须单独存,否则退出设置页再回来只能按 `mail_provider` 反推,「MailAPI URL」会永远退回「Outlook」(源码注释直接点明了这个坑) |
| CF Worker 域名池 | `:1481-1494` | 域名列表去噪 → 启用列表与总列表求交 → **序列化成 JSON 字符串**存;域名池非空时清掉单域名字段 `cfworker_domain` |
| 至少启用一个域名 | `:1484` | 域名池非空但启用为空 → 切回 `mailbox` Tab + 报错**中断保存** |
| 布尔归一 | `:1495-1502` | `cpa_enabled` / `sub2api_enabled` / 两个随机子域名开关 / `email_domain_rule_enabled` / 全部 `SMS_BOOLEAN_KEYS` 走 `parseBooleanConfigValue` |
| 国家列表 | `:1503` | 下拉给的是数组,库里要 `"52,4,10"` 逗号串 |
| 域名级数校验 | `:1506-1516` | 仅当 `mail_provider === 'cfworker'` 且启用域名规则时,要求级数是 ≥2 的整数,否则切 Tab 报错中断;最终统一写成字符串,兜底 `'2'` |
| 保存后回填 | `:1519-1533` | 把规范化后的值写回 Form,避免"界面显示的还是脏值" |

**保存反馈**:`message.success('保存成功')` + `saved` 状态 2 秒自动复位(`:1534-1536`)。**没有脏值提示**——切 Tab 或离开页面不会警告未保存的改动。

**浮动保存按钮**:`mailbox` 与 `chatgpt` 两个 Tab 内容特别长,所以这两个 Tab 会显示一个 `position: fixed` 的浮动保存条(`:1378`、`:1555`),用 `ResizeObserver` + `resize` 监听内容区宽度来对齐(`:1440-1471`)。

#### 3.4 配置值的类型往返问题

后端 `config` 表把所有值当字符串存,前端表单要的是 boolean / number / 数组,于是产生了一层往返转换。承担这件事的是 `lib/configValueParsers.ts` —— 全文只有 10 行,一个函数:

```ts
export function parseBooleanConfigValue(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  const normalized = String(value ?? '').trim().toLowerCase()
  return ['1', 'true', 'yes', 'on'].includes(normalized)
}
```

它容忍 `1` / `true` / `yes` / `on` 四种真值写法,是为了兼容历史上不同代码路径写进库里的不同格式。

其余的类型转换**散落在 `Settings.tsx` 内部**,没有收进 parser 模块:

| 转换 | 加载侧(`:1382-1437`) | 保存侧(`:1473-1517`) |
| --- | --- | --- |
| 域名列表 | `parseStoredDomainList`(`:518`) | `normalizeDomainList`(`:505`) + `JSON.stringify` |
| 国家 ID 列表 | `parseCountryIdList` | `.join(',')` |
| 布尔 | `parseBooleanConfigValue` × N | `parseBooleanConfigValue` × N |
| 派生开关 | `resolveFeatureEnabledConfig`(`:540`)—— `cpa_enabled` 未显式设置时,按「API URL 是否非空」推断 | 直接归一 |
| 默认值兜底 | 加载时给 10 个字段填硬编码默认值(`:1389-1412`,如 `applemail_base_url`、`luckmail_base_url`、`cloudmail_timeout=30`) | — |

**加载侧的默认值是硬编码在前端的**(`:1389-1412`),后端 `/api/config` 返回空值时前端补默认。同一个默认值可能在前后端各存一份,是潜在的不一致来源。

#### 3.5 即时动作面板

这些面板不参与整页保存,点一下立刻打后端:

| 面板 | 动作 | 接口 |
| --- | --- | --- |
| `SmsProbePanel`(`:739`) | 测试余额 / 查询国家排名 | `apiFetch('/sms/${kind}')`(`:755`) |
| `SolverStatus`(`:800`) | 查状态 / 重启 | `GET /solver/status`(`:805`)、`POST /solver/restart`(`:813`) |
| `IntegrationsPanel`(`:855`) | 列服务 + 读配置 | `GET /integrations/services` + `GET /config` 并发(`:881-882`) |
| | 补传远端未发现 | `POST /integrations/backfill`(`:917`) |
| | 保存安装/更新策略 | `PUT /config`(`:934`,只提交策略字段) |
| | 全部启动 / 全部停止 | `POST /integrations/services/start-all` / `stop-all`(`:1029`、`:1032`) |
| | 单个安装/更新 | `POST /integrations/services/{name}/install`(`:1070`、`:1077`) |
| | 单个启动/停止/卸载 | `.../start`、`.../stop`、`.../uninstall`(`:1085`、`:1091`、`:1104`) |
| `SecurityPanel`(`:1128`) | 查鉴权状态 | `GET /auth/status`(`:1143`) |
| | 首次设密码 / 关闭密码 | `POST /auth/setup`(`:1157`)、`POST /auth/disable`(`:1175`) |
| | 改密码 | `POST /auth/change-password`(`:1193`) |
| | 后台 2FA 三步 | `GET /auth/2fa/setup`(`:1209`)→ `POST /auth/2fa/enable`(`:1223`)→ `POST /auth/2fa/disable`(`:1241`) |

`IntegrationsPanel` 的插件更新有两种模式(`updateMode: 'tag' | 'branch'`,`:859`):默认按最新 semver tag,可切成分支 HEAD。结果统一进一个 `resultModal` 弹窗展示(`:867`),由 `formatResultText`(`:496`)把任意后端返回格式化成可读文本。

`SecurityPanel` 的 2FA 是**面板自身的登录 2FA**,与 ChatGPT 账号的 TOTP 绑定是两件不相干的事,阅读时容易混。

#### 3.6 邮箱服务分区的动态收拢

`mailbox` Tab 有 15 个 provider 分区,但同时只该看到相关的那几个。`splitMailboxSections`(`:469`)把分区切成三份:

| 返回字段 | 含义 |
| --- | --- |
| `defaultSection` | 「默认邮箱服务」分区,恒定置顶 |
| `selectedSection` | 当前选中 provider 对应的那个分区,紧随其后 |
| `remainingSections` | 其余 provider 的分区,折叠在后面 |

当前生效的 provider 由三个值算出来(`:1375-1377`):`mail_provider` 原始值 + 存储的 `mail_import_source` → `resolveEffectiveMailProvider`。这里的复杂度来自「邮箱导入」这一类:UI 上它是一个 `mail_provider` 选项,但底下还要再选具体导入源(微软 / Outlook / Hotmail / MailAPI URL / AppleMail),两个值合起来才能确定实际用哪个后端。

### 四、邮件导入面板 MailImportPanel.tsx(780 行)

「邮箱服务 → 邮箱导入」这一类 provider 不像其它临时邮箱那样调 API 现开地址,而是**把已有的邮箱账号批量导进本地号池**,注册时再从号池领号。这个面板就是导入入口,它嵌在 Settings 的 `mailbox` Tab 里,接收父级的 `FormInstance`(`frontend/src/components/settings/MailImportPanel.tsx:12`)从而与整页保存共享一份表单状态。

#### 4.1 两类导入源

面板顶层先分 provider 类型(`frontend/src/components/settings/MailImportPanel.tsx:8`):

```ts
type MailImportProviderType = 'applemail' | 'microsoft'
```

| 维度 | `microsoft` | `applemail` |
| --- | --- | --- |
| UI 名称 | 邮箱导入(微软 / Outlook / Hotmail) | 邮箱导入(AppleMail / 小苹果) |
| 细分导入源 | Outlook / Hotmail / MailAPI URL 三种 | 单一 |
| 关键配置 | `outlook_backend`(微软收信方式:`graph` 等) | `applemail_base_url`、`applemail_pool_dir`、`applemail_pool_file`、`applemail_mailboxes`(轮询文件夹,默认 `INBOX,Junk`) |
| 落库表 | `outlook_accounts`(见 01 章 `OutlookAccountModel`) | 文件型号池目录 |

微软类之所以要再分三种源,是因为收信后端不同:Outlook/Hotmail 走 OAuth refresh_token 换 access_token,MailAPI URL 走第三方中转地址。这个"一个 provider 选项 + 一个子源选项"的两级结构,是 `Settings.tsx` 里 `resolveEffectiveMailProvider` 存在的原因(见 3.6)。

> MailAPI URL 这一源除了手工粘 `邮箱----取码地址`,还可以从 iCloud 页一键导入隐私邮箱(`POST /api/icloud/aliases/import-to-pool`,见 04 章 4.4.1)——它走的就是本面板同一条 `MicrosoftMailImportStrategy` 流水线,只是内容由后端拼。

#### 4.2 交互流程

1. 顶部 `Select` 选 provider 类型(`frontend/src/components/settings/MailImportPanel.tsx:614`),`loading` 态由 `loadingProviders` 驱动,选项从后端 `/api/mail-imports/providers` 拉。
2. 按所选类型渲染对应的凭据/参数表单字段。
3. 粘贴或选择待导入的账号数据。
4. 提交导入,后端按 provider 规则解析(微软类的筛选规则在后端 `services/mail_imports/microsoft_import_rules.py`)。
5. 面板展示导入结果统计(成功/跳过/失败)。

`lib/mailImport.ts` 承担纯函数职责:`normalizeMailImportSource`(把任意历史值归一到合法枚举)、`isMailImportProvider`(判断某个 `mail_provider` 值是否属于导入类)、`resolveEffectiveMailProvider`(两级值合成实际 provider)。这三个函数同时被 `Settings.tsx` 的加载与保存两侧调用,是**前后端配置值往返一致性的关键**——源码注释明确写了不单独存 `mail_import_source` 会导致「MailAPI URL 永远退回 Outlook」。

---

### 五、iCloud 页 ICloud.tsx(1007 行)

#### 5.1 页面骨架

顶部三个动作按钮(`frontend/src/pages/ICloud.tsx:369-376`):**应用内登录**(SRP 全流程)、**手工导入 Cookie**、刷新。下面是两个 Tab:

| Tab key | 标签 | 内容 |
| --- | --- | --- |
| `accounts` | `主号管理 (n)` | Apple ID 主号表:状态、区域、启停、同步、删除 |
| `aliases` | `隐私邮箱 (n)` | 别名表:地址、所属主号、标签/备注、收件、删除;顶部有批量生成、导出、**导入 MailAPI 号池**、批量删除 |

收件箱不是第三个 Tab,而是从别名行点「收件」弹出的 `AliasInboxDrawer`(`frontend/src/pages/ICloud.tsx:841`)。

#### 5.2 数据加载

`load()`(`frontend/src/pages/ICloud.tsx:93`)用 `Promise.all` 并发拉主号与别名两个列表:

```ts
const [nextAccounts, nextAliases] = await Promise.all([
  listICloudAccounts(),
  listICloudAliases(filterAccountId),
])
```

`filterAccountId` 变化会触发重新加载(`useCallback` 依赖),同时有一个专门的 `useEffect` 在切换主号筛选时清空已选行(`frontend/src/pages/ICloud.tsx:132`),注释说明了原因:换筛选后原来选中的行已不在表里,留着只会误删。这类细节在这一页有多处,是被真实踩过的坑。

所有单行动作统一走 `withBusy(id, action, successText)`(`frontend/src/pages/ICloud.tsx:114`):置 `busyId` → 执行 → 成功提示 → **重新 `load()` 全量刷新** → 清 `busyId`。这是全页统一的写后刷新策略,简单但每次动作都要重拉两个列表。

#### 5.3 登录弹窗的两条路径

##### 路径一:应用内登录(`ICloudLoginModal.tsx`)

用 AntD `Steps` 表达两步(`frontend/src/components/icloud/ICloudLoginModal.tsx:132`):`填写账号` → `双重认证`,`current` 由 `login` 状态是否存在决定。

**第一步表单字段**:

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `email` | ✅ | Apple ID |
| `password` | ✅ | Apple ID 密码 |
| `imap_password` | — | **IMAP 应用专用密码**,`extra` 提示"在 Apple 账号页面单独生成,用于连接 iCloud IMAP 收件" |
| `region` | 默认 `global` | 区域 |
| `imap_host` / `imap_port` | 默认 `imap.mail.me.com` / `993` | 来自 `frontend/src/lib/icloud.ts:3-4` 的常量 |

表单顶部有一条 `Alert`(`:149`)明确告知:**Apple ID 密码只参与本次登录握手,不会保存;收件使用单独生成的应用专用密码。** 这与后端 `credentials_cipher` 只存 session 与 IMAP 密码的设计一致(见 06 章)。

**第二步的三种投递方式**由后端返回的 `delivery` 字段决定,前端有对应文案表(`frontend/src/components/icloud/ICloudLoginModal.tsx:30`):

| `delivery` 值 | 提示文案 | 前端行为 |
| --- | --- | --- |
| `trusted_devices` | 验证码已推送到该 Apple ID 的受信任设备 | 直接输 6 位码 |
| `sms` | 验证码已通过短信发送到受信任手机号 | 直接输 6 位码 |
| `sms_selection_required` | 该账号没有受信任设备,请先选择接收验证码的手机号 | **必须先选号**:禁用「验证并保存」与「重新发送」,先调 `sendICloudLoginSMS` |

「验证并保存」按钮的 disabled 条件是 `needsPhoneChoice || code.trim().length !== 6`(`:116`)——码必须正好 6 位。

**取消语义**:关闭弹窗时若登录未完成,会 `cancelICloudLogin(login_id)` 通知后端释放这次握手(`:87-92`),`.catch(() => {})` 静默忽略失败。

##### 路径二:Cookie 导入(`CookieImportModal`,`frontend/src/pages/ICloud.tsx:530`)

跳过 SRP 与双重认证,直接粘贴浏览器导出的 iCloud Cookie。适用于 SRP 路径受阻或已有浏览器会话的场景。

#### 5.4 别名生成与限流提示

`GenerateAliasModal`(`frontend/src/pages/ICloud.tsx:603`)的字段:主号(必填)、生成数量(默认 1)、标签(可选)、备注(可选)。

限流在**前端就硬约束**了:

```ts
<InputNumber min={1} max={ICLOUD_HOURLY_ALIAS_LIMIT} ... />
```

`ICLOUD_HOURLY_ALIAS_LIMIT = 5` 定义在 `frontend/src/lib/icloud.ts:7`,提示文案是"Apple 限制每个主号每滚动小时最多 5 个"。同时弹窗会读该主号的 `quota` 字段展示剩余额度(`frontend/src/pages/ICloud.tsx:639`)。**前端上限只是体验优化,真正的限流在后端按主号维度计数**(见 06 章)。

#### 5.5 别名导出与分享链接

别名表顶部的导出支持两种格式(`frontend/src/pages/ICloud.tsx:435-436`),都是 `----` 分隔的两列:

| 模式 | 一行长什么样 |
| --- | --- |
| `mail_url` | `隐私邮箱----邮件 URL` |
| `account` | `隐私邮箱----所属主号` |

导出逻辑在 `lib/icloud.ts` 里做成了纯函数:`formatAliasExport`(`:37`)、`countExportableAliases`(`:54`)、`downloadTextFile`(`:62`)、`aliasMailUrl(shareToken)`(`:75`)。文件名常量 `ALIAS_EXPORT_FILENAME = 'icloud_aliases.txt'`。

`aliasMailUrl` 是把别名的 `share_token` 拼成**免登录邮件页链接**——这个链接本身就是权限凭证,导出成文本即意味着凭证离开了系统,是需要注意的一点(后端侧风险见 06 章)。

#### 5.5.1 「导入 MailAPI 号池」按钮与 `AliasPoolImportModal`

导出按钮右边还有一个**导入 MailAPI 号池**按钮(`frontend/src/pages/ICloud.tsx:444-450`,`ImportOutlined`),`disabled` 条件与导出一致(`targetAliases.length === 0`)——它做的事就是"导出 `mail_url` 再手工粘进邮箱导入面板"的自动化版本,省掉中间那次下载与上传:

```ts
const response = await importICloudAliasesToPool({
  ids: aliases.map((alias) => alias.id),
  account_id: accountId,          // 全量导时带上主号筛选，避免把别的主号的别名一起塞进去
  origin: window.location.origin, // 反代/穿透/换端口时服务端猜不到对外地址
})
```

弹窗组件 `AliasPoolImportModal`(`frontend/src/pages/ICloud.tsx:678`)结构:

| 部分 | 内容 |
| --- | --- |
| 说明 | 「一行一条 `隐私邮箱----邮件 URL`,等价于先导出再手工导入」+ 导入后设置里的导入类型会自动切到 MailAPI URL |
| 计数 | 待导入 `aliases.length` 个;可导入数由 `countExportableAliases(aliases, 'mail_url')` 算出,不足时提示「N 个还没有邮件 URL,会被跳过」——与导出用同一个纯函数,两边口径不会漂 |
| 结果 | `Alert` 显示「导入完成:成功 X / 失败 Y / 跳过 Z」,`errors` 用 `<pre>` 逐行铺开(含"早就在号池里了"那类提示) |
| 收尾 | 明确一句「邮件链接就是权限,导进号池等于把它交给了注册任务」 |

`open` 变化时清空上一次结果(`useEffect` + `setResult(null)`),`destroyOnHidden` 让弹窗关闭即卸载。成功提示按 `imported > 0` 分岔:有导入走 `message.success`,一条没进则 `message.warning('没有导入新的隐私邮箱,看看下面的原因')`。

#### 5.6 收件箱抽屉

`AliasInboxDrawer`(`frontend/src/pages/ICloud.tsx:841`)是一次性拉取而非增量:`listICloudAliasMessages(alias.id)` 拉全量,之后的搜索是**纯前端过滤**(`frontend/src/pages/ICloud.tsx:871`),匹配主题、发件人名、发件人邮箱、摘要、纯文本正文五个字段。

响应式行为(`frontend/src/pages/ICloud.tsx:882-884`):

- **宽屏**(`screens.md`):左列表右详情双栏,默认自动摊开第一封,省一次点击。
- **窄屏**:列表优先,点了才进详情。

抽屉宽度 `isNarrow ? '100%' : 'min(1080px, 92vw)'`。列表项**刻意不用 AntD `Space`**,源码注释解释了原因:`Space` 会给每个子元素套一层 div,`flex: 1` 落不到文本上,导致长主题和长发件人地址截不断——用 `display: grid` + `Text ellipsis` 才能正确截断。这条注释是很有价值的排错记录。

未读态用 `Text strong` + `Badge status="processing"` 表达,附件用 `PaperClipOutlined`,时间用 `formatRelativeTime` 显示相对时间、`Tooltip` 里给绝对时间(`lib/icloud.ts:79/90`)。

#### 5.7 api/icloud.ts 函数清单

| 函数 | 作用 |
| --- | --- |
| `listICloudAccounts()` | 主号列表 |
| `startICloudLogin(payload)` | 发起 SRP 登录,返回 `ICloudLoginState` |
| `verifyICloudLogin(loginId, code)` | 提交双重认证码 |
| `resendICloudLoginCode(loginId)` | 重发验证码 |
| `sendICloudLoginSMS(...)` | 指定受信任手机号发短信 |
| `cancelICloudLogin(loginId)` | 取消本次登录握手 |
| `importICloudCookie(payload)` | Cookie 导入主号 |
| `setICloudAccountEnabled(id, enabled)` | 启停主号 |
| `deleteICloudAccount(id)` | 删除主号 |
| `syncICloudAccount(id)` | 同步主号(拉远端别名) |
| `listICloudAliases(accountId?)` | 别名列表,可按主号过滤 |
| `generateICloudAliases(payload)` | 批量生成别名 |
| `deleteICloudAlias(id, remote = true)` | 删单个别名,`remote` 控制是否同时删 Apple 侧 |
| `batchDeleteICloudAliases(...)` | 批量删除,返回 `ICloudBatchDeleteResult` |
| `listICloudAliasMessages(aliasId)` | 拉别名收件箱 |
| `importICloudAliasesToPool({ ids?, account_id?, origin?, enabled? })` | 把隐私邮箱导进 MailAPI URL 号池,返回 `ICloudAliasPoolImportResult`(`total` / `imported` / `skipped` / `failed` / `skipped_aliases` / `errors` / `source`);等价于导出 `mail_url` 再导入 |

删除文案值得注意:删主号提示"将同时移除本地记录的隐私邮箱,**不会删除 iCloud 上游地址**"(`frontend/src/pages/ICloud.tsx:274`);删别名提示"会先在 iCloud 停用并删除该地址,**删除后无法恢复**"(`frontend/src/pages/ICloud.tsx:348`)。两者对上游的影响相反,文案区分得很清楚。

---

### 六、付费页 Payments.tsx

#### 6.1 两个 Tab

| Tab | 内容 |
| --- | --- |
| 操作 | 选 ChatGPT 账号 → 选操作与渠道选项 → 创建任务 → 看日志与结果 |
| 直卡卡片库 | 卡片增删、使用次数重置 |

#### 6.2 操作流

1. **加载账号**:`apiFetch('/accounts?platform=chatgpt&page=1&page_size=1000')`(`frontend/src/pages/Payments.tsx:63`)—— 一次拉 1000 条,前端搜索过滤(`Input.Search` 改 `accountSearch`)。
2. **加载渠道与卡片与代理设置**:三个请求并发(`frontend/src/pages/Payments.tsx:68`):`listPaymentChannels()` + `listPaymentCards('direct')` + `getPaymentSettings()`。
3. **选操作**:`operation: 'link' | 'pay'`(`frontend/src/api/payments.ts:3`)——生成支付链接 或 直接支付。
4. **填渠道选项**:`PaymentOptionsForm` 按后端下发的 `PaymentChannel.fields` **动态渲染**,不是硬编码表单。
5. **创建任务**:`createPaymentTask({ account_ids, operation, channel, options, concurrency: 1, delay_seconds: 0 })`(`frontend/src/pages/Payments.tsx:110`)—— 注意并发固定为 1、延迟固定为 0。
6. **看日志与结果**:选中任务后复用注册任务那套 `TaskLogPanel`(传 `kind="payment"`),下面接 `PaymentResultsTable`(`frontend/src/pages/Payments.tsx:215`)。
7. **重跑**:从任务 `meta` 里取回原始参数再 `createPaymentTask`(`frontend/src/pages/Payments.tsx:136`)。

任务列表来自 `apiFetch('/tasks')`(`frontend/src/pages/Payments.tsx:85`)—— 与注册任务共用同一套任务基础设施,靠 `meta.operation` 区分。

#### 6.3 三个子组件

| 组件 | 职责 |
| --- | --- |
| `PaymentOptionsForm` | 按 `PaymentField` 描述符动态渲染控件:`boolean` → Switch、`number` → InputNumber(`min=1`)、枚举 → Select、其余 → Input。`_proxy` 结尾的字段自动带"留空自动选择"占位符(`frontend/src/components/payments/PaymentOptionsForm.tsx:51`) |
| `PaymentResultsTable` | 四列:账号(省略号截断,220px)、渠道(Tag)、状态(`ok` 布尔渲染)、详情(220px) |
| `PaymentOperationDrawer` | 单账号操作抽屉,自持表单初值 `initialValues(channel, operation)`,带 `useRef` 清理副作用(`:72`) |

`PaymentOptionsForm` 的字段描述符驱动是这一块的核心设计:**加一个渠道参数只需后端改 `fields`,前端零改动**。与 `get_platform_actions` 驱动账号操作菜单是同一思路。

#### 6.4 卡片库

表格列(`frontend/src/pages/Payments.tsx:222`):品牌、卡片(**只显示 `•••• {last4}`**)、名称、使用次数(`uses/max_uses`)、备注、删除。

两个动作:`resetPaymentCardUses()` 重置使用次数、`addPaymentCard` 添加。**前端从不接收也不展示完整卡号**——列表只有 `last4` 与 `brand`,这是正确的处理。

代理配置按操作类型分开存:`proxyKey(operation)`(`frontend/src/pages/Payments.tsx:26`)映射到 `linkProxy` / `payProxy` 两个独立设置,`savePaymentProxy(kind, proxy)` 保存。

---

### 七、状态管理与前端技术债

#### 7.1 没有全局状态库

仓库里**没有** Redux / Zustand / MobX / Jotai,也没有自建的全局 Context store。数据流是:

```text
每个页面组件 useState 持有自己的数据
      ↓
useEffect / useCallback 里 apiFetch 拉取
      ↓
动作完成后重新调用 load() 全量刷新
```

跨页面共享的只有三类:

| 共享物 | 机制 |
| --- | --- |
| 鉴权 token | `localStorage` (key `auth_token`),由 `lib/utils.ts:apiFetch` 统一读取注入 |
| 三个 ChatGPT 注册开关 | `localStorage`,由 `hooks/usePersistent*.ts` 三个 hook 封装 |
| 平台列表 | 每个需要的页面各自调 `/api/platforms`(无缓存) |

这套做法对这个规模的后台是可行的,代价是下面几条。

#### 7.2 具体技术债

| 问题 | 事实 | 影响 |
| --- | --- | --- |
| **超大单文件** | `Accounts.tsx` 1919 行、`Settings.tsx` 1653 行、`ICloud.tsx` 1007 行、`MailImportPanel.tsx` 780 行、`RegisterTaskPage.tsx` 738 行 | 单文件内多个未导出的子组件(如 `ICloud.tsx` 里就有 7 个,含 `AliasPoolImportModal`),无法单独测试或复用;合并冲突概率高 |
| **`any` 逃逸** | `Accounts.tsx` 28 处、`Settings.tsx` 14 处、`Proxies.tsx` 7 处、`Dashboard.tsx` 3、`Login.tsx` 2、`RunningTasks.tsx` 1、`RegisterTaskPage.tsx` 1、`App.tsx` 1(共 57 处) | 后端返回结构变更时 TS 完全无法报警。集中在账号页与设置页——恰好是后端字段最多、`extra_json` 最灵活的两处,是最需要类型保护的地方 |
| **无 ErrorBoundary** | 全仓零个 | 任一渲染期异常会白屏整个 SPA,而不是降级到某个区块 |
| **写后全量刷新** | `withBusy` 模式在每次单行动作后重拉整个列表 | 别名/账号列表大时,一次删除要付两次全表查询的代价;没有乐观更新 |
| **前端硬编码默认值** | `frontend/src/pages/Settings.tsx:1394-1417` 给 10 个配置字段填默认值 | 同一默认值前后端各存一份,易不一致 |
| **类型转换分散** | `configValueParsers.ts` 只有 1 个函数(10 行),其余转换散落在 `Settings.tsx` 内部 | 加新配置类型时要记得在加载侧和保存侧各改一处,漏一处就是脏值 |
| **一次拉 1000 条** | `frontend/src/pages/Payments.tsx:63` 用 `page_size=1000` 拉全部 ChatGPT 账号做前端搜索 | 账号量上万后会明显卡顿,应改后端搜索 |
| **收件箱全量拉取** | `AliasInboxDrawer` 一次拉完所有邮件再前端过滤 | 与后端的渐进式加载理念不一致,邮件多时首屏慢 |

#### 7.3 值得保留的设计

同样应该记下来的是这一版前端做对的地方:

1. **声明式配置表**(`frontend/src/pages/Settings.tsx:93` 的 `TAB_ITEMS`)—— 加配置项只改一行数据。
2. **后端驱动的动态 UI** —— 账号操作菜单由 `get_platform_actions` 下发、付费选项由 `PaymentChannel.fields` 下发、导出格式由后端 `EXPORT_FORMATS` 下发。三处都做到了"后端加能力,前端零改动"。
3. **统一请求封装** —— `lib/utils.ts:apiFetch` 集中处理 token 注入、401 跳转、错误抛出,全站只有 5 处正当的裸 `fetch`。
4. **纯函数抽离** —— `lib/` 下 11 个模块把格式化、归一化、校验逻辑从组件里摘了出来,这部分是可测试的。
5. **踩坑注释** —— 多处注释记录了真实排错结论(`Space` 破坏 ellipsis、`mail_import_source` 必须单独落库、切筛选要清选中行),对接手的人价值很高。
