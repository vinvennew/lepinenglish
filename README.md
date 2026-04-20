# 🧠 乐拼英语 (Lepi English)

帮助初中生通过**听音 → 拼写 → AI 助记**三步法高效记忆英语单词的 Web 应用。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env          # 编辑 .env 填入密钥
python server.py              # 启动后访问 http://127.0.0.1:5000
```

## 项目结构

```
remember-me/
├── server.py                 # Flask 后端（API + 静态文件）
├── index.html                # 单页前端（Tailwind + 原生 JS）
├── manage.py                 # 命令行管理工具
├── requirements.txt          # Python 依赖
├── .env.example              # 环境变量模板
├── .env                      # 🔒 实际密钥（不入仓库）
├── .gitignore
├── wordbooks/                # 词库 JSON 文件
│   └── guangzhou_zhongkao_688.json
├── data/                     # 🔒 运行时数据（不入仓库）
│   └── app.db                # SQLite 数据库
└── *.png                     # Logo 图片
```

## 数据库结构与关系

```
┌──────────────┐       ┌───────────────────────┐       ┌──────────────────┐
│    users     │  1:N  │      user_words        │       │ mnemonic_cache   │
├──────────────┤◄──────┤───────────────────────│       ├──────────────────┤
│ id (PK)      │       │ user_id (PK,FK)       │       │ english (PK)     │
│ username     │       │ word_id (PK)          │       │ chinese          │
│ password_hash│       │ english               │       │ mnemonic         │
│ wordbook_name│       │ phonetic              │       │ model            │
│ created_at   │       │ chinese               │       │ created_at       │
└──────────────┘       │ chunks_json           │       └──────────────────┘
                       │ phrase                │        全局共享，所有用户
                       │ status                │        共用同一份缓存
                       │ attempts              │
                       └───────────────────────┘
```


### 关键说明

| 表 | 作用 | 数据归属 |
|---|---|---|
| `users` | 存储用户账号和当前选择的词库 | 每人一行 |
| `user_words` | 每个用户的每个单词的学习进度 | **每人独立**，互不影响 |
| `mnemonic_cache` | AI 生成的助记内容缓存 | **全局共享**，所有用户共用 |

- **用户 A** 和 **用户 B** 学同一个词 `apple`，各自有独立的 `status`（掌握/未掌握）和 `attempts`（尝试次数）
- 但他们点"✨ AI 助记"时，共享同一条缓存——只有第一个人需要调 API，后面所有人秒回
- 图片搜索（Pixabay/Wikipedia）不缓存，每次实时搜索

### status 字段含义

| 值 | 含义 | 触发条件 |
|---|---|---|
| `NULL` | 未学习 | 初始状态 |
| `proficient` | 已掌握 | 拼写正确 |
| `not_proficient` | 不熟练 | 拼写错误 |
| `forgotten` | 已遗忘 | 之前掌握，后来又拼错 |

## 用户管理（manage.py）

所有管理操作通过命令行工具 `manage.py` 完成，**不需要启动 Web 服务器**。

### 查看所有用户

```bash
python manage.py users
```

输出示例：
```
  ID  用户名          词库                                 总词数  已掌握  不熟练  遗忘  未学  注册时间
────────────────────────────────────────────────────────────────────────────────────────────────────
   1  test@test.com  guangzhou_zhongkao_688.json          688     19      3     1   665  2026-04-20
   2  xiaoming       guangzhou_zhongkao_688.json          688      0      0     0   688  2026-04-20
```

### 查看单个用户详情

```bash
python manage.py user test@test.com
```

### 清零用户进度

```bash
# 仅清零进度（保留当前词库）
python manage.py reset test@test.com

# 清零进度并切换到另一个词库
python manage.py reset test@test.com --wordbook guangzhou_zhongkao_688.json
```

> ⚠️ 清零后不可恢复！该用户的所有 status 和 attempts 归零。

### 删除用户

```bash
python manage.py delete test@test.com
```

> ⚠️ 会同时删除该用户的所有学习记录。

### AI 助记缓存管理

```bash
# 查看缓存统计（已缓存多少词、覆盖率）
python manage.py cache

# 清空全部缓存（下次调 AI 助记时会重新生成）
python manage.py cache-clear
```

### 数据库概览

```bash
python manage.py db
```

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `SECRET_KEY` | ✅ | Flask Session 签名密钥 |
| `QWEN_API_KEY` | ✅ | 通义千问 API Key（AI 助记） |
| `QWEN_MODEL` | | 模型名，默认 `qwen-turbo` |
| `PIXABAY_KEY` | | Pixabay 图片搜索 Key（免费） |
| `HOST` | | 监听地址，默认 `127.0.0.1` |
| `PORT` | | 监听端口，默认 `5000` |

## 技术栈

- **后端**: Python 3 + Flask + SQLite3
- **前端**: HTML5 + Tailwind CSS + 原生 JavaScript
- **AI**: 阿里通义千问（DashScope OpenAI 兼容接口）
- **图片**: Pixabay API（插画优先）+ Wikipedia 兜底
- **语音**: 浏览器 Web Speech API