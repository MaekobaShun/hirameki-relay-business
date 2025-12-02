import os
import random
from datetime import datetime, timedelta

USE_SUPABASE = bool(
    os.environ.get('SUPABASE_DATABASE_URL')
    or os.environ.get('DATABASE_URL')
    or os.environ.get('SUPABASE_HOST')
)

if USE_SUPABASE:
    import psycopg  # type: ignore
else:
    import sqlite3  # type: ignore

_DEFAULT_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'database.db')
)
DATABASE = os.environ.get('DB_PATH', _DEFAULT_DB_PATH)

_SUPABASE_SETTINGS: dict[str, str | int] = {}

if USE_SUPABASE:
    conninfo = os.environ.get('SUPABASE_DATABASE_URL') or os.environ.get('DATABASE_URL')
    if conninfo:
        _SUPABASE_SETTINGS['conninfo'] = conninfo
    else:
        host = os.environ.get('SUPABASE_HOST')
        user = os.environ.get('SUPABASE_USER')
        password = os.environ.get('SUPABASE_PASSWORD')
        dbname = os.environ.get('SUPABASE_DB') or os.environ.get('SUPABASE_DATABASE')
        if host and user and password and dbname:
            _SUPABASE_SETTINGS = {
                'host': host,
                'port': int(os.environ.get('SUPABASE_PORT', 5432)),
                'user': user,
                'password': password,
                'dbname': dbname,
            }


def using_supabase() -> bool:
    return USE_SUPABASE and bool(_SUPABASE_SETTINGS)


def _prepare_query(query: str) -> str:
    if using_supabase():
        return query.replace('?', '%s')
    return query


class SupabaseCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=()):
        self._cursor.execute(_prepare_query(query), params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self):
        self._cursor.close()


class SupabaseConnection:
    def __init__(self):
        if not using_supabase():
            raise RuntimeError('Supabase connection settings are not configured.')
        self._conn = psycopg.connect(**_SUPABASE_SETTINGS)  # type: ignore[arg-type]
        self._conn.autocommit = False

    def execute(self, query, params=()):
        cursor = self._conn.cursor()
        return SupabaseCursor(cursor).execute(query, params)

    def cursor(self):
        return SupabaseCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False


def get_connection():
    if using_supabase():
        return SupabaseConnection()
    return sqlite3.connect(DATABASE)  # type: ignore[return-value]


def create_table():
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS mypage (
                user_id      VARCHAR(64) PRIMARY KEY,
                nickname     VARCHAR(32) NOT NULL,
                password     TEXT NOT NULL,
                email        VARCHAR(255) UNIQUE NOT NULL,
                icon_path    TEXT,
                created_at   TIMESTAMP NOT NULL
            )
        """)

        if using_supabase():
            con.execute("ALTER TABLE mypage ADD COLUMN IF NOT EXISTS icon_path TEXT")
            try:
                con.execute("ALTER TABLE mypage ALTER COLUMN password TYPE TEXT")
            except Exception:
                pass
            try:
                con.execute("ALTER TABLE mypage ALTER COLUMN email TYPE VARCHAR(255)")
            except Exception:
                pass
        else:
            try:
                con.execute("ALTER TABLE mypage ADD COLUMN icon_path TEXT")
            except Exception:
                pass

        con.execute("""
            CREATE TABLE IF NOT EXISTS ideas (
                idea_id      VARCHAR(64) PRIMARY KEY,
                title        VARCHAR(128) NOT NULL,
                detail       TEXT NOT NULL,
                category     VARCHAR(32) NOT NULL,
                user_id      VARCHAR(64) NOT NULL,
                created_at   TIMESTAMP NOT NULL
            )
        """)

        # Add inheritance_flag column to ideas table
        if using_supabase():
            con.execute("ALTER TABLE ideas ADD COLUMN IF NOT EXISTS inheritance_flag BOOLEAN DEFAULT FALSE")
        else:
            try:
                con.execute("ALTER TABLE ideas ADD COLUMN inheritance_flag INTEGER DEFAULT 0")
            except Exception:
                pass

        con.execute("""
            CREATE TABLE IF NOT EXISTS gacha_result (
                result_id    VARCHAR(64) PRIMARY KEY,
                user_id      VARCHAR(64) NOT NULL,
                idea_id      VARCHAR(64) NOT NULL,
                created_at   TIMESTAMP NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS revival_notify (
                notify_id    VARCHAR(64) PRIMARY KEY,
                idea_id      VARCHAR(64) NOT NULL,
                author_id    VARCHAR(64) NOT NULL,
                picker_id    VARCHAR(64) NOT NULL,
                created_at   TIMESTAMP NOT NULL
            )
        """)
        
        # Add read_at column for tracking read status (migration)
        if using_supabase():
            try:
                con.execute("ALTER TABLE revival_notify ADD COLUMN IF NOT EXISTS read_at TIMESTAMP")
            except Exception:
                pass
        else:
            try:
                con.execute("ALTER TABLE revival_notify ADD COLUMN read_at TIMESTAMP")
            except Exception:
                pass

        con.execute("""
            CREATE TABLE IF NOT EXISTS thanks (
                thanks_id       VARCHAR(64) PRIMARY KEY,
                gacha_type_id   VARCHAR(64) NOT NULL,
                sender_id       VARCHAR(64) NOT NULL,
                receiver_id     VARCHAR(64) NOT NULL,
                stamp_type      VARCHAR(32) NOT NULL,
                created_at      TIMESTAMP NOT NULL
            )
        """)

        # idea_inheritanceテーブルを作成（child_idea_idはNULLを許可）
        # 既存のテーブルがある場合は再作成（マイグレーション）
        try:
            # 既存のテーブルがあるか確認
            if using_supabase():
                # PostgreSQL用のクエリ
                existing_table = con.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'idea_inheritance'"
                ).fetchone()
            else:
                # SQLite用のクエリ
                existing_table = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='idea_inheritance'"
                ).fetchone()
            
            if existing_table and not using_supabase():
                # SQLiteの場合：既存のデータを一時テーブルにコピー
                try:
                    con.execute("""
                        CREATE TABLE idea_inheritance_temp (
                            inheritance_id  VARCHAR(64) PRIMARY KEY,
                            parent_idea_id  VARCHAR(64),
                            parent_user_id  VARCHAR(64),
                            child_idea_id   VARCHAR(64),
                            child_user_id   VARCHAR(64),
                            add_point       VARCHAR(64),
                            add_detail      TEXT,
                            created_at      TIMESTAMP NOT NULL
                        )
                    """)
                    # 既存のデータをコピー（child_idea_idがNULLの場合は空文字列として扱う）
                    con.execute("""
                        INSERT INTO idea_inheritance_temp 
                        SELECT 
                            inheritance_id,
                            parent_idea_id,
                            parent_user_id,
                            CASE WHEN child_idea_id IS NULL OR child_idea_id = '' THEN NULL ELSE child_idea_id END,
                            child_user_id,
                            add_point,
                            add_detail,
                            created_at
                        FROM idea_inheritance
                    """)
                    # 既存のテーブルを削除
                    con.execute("DROP TABLE idea_inheritance")
                    # 新しいテーブルを作成（child_idea_idはNULLを許可）
                    con.execute("""
                        CREATE TABLE idea_inheritance (
                            inheritance_id  VARCHAR(64) PRIMARY KEY,
                            parent_idea_id  VARCHAR(64),
                            parent_user_id  VARCHAR(64),
                            child_idea_id   VARCHAR(64),
                            child_user_id   VARCHAR(64),
                            add_point       VARCHAR(64),
                            add_detail      TEXT,
                            created_at      TIMESTAMP NOT NULL
                        )
                    """)
                    # データを復元
                    con.execute("""
                        INSERT INTO idea_inheritance 
                        SELECT * FROM idea_inheritance_temp
                    """)
                    con.execute("DROP TABLE idea_inheritance_temp")
                except Exception:
                    # エラーが発生した場合はテーブルを削除して再作成
                    try:
                        con.execute("DROP TABLE IF EXISTS idea_inheritance_temp")
                        con.execute("DROP TABLE idea_inheritance")
                    except Exception:
                        pass
                    con.execute("""
                        CREATE TABLE idea_inheritance (
                            inheritance_id  VARCHAR(64) PRIMARY KEY,
                            parent_idea_id  VARCHAR(64),
                            parent_user_id  VARCHAR(64),
                            child_idea_id   VARCHAR(64),
                            child_user_id   VARCHAR(64),
                            add_point       VARCHAR(64),
                            add_detail      TEXT,
                            created_at      TIMESTAMP NOT NULL
                        )
                    """)
            else:
                # 新規作成またはSupabaseの場合
                con.execute("""
                    CREATE TABLE IF NOT EXISTS idea_inheritance (
                        inheritance_id  VARCHAR(64) PRIMARY KEY,
                        parent_idea_id  VARCHAR(64),
                        parent_user_id  VARCHAR(64),
                        child_idea_id   VARCHAR(64),
                        child_user_id   VARCHAR(64),
                        add_point       VARCHAR(64),
                        add_detail      TEXT,
                        created_at      TIMESTAMP NOT NULL
                    )
                """)
        except Exception:
            # エラーが発生した場合は通常の作成を試みる
            con.execute("""
                CREATE TABLE IF NOT EXISTS idea_inheritance (
                    inheritance_id  VARCHAR(64) PRIMARY KEY,
                    parent_idea_id  VARCHAR(64),
                    parent_user_id  VARCHAR(64),
                    child_idea_id   VARCHAR(64),
                    child_user_id   VARCHAR(64),
                    add_point       VARCHAR(64),
                    add_detail      TEXT,
                    created_at      TIMESTAMP NOT NULL
                )
            """)

        # eventsテーブルを作成
        con.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id       VARCHAR(64) PRIMARY KEY,
                name           VARCHAR(128) NOT NULL,
                password_hash  TEXT NOT NULL,
                start_date     TIMESTAMP NOT NULL,
                end_date       TIMESTAMP NOT NULL,
                created_at     TIMESTAMP NOT NULL,
                created_by     VARCHAR(64) NOT NULL,
                status         VARCHAR(16) DEFAULT 'upcoming' NOT NULL,
                is_public      BOOLEAN DEFAULT FALSE NOT NULL
            )
        """)

        # is_publicカラムの追加（マイグレーション）
        if using_supabase():
            try:
                con.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT FALSE NOT NULL")
            except Exception:
                pass
            # password_hashカラムの型変更（マイグレーション）
            try:
                con.execute("ALTER TABLE events ALTER COLUMN password_hash TYPE TEXT")
            except Exception:
                pass
        else:
            try:
                con.execute("ALTER TABLE events ADD COLUMN is_public INTEGER DEFAULT 0")
            except Exception:
                pass

        # event_participantsテーブルを作成
        con.execute("""
            CREATE TABLE IF NOT EXISTS event_participants (
                event_id       VARCHAR(64) NOT NULL,
                user_id        VARCHAR(64) NOT NULL,
                joined_at      TIMESTAMP NOT NULL,
                PRIMARY KEY (event_id, user_id)
            )
        """)

        # event_ideasテーブルを作成（イベント中に作成された投稿を追跡）
        con.execute("""
            CREATE TABLE IF NOT EXISTS event_ideas (
                event_id       VARCHAR(64) NOT NULL,
                idea_id        VARCHAR(64) NOT NULL,
                PRIMARY KEY (event_id, idea_id)
            )
        """)


def fetch_items(exclude_user_id=None, category=None):
    with get_connection() as con:
        query = "SELECT * FROM ideas WHERE 1=1"
        params = []

        if exclude_user_id:
            query += " AND user_id != ?"
            params.append(exclude_user_id)

        if category:
            query += " AND category = ?"
            params.append(category)

        rows = con.execute(query, tuple(params)).fetchall()
    return rows


def fetch_random_item(exclude_user_id=None, category=None):
    items = fetch_items(exclude_user_id=exclude_user_id, category=category)
    if items:
        return random.choice(items)
    return None


def get_user_by_email(email: str):
    with get_connection() as con:
        row = con.execute(
            "SELECT user_id, nickname, password, email, icon_path, created_at FROM mypage WHERE email = ?",
            (email,)
        ).fetchone()
    return row


def get_user_by_user_id(user_id: str):
    with get_connection() as con:
        row = con.execute(
            "SELECT user_id, nickname, password, email, icon_path, created_at FROM mypage WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    return row


def insert_user(user_id: str, nickname: str, password_hash: str, email: str, icon_path: str | None, created_at: str) -> None:
    with get_connection() as con:
        # ticket_count カラムが存在する場合はそれを使用、なければ tickets
        try:
            con.execute(
                "INSERT INTO mypage (user_id, nickname, password, email, icon_path, created_at, ticket_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, nickname, password_hash, email, icon_path, created_at, 1)
            )
        except Exception:
            # ticket_count カラムが存在しない場合（後方互換性）
            try:
                con.execute(
                    "INSERT INTO mypage (user_id, nickname, password, email, icon_path, created_at, tickets) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, nickname, password_hash, email, icon_path, created_at, 1)
                )
            except Exception:
                # tickets カラムも存在しない場合
                con.execute(
                    "INSERT INTO mypage (user_id, nickname, password, email, icon_path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, nickname, password_hash, email, icon_path, created_at)
                )
        # SQLiteの場合は明示的にコミット（SupabaseConnectionは__exit__で自動コミット）
        if not using_supabase():
            con.commit()


def get_user_tickets(user_id: str) -> int:
    """ユーザーのチケット数を取得"""
    with get_connection() as con:
        try:
            row = con.execute(
                "SELECT ticket_count FROM mypage WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        except Exception:
            try:
                row = con.execute(
                    "SELECT tickets FROM mypage WHERE user_id = ?",
                    (user_id,)
                ).fetchone()
            except Exception:
                return 0
    return row[0] if row and row[0] is not None else 0


# ==================== アイデア統計関連の関数 ====================

def get_inheritance_count(idea_id: str) -> int:
    """アイデアが継承された回数を取得"""
    with get_connection() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM idea_inheritance WHERE parent_idea_id = ?",
            (idea_id,)
        ).fetchone()
    return row[0] if row else 0


def get_gacha_count(idea_id: str) -> int:
    """アイデアがガチャで引かれた回数を取得"""
    with get_connection() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM gacha_result WHERE idea_id = ?",
            (idea_id,)
        ).fetchone()
    return row[0] if row else 0


# ==================== イベント関連の関数 ====================

def get_event_status(start_date: datetime, end_date: datetime) -> str:
    """イベントの状態を取得（upcoming, active, ended）"""
    now = datetime.now()
    if now < start_date:
        return 'upcoming'
    elif now > end_date:
        return 'ended'
    else:
        return 'active'


def create_event(event_id: str, name: str, password_hash: str, start_date: datetime, end_date: datetime, created_by: str, is_public: bool = False) -> None:
    """イベントを作成"""
    with get_connection() as con:
        status = get_event_status(start_date, end_date)
        con.execute(
            "INSERT INTO events (event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, name, password_hash, start_date, end_date, datetime.now(), created_by, status, is_public)
        )
        if not using_supabase():
            con.commit()


def get_event(event_id: str):
    """イベントを取得"""
    with get_connection() as con:
        row = con.execute(
            "SELECT event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public FROM events WHERE event_id = ?",
            (event_id,)
        ).fetchone()
    return row


def get_all_events():
    """すべてのイベントを取得"""
    with get_connection() as con:
        rows = con.execute(
            "SELECT event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public FROM events ORDER BY start_date DESC"
        ).fetchall()
    return rows


def get_public_events():
    """公開されているイベントを取得"""
    with get_connection() as con:
        if using_supabase():
            # PostgreSQL用: BOOLEAN型なのでTRUEを使用
            query = "SELECT event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public FROM events WHERE is_public = TRUE ORDER BY start_date DESC"
        else:
            # SQLite用: INTEGER型なので1を使用
            query = "SELECT event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public FROM events WHERE is_public = 1 ORDER BY start_date DESC"
        rows = con.execute(query).fetchall()
    return rows


def get_active_events():
    """開催中のイベントを取得（status='active'のみ）"""
    now = datetime.now()
    with get_connection() as con:
        rows = con.execute(
            "SELECT event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public FROM events WHERE start_date <= ? AND end_date >= ? AND status = 'active' ORDER BY start_date DESC",
            (now, now)
        ).fetchall()
    return rows


def update_event(event_id: str, name: str = None, start_date: datetime = None, end_date: datetime = None, is_public: bool = None) -> None:
    """イベント情報を更新"""
    with get_connection() as con:
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if start_date is not None:
            updates.append("start_date = ?")
            params.append(start_date)
        
        if end_date is not None:
            updates.append("end_date = ?")
            params.append(end_date)
        
        if is_public is not None:
            updates.append("is_public = ?")
            params.append(is_public)
        
        # 日時が変更された場合はstatusを再計算
        if start_date is not None or end_date is not None:
            event_row = get_event(event_id)
            if event_row:
                current_start = start_date if start_date else event_row[3]
                current_end = end_date if end_date else event_row[4]
                if isinstance(current_start, str):
                    current_start = _parse_datetime(current_start)
                if isinstance(current_end, str):
                    current_end = _parse_datetime(current_end)
                status = get_event_status(current_start, current_end)
                updates.append("status = ?")
                params.append(status)
        
        if updates:
            params.append(event_id)
            query = f"UPDATE events SET {', '.join(updates)} WHERE event_id = ?"
            con.execute(query, tuple(params))
            if not using_supabase():
                con.commit()


def delete_event(event_id: str) -> None:
    """イベントを削除（関連データも削除）"""
    with get_connection() as con:
        # 関連データを削除
        con.execute("DELETE FROM event_participants WHERE event_id = ?", (event_id,))
        con.execute("DELETE FROM event_ideas WHERE event_id = ?", (event_id,))
        # イベント本体を削除
        con.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
        if not using_supabase():
            con.commit()


def join_event(event_id: str, user_id: str) -> bool:
    """イベントに参加（既に参加している場合はFalse）"""
    with get_connection() as con:
        # 既に参加しているかチェック
        existing = con.execute(
            "SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ?",
            (event_id, user_id)
        ).fetchone()
        if existing:
            return False
        
        con.execute(
            "INSERT INTO event_participants (event_id, user_id, joined_at) VALUES (?, ?, ?)",
            (event_id, user_id, datetime.now())
        )
        if not using_supabase():
            con.commit()
    return True


def is_event_participant(event_id: str, user_id: str) -> bool:
    """ユーザーがイベントに参加しているかチェック"""
    with get_connection() as con:
        row = con.execute(
            "SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ?",
            (event_id, user_id)
        ).fetchone()
    return row is not None


def get_event_participants(event_id: str):
    """イベントの参加者一覧を取得"""
    with get_connection() as con:
        rows = con.execute("""
            SELECT ep.user_id, ep.joined_at, u.nickname, u.icon_path
            FROM event_participants ep
            JOIN mypage u ON ep.user_id = u.user_id
            WHERE ep.event_id = ?
            ORDER BY ep.joined_at ASC
        """, (event_id,)).fetchall()
    return rows


def add_event_idea(event_id: str, idea_id: str) -> None:
    """イベント中に作成されたアイデアを記録"""
    with get_connection() as con:
        try:
            con.execute(
                "INSERT INTO event_ideas (event_id, idea_id) VALUES (?, ?)",
                (event_id, idea_id)
            )
            if not using_supabase():
                con.commit()
        except Exception:
            # 既に存在する場合は無視
            pass


def get_event_ideas(event_id: str):
    """イベント中に作成されたアイデアを取得"""
    with get_connection() as con:
        rows = con.execute("""
            SELECT ei.idea_id, i.title, i.detail, i.category, i.user_id, i.created_at, u.nickname
            FROM event_ideas ei
            JOIN ideas i ON ei.idea_id = i.idea_id
            LEFT JOIN mypage u ON i.user_id = u.user_id
            WHERE ei.event_id = ?
            ORDER BY i.created_at DESC
        """, (event_id,)).fetchall()
    return rows


def get_event_ranking(event_id: str):
    """イベント中のランキング（投稿数）を取得"""
    with get_connection() as con:
        rows = con.execute("""
            SELECT 
                u.user_id,
                u.nickname,
                u.icon_path,
                COUNT(i.idea_id) as post_count
            FROM event_participants ep
            JOIN mypage u ON ep.user_id = u.user_id
            LEFT JOIN event_ideas ei ON ep.event_id = ei.event_id
            LEFT JOIN ideas i ON ei.idea_id = i.idea_id AND i.user_id = u.user_id
            WHERE ep.event_id = ?
            GROUP BY u.user_id, u.nickname, u.icon_path, ep.joined_at
            HAVING COUNT(i.idea_id) > 0
            ORDER BY post_count DESC, ep.joined_at ASC
        """, (event_id,)).fetchall()
    return rows


def _parse_datetime(date_value):
    """データベースから取得した日時をdatetimeオブジェクトに変換"""
    if isinstance(date_value, datetime):
        return date_value
    if isinstance(date_value, str):
        # 複数のフォーマットを試す
        for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f']:
            try:
                return datetime.strptime(date_value, fmt)
            except ValueError:
                continue
        # パースに失敗した場合は現在時刻を返す
        return datetime.now()
    return date_value


def update_event_statuses() -> None:
    """全イベントの状態を更新"""
    with get_connection() as con:
        events = con.execute(
            "SELECT event_id, start_date, end_date FROM events"
        ).fetchall()
        now = datetime.now()
        for event_id, start_date, end_date in events:
            # 文字列の場合はdatetimeオブジェクトに変換
            start_date = _parse_datetime(start_date)
            end_date = _parse_datetime(end_date)
            status = get_event_status(start_date, end_date)
            con.execute(
                "UPDATE events SET status = ? WHERE event_id = ?",
                (status, event_id)
            )
        if not using_supabase():
            con.commit()


def get_ranking_by_period(period: str = 'all', limit: int = 5):
    """
    期間別ランキングを取得
    period: 'all' (総合), 'weekly' (週間), 'monthly' (月間), 'yearly' (年間)
    limit: 取得件数
    """
    now = datetime.now()
    
    # 期間に応じた開始日を計算
    if period == 'weekly':
        start_date = now - timedelta(days=7)
    elif period == 'monthly':
        start_date = now - timedelta(days=30)
    elif period == 'yearly':
        start_date = now - timedelta(days=365)
    else:  # 'all' またはその他
        start_date = None
    
    with get_connection() as con:
        if start_date:
            # 期間指定あり（文字列形式に変換して比較）
            start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
            query = _prepare_query("""
                SELECT 
                    u.user_id,
                    u.nickname,
                    u.icon_path,
                    COUNT(i.idea_id) as post_count
                FROM mypage u
                INNER JOIN ideas i ON u.user_id = i.user_id
                WHERE i.created_at >= ?
                GROUP BY u.user_id, u.nickname, u.icon_path, u.created_at
                ORDER BY post_count DESC, u.created_at ASC
                LIMIT ?
            """)
            rows = con.execute(query, (start_date_str, limit)).fetchall()
        else:
            # 全期間
            query = _prepare_query("""
                SELECT 
                    u.user_id,
                    u.nickname,
                    u.icon_path,
                    COUNT(i.idea_id) as post_count
                FROM mypage u
                LEFT JOIN ideas i ON u.user_id = i.user_id
                GROUP BY u.user_id, u.nickname, u.icon_path, u.created_at
                HAVING COUNT(i.idea_id) > 0
                ORDER BY post_count DESC, u.created_at ASC
                LIMIT ?
            """)
            rows = con.execute(query, (limit,)).fetchall()
    
    rankings = []
    for rank, row in enumerate(rows, start=1):
        rankings.append({
            'rank': rank,
            'user_id': row[0],
            'nickname': row[1],
            'icon_path': row[2],
            'post_count': row[3]
        })
    
    return rankings


def get_inheritance_ranking_by_period(period: str = 'all', limit: int = 5):
    """
    期間別継承数ランキングを取得
    period: 'all' (総合), 'weekly' (週間), 'monthly' (月間), 'yearly' (年間)
    limit: 取得件数
    """
    now = datetime.now()
    
    # 期間に応じた開始日を計算
    if period == 'weekly':
        start_date = now - timedelta(days=7)
    elif period == 'monthly':
        start_date = now - timedelta(days=30)
    elif period == 'yearly':
        start_date = now - timedelta(days=365)
    else:  # 'all' またはその他
        start_date = None
    
    with get_connection() as con:
        if start_date:
            # 期間指定あり
            start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
            query = _prepare_query("""
                SELECT 
                    u.user_id,
                    u.nickname,
                    u.icon_path,
                    COUNT(ii.inheritance_id) as inheritance_count
                FROM mypage u
                INNER JOIN idea_inheritance ii ON u.user_id = ii.child_user_id
                WHERE ii.created_at >= ?
                GROUP BY u.user_id, u.nickname, u.icon_path, u.created_at
                ORDER BY inheritance_count DESC, u.created_at ASC
                LIMIT ?
            """)
            rows = con.execute(query, (start_date_str, limit)).fetchall()
        else:
            # 全期間
            query = _prepare_query("""
                SELECT 
                    u.user_id,
                    u.nickname,
                    u.icon_path,
                    COUNT(ii.inheritance_id) as inheritance_count
                FROM mypage u
                LEFT JOIN idea_inheritance ii ON u.user_id = ii.child_user_id
                GROUP BY u.user_id, u.nickname, u.icon_path, u.created_at
                HAVING COUNT(ii.inheritance_id) > 0
                ORDER BY inheritance_count DESC, u.created_at ASC
                LIMIT ?
            """)
            rows = con.execute(query, (limit,)).fetchall()
    
    rankings = []
    for rank, row in enumerate(rows, start=1):
        rankings.append({
            'rank': rank,
            'user_id': row[0],
            'nickname': row[1],
            'icon_path': row[2],
            'inheritance_count': row[3]
        })
    
    return rankings
