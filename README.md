# 薪火 Ember

面向中小团队的企业知识库问答与培训助手。把公司制度、产品手册、销售话术、客服口径放进来，
员工问一句就能得到**带出处**的答案；学完一章可以当堂测验，测验结果自动汇成学情。

不是「接个大模型就能问答」的演示：**查不到会拒答，答出来能点回原文，答完能考，考完有记录。**

## 界面

| 问答 | 培训 |
| --- | --- |
| ![问答](docs/screens/01-ask.png) | ![培训](docs/screens/02-learn.png) |

| 学情 | 资料 |
| --- | --- |
| ![学情](docs/screens/03-insights.png) | ![资料](docs/screens/04-library.png) |

## 为什么是这四个功能

| 功能 | 解决什么 | 关键取舍 |
| --- | --- | --- |
| 知识问答 | 员工查制度、查口径，不用翻文档 | 引用可追溯；资料外拒答，不猜 |
| 章节测验 | 培训不能只发文档，得知道学没学会 | 题目由资料生成，答案不下发前端 |
| 学情画像 | 管理者看进度，员工看短板 | 只存原始作答，掌握度实时算 |
| 资料管理 | 制度改了要立刻生效 | Markdown 上传即重建索引 |

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

Python 3.13 · FastAPI · SQLAlchemy/SQLite · jieba BM25 · LangGraph · DeepSeek（OpenAI 兼容）
React 18 · TypeScript · Vite · Tailwind v4 · lucide-react
pytest（27 项）· GitHub Actions

## 架构

```
前端（React）
  ├─ 问答   /api/ask       SSE 流式，token → 引用 → 拒答判定
  ├─ 培训   /api/quiz      出题（结构化校验）/ 判分 / 学情
  ├─ 学情   /api/mastery   章节掌握度、薄弱项
  └─ 资料   /api/library   上传 / 删除 / 重建索引
                    │
后端（FastAPI）
  ├─ flow.py      LangGraph：检索 → 判官 → 回答 / 拒答
  ├─ coach.py     Function Calling 循环（查资料 / 出题 / 看进度）
  ├─ training.py  章节派生、出题校验、判分、掌握度聚合
  ├─ library.py   Markdown 管理，指纹变化自动重建索引
  ├─ agent/rag.py 标题分块 + 元信息 + BM25 + RRF
  └─ metrics.py   成本 / 延迟 / 工具成功率记账
```

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填 DEEPSEEK_API_KEY
python scripts/seed_demo.py   # 可选：灌几条学习记录，学情页有东西看
./start.command               # 后端 8001 + 前端
```

打开 http://127.0.0.1:8001 即可。前端单独开发：`cd web && npm install && npm run dev`。

## 三个值得看的设计

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

### 3. 章节是文档派生出来的，不是单独维护的课程表

`training.py` 直接从知识库文档生成章节与小节。加一份 Markdown 就多一章，
「资料管理」和「培训课程」是同一份数据的两种看法——避免了两处维护、两份数据打架。

出题走结构化校验：模型返回的题必须字段齐全、答案下标合法、选项不重复，
不合格的题单条丢弃；模型不可用时退化为原文挖空题，保证培训流程不中断。

## 测试与评测

```bash
pytest tests/ -q                      # 27 项：检索过滤、拒答分支、引用映射、出题校验、判分、上传校验
python scripts/eval_qa.py --save      # 42 条标注集，输出指标并落盘
python scripts/eval_qa.py --limit 6   # 冒烟，不花钱
```

评测报告带提示词版本哈希与 commit：提示词一改行为就变，没有版本号的数字不可比。

## 目录

```
backend/app/
  flow.py         知识问答图（检索 → 判官 → 回答/拒答）
  coach.py        培训教练（Function Calling）
  training.py     章节、出题、判分、学情
  library.py      资料管理
  llm.py          模型调用（文本 / JSON / 流式）
  metrics.py      成本与延迟记账
  db.py           会话、学习记录、调用指标
  agent/
    rag.py        分块 + 元信息 + BM25 + RRF
    loop.py       Function Calling 循环（同步 + 流式）
    memory.py     消息序列安全截断
    roles.py      教练提示词 + 版本号
  tools/          工具声明与实现
server/main.py    API 与静态托管
scripts/
  eval_qa.py      问答评测
  seed_demo.py    演示学习记录
web/              React 前端
data/knowledge/   9 份企业资料（虚构公司，自编内容）
data/eval/        标注集与历史报告
```

## License

MIT
