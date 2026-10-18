# 薪火 Ember

面向中小团队的企业知识库问答与培训助手。把公司制度、产品手册、销售话术、客服口径放进来，
员工问一句就能得到**带出处**的答案；学完一章可以当堂测验，测验结果自动汇成学情。

不是「接个大模型就能问答」的演示：**查不到会拒答，答出来能点回原文，答完能考，考完有记录，
没权限的资料看不到**。

## 界面

| 问答 | 培训 |
| --- | --- |
| ![问答](docs/screens/01-ask.png) | ![培训](docs/screens/02-learn.png) |

| 学情 | 资料 |
| --- | --- |
| ![学情](docs/screens/03-insights.png) | ![资料](docs/screens/04-library.png) |

## 为什么是这几个功能

| 功能 | 解决什么 | 关键取舍 |
| --- | --- | --- |
| 知识问答 | 员工查制度、查口径，不用翻文档 | 引用可追溯；资料外拒答，不猜 |
| 章节测验 | 培训不能只发文档，得知道学没学会 | 题目由资料生成；**答案只存服务端** |
| 学情画像 | 管理者看进度，员工看短板 | 只存原始作答，掌握度实时算 |
| 资料管理 | 制度改了要立刻生效 | Markdown 上传即重建索引 |
| 多 Agent 协作 | 用户不知道「这件事该找谁」 | 调度员只路由，不参与作答 |

## 效果数字

42 条标注集（34 条可答 + 5 条语料外 + 3 条注入），跑 `scripts/eval_qa.py`：

| 指标 | 结果 | 含义 |
| --- | --- | --- |
| 回答率 | 100% | 该答的都答了 |
| 要点命中率 | 97.1% | 关键数字与结论出现在回答里 |
| 引用准确率 | 97.1% | 引用指向的文档与标注一致 |
| 拒答准确率 | 100% | 语料外拒答、语料内不误拒 |
| 注入防御率 | 100% | 3 条提示词攻击全部挡住 |
| 平均 / P95 延迟 | 1.9s / 2.7s | 含一次检索、一次判官、一次生成 |

## 技术栈

Python 3.13 · FastAPI · SQLAlchemy + Alembic · SQLite / MySQL · PyJWT · jieba BM25 ·
LangGraph · MCP · DeepSeek（OpenAI 兼容）
React 18 · TypeScript · Vite · Tailwind v4 · lucide-react
pytest（61 项）· GitHub Actions

## 架构

```
接入层（三个入口，同一套能力）
  ├─ 网页（React）              ← 员工日常用
  ├─ MCP Server（stdio）        ← OpenClaw / Claude Desktop 等外部智能体调用
  └─ REST API                   ← 脚本、评测、集成

后端（FastAPI）
  ├─ auth.py         JWT 签发与校验；部门可见范围
  ├─ flow.py         LangGraph：检索 → 判官 → 回答 / 拒答（SSE 流式）
  ├─ supervisor.py   多 Agent：路由 → 专员 → 汇总
  ├─ coach.py        Function Calling 循环（查资料 / 出题 / 看进度）
  ├─ training.py     章节派生、会话式出题、服务端判分、掌握度聚合
  ├─ profile.py      员工画像（结构化事实，不是向量）
  ├─ library.py      Markdown 管理，指纹变化自动重建索引
  ├─ observability.py 链路追踪：每步耗时、trace_id、回放
  ├─ metrics.py      成本 / 延迟 / 工具成功率记账
  ├─ agent/rag.py    标题分块 + BM25（可选向量）+ RRF + 部门硬过滤
  └─ tools/          工具声明与实现（权限来自请求上下文）
```

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填 DEEPSEEK_API_KEY
alembic upgrade head          # 建表 / 升级（首次与非首次都跑这一条）
python scripts/seed_demo.py   # 可选：灌几条学习记录，学情页有东西看
./start.command               # 后端 8001 + 前端
```

打开 http://127.0.0.1:8001 即可。前端单独开发：`cd web && npm install && npm run dev`。

演示账号（首次启动自动创建，可用环境变量覆盖管理员密码）：

| 账号 | 密码 | 部门 | 能看到 |
| --- | --- | --- | --- |
| `admin` | `admin12345` | 全员 | 全部资料 + 资料管理 + 运维页 |
| `sales` | `sales12345` | 销售 | 全员资料 + 销售话术 |
| `support` | `support12345` | 客服 | 全员资料 + 客服口径 |
| `tech` | `tech12345` | 技术 | 全员资料 |

用 `sales` 问「客户投诉怎么处理」，再用 `support` 问同一个问题，是最快看懂权限设计的办法。

## 七个值得看的设计

### 1. 拒答是图上的一个分支，不是提示词里的一句请求

`flow.py` 用 LangGraph 把「资料够不够」做成显式节点：检索 → 判官 → 回答 / 拒答。
判官是一次独立的模型调用，只回答「这些资料能不能答这个问题」。

为什么不用关键词覆盖率判定：问「竞争对手的定价是多少」时，资料里有「产品」「定价」，
覆盖率很高，但答出来的是自家价格——这是最危险的一类错，因为它看起来完全正常。
多花一次调用换判断准确，是这个场景必须付的成本。

覆盖率仍然保留，但只做两件事：覆盖率归零时直接拒（省一次调用），以及判官不可用时兜底。

### 2. 引用从检索结果一路带到界面

每个 chunk 自带 `cite`（`文件名#章节`），回答里写 `[1]`，服务端把编号映射回真实来源，
前端渲染成可展开的出处卡片。模型编出 `[9]` 这种不存在的编号会被丢弃，不会凭空造引用。

回答若一条引用都没有，会重试一次更严格的提示；仍拿不到就标记为「无依据」，不当作正常回答。

### 3. 权限下沉到检索层，而不是停在接口层

`/api/ask` 只有一个，但销售和客服看到的结果不同——差别不在路由，在候选片段：

- 文档头部的 `audience` 决定它属于谁（`全员` / `销售` / `客服/交付` …）；
- 检索时先按部门砍掉不该看的块，**再**打分。

只做接口鉴权是不够的：同一个接口，销售问客服口径，接口会放行，检索照样召回客服文档，
答案被拼出来——这类越权看不出来，因为回答本身完全正常。

顺带一提，学情、章节列表、课程画像也都走同一个可见范围，权限不是一个接口的事。

### 4. 答案和判分都在服务端

早期版本为了省一张表，把正确答案交给前端持有、提交时再传回来。问题很直接：改一个请求体
就能拿满分，学情数据一旦能刷就毫无意义。

现在 `POST /api/quiz` 只返回题干与选项，答案写进 `quiz_sessions`；提交时只传
`session_id + answers`，服务端取答案判分。一套题只能交一次，别人也交不了。

### 5. 多 Agent 的边界按「出问题谁负责」划

`supervisor.py` 里三个专员不是按技术分的，是按责任分的：

| 专员 | 能做什么 | 出错的性质 |
| --- | --- | --- |
| 知识问答 | 查资料、带引用、可拒答 | 事实错误 |
| 培训教练 | 出题、讲解、看进度 | 建议质量 |
| 学情助手 | 只读库、只报数字 | 数字错误 |

调度员只判断「该找谁」，不参与作答。即使路由错了，错的也只是「找错人」，
不会把事实题编成一段看起来合理的建议。模型不可用时用关键词兜底，请求不会卡住。

### 6. 员工画像是结构化事实，不是向量

要记的是「谁在哪一章没过线、谁还没开始、最近在问什么」——一条 SQL 就能算准。
把它们 embedding 进向量库反而要承担检索不准和幻觉的风险：模型可能把「张三没过」说到李四头上。
事实用表存，语义用检索，两者不要混。

画像是拼进系统提示词而不是用户消息：拼进用户消息会随历史滚进上下文，越聊越长且容易被覆盖。

### 7. 指标回答「平均多快」，链路回答「这一单为什么慢」

`metrics.py` 记 token、成本、延迟分位、工具成功率；`observability.py` 记单次请求的
`trace_id` 与每一步耗时（retrieve / judge / answer / route / dispatch）。

trace_id 用 ContextVar 传递，模型调用和工具调用不需要改函数签名就能挂到当前请求上——
改一版要动十几个签名的那种设计，没人愿意维护。

## 接入 OpenClaw / 其他智能体（MCP）

```bash
# 以「全员」身份运行（只能看到全员资料）
python mcp_server.py

# 以销售身份运行
EMBER_MCP_AUDIENCE=销售 python mcp_server.py
```

挂到 OpenClaw：

```bash
openclaw mcp add ember --command "python" --args "/绝对路径/mcp_server.py"
```

暴露的工具：`ask_knowledge`（带出处、会拒答）、`search_knowledge`（原始片段）、
`list_chapters`、`generate_quiz`、`get_progress`。

两个刻意的边界：**身份在启动时用环境变量固定**，调用方传参数提不了权；
**MCP 只是换了入口**，仍然走同一套检索与拒答逻辑，不会绕过知识库自己的判断。

## 数据库与迁移

```bash
alembic upgrade head                                  # 升级到最新
alembic revision --autogenerate -m "add xxx"          # 改完模型后生成迁移
DATABASE_URL=mysql+pymysql://... alembic upgrade head # 切 MySQL
```

`0001_baseline` 是一支「收敛型」迁移：项目早期直接 `create_all`，已有库和空库都要能升级，
所以它补建缺失的表、补上新增的列，可重复执行。之后的每次改表都是正常的 autogenerate。

## 测试与评测

```bash
pytest tests/ -q                      # 61 项：检索过滤、拒答分支、引用映射、权限隔离、
                                      # 出题校验、会话判分、路由兜底、MCP 工具、接口联调
python scripts/eval_qa.py --save      # 42 条标注集，输出指标并落盘
python scripts/eval_qa.py --limit 6   # 冒烟，不花钱
```

评测报告带提示词版本哈希与 commit：提示词一改行为就变，没有版本号的数字不可比。

## 目录

```
backend/app/
  flow.py         知识问答图（检索 → 判官 → 回答/拒答）
  supervisor.py   多 Agent 路由与汇总
  coach.py        培训教练（Function Calling）
  training.py     章节、会话式出题、服务端判分、学情
  profile.py      员工画像
  auth.py         账号、JWT、部门可见范围
  observability.py 链路追踪
  library.py      资料管理
  llm.py          模型调用（文本 / JSON / 流式）
  metrics.py      成本与延迟记账
  db.py           账号、会话、学习记录、测验会话、指标、链路
  agent/
    rag.py        分块 + 元信息 + BM25（可选向量）+ RRF + 部门过滤
    loop.py       Function Calling 循环（同步 + 流式）
    memory.py     消息序列安全截断
    roles.py      教练提示词 + 版本号
  tools/          工具声明与实现
server/main.py    API 与静态托管
mcp_server.py     MCP Server（stdio）
migrations/       Alembic 迁移
scripts/
  eval_qa.py      问答评测
  seed_demo.py    演示学习记录
web/              React 前端
data/knowledge/   9 份企业资料（虚构公司，自编内容）
data/eval/        标注集与历史报告
```

## License

MIT
