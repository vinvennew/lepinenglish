"""乐拼英语 - 管理工具
用法：
    python manage.py users                    查看所有用户
    python manage.py user <用户名>             查看单个用户详情 + 学习进度
    python manage.py reset <用户名>            清零某用户的学习进度
    python manage.py reset <用户名> --wordbook <词库名>   清零并切换词库
    python manage.py delete <用户名>           删除某用户及其全部数据
    python manage.py cache                    查看 AI 助记缓存统计
    python manage.py cache-clear              清空全部 AI 助记缓存
    python manage.py db                       查看数据库概览
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "app.db"
WORDBOOK_DIR = BASE_DIR / "wordbooks"


def get_db():
    if not DB_PATH.exists():
        print(f"❌ 数据库不存在：{DB_PATH}")
        print("   请先运行 python server.py 启动一次服务来初始化数据库。")
        sys.exit(1)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# ==================== users ====================
def cmd_users():
    """列出所有用户"""
    db = get_db()
    rows = db.execute("""
        SELECT u.id, u.username, u.wordbook_name, u.created_at,
               COUNT(w.word_id) AS total_words,
               SUM(CASE WHEN w.status = 'proficient' THEN 1 ELSE 0 END) AS proficient,
               SUM(CASE WHEN w.status = 'not_proficient' THEN 1 ELSE 0 END) AS not_proficient,
               SUM(CASE WHEN w.status = 'forgotten' THEN 1 ELSE 0 END) AS forgotten,
               SUM(CASE WHEN w.status IS NULL THEN 1 ELSE 0 END) AS not_started
        FROM users u
        LEFT JOIN user_words w ON u.id = w.user_id
        GROUP BY u.id
        ORDER BY u.id
    """).fetchall()
    if not rows:
        print("📭 暂无注册用户。")
        return
    print(f"\n{'ID':>4}  {'用户名':<14} {'词库':<36} {'总词数':>6} {'已掌握':>6} {'不熟练':>6} {'遗忘':>5} {'未学':>5}  注册时间")
    print("─" * 120)
    for r in rows:
        print(f"{r['id']:>4}  {r['username']:<14} {r['wordbook_name']:<36} "
              f"{r['total_words']:>6} {r['proficient'] or 0:>6} {r['not_proficient'] or 0:>6} "
              f"{r['forgotten'] or 0:>5} {r['not_started'] or 0:>5}  {r['created_at'] or ''}")
    print(f"\n共 {len(rows)} 个用户\n")


# ==================== user <name> ====================
def cmd_user(username):
    """查看单个用户详情"""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not user:
        print(f"❌ 用户 '{username}' 不存在")
        return
    print(f"\n👤 用户详情")
    print(f"   ID:       {user['id']}")
    print(f"   用户名:   {user['username']}")
    print(f"   当前词库: {user['wordbook_name']}")
    print(f"   注册时间: {user['created_at']}")

    stats = db.execute("""
        SELECT status, COUNT(*) AS cnt, SUM(attempts) AS total_attempts
        FROM user_words WHERE user_id = ?
        GROUP BY status
    """, (user['id'],)).fetchall()
    print(f"\n📊 学习进度:")
    total = 0
    for s in stats:
        label = {'proficient': '✅ 已掌握', 'not_proficient': '⚠️ 不熟练',
                 'forgotten': '❌ 已遗忘', None: '⬜ 未学习'}.get(s['status'], s['status'])
        print(f"   {label}: {s['cnt']} 词 (共尝试 {s['total_attempts'] or 0} 次)")
        total += s['cnt']
    print(f"   📚 总计: {total} 词\n")


# ==================== reset <name> ====================
def cmd_reset(username, wordbook_name=None):
    """清零用户进度"""
    db = get_db()
    user = db.execute("SELECT id, wordbook_name FROM users WHERE username = ?", (username,)).fetchone()
    if not user:
        print(f"❌ 用户 '{username}' 不存在")
        return
    wb = wordbook_name or user['wordbook_name']
    wb_path = WORDBOOK_DIR / os.path.basename(wb)
    if not wb_path.is_file():
        print(f"❌ 词库 '{wb}' 不存在 (路径: {wb_path})")
        return
    # 更新词库名
    if wordbook_name:
        db.execute("UPDATE users SET wordbook_name = ? WHERE id = ?", (wb, user['id']))
    # 清空并重新播种
    with open(wb_path, "r", encoding="utf-8") as f:
        items = json.load(f)
    db.execute("DELETE FROM user_words WHERE user_id = ?", (user['id'],))
    rows = [(user['id'], int(w.get("id") or i+1), w.get("english",""), w.get("phonetic",""),
             w.get("chinese",""), json.dumps(w.get("chunks") or [w.get("english","")], ensure_ascii=False),
             w.get("phrase",""), None, 0) for i, w in enumerate(items)]
    db.executemany("INSERT INTO user_words (user_id,word_id,english,phonetic,chinese,chunks_json,phrase,status,attempts) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    action = f"清零进度并切换到词库 '{wb}'" if wordbook_name else f"清零进度 (词库: {wb})"
    print(f"✅ 用户 '{username}' {action}，共 {len(rows)} 个单词已重置。")


# ==================== delete <name> ====================
def cmd_delete(username):
    """删除用户"""
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not user:
        print(f"❌ 用户 '{username}' 不存在")
        return
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("DELETE FROM users WHERE id = ?", (user['id'],))
    db.commit()
    print(f"✅ 用户 '{username}' (ID={user['id']}) 及其所有学习记录已删除。")


# ==================== cache ====================
def cmd_cache():
    """查看 AI 助记缓存统计"""
    db = get_db()
    row = db.execute("SELECT COUNT(*) AS cnt FROM mnemonic_cache").fetchone()
    total = row['cnt'] if row else 0
    print(f"\n📦 AI 助记缓存统计")
    print(f"   已缓存单词数: {total}")
    if total > 0:
        sample = db.execute("SELECT english, substr(mnemonic,1,50) AS preview FROM mnemonic_cache ORDER BY created_at DESC LIMIT 5").fetchall()
        print(f"\n   最近缓存的 5 条:")
        for s in sample:
            print(f"     {s['english']:<16} {s['preview']}...")
    # 统计覆盖率（和每个词库的词比较）
    for wb_file in sorted(WORDBOOK_DIR.glob("*.json")):
        with open(wb_file, "r", encoding="utf-8") as f:
            items = json.load(f)
        wb_words = {w.get("english","").lower() for w in items}
        cached_words = {r['english'] for r in db.execute("SELECT english FROM mnemonic_cache").fetchall()}
        covered = len(wb_words & cached_words)
        print(f"\n   词库 '{wb_file.name}': {covered}/{len(wb_words)} 词已缓存 ({covered*100//max(len(wb_words),1)}%)")
    print()


def cmd_cache_clear():
    """清空全部 AI 助记缓存"""
    db = get_db()
    cnt = db.execute("SELECT COUNT(*) AS cnt FROM mnemonic_cache").fetchone()['cnt']
    db.execute("DELETE FROM mnemonic_cache")
    db.commit()
    print(f"✅ 已清空 {cnt} 条 AI 助记缓存。")


# ==================== db ====================
def cmd_db():
    """数据库概览"""
    db = get_db()
    print(f"\n🗄️  数据库概览")
    print(f"   路径: {DB_PATH}")
    print(f"   大小: {DB_PATH.stat().st_size / 1024:.1f} KB")
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(f"\n   表结构:")
    for t in tables:
        cnt = db.execute(f"SELECT COUNT(*) AS cnt FROM [{t['name']}]").fetchone()['cnt']
        cols = db.execute(f"PRAGMA table_info([{t['name']}])").fetchall()
        col_names = ", ".join(c['name'] for c in cols)
        print(f"     📋 {t['name']} ({cnt} 行)")
        print(f"        列: {col_names}")
    print()


# ==================== main ====================
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1].lower()
    if cmd == "users":
        cmd_users()
    elif cmd == "user" and len(sys.argv) >= 3:
        cmd_user(sys.argv[2])
    elif cmd == "reset" and len(sys.argv) >= 3:
        wb = None
        if "--wordbook" in sys.argv:
            idx = sys.argv.index("--wordbook")
            if idx + 1 < len(sys.argv):
                wb = sys.argv[idx + 1]
        cmd_reset(sys.argv[2], wb)
    elif cmd == "delete" and len(sys.argv) >= 3:
        cmd_delete(sys.argv[2])
    elif cmd == "cache":
        cmd_cache()
    elif cmd == "cache-clear":
        cmd_cache_clear()
    elif cmd == "db":
        cmd_db()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
