import os
import random

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
        # mypageテーブルの作成とマイグレーション
        con.execute("""
            CREATE TABLE IF NOT EXISTS mypage (
                user_id      VARCHAR(64) PRIMARY KEY,
                nickname     VARCHAR(32) NOT NULL,
                password     VARCHAR(128) NOT NULL,
                email        VARCHAR(128) UNIQUE NOT NULL,
                icon_path    VARCHAR(255),
                created_at   TIMESTAMP NOT NULL,
                ticket_count INTEGER DEFAULT 1 NOT NULL
            )
        """)

        # マイグレーション処理
        if using_supabase():
            # Supabase用のマイグレーション
            try:
                con.execute("ALTER TABLE mypage ADD COLUMN IF NOT EXISTS icon_path VARCHAR(255)")
            except Exception:
                pass
            try:
                con.execute("ALTER TABLE mypage ADD COLUMN tickets INTEGER DEFAULT 0 NOT NULL")
            except Exception:
                pass

            # tickets → ticket_count のリネーム（既存の場合）
            try:
                # ticketsカラムが存在するか確認してリネーム
                con.execute("""
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM information_schema.columns
                                   WHERE table_name='mypage' AND column_name='tickets') THEN
                            ALTER TABLE mypage RENAME COLUMN tickets TO ticket_count;
                        END IF;
                    END $$;
                """)
            except Exception:
                pass

            # ticket_countカラムが存在しない場合は追加
            try:
                con.execute("ALTER TABLE mypage ADD COLUMN IF NOT EXISTS ticket_count INTEGER DEFAULT 1 NOT NULL")
            except Exception:
                pass

            # デフォルト値を1に設定
            try:
                con.execute("ALTER TABLE mypage ALTER COLUMN ticket_count SET DEFAULT 1")
            except Exception:
                pass

            # 既存データでNULLの場合は1に更新
            try:
                con.execute("UPDATE mypage SET ticket_count = 1 WHERE ticket_count IS NULL OR ticket_count = 0")
            except Exception:
                pass

            # カラムの型変更
            try:
                con.execute("ALTER TABLE mypage ALTER COLUMN password TYPE VARCHAR(128)")
            except Exception:
                pass
            try:
                con.execute("ALTER TABLE mypage ALTER COLUMN email TYPE VARCHAR(128)")
            except Exception:
                pass
            try:
                con.execute("ALTER TABLE mypage ALTER COLUMN icon_path TYPE VARCHAR(255)")
            except Exception:
                pass
        else:
            # SQLite用のマイグレーション
            try:
                con.execute("ALTER TABLE mypage ADD COLUMN icon_path VARCHAR(255)")
            except Exception:
                pass

            # ticket_countカラムを追加（存在しない場合）
            try:
                con.execute("ALTER TABLE mypage ADD COLUMN ticket_count INTEGER DEFAULT 1")
            except Exception:
                pass

            # 既存のticketsカラムからticket_countにデータを移行
            try:
                # ticketsカラムがあれば、その値をticket_countにコピー
                # SQLiteではカラムの存在確認が難しいため、直接更新を試みる
                # エラーが発生した場合はticketsカラムが存在しないと判断
                con.execute("UPDATE mypage SET ticket_count = COALESCE(tickets, 1) WHERE ticket_count IS NULL OR ticket_count = 0")
            except Exception:
                # ticketsカラムが存在しない場合は何もしない
                pass

            # NULLや0の値を1に更新（デフォルト値）
            try:
                con.execute("UPDATE mypage SET ticket_count = 1 WHERE ticket_count IS NULL OR ticket_count = 0")
            except Exception:
                pass

        # ideasテーブルの作成とマイグレーション
        con.execute("""
            CREATE TABLE IF NOT EXISTS ideas (
                idea_id      VARCHAR(64) PRIMARY KEY,
                title        VARCHAR(128) NOT NULL,
                detail       TEXT NOT NULL,
                category     VARCHAR(32) NOT NULL,
                user_id      VARCHAR(64) NOT NULL,
                created_at   TIMESTAMP NOT NULL,
                inheritance_flag BOOLEAN DEFAULT FALSE
            )
        """)

        # inheritance_flagカラムの追加
        if using_supabase():
            try:
                con.execute("ALTER TABLE ideas ADD COLUMN IF NOT EXISTS inheritance_flag BOOLEAN DEFAULT FALSE")
            except Exception:
                pass
        else:
            try:
                con.execute("ALTER TABLE ideas ADD COLUMN inheritance_flag INTEGER DEFAULT 0")
            except Exception:
                pass

        # gacha_resultテーブル（変更なし）
        con.execute("""
            CREATE TABLE IF NOT EXISTS gacha_result (
                result_id    VARCHAR(64) PRIMARY KEY,
                user_id      VARCHAR(64) NOT NULL,
                idea_id      VARCHAR(64) NOT NULL,
                created_at   TIMESTAMP NOT NULL
            )
        """)

        # revival_notifyテーブル（変更なし）
        con.execute("""
            CREATE TABLE IF NOT EXISTS revival_notify (
                notify_id    VARCHAR(64) PRIMARY KEY,
                idea_id      VARCHAR(64) NOT NULL,
                author_id    VARCHAR(64) NOT NULL,
                picker_id    VARCHAR(64) NOT NULL,
                created_at   TIMESTAMP NOT NULL
            )
        """)

        # thanksテーブルの作成とマイグレーション
        con.execute("""
            CREATE TABLE IF NOT EXISTS thanks (
                thanks_id       VARCHAR(64) PRIMARY KEY,
                gacha_result_id VARCHAR(64) NOT NULL,
                sender_id       VARCHAR(64) NOT NULL,
                receiver_id     VARCHAR(64) NOT NULL,
                stamp_type      VARCHAR(32) NOT NULL,
                created_at      TIMESTAMP NOT NULL
            )
        """)

        # gacha_type_id → gacha_result_id のリネーム
        if using_supabase():
            try:
                # gacha_type_idカラムが存在する場合、リネーム
                con.execute("""
                    DO $$
                    BEGIN
                        IF EXISTS (SELECT 1 FROM information_schema.columns
                                   WHERE table_name='thanks' AND column_name='gacha_type_id') THEN
                            ALTER TABLE thanks RENAME COLUMN gacha_type_id TO gacha_result_id;
                        END IF;
                    END $$;
                """)
            except Exception:
                pass
        else:
            # SQLiteでは直接リネームできないため、新しいカラムを追加してデータを移行
            # gacha_result_idカラムを追加（存在しない場合）
            try:
                con.execute("ALTER TABLE thanks ADD COLUMN gacha_result_id VARCHAR(64)")
            except Exception:
                pass

            # 既存のgacha_type_idからgacha_result_idにデータを移行
            try:
                con.execute("""
                    UPDATE thanks
                    SET gacha_result_id = gacha_type_id
                    WHERE gacha_result_id IS NULL
                    AND gacha_type_id IS NOT NULL
                """)
            except Exception:
                pass

        # idea_inheritanceテーブルを新規作成
        con.execute("""
            CREATE TABLE IF NOT EXISTS idea_inheritance (
                inheritance_id VARCHAR(64) PRIMARY KEY,
                parent_idea_id VARCHAR(64) NOT NULL,
                parent_user_id VARCHAR(64) NOT NULL,
                child_idea_id  VARCHAR(64) NOT NULL,
                child_user_id  VARCHAR(64) NOT NULL,
                add_point      VARCHAR(64) NOT NULL,
                add_detail     TEXT,
                created_at     TIMESTAMP NOT NULL
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
        # ticket_count を優先し、存在しない場合は tickets を参照
        try:
            row = con.execute(
                "SELECT user_id, nickname, password, email, icon_path, created_at, COALESCE(ticket_count, tickets, 1) FROM mypage WHERE email = ?",
                (email,)
            ).fetchone()
        except Exception:
            try:
                row = con.execute(
                    "SELECT user_id, nickname, password, email, icon_path, created_at, COALESCE(tickets, 1) FROM mypage WHERE email = ?",
                    (email,)
                ).fetchone()
            except Exception:
                row = con.execute(
                    "SELECT user_id, nickname, password, email, icon_path, created_at, 1 FROM mypage WHERE email = ?",
                    (email,)
                ).fetchone()
    return row


def get_user_by_user_id(user_id: str):
    with get_connection() as con:
        # ticket_count を優先し、存在しない場合は tickets を参照
        try:
            row = con.execute(
                "SELECT user_id, nickname, password, email, icon_path, created_at, COALESCE(ticket_count, tickets, 1) FROM mypage WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        except Exception:
            try:
                row = con.execute(
                    "SELECT user_id, nickname, password, email, icon_path, created_at, COALESCE(tickets, 1) FROM mypage WHERE user_id = ?",
                    (user_id,)
                ).fetchone()
            except Exception:
                row = con.execute(
                    "SELECT user_id, nickname, password, email, icon_path, created_at, 1 FROM mypage WHERE user_id = ?",
                    (user_id,)
                ).fetchone()
    return row


def get_user_tickets(user_id: str) -> int:
    """ユーザーのチケット数を取得（ticket_count または tickets を参照）"""
    with get_connection() as con:
        # まず ticket_count を試し、存在しない場合は tickets を参照
        try:
            row = con.execute(
                "SELECT COALESCE(ticket_count, tickets, 1) FROM mypage WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        except Exception:
            # ticket_count カラムが存在しない場合
            try:
                row = con.execute(
                    "SELECT COALESCE(tickets, 1) FROM mypage WHERE user_id = ?",
                    (user_id,)
                ).fetchone()
            except Exception:
                return 1  # デフォルト値
    return row[0] if row else 1


def update_user_tickets(user_id: str, tickets: int) -> None:
    """ユーザーのチケット数を更新（ticket_count を優先）"""
    with get_connection() as con:
        # ticket_count カラムが存在する場合はそれを使用、なければ tickets
        try:
            con.execute(
                "UPDATE mypage SET ticket_count = ? WHERE user_id = ?",
                (tickets, user_id)
            )
        except Exception:
            # ticket_count カラムが存在しない場合
            try:
                con.execute(
                    "UPDATE mypage SET tickets = ? WHERE user_id = ?",
                    (tickets, user_id)
                )
            except Exception:
                pass
        # SQLiteの場合は明示的にコミット（SupabaseConnectionは__exit__で自動コミット）
        if not using_supabase():
            con.commit()


def add_user_tickets(user_id: str, amount: int) -> int:
    """ユーザーのチケットを増やす（負の値も可）"""
    current_tickets = get_user_tickets(user_id)
    new_tickets = max(0, current_tickets + amount)  # 0以下にはならない
    update_user_tickets(user_id, new_tickets)
    return new_tickets


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
