"""Lepi English 后端 - Flask + SQLite
运行：
    pip install -r requirements.txt
    cp .env.example .env   (然后填 SECRET_KEY 和 QWEN_* 密钥)
    python server.py
默认监听 http://127.0.0.1:5000
"""
import json
import logging
import os
import sqlite3
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

# 开启 Flask / werkzeug 请求日志
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "app.db"
WORDBOOK_DIR = BASE_DIR / "wordbooks"
WORDBOOK_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv("SECRET_KEY", "dev-insecure-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # 30 天
)


# ================= 数据库 =================
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                wordbook_name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS user_words (
                user_id INTEGER NOT NULL,
                word_id INTEGER NOT NULL,
                english TEXT NOT NULL,
                phonetic TEXT,
                chinese TEXT,
                chunks_json TEXT,
                phrase TEXT,
                status TEXT,
                attempts INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, word_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )


def load_wordbook_file(name: str):
    """读取 wordbooks/ 下的某个 json 文件；仅允许裸文件名防止路径穿越"""
    safe = os.path.basename(name)
    path = WORDBOOK_DIR / safe
    if not path.is_file():
        raise FileNotFoundError(f"wordbook not found: {safe}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("wordbook json must be a list")
    return data


def list_wordbook_files():
    return sorted(p.name for p in WORDBOOK_DIR.glob("*.json"))


def seed_user_words(user_id: int, wordbook_name: str):
    """把词库文件内容拷贝为某用户的 user_words 初始数据"""
    items = load_wordbook_file(wordbook_name)
    db = get_db()
    db.execute("DELETE FROM user_words WHERE user_id = ?", (user_id,))
    rows = [
        (
            user_id,
            int(w.get("id") or i + 1),
            w.get("english", ""),
            w.get("phonetic", ""),
            w.get("chinese", ""),
            json.dumps(w.get("chunks") or [w.get("english", "")], ensure_ascii=False),
            w.get("phrase", ""),
            None,
            0,
        )
        for i, w in enumerate(items)
    ]
    db.executemany(
        """
        INSERT OR REPLACE INTO user_words
        (user_id, word_id, english, phonetic, chinese, chunks_json, phrase, status, attempts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    db.commit()


# ================= 认证装饰器 =================
def require_login(func):
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify(error="未登录"), 401
        return func(*args, **kwargs)

    return wrapper



# ================= 认证 API =================
@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    wordbook = (data.get("wordbook_name") or "").strip()
    if len(username) < 2 or len(password) < 4:
        return jsonify(error="用户名至少 2 位，密码至少 4 位"), 400
    books = list_wordbook_files()
    if not books:
        return jsonify(error="服务器没有可用词库，请管理员放入 wordbooks/ 目录"), 500
    if wordbook not in books:
        wordbook = books[0]
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (username, password_hash, wordbook_name) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), wordbook),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="用户名已存在"), 409
    user_id = cur.lastrowid
    seed_user_words(user_id, wordbook)
    session["user_id"] = user_id
    session.permanent = True
    return jsonify(id=user_id, username=username, wordbook_name=wordbook)


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    row = get_db().execute(
        "SELECT id, password_hash, wordbook_name FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify(error="用户名或密码错误"), 401
    session["user_id"] = row["id"]
    session.permanent = True
    return jsonify(id=row["id"], username=username, wordbook_name=row["wordbook_name"])


@app.post("/api/logout")
def logout():
    session.pop("user_id", None)
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    uid = session.get("user_id")
    if not uid:
        return jsonify(error="未登录"), 401
    row = get_db().execute(
        "SELECT id, username, wordbook_name FROM users WHERE id = ?", (uid,)
    ).fetchone()
    if not row:
        session.pop("user_id", None)
        return jsonify(error="用户不存在"), 401
    return jsonify(dict(row))


# ================= 词库 / 进度 API =================
@app.get("/api/wordbooks")
def api_wordbooks():
    return jsonify(list_wordbook_files())


@app.get("/api/words")
@require_login
def get_words():
    uid = session["user_id"]
    rows = get_db().execute(
        """
        SELECT word_id AS id, english, phonetic, chinese, chunks_json, phrase, status, attempts
        FROM user_words WHERE user_id = ? ORDER BY word_id
        """,
        (uid,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["chunks"] = json.loads(d.pop("chunks_json") or "[]")
        except json.JSONDecodeError:
            d["chunks"] = [d["english"]]
        out.append(d)
    return jsonify(out)


@app.patch("/api/words/<int:word_id>")
@require_login
def patch_word(word_id):
    data = request.get_json(silent=True) or {}
    fields, values = [], []
    if "status" in data:
        if data["status"] not in (None, "proficient", "not_proficient", "forgotten"):
            return jsonify(error="status 非法"), 400
        fields.append("status = ?")
        values.append(data["status"])
    if "attempts" in data:
        fields.append("attempts = ?")
        values.append(int(data["attempts"]))
    if not fields:
        return jsonify(error="没有可更新字段"), 400
    values += [session["user_id"], word_id]
    db = get_db()
    cur = db.execute(
        f"UPDATE user_words SET {', '.join(fields)} WHERE user_id = ? AND word_id = ?",
        values,
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify(error="单词不存在"), 404
    return jsonify(ok=True)


@app.post("/api/switch-wordbook")
@require_login
def switch_wordbook():
    data = request.get_json(silent=True) or {}
    name = (data.get("wordbook_name") or "").strip()
    if name not in list_wordbook_files():
        return jsonify(error="词库不存在"), 400
    db = get_db()
    db.execute("UPDATE users SET wordbook_name = ? WHERE id = ?", (name, session["user_id"]))
    db.commit()
    seed_user_words(session["user_id"], name)
    return jsonify(ok=True, wordbook_name=name)



# ================= 通义千问 助记文本生成 =================
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv(
    "QWEN_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")


@app.post("/api/mnemonic")
@require_login
def api_mnemonic():
    if not QWEN_API_KEY:
        return jsonify(error="服务器未配置 QWEN_API_KEY"), 500
    data = request.get_json(silent=True) or {}
    english = (data.get("english") or "").strip()
    chinese = (data.get("chinese") or "").strip()
    if not english:
        return jsonify(error="缺少 english"), 400

    system_prompt = (
        "你是一名有趣的英语老师，擅长用生动易记的方式帮助初中生记单词。"
        "回答请控制在 120 字以内，使用中文，结构如下：\n"
        "1. 一句形象化的联想/画面/小故事（可用谐音、词根、场景联想）\n"
        "2. 一个简单例句（英文 + 中文翻译）\n"
        "不要使用 Markdown 标题，直接输出正文。"
    )
    user_prompt = f"单词：{english}\n中文含义：{chinese or '（未提供）'}\n请给出记忆法和例句。"

    body = {
        "model": QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 400,
    }
    headers = {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json",
    }
    url = QWEN_BASE_URL.rstrip("/") + "/chat/completions"
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=60)
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=f"调用千问接口失败: {exc}"), 502

    try:
        payload = resp.json()
    except ValueError:
        return jsonify(error=f"千问返回非 JSON: HTTP {resp.status_code}", raw=resp.text[:500]), 502

    if resp.status_code != 200:
        return jsonify(error=f"千问返回错误: HTTP {resp.status_code}", raw=payload), 502

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return jsonify(error="千问返回格式异常", raw=payload), 502
    return jsonify(text=text.strip(), model=QWEN_MODEL)


# ================= 图片搜索（Pixabay 主 / Wikipedia 兜底） =================
PIXABAY_KEY = os.getenv("PIXABAY_KEY", "")


def _pixabay_once(query: str, image_type: str):
    try:
        resp = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": PIXABAY_KEY,
                "q": query,
                "image_type": image_type,
                "safesearch": "true",
                "per_page": 3,
                "lang": "en",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            app.logger.warning("pixabay %s/%s -> HTTP %s: %s", query, image_type, resp.status_code, resp.text[:200])
            return None
        hits = (resp.json() or {}).get("hits") or []
        if not hits:
            return None
        top = hits[0]
        return {
            "url": top.get("webformatURL") or top.get("largeImageURL"),
            "page": top.get("pageURL"),
            "source": "pixabay",
            "title": top.get("tags", ""),
        }
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("pixabay %s/%s error: %s", query, image_type, exc)
        return None


def _search_pixabay(query: str):
    if not PIXABAY_KEY:
        return None
    # 优先卡通插画（更适合小孩），失败再退化到所有类型
    return _pixabay_once(query, "illustration") or _pixabay_once(query, "all")


def _wiki_summary_thumb(title: str):
    try:
        resp = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}",
            headers={"User-Agent": "LepiEnglish/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json() or {}
        thumb = (data.get("originalimage") or {}).get("source") or (data.get("thumbnail") or {}).get("source")
        if not thumb:
            return None
        return {
            "url": thumb,
            "page": (data.get("content_urls") or {}).get("desktop", {}).get("page"),
            "source": "wikipedia",
            "title": data.get("title") or title,
        }
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("wiki summary %s error: %s", title, exc)
        return None


def _search_wikipedia(query: str):
    # 先直接按 title 取，失败再走搜索找到最相关条目再取缩略图
    direct = _wiki_summary_thumb(query)
    if direct:
        return direct
    try:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 3,
                "format": "json",
            },
            headers={"User-Agent": "LepiEnglish/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        hits = ((resp.json() or {}).get("query") or {}).get("search") or []
        for hit in hits:
            t = hit.get("title")
            if not t:
                continue
            got = _wiki_summary_thumb(t)
            if got:
                return got
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("wiki search %s error: %s", query, exc)
    return None


@app.post("/api/word-image")
@require_login
def api_word_image():
    data = request.get_json(silent=True) or {}
    english = (data.get("english") or "").strip()
    if not english:
        return jsonify(error="缺少 english"), 400
    # 短语只取首个实词做搜索
    query = english.split()[0] if " " in english else english
    app.logger.info("word-image query=%r (pixabay_key=%s)", query, "yes" if PIXABAY_KEY else "no")
    result = _search_pixabay(query) or _search_wikipedia(query)
    if not result and query != english:
        result = _search_pixabay(english) or _search_wikipedia(english)
    if not result:
        app.logger.info("word-image %r -> no result", query)
        return jsonify(error="未找到相关图片"), 404
    app.logger.info("word-image %r -> %s", query, result.get("source"))
    return jsonify(**result)


# ================= 静态文件 =================
@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    # 不允许越权访问 data/、.env 等敏感路径
    if filename.startswith(("data/", ".env", "server.py")):
        return "forbidden", 403
    target = BASE_DIR / filename
    if not target.exists() or not target.is_file():
        return "not found", 404
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    init_db()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    print(f"[启动诊断] PIXABAY_KEY={'已配置' if PIXABAY_KEY else '未配置'}")
    print(f"[启动诊断] QWEN_API_KEY={'已配置' if QWEN_API_KEY else '未配置'}")
    app.run(host=host, port=port, debug=debug)
