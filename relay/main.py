from relay import app
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
    jsonify,
)
from relay.db import (
    fetch_random_item,
    get_connection,
    get_user_by_email,
    get_user_by_user_id,
    insert_user,
    get_user_tickets,
    get_inheritance_count,
    get_gacha_count,
    using_supabase,
    create_event,
    get_event,
    get_all_events,
    get_public_events,
    get_active_events,
    join_event,
    is_event_participant,
    get_event_participants,
    add_event_idea,
    get_event_ideas,
    get_event_ranking,
    get_event_status,
    update_event_statuses,
    update_event,
    delete_event,
    get_ranking_by_period,
    get_inheritance_ranking_by_period,
    get_company_code_by_user_id,
    get_all_companies,
    get_company,
    create_company,
)
from relay.content_moderation import check_content, suggest_category, fuse_ideas
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
import unicodedata
import os
from urllib.parse import urlparse

import cloudinary.uploader
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps

ALLOWED_ICON_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif'}
MAX_NICKNAME_LENGTH = 32

MAX_TITLE_LENGTH = 60
MAX_POST_LENGTH = 500

# 日本時間（JST）を取得するヘルパー関数
JST = ZoneInfo('Asia/Tokyo')

def now_jst():
    """現在時刻を日本時間（JST）で返す（タイムゾーン情報なし）"""
    # データベースの日時と比較するため、タイムゾーン情報を削除
    return datetime.now(JST).replace(tzinfo=None)


@app.context_processor
def inject_notifications():
    if 'user_id' not in session:
        return dict(revival_notifications=[], unread_notification_count=0, ticket_count=0)
    
    user_id = session['user_id']
    with get_connection() as con:
        # 全通知を取得（通知パネル表示用）
        revival_rows = con.execute("""
            SELECT 
                rn.notify_id,
                rn.created_at,
                rn.picker_id,
                rn.read_at,
                picker.nickname,
                picker.icon_path,
                i.title,
                i.category
            FROM revival_notify rn
            JOIN ideas i ON rn.idea_id = i.idea_id
            LEFT JOIN mypage picker ON rn.picker_id = picker.user_id
            WHERE rn.author_id = ?
            ORDER BY rn.created_at DESC
        """, (user_id,)).fetchall()

        # 未読通知数を取得（バッジ表示用）
        unread_count_row = con.execute("""
            SELECT COUNT(*) 
            FROM revival_notify 
            WHERE author_id = ? AND read_at IS NULL
        """, (user_id,)).fetchone()
        
        unread_count = unread_count_row[0] if unread_count_row else 0

    revival_notifications = []
    for row in revival_rows:
        revival_notifications.append({
            'notify_id': row[0],
            'created_at': row[1],
            'picker_id': row[2],
            'read_at': row[3],
            'picker_nickname': row[4] if row[4] else '不明なユーザー',
            'picker_icon_path': row[5],
            'idea_title': row[6],
            'category': row[7]
        })
    
    # データベースからチケット数を取得
    ticket_count = get_user_tickets(user_id)
    
    return dict(
        revival_notifications=revival_notifications,
        unread_notification_count=unread_count,
        ticket_count=ticket_count
    )


def calculate_text_length(text):
    """文字数を計算（日本語も1文字としてカウント）"""
    return len(text)


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            next_url = request.url
            return redirect(url_for('login', next=next_url))
        return view_func(*args, **kwargs)

    return wrapper


def admin_required(view_func):
    """管理者のみアクセス可能なデコレータ"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            next_url = request.url
            return redirect(url_for('login', next=next_url))
        
        admin_user_id = os.environ.get('ADMIN_USER_ID')
        if not admin_user_id or session.get('user_id') != admin_user_id:
            flash('このページにアクセスする権限がありません。')
            return redirect(url_for('index'))
        
        return view_func(*args, **kwargs)

    return wrapper



def store_icon_file(icon_file, extension):
    if app.config.get('USE_CLOUDINARY'):
        icon_file.stream.seek(0)
        upload_options = {'resource_type': 'image'}
        folder = os.environ.get('CLOUDINARY_UPLOAD_FOLDER')
        if folder:
            upload_options['folder'] = folder
        upload_result = cloudinary.uploader.upload(icon_file, **upload_options)
        return upload_result.get('secure_url')

    uploads_dir = app.config['UPLOAD_FOLDER']
    os.makedirs(uploads_dir, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    save_path = os.path.join(uploads_dir, stored_filename)
    icon_file.stream.seek(0)
    icon_file.save(save_path)
    return os.path.join('uploads', stored_filename)


def delete_icon_file(icon_path):
    if not icon_path:
        return
    if icon_path.startswith('http'):
        if app.config.get('USE_CLOUDINARY'):
            public_id = _extract_public_id(icon_path)
            if public_id:
                cloudinary.uploader.destroy(public_id, invalidate=True)
        return
    if icon_path.startswith('uploads/'):
        filename = icon_path.split('/', 1)[1]
        absolute_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(absolute_path):
        os.remove(absolute_path)


def _extract_public_id(url: str) -> str | None:
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')
    try:
        upload_index = path_parts.index('upload')
    except ValueError:
        return None
    public_parts = path_parts[upload_index + 1 :]
    if public_parts and public_parts[0].startswith('v') and public_parts[0][1:].isdigit():
        public_parts = public_parts[1:]
    if not public_parts:
        return None
    public_id_with_ext = '/'.join(public_parts)
    public_id, _ = os.path.splitext(public_id_with_ext)
    return public_id or None


def get_current_user_id():
    return session.get('user_id')


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/')
@login_required
def index():
    user_id = session['user_id']
    user_name = session['nickname']
    # ユーザーの会社コードを取得
    company_code = get_company_code_by_user_id(user_id) or 'test'

    # イベント状態を更新
    update_event_statuses()

    # 開催中のイベントを取得（ユーザーが参加しているもののみ）
    active_events_rows = get_active_events()
    active_events = []
    
    for event_row in active_events_rows:
        event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public = event_row
        
        # ユーザーが参加しているイベントのみを表示
        if not is_event_participant(event_id, user_id):
            continue
        
        # 日時が文字列の場合はdatetimeオブジェクトに変換
        if isinstance(start_date, str):
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError:
                    continue
        if isinstance(end_date, str):
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    end_date = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError:
                    continue

        # 残り日数を計算（終了日までの日数）
        now = now_jst()
        remaining_days = (end_date - now).days
        if remaining_days < 0:
            remaining_days = 0

        active_events.append({
            'event_id': event_id,
            'name': name,
            'start_date': start_date,
            'end_date': end_date,
            'status': status,
            'remaining_days': remaining_days
        })

    # 期間パラメータを取得（デフォルトは総合）
    period = request.args.get('period', 'all')
    valid_periods = ['all', 'weekly', 'monthly', 'yearly']
    if period not in valid_periods:
        period = 'all'
    
    # 期間別ランキングを取得（各期間トップ5、会社コードでフィルタリング）
    rankings_by_period = {}
    for p in valid_periods:
        rankings_by_period[p] = get_ranking_by_period(p, limit=5, company_code=company_code)
    
    # 現在選択中のランキング
    current_rankings = rankings_by_period[period]
    
    return render_template(
        'index.html',
        active_events=active_events,
        rankings=current_rankings,
        rankings_by_period=rankings_by_period,
        current_period=period,
        user_name=user_name
    )

@app.route('/form')
@login_required
def form():
    return render_template(
        'form.html'
    )


@app.route('/inheritance/<idea_id>')
@login_required
def inheritance_form(idea_id):
    user_id = session['user_id']
    # ユーザーの会社コードを取得
    company_code = get_company_code_by_user_id(user_id) or 'test'
    
    with get_connection() as con:
        idea_row = con.execute(
            "SELECT idea_id, title, detail, category, user_id, created_at FROM ideas WHERE idea_id = ? AND company_code = ?",
            (idea_id, company_code)
        ).fetchone()
        
        if not idea_row:
            flash('アイデアが見つかりません。')
            return redirect(url_for('mypage'))
        
        parent_user_row = con.execute(
            "SELECT user_id, nickname FROM mypage WHERE user_id = ?",
            (idea_row[4],)
        ).fetchone()
        
        # 保存済みの継承データがあるか確認
        saved_inheritance = con.execute(
            "SELECT add_point, add_detail FROM idea_inheritance WHERE parent_idea_id = ? AND child_user_id = ? AND child_idea_id IS NULL",
            (idea_id, user_id)
        ).fetchone()
    
    idea = {
        'idea_id': idea_row[0],
        'title': idea_row[1],
        'detail': idea_row[2],
        'category': idea_row[3],
        'user_id': idea_row[4],
        'created_at': idea_row[5],
        'author_nickname': parent_user_row[1] if parent_user_row else '不明なユーザー',
        'saved_add_point': saved_inheritance[0] if saved_inheritance else '',
        'saved_add_detail': saved_inheritance[1] if saved_inheritance else ''
    }
    
    return render_template(
        'inheritance_form.html',
        idea=idea
    )


@app.route('/inheritance/<idea_id>/save', methods=['POST'])
@login_required
def save_inheritance(idea_id):
    user_id = session['user_id']
    add_point = request.form.get('add_point', '').strip()
    add_detail = request.form.get('add_detail', '').strip()
    parent_idea_id = request.form.get('parent_idea_id')
    parent_user_id = request.form.get('parent_user_id')

    if not add_point:
        flash('追加したポイントを入力してください。')
        return redirect(url_for('inheritance_form', idea_id=idea_id))

    if calculate_text_length(add_point) > 64:
        flash('追加したポイントは64文字以内で入力してください。')
        return redirect(url_for('inheritance_form', idea_id=idea_id))

    with get_connection() as con:
        # 既存の継承レコードがあるか確認
        existing = con.execute(
            "SELECT inheritance_id FROM idea_inheritance WHERE parent_idea_id = ? AND child_user_id = ? AND child_idea_id IS NULL",
            (parent_idea_id, user_id)
        ).fetchone()

        inheritance_id = str(uuid.uuid4())
        created_at = now_jst().strftime('%Y-%m-%d %H:%M:%S')

        if existing:
            # 既存のレコードを更新
            con.execute(
                """
                UPDATE idea_inheritance 
                SET add_point = ?, add_detail = ?, created_at = ?
                WHERE inheritance_id = ?
                """,
                (add_point, add_detail if add_detail else None, created_at, existing[0])
            )
        else:
            # 新規レコードを作成
            # child_idea_idはNULLを許可（保存時はNULL、投稿時は実際のIDを設定）
            try:
                con.execute(
                    """
                    INSERT INTO idea_inheritance 
                    (inheritance_id, parent_idea_id, parent_user_id, child_idea_id, child_user_id, add_point, add_detail, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (inheritance_id, parent_idea_id, parent_user_id, None, user_id, add_point, add_detail if add_detail else None, created_at)
                )
            except Exception as e:
                # NOT NULL制約エラーの場合、空文字列を設定（マイグレーション前の暫定対応）
                if 'NOT NULL' in str(e) or 'constraint' in str(e).lower():
                    con.execute(
                        """
                        INSERT INTO idea_inheritance 
                        (inheritance_id, parent_idea_id, parent_user_id, child_idea_id, child_user_id, add_point, add_detail, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (inheritance_id, parent_idea_id, parent_user_id, '', user_id, add_point, add_detail if add_detail else None, created_at)
                    )
                else:
                    raise

    flash('継承情報を保存しました。')
    return redirect(url_for('mypage'))


@app.route('/inheritance/<idea_id>/post', methods=['POST'])
@login_required
def post_inheritance(idea_id):
    user_id = session['user_id']
    add_point = request.form.get('add_point', '').strip()
    add_detail = request.form.get('add_detail', '').strip()
    parent_idea_id = request.form.get('parent_idea_id')
    parent_user_id = request.form.get('parent_user_id')

    # フォームから値が取得できない場合、保存済みデータを取得
    if not add_point:
        with get_connection() as con:
            saved_inheritance = con.execute(
                "SELECT add_point, add_detail FROM idea_inheritance WHERE parent_idea_id = ? AND child_user_id = ? AND child_idea_id IS NULL",
                (parent_idea_id or idea_id, user_id)
            ).fetchone()
            
            if saved_inheritance:
                add_point = saved_inheritance[0] or ''
                add_detail = saved_inheritance[1] or ''
            else:
                flash('追加したポイントを入力してください。')
                return redirect(url_for('inheritance_form', idea_id=idea_id))

    if not add_point:
        flash('追加したポイントを入力してください。')
        return redirect(url_for('inheritance_form', idea_id=idea_id))

    if calculate_text_length(add_point) > 64:
        flash('追加したポイントは64文字以内で入力してください。')
        return redirect(url_for('inheritance_form', idea_id=idea_id))

    with get_connection() as con:
        # 親アイデアの情報を取得
        parent_idea = con.execute(
            "SELECT title, detail, category FROM ideas WHERE idea_id = ?",
            (parent_idea_id,)
        ).fetchone()

        if not parent_idea:
            flash('継承元のアイデアが見つかりません。')
            return redirect(url_for('mypage'))

        # 保存済みの継承レコードがあるか確認
        existing_inheritance = con.execute(
            "SELECT inheritance_id FROM idea_inheritance WHERE parent_idea_id = ? AND child_user_id = ? AND child_idea_id IS NULL",
            (parent_idea_id, user_id)
        ).fetchone()

        # 新しいアイデアを作成（継承元の情報をベースに）
        child_idea_id = str(uuid.uuid4())
        created_at = now_jst().strftime('%Y-%m-%d %H:%M:%S')
        
        # タイトルと詳細を継承元から取得（必要に応じて編集可能にする場合は変更）
        child_title = parent_idea[0]  # 親のタイトルを使用
        child_detail = parent_idea[1]  # 親の詳細を使用
        child_category = parent_idea[2]  # 親のカテゴリを使用

        # AI判定を実行（継承投稿の場合、add_detailが投稿内容）
        print("\n[継承投稿処理] AI判定を開始します...")
        is_inappropriate, is_thin_content, reason = check_content(child_title, add_detail, child_category)
        
        if is_inappropriate:
            print(f"[継承投稿処理] 不適切な投稿として拒否されました: {reason}")
            flash(f'不適切な内容が含まれているため、投稿できませんでした。{reason if reason else ""}')
            # 入力値を一時保存して継承フォームに戻す
            try:
                if existing_inheritance:
                    # 既存レコードを更新
                    con.execute(
                        """
                        UPDATE idea_inheritance 
                        SET add_point = ?, add_detail = ?, created_at = ?
                        WHERE inheritance_id = ?
                        """,
                        (add_point, add_detail if add_detail else None, created_at, existing_inheritance[0])
                    )
                else:
                    # 新規レコードを作成
                    inheritance_id = str(uuid.uuid4())
                    con.execute(
                        """
                        INSERT INTO idea_inheritance 
                        (inheritance_id, parent_idea_id, parent_user_id, child_idea_id, child_user_id, add_point, add_detail, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (inheritance_id, parent_idea_id, parent_user_id, None, user_id, add_point, add_detail if add_detail else None, created_at)
                    )
                if not using_supabase():
                    con.commit()
            except Exception:
                # 保存に失敗してもフォームに戻す
                pass
            return redirect(url_for('inheritance_form', idea_id=idea_id))
        
        if is_thin_content:
            print(f"[継承投稿処理] 内容が薄い投稿として拒否されました: {reason}")
            flash(f'内容が不十分なため、投稿できませんでした。{reason if reason else "もう少し詳しく説明してください。"}')
            # 入力値を一時保存して継承フォームに戻す
            try:
                if existing_inheritance:
                    # 既存レコードを更新
                    con.execute(
                        """
                        UPDATE idea_inheritance 
                        SET add_point = ?, add_detail = ?, created_at = ?
                        WHERE inheritance_id = ?
                        """,
                        (add_point, add_detail if add_detail else None, created_at, existing_inheritance[0])
                    )
                else:
                    # 新規レコードを作成
                    inheritance_id = str(uuid.uuid4())
                    con.execute(
                        """
                        INSERT INTO idea_inheritance 
                        (inheritance_id, parent_idea_id, parent_user_id, child_idea_id, child_user_id, add_point, add_detail, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (inheritance_id, parent_idea_id, parent_user_id, None, user_id, add_point, add_detail if add_detail else None, created_at)
                    )
                if not using_supabase():
                    con.commit()
            except Exception:
                # 保存に失敗してもフォームに戻す
                pass
            return redirect(url_for('inheritance_form', idea_id=idea_id))
        
        print("[継承投稿処理] AI判定を通過しました。投稿を保存します...")

        # ユーザーの会社コードを取得
        company_code = get_company_code_by_user_id(user_id) or 'test'
        # アイデアを登録
        con.execute(
            "INSERT INTO ideas (idea_id, title, detail, category, user_id, created_at, inheritance_flag, company_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (child_idea_id, child_title, add_detail, child_category, user_id, created_at, True, company_code)
        )

        # 既存の継承レコードがある場合は更新、なければ新規作成
        if existing_inheritance:
            # 既存レコードを更新（child_idea_idを設定）
            con.execute(
                """
                UPDATE idea_inheritance 
                SET child_idea_id = ?, add_point = ?, add_detail = ?, created_at = ?
                WHERE inheritance_id = ?
                """,
                (child_idea_id, add_point, add_detail if add_detail else None, created_at, existing_inheritance[0])
            )
        else:
            # 新規継承情報を登録
            inheritance_id = str(uuid.uuid4())
            con.execute(
                """
                INSERT INTO idea_inheritance 
                (inheritance_id, parent_idea_id, parent_user_id, child_idea_id, child_user_id, add_point, add_detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (inheritance_id, parent_idea_id, parent_user_id, child_idea_id, user_id, add_point, add_detail if add_detail else None, created_at)
            )
        
        if not using_supabase():
            con.commit()
    
    # イベント中に投稿した場合、イベントに関連付ける
    active_events = get_active_events()
    now = now_jst()
    for event_row in active_events:
        # is_publicカラムが追加されたため9カラム
        event_id_e, name_e, password_hash_e, start_date_e, end_date_e, created_at_e, created_by_e, status_e, is_public_e = event_row
        # 日時が文字列の場合はdatetimeオブジェクトに変換
        if isinstance(start_date_e, str):
            try:
                start_date_e = datetime.strptime(start_date_e, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    start_date_e = datetime.strptime(start_date_e, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError:
                    continue
        if isinstance(end_date_e, str):
            try:
                end_date_e = datetime.strptime(end_date_e, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    end_date_e = datetime.strptime(end_date_e, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError:
                    continue
        if is_event_participant(event_id_e, user_id) and start_date_e <= now <= end_date_e:
            add_event_idea(event_id_e, child_idea_id)
    
    # アイデア投稿時にチケット+1枚付与
    # セッションのチケット数を取得（なければDBから取得）
    current_tickets = session.get('tickets')
    if current_tickets is None:
        current_tickets = get_user_tickets(user_id)
    
    # チケットを1枚増やす
    new_tickets = current_tickets + 1
    
    # DBとセッションの両方を更新
    with get_connection() as con:
        try:
            con.execute(
                "UPDATE mypage SET ticket_count = ? WHERE user_id = ?",
                (new_tickets, user_id)
            )
        except Exception:
            try:
                con.execute(
                    "UPDATE mypage SET tickets = ? WHERE user_id = ?",
                    (new_tickets, user_id)
                )
            except Exception:
                pass
        if not using_supabase():
            con.commit()
    
    session['tickets'] = new_tickets
    session.modified = True

    flash('アイデアを継承して新規投稿しました。')
    return redirect(url_for('index'))

@app.route('/inheritance/view/<inheritance_id>')
@login_required
def inheritance_view(inheritance_id):
    user_id = session['user_id']
    # ユーザーの会社コードを取得
    company_code = get_company_code_by_user_id(user_id) or 'test'
    
    with get_connection() as con:
        row = con.execute("""
            SELECT 
                ii.inheritance_id,
                ii.parent_idea_id,
                ii.child_idea_id,
                ii.add_point,
                ii.add_detail,
                ii.created_at,
                parent_i.title as parent_title,
                parent_i.detail as parent_detail,
                parent_i.category as parent_category,
                parent_u.nickname as parent_nickname,
                child_i.title as child_title,
                child_i.detail as child_detail,
                child_i.category as child_category
            FROM idea_inheritance ii
            LEFT JOIN ideas parent_i ON ii.parent_idea_id = parent_i.idea_id
            LEFT JOIN mypage parent_u ON ii.parent_user_id = parent_u.user_id
            LEFT JOIN ideas child_i ON ii.child_idea_id = child_i.idea_id
            WHERE ii.inheritance_id = ? AND parent_i.company_code = ?
        """, (inheritance_id, company_code)).fetchone()
        
        if not row:
            flash('継承情報が見つかりません。')
            return redirect(url_for('mypage'))
            
        inheritance = {
            'inheritance_id': row[0],
            'parent_idea_id': row[1],
            'child_idea_id': row[2],
            'add_point': row[3],
            'add_detail': row[4],
            'created_at': row[5],
            'parent_title': row[6],
            'parent_detail': row[7],
            'parent_category': row[8],
            'parent_nickname': row[9] if row[9] else '不明なユーザー',
            'child_title': row[10],
            'child_detail': row[11],
            'child_category': row[12]
        }
        
    return render_template('inheritance_view.html', inheritance=inheritance)


@app.route('/api/suggest-category', methods=['POST'])
@login_required
def api_suggest_category():
    """カテゴリ自動判定用のAPIエンドポイント"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'リクエストボディが必要です'}), 400
    
    title = data.get('title', '').strip()
    detail = data.get('detail', '').strip()
    
    if not title or not detail:
        return jsonify({'error': 'タイトルと詳細が必要です'}), 400
    
    print(f"[API] カテゴリ判定リクエスト: タイトル={title[:50]}...")
    suggested_category = suggest_category(title, detail)
    
    if suggested_category:
        return jsonify({'category': suggested_category})
    else:
        return jsonify({'category': '', 'error': 'カテゴリを判定できませんでした'}), 200


@app.route('/post', methods=['POST'])
def post():
    if 'user_id' not in session:
        return redirect(url_for('login', next=url_for('form')))

    title = request.form['title']
    detail = request.form['detail']
    category = request.form.get('category', '').strip()

    # カテゴリが空の場合、AIで自動判定
    if not category:
        print("[投稿処理] カテゴリが空のため、AIで自動判定します...")
        suggested_category = suggest_category(title, detail)
        if suggested_category:
            category = suggested_category
            flash(f'カテゴリを自動判定しました: {category}')
        else:
            # AI判定に失敗した場合は「その他」をデフォルトに
            category = 'その他'
            flash('カテゴリを自動判定できませんでした。「その他」に設定されました。')

    if calculate_text_length(title) > MAX_TITLE_LENGTH:
        flash(
            f'タイトルは{MAX_TITLE_LENGTH}文字以内で入力してください。'
        )
        return render_template('form.html', form_data={'title': title, 'detail': detail, 'category': category})
    
    if calculate_text_length(detail) > MAX_POST_LENGTH:
        flash(
            f'アイデアの詳細は{MAX_POST_LENGTH}文字以内で入力してください。'
        )
        return render_template('form.html', form_data={'title': title, 'detail': detail, 'category': category})

    # AI判定を実行
    print("\n[投稿処理] AI判定を開始します...")
    is_inappropriate, is_thin_content, reason = check_content(title, detail, category)
    
    if is_inappropriate:
        print(f"[投稿処理] 不適切な投稿として拒否されました: {reason}")
        flash(f'不適切な内容が含まれているため、投稿できませんでした。{reason if reason else ""}')
        return render_template('form.html', form_data={'title': title, 'detail': detail, 'category': category})
    
    if is_thin_content:
        print(f"[投稿処理] 内容が薄い投稿として拒否されました: {reason}")
        flash(f'内容が不十分なため、投稿できませんでした。{reason if reason else "もう少し詳しく説明してください。"}')
        return render_template('form.html', form_data={'title': title, 'detail': detail, 'category': category})
    
    print("[投稿処理] AI判定を通過しました。投稿を保存します...")

    with get_connection() as con:
        idea_id = str(uuid.uuid4())
        user_id = session['user_id']
        created_at = now_jst().strftime('%Y-%m-%d %H:%M:%S')
        # inheritance_flagはデフォルトでFalse（SQLiteの場合は0）
        inheritance_flag = False
        # ユーザーの会社コードを取得
        company_code = get_company_code_by_user_id(user_id) or 'test'
        con.execute(
            "INSERT INTO ideas (idea_id, title, detail, category, user_id, created_at, inheritance_flag, company_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [idea_id, title, detail, category, user_id, created_at, inheritance_flag, company_code]
        )
        if not using_supabase():
            con.commit()
    
    # イベント中に投稿した場合、イベントに関連付ける
    active_events = get_active_events()
    now = now_jst()
    for event_row in active_events:
        # is_publicカラムが追加されたため9カラム
        event_id_e, name_e, password_hash_e, start_date_e, end_date_e, created_at_e, created_by_e, status_e, is_public_e = event_row
        # 日時が文字列の場合はdatetimeオブジェクトに変換
        if isinstance(start_date_e, str):
            try:
                start_date_e = datetime.strptime(start_date_e, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    start_date_e = datetime.strptime(start_date_e, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError:
                    continue
        if isinstance(end_date_e, str):
            try:
                end_date_e = datetime.strptime(end_date_e, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    end_date_e = datetime.strptime(end_date_e, '%Y-%m-%d %H:%M:%S.%f')
                except ValueError:
                    continue
        if is_event_participant(event_id_e, user_id) and start_date_e <= now <= end_date_e:
            add_event_idea(event_id_e, idea_id)
    
    # アイデア投稿時にチケット+1枚付与
    # セッションのチケット数を取得（なければDBから取得）
    current_tickets = session.get('tickets')
    if current_tickets is None:
        current_tickets = get_user_tickets(user_id)
    
    # チケットを1枚増やす
    new_tickets = current_tickets + 1
    
    # DBとセッションの両方を更新
    with get_connection() as con:
        try:
            con.execute(
                "UPDATE mypage SET ticket_count = ? WHERE user_id = ?",
                (new_tickets, user_id)
            )
        except Exception:
            try:
                con.execute(
                    "UPDATE mypage SET tickets = ? WHERE user_id = ?",
                    (new_tickets, user_id)
                )
            except Exception:
                pass
        if not using_supabase():
            con.commit()
    
    session['tickets'] = new_tickets
    session.modified = True

    return redirect(url_for('index'))


@app.route('/ideas/<idea_id>/delete', methods=['POST'])
@login_required
def delete_idea(idea_id):
    user_id = session['user_id']

    with get_connection() as con:
        cur = con.cursor()
        idea_row = cur.execute(
            "SELECT user_id, is_deleted FROM ideas WHERE idea_id = ?",
            (idea_id,)
        ).fetchone()

        if not idea_row or idea_row[0] != user_id:
            flash('指定した投稿を削除できません。')
            return redirect(url_for('mypage'))

        # 既に削除済みの場合
        if idea_row[1]:
            flash('この投稿は既に削除されています。')
            return redirect(url_for('mypage'))

        # 論理削除（is_deletedフラグを立てる）
        if using_supabase():
            cur.execute("UPDATE ideas SET is_deleted = TRUE WHERE idea_id = ?", (idea_id,))
        else:
            cur.execute("UPDATE ideas SET is_deleted = 1 WHERE idea_id = ?", (idea_id,))
        
        if not using_supabase():
            con.commit()

    flash('投稿を削除しました。')
    return redirect(url_for('mypage'))


@app.route('/gacha/<result_id>/delete', methods=['POST'])
@login_required
def delete_gacha_result(result_id):
    """ガチャで引いたアイデアを履歴から削除"""
    user_id = session['user_id']

    with get_connection() as con:
        cur = con.cursor()
        # 自分のガチャ結果か確認
        result_row = cur.execute(
            "SELECT user_id FROM gacha_result WHERE result_id = ?",
            (result_id,)
        ).fetchone()

        if not result_row or result_row[0] != user_id:
            flash('指定したガチャ結果を削除できません。')
            return redirect(url_for('mypage'))

        # ガチャ結果を削除
        cur.execute("DELETE FROM gacha_result WHERE result_id = ?", (result_id,))
        
        if not using_supabase():
            con.commit()

    flash('ガチャ履歴から削除しました。')
    return redirect(url_for('mypage'))


@app.route('/inheritance/<inheritance_id>/delete', methods=['POST'])
@login_required
def delete_inheritance(inheritance_id):
    """継承したアイデアを履歴から削除"""
    user_id = session['user_id']

    with get_connection() as con:
        cur = con.cursor()
        # 自分の継承か確認
        inheritance_row = cur.execute(
            "SELECT child_user_id FROM idea_inheritance WHERE inheritance_id = ?",
            (inheritance_id,)
        ).fetchone()

        if not inheritance_row or inheritance_row[0] != user_id:
            flash('指定した継承を削除できません。')
            return redirect(url_for('mypage'))

        # 継承を削除
        cur.execute("DELETE FROM idea_inheritance WHERE inheritance_id = ?", (inheritance_id,))
        
        if not using_supabase():
            con.commit()

    flash('継承履歴から削除しました。')
    return redirect(url_for('mypage'))


@app.route('/posts/<idea_id>')
@login_required
def post_view(idea_id):
    user_id = session['user_id']
    # ユーザーの会社コードを取得
    company_code = get_company_code_by_user_id(user_id) or 'test'

    with get_connection() as con:
        row = con.execute(
            """
            SELECT 
                i.idea_id,
                i.title,
                i.detail,
                i.category,
                i.created_at,
                i.user_id,
                u.nickname,
                u.icon_path,
                i.inheritance_flag
            FROM ideas i
            LEFT JOIN mypage u ON i.user_id = u.user_id
            WHERE i.idea_id = ? AND i.company_code = ?
            """,
            (idea_id, company_code)
        ).fetchone()

    if not row:
        flash('投稿が見つかりませんでした。')
        return redirect(url_for('mypage'))

    # 継承されたアイデアの場合は継承詳細画面へリダイレクト
    if row[8]: # inheritance_flag
        with get_connection() as con:
            inheritance_row = con.execute(
                "SELECT inheritance_id FROM idea_inheritance WHERE child_idea_id = ?",
                (idea_id,)
            ).fetchone()
            if inheritance_row:
                return redirect(url_for('inheritance_view', inheritance_id=inheritance_row[0]))

    idea = {
        'idea_id': row[0],
        'title': row[1],
        'detail': row[2],
        'category': row[3],
        'created_at': row[4],
        'user_id': row[5],
        'nickname': row[6],
        'icon_path': row[7],
    }

    return render_template('post_view.html', idea=idea)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    errors = []

    form_data = {
        'user_id': request.form.get('user_id', '@').strip() if request.method == 'POST' else '@',
        'nickname': request.form.get('nickname', '').strip() if request.method == 'POST' else '',
        'email': request.form.get('email', '').strip() if request.method == 'POST' else '',
        'company_code': request.form.get('company_code', '').strip() if request.method == 'POST' else ''
    }

    if request.method == 'POST':
        raw_user_id = None
        user_id_input = form_data['user_id']
        nickname = form_data['nickname']
        email = form_data['email']
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        icon_file = request.files.get('icon')

        if not user_id_input:
            errors.append('ユーザーIDを入力してください。')
        elif not user_id_input.startswith('@'):
            errors.append('ユーザーIDは先頭に@を付けて入力してください。')
        elif len(user_id_input) == 1:
            errors.append('ユーザーIDが短すぎます。@の後に文字を入力してください。')
        else:
            raw_user_id = user_id_input[1:].strip()
            if not raw_user_id:
                errors.append('ユーザーIDが短すぎます。@の後に文字を入力してください。')
            elif len(raw_user_id) > 31:
                errors.append('ユーザーIDは31文字以内で入力してください。')
            elif not raw_user_id.replace('_', '').replace('-', '').isalnum():
                errors.append('ユーザーIDは英数字と-_のみ使用できます。')
            else:
                existing_user_id = get_user_by_user_id(raw_user_id)
                if existing_user_id:
                    errors.append('このユーザーIDは既に利用されています。')

        if not nickname:
            errors.append('ニックネームを入力してください。')

        if not email:
            errors.append('メールアドレスを入力してください。')
        elif '@' not in email or '.' not in email:
            errors.append('正しい形式のメールアドレスを入力してください。')

        if not password:
            errors.append('パスワードを入力してください。')
        elif len(password) < 8:
            errors.append('パスワードは8文字以上で入力してください。')
        elif password != confirm_password:
            errors.append('パスワードと確認用パスワードが一致しません。')

        existing_user = get_user_by_email(email) if email else None
        if existing_user:
            errors.append('このメールアドレスは既に登録されています。')

        # 会社コードのバリデーション
        company_code = request.form.get('company_code', '').strip()
        if not company_code:
            errors.append('会社コードを入力してください。')
        else:
            # 会社コードの存在チェック
            company = get_company(company_code)
            if not company:
                errors.append('存在しない会社コードです。管理者に確認してください。')

        icon_path = None
        icon_candidate = None
        if icon_file and icon_file.filename:
            filename = secure_filename(icon_file.filename)
            _, ext = os.path.splitext(filename)
            allowed_extensions = {'.png', '.jpg', '.jpeg', '.gif'}
            if ext.lower() not in allowed_extensions:
                errors.append('アイコン画像はPNG/JPG/GIF形式のみアップロードできます。')
            else:
                icon_candidate = (icon_file, ext.lower())

        if not errors:
            if icon_candidate:
                icon_stream, ext = icon_candidate
                icon_path = store_icon_file(icon_stream, ext)

            user_id = raw_user_id if raw_user_id and not errors else None
            password_hash = generate_password_hash(password)
            created_at = now_jst().strftime('%Y-%m-%d %H:%M:%S')
            insert_user(user_id, nickname, password_hash, email, icon_path, created_at, company_code)
            session.clear()
            session.permanent = True
            session['user_id'] = user_id
            session['nickname'] = nickname
            session['email'] = email
            session['icon_path'] = icon_path
            
            # 新規ユーザーのチケット数を取得してセッションに設定（初期値は0）
            tickets = get_user_tickets(user_id)
            session['tickets'] = tickets
            
            return redirect(url_for('index'))

    return render_template(
        'signup.html',
        errors=errors,
        form_data=form_data
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    errors = []
    form_data = {
        'identifier': request.form.get('identifier', '').strip() if request.method == 'POST' else ''
    }

    next_url = request.args.get('next') or request.form.get('next')

    if request.method == 'POST':
        identifier = form_data['identifier']
        password = request.form.get('password', '')

        if not identifier:
            errors.append('ユーザーIDまたはメールアドレスを入力してください。')

        if not password:
            errors.append('パスワードを入力してください。')

        user_row = None

        if identifier and not errors:
            if identifier.startswith('@'):
                candidate_id = identifier[1:].strip()
                if candidate_id:
                    user_row = get_user_by_user_id(candidate_id)
            else:
                user_row = get_user_by_email(identifier)
                if not user_row:
                    candidate_id = identifier.strip()
                    if candidate_id:
                        user_row = get_user_by_user_id(candidate_id)

            if not user_row:
                errors.append('該当するユーザーが見つかりませんでした。')

        if not errors and user_row:
            stored_hash = user_row[2]
            if not check_password_hash(stored_hash, password):
                errors.append('ユーザーIDまたはパスワードが正しくありません。')

        if not errors and user_row:
            session.clear()
            session.permanent = True
            user_id = user_row[0]
            session['user_id'] = user_id
            session['nickname'] = user_row[1]
            session['email'] = user_row[3]
            session['icon_path'] = user_row[4]
            
            # チケット数をDBから取得してセッションに設定
            tickets = get_user_tickets(user_id)
            session['tickets'] = tickets

            if next_url:
                return redirect(next_url)
            return redirect(url_for('index'))

    return render_template(
        'login.html',
        errors=errors,
        form_data=form_data,
        next_url=next_url
    )


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return redirect(url_for('index'))

# ここからガチャ機能
@app.route('/gacha')
@login_required
def gacha():
    selected_category = request.args.get("category", "")
    user_id = session.get('user_id')
    
    # セッションにチケット数があればそれを使用、なければDBから取得して同期
    tickets = session.get('tickets')
    if tickets is None and user_id:
        tickets = get_user_tickets(user_id)
        session['tickets'] = tickets
    elif tickets is None:
        tickets = 0
        session['tickets'] = 0
    
    return render_template("gacha.html", selected_category=selected_category, tickets=tickets)

# ランダムに1つのアイテムを表示するルート
@app.route('/result')
@login_required
def result():
    idea = None
    inheritance_count = 0
    gacha_count = 0
    idea_id = session.pop('last_gacha_idea_id', None)

    if idea_id:
        with get_connection() as con:
            idea = con.execute(
                "SELECT idea_id, title, detail, category, user_id, created_at FROM ideas WHERE idea_id = ?",
                (idea_id,)
            ).fetchone()
        
        # 統計情報を取得
        if idea:
            inheritance_count = get_inheritance_count(idea_id)
            gacha_count = get_gacha_count(idea_id)

    return render_template(
        "result.html", 
        item=idea, 
        inheritance_count=inheritance_count,
        gacha_count=gacha_count
    )

# ガチャを回して結果ページにリダイレクトするルート
@app.route('/spin')
@login_required
def spin():
    current_user_id = session.get('user_id')
    category = request.args.get('category')  # 💡カテゴリを取得

    # セッションのチケット数をチェック（セッションを優先）
    session_tickets = session.get('tickets')
    if session_tickets is None:
        # セッションに値がない場合のみDBから取得
        session_tickets = get_user_tickets(current_user_id)
        session['tickets'] = session_tickets
    
    if session_tickets < 1:
        flash('ガチャチケットが不足しています。アイデアを投稿するとチケットがもらえます。')
        return redirect(url_for('gacha', category=category))

    # ユーザーの会社コードを取得
    company_code = get_company_code_by_user_id(current_user_id) or 'test'
    
    item = fetch_random_item(
        exclude_user_id=current_user_id,
        category=category,
        company_code=company_code
    )

    if not item:
        session['last_gacha_idea_id'] = None
        flash('現在引けるアイデアがありません。')
        return redirect(url_for('result', category=category))

    idea_id = item[0]
    author_id = item[4]
    now = now_jst().strftime('%Y-%m-%d %H:%M:%S')

    # トランザクション内でチケットを消費してガチャ結果を保存
    with get_connection() as con:
        # トランザクション内でDBのチケット数を取得（セッションと整合性チェック用）
        try:
            ticket_row = con.execute(
                "SELECT ticket_count FROM mypage WHERE user_id = ?",
                (current_user_id,)
            ).fetchone()
        except Exception:
            try:
                ticket_row = con.execute(
                    "SELECT tickets FROM mypage WHERE user_id = ?",
                    (current_user_id,)
                ).fetchone()
            except Exception:
                ticket_row = (session_tickets,)
        
        db_tickets = ticket_row[0] if ticket_row else session_tickets
        
        # セッションとDBの値のうち、より小さい方を使用（安全側に倒す）
        current_tickets = min(session_tickets, db_tickets)
        
        if current_tickets < 1:
            session['tickets'] = 0
            flash('ガチャチケットが不足しています。アイデアを投稿するとチケットがもらえます。')
            return redirect(url_for('gacha', category=category))
        
        # チケットを1枚消費
        new_tickets = max(0, current_tickets - 1)
        try:
            con.execute(
                "UPDATE mypage SET ticket_count = ? WHERE user_id = ?",
                (new_tickets, current_user_id)
            )
        except Exception:
            try:
                con.execute(
                    "UPDATE mypage SET tickets = ? WHERE user_id = ?",
                    (new_tickets, current_user_id)
                )
            except Exception:
                pass
        
        # ガチャ結果を保存
        con.execute(
            "INSERT INTO gacha_result (result_id, user_id, idea_id, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), current_user_id, idea_id, now)
        )
        if author_id and author_id != current_user_id:
            con.execute(
                "INSERT INTO revival_notify (notify_id, idea_id, author_id, picker_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), idea_id, author_id, current_user_id, now)
            )
        
        # SQLiteの場合は明示的にコミット
        if not using_supabase():
            con.commit()
    
    # セッションのチケット数を更新（確実に反映されるように）
    session['tickets'] = new_tickets
    session['last_gacha_idea_id'] = idea_id
    session.modified = True  # セッションの変更を明示的にマーク

    # ✅ カテゴリをつけて結果ページにリダイレクト
    return redirect(url_for('result', category=category))

# ここまでガチャ機能

# ==================== アイデア融合機能 ====================

@app.route('/fusion')
@login_required
def fusion():
    """アイデア融合選択画面"""
    user_id = session.get('user_id')
    
    # セッションにチケット数があればそれを使用、なければDBから取得して同期
    tickets = session.get('tickets')
    if tickets is None and user_id:
        tickets = get_user_tickets(user_id)
        session['tickets'] = tickets
    elif tickets is None:
        tickets = 0
        session['tickets'] = 0
    
    # ユーザーの会社コードを取得
    company_code = get_company_code_by_user_id(user_id) or 'test'
    
    # 自分の投稿したアイデアを取得（削除されていないもののみ、同じ会社のもののみ）
    with get_connection() as con:
        if using_supabase():
            posted_ideas = con.execute(
                "SELECT idea_id, title, detail, category, created_at FROM ideas WHERE user_id = ? AND company_code = ? AND (is_deleted IS NULL OR is_deleted = FALSE) ORDER BY created_at DESC",
                (user_id, company_code)
            ).fetchall()
            
            # ガチャで獲得したアイデアを取得（削除されていないもののみ、同じ会社のもののみ）
            gacha_ideas = con.execute("""
                SELECT DISTINCT i.idea_id, i.title, i.detail, i.category, i.created_at
                FROM ideas i
                JOIN gacha_result gr ON i.idea_id = gr.idea_id
                WHERE gr.user_id = ? AND i.company_code = ? AND (i.is_deleted IS NULL OR i.is_deleted = FALSE)
                ORDER BY i.created_at DESC
            """, (user_id, company_code)).fetchall()
        else:
            posted_ideas = con.execute(
                "SELECT idea_id, title, detail, category, created_at FROM ideas WHERE user_id = ? AND company_code = ? AND (is_deleted IS NULL OR is_deleted = 0) ORDER BY created_at DESC",
                (user_id, company_code)
            ).fetchall()
            
            # ガチャで獲得したアイデアを取得（削除されていないもののみ、同じ会社のもののみ）
            gacha_ideas = con.execute("""
                SELECT DISTINCT i.idea_id, i.title, i.detail, i.category, i.created_at
                FROM ideas i
                JOIN gacha_result gr ON i.idea_id = gr.idea_id
                WHERE gr.user_id = ? AND i.company_code = ? AND (i.is_deleted IS NULL OR i.is_deleted = 0)
                ORDER BY i.created_at DESC
            """, (user_id, company_code)).fetchall()
    
    # アイデアを辞書形式に変換
    posted_ideas_list = []
    for row in posted_ideas:
        posted_ideas_list.append({
            'idea_id': row[0],
            'title': row[1],
            'detail': row[2],
            'category': row[3],
            'created_at': row[4],
            'source': 'posted'
        })
    
    gacha_ideas_list = []
    for row in gacha_ideas:
        gacha_ideas_list.append({
            'idea_id': row[0],
            'title': row[1],
            'detail': row[2],
            'category': row[3],
            'created_at': row[4],
            'source': 'gacha'
        })
    
    # 全てのアイデアを結合
    all_ideas = posted_ideas_list + gacha_ideas_list
    
    return render_template(
        'fusion.html',
        ideas=all_ideas,
        tickets=tickets
    )


@app.route('/fusion/execute', methods=['POST'])
@login_required
def fusion_execute():
    """アイデア融合実行"""
    user_id = session.get('user_id')
    
    # 選択されたアイデアIDを取得
    selected_idea_ids = request.form.getlist('idea_ids')
    
    # アイデア数チェック（2〜3個）
    if len(selected_idea_ids) < 2 or len(selected_idea_ids) > 3:
        flash('アイデアは2〜3個選択してください。')
        return redirect(url_for('fusion'))
    
    # チケット数チェック
    session_tickets = session.get('tickets')
    if session_tickets is None:
        session_tickets = get_user_tickets(user_id)
        session['tickets'] = session_tickets
    
    if session_tickets < 1:
        flash('ガチャチケットが不足しています。アイデアを投稿するとチケットがもらえます。')
        return redirect(url_for('fusion'))
    
    # ユーザーの会社コードを取得
    company_code = get_company_code_by_user_id(user_id) or 'test'
    
    # 選択されたアイデアの情報を取得（同じ会社のもののみ）
    with get_connection() as con:
        ideas_data = []
        for idea_id in selected_idea_ids:
            row = con.execute(
                "SELECT idea_id, title, detail, category FROM ideas WHERE idea_id = ? AND company_code = ?",
                (idea_id, company_code)
            ).fetchone()
            if row:
                ideas_data.append({
                    'idea_id': row[0],
                    'title': row[1],
                    'detail': row[2],
                    'category': row[3]
                })
    
    if len(ideas_data) != len(selected_idea_ids):
        flash('選択されたアイデアの一部が見つかりませんでした。')
        return redirect(url_for('fusion'))
    
    # AI融合を実行
    print(f"\n[アイデア融合] {len(ideas_data)}つのアイデアを融合します...")
    fused_result = fuse_ideas(ideas_data)
    
    if not fused_result or not fused_result.get('title'):
        flash('アイデアの融合に失敗しました。もう一度お試しください。')
        return redirect(url_for('fusion'))
    
    # チケットを消費して融合結果を保存
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    fusion_id = str(uuid.uuid4())
    
    with get_connection() as con:
        # トランザクション内でチケットを消費
        try:
            ticket_row = con.execute(
                "SELECT ticket_count FROM mypage WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        except Exception:
            try:
                ticket_row = con.execute(
                    "SELECT tickets FROM mypage WHERE user_id = ?",
                    (user_id,)
                ).fetchone()
            except Exception:
                ticket_row = (session_tickets,)
        
        db_tickets = ticket_row[0] if ticket_row else session_tickets
        current_tickets = min(session_tickets, db_tickets)
        
        if current_tickets < 1:
            session['tickets'] = 0
            flash('ガチャチケットが不足しています。')
            return redirect(url_for('fusion'))
        
        # チケットを1枚消費
        new_tickets = max(0, current_tickets - 1)
        try:
            con.execute(
                "UPDATE mypage SET ticket_count = ? WHERE user_id = ?",
                (new_tickets, user_id)
            )
        except Exception:
            try:
                con.execute(
                    "UPDATE mypage SET tickets = ? WHERE user_id = ?",
                    (new_tickets, user_id)
                )
            except Exception:
                pass
        
        # 融合履歴を保存
        parent_idea_id_1 = selected_idea_ids[0]
        parent_idea_id_2 = selected_idea_ids[1]
        parent_idea_id_3 = selected_idea_ids[2] if len(selected_idea_ids) > 2 else None
        
        con.execute("""
            INSERT INTO idea_fusion 
            (fusion_id, user_id, parent_idea_id_1, parent_idea_id_2, parent_idea_id_3, fused_idea_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fusion_id, user_id, parent_idea_id_1, parent_idea_id_2, parent_idea_id_3, None, now))
        
        if not using_supabase():
            con.commit()
    
    # セッションのチケット数を更新
    session['tickets'] = new_tickets
    session.modified = True
    
    # 融合結果をセッションに保存（結果ページで表示するため）
    session['last_fusion_result'] = {
        'fusion_id': fusion_id,
        'fused_title': fused_result['title'],
        'fused_detail': fused_result['detail'],
        'fused_category': fused_result['category'],
        'parent_ideas': ideas_data
    }
    
    return redirect(url_for('fusion_result', fusion_id=fusion_id))


@app.route('/fusion/result/<fusion_id>')
@login_required
def fusion_result(fusion_id):
    """融合結果表示"""
    user_id = session.get('user_id')
    
    # セッションから融合結果を取得
    fusion_result_data = session.pop('last_fusion_result', None)
    
    if not fusion_result_data:
        # セッションにない場合はDBから取得
        with get_connection() as con:
            fusion_row = con.execute(
                "SELECT fusion_id, user_id, parent_idea_id_1, parent_idea_id_2, parent_idea_id_3, fused_idea_id, created_at FROM idea_fusion WHERE fusion_id = ?",
                (fusion_id,)
            ).fetchone()
            
            if not fusion_row or fusion_row[1] != user_id:
                flash('融合結果が見つかりませんでした。')
                return redirect(url_for('fusion'))
            
            # 親アイデアの情報を取得
            parent_ideas = []
            for parent_id in [fusion_row[2], fusion_row[3], fusion_row[4]]:
                if parent_id:
                    idea_row = con.execute(
                        "SELECT idea_id, title, detail, category FROM ideas WHERE idea_id = ?",
                        (parent_id,)
                    ).fetchone()
                    if idea_row:
                        parent_ideas.append({
                            'idea_id': idea_row[0],
                            'title': idea_row[1],
                            'detail': idea_row[2],
                            'category': idea_row[3]
                        })
            
            # 融合結果のアイデアが既に投稿されている場合
            fused_idea = None
            if fusion_row[5]:
                fused_row = con.execute(
                    "SELECT idea_id, title, detail, category FROM ideas WHERE idea_id = ?",
                    (fusion_row[5],)
                ).fetchone()
                if fused_row:
                    fused_idea = {
                        'idea_id': fused_row[0],
                        'title': fused_row[1],
                        'detail': fused_row[2],
                        'category': fused_row[3]
                    }
            
            fusion_result_data = {
                'fusion_id': fusion_id,
                'parent_ideas': parent_ideas,
                'fused_idea': fused_idea,
                'created_at': fusion_row[6]
            }
    
    return render_template(
        'fusion_result.html',
        fusion_id=fusion_id,
        fusion_result=fusion_result_data
    )


@app.route('/fusion/post', methods=['POST'])
@login_required
def fusion_post():
    """融合結果を投稿として保存"""
    user_id = session.get('user_id')
    fusion_id = request.form.get('fusion_id')
    title = request.form.get('title', '').strip()
    detail = request.form.get('detail', '').strip()
    category = request.form.get('category', '').strip()
    
    if not fusion_id or not title or not detail or not category:
        flash('すべての項目を入力してください。')
        return redirect(url_for('fusion_result', fusion_id=fusion_id))
    
    # 融合履歴を確認
    with get_connection() as con:
        fusion_row = con.execute(
            "SELECT user_id, fused_idea_id FROM idea_fusion WHERE fusion_id = ?",
            (fusion_id,)
        ).fetchone()
        
        if not fusion_row or fusion_row[0] != user_id:
            flash('融合結果が見つかりませんでした。')
            return redirect(url_for('fusion'))
        
        # 既に投稿されている場合は更新、そうでなければ新規作成
        if fusion_row[1]:
            # 既存のアイデアを更新
            con.execute(
                "UPDATE ideas SET title = ?, detail = ?, category = ? WHERE idea_id = ?",
                (title, detail, category, fusion_row[1])
            )
            idea_id = fusion_row[1]
        else:
            # 新規アイデアを作成
            idea_id = str(uuid.uuid4())
            created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # ユーザーの会社コードを取得
            company_code = get_company_code_by_user_id(user_id) or 'test'
            con.execute(
                "INSERT INTO ideas (idea_id, title, detail, category, user_id, created_at, inheritance_flag, company_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (idea_id, title, detail, category, user_id, created_at, False, company_code)
            )
            
            # 融合履歴を更新
            con.execute(
                "UPDATE idea_fusion SET fused_idea_id = ? WHERE fusion_id = ?",
                (idea_id, fusion_id)
            )
        
        if not using_supabase():
            con.commit()
    
    flash('融合結果を投稿しました！')
    return redirect(url_for('post_view', idea_id=idea_id))


@app.route('/fusion/<fusion_id>/delete', methods=['POST'])
@login_required
def delete_fusion(fusion_id):
    """融合履歴を削除"""
    user_id = session.get('user_id')
    
    with get_connection() as con:
        # 融合履歴がユーザーのものか確認
        fusion_row = con.execute(
            "SELECT user_id FROM idea_fusion WHERE fusion_id = ?",
            (fusion_id,)
        ).fetchone()
        
        if not fusion_row or fusion_row[0] != user_id:
            flash('この融合履歴を削除できません。')
            return redirect(url_for('mypage'))
        
        # 融合履歴を削除（fused_idea_idがある場合でも、ideasテーブルのアイデアは削除しない）
        con.execute(
            "DELETE FROM idea_fusion WHERE fusion_id = ?",
            (fusion_id,)
        )
        
        if not using_supabase():
            con.commit()
    
    flash('融合履歴を削除しました。')
    return redirect(url_for('mypage'))

# マイページ
@app.route('/mypage/update', methods=['POST'])
def update_profile():
    user_id = get_current_user_id()

    nickname = request.form.get('nickname', '').strip()
    remove_icon = request.form.get('remove_icon') == '1'
    icon_file = request.files.get('icon')

    errors = []

    if not nickname:
        errors.append('ニックネームを入力してください。')
    elif len(nickname) > MAX_NICKNAME_LENGTH:
        errors.append(f'ニックネームは{MAX_NICKNAME_LENGTH}文字以内で入力してください。')

    icon_candidate = None
    if icon_file and icon_file.filename:
        filename = secure_filename(icon_file.filename)
        _, ext = os.path.splitext(filename)
        ext = ext.lower()
        if ext not in ALLOWED_ICON_EXTENSIONS:
            errors.append('アイコン画像はPNG/JPG/GIF形式のみアップロードできます。')
        else:
            icon_candidate = (icon_file, ext)

    if errors:
        for message in errors:
            flash(message)
        return redirect(url_for('mypage'))

    with get_connection() as con:
        cur = con.cursor()
        current_row = cur.execute(
            "SELECT icon_path FROM mypage WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if not current_row:
            flash('ユーザー情報が見つかりません。')
            return redirect(url_for('mypage'))

        current_icon_path = current_row[0]
        new_icon_path = current_icon_path

        if icon_candidate:
            new_icon_path = store_icon_file(icon_candidate[0], icon_candidate[1])
        elif remove_icon:
            new_icon_path = None

        cur.execute(
            "UPDATE mypage SET nickname = ?, icon_path = ? WHERE user_id = ?",
            (nickname, new_icon_path, user_id)
        )
        con.commit()

    if (icon_candidate or remove_icon) and current_icon_path and current_icon_path != new_icon_path:
        delete_icon_file(current_icon_path)

    flash('プロフィールを更新しました。')
    return redirect(url_for('mypage'))


@app.route('/mypage')
@login_required
def mypage():
    user_id = session['user_id']

    with get_connection() as con:
        user_row = con.execute(
            "SELECT user_id, nickname, email, icon_path, created_at FROM mypage WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if not user_row:
            session.clear()
            return redirect(url_for('login'))

        user = {
            'user_id': user_row[0],
            'nickname': user_row[1],
            'email': user_row[2],
            'icon_path': user_row[3],
            'created_at': user_row[4]
        }

        # 自分の投稿一覧（削除済みも含む、削除済みフラグ付き）
        idea_rows = con.execute(
            "SELECT idea_id, title, detail, category, created_at, is_deleted FROM ideas WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()

        # ガチャ結果（削除済みアイデアも含む、削除済みフラグ付き）
        gacha_rows = con.execute("""
            SELECT gr.result_id, gr.created_at, i.idea_id, i.title, i.detail, i.category, i.is_deleted
            FROM gacha_result gr
            JOIN ideas i ON gr.idea_id = i.idea_id
            WHERE gr.user_id = ?
            ORDER BY gr.created_at DESC
        """, (user_id,)).fetchall()

        revival_rows = con.execute("""
            SELECT 
                rn.notify_id,
                rn.created_at,
                rn.picker_id,
                picker.nickname,
                picker.icon_path,
                i.title,
                i.category
            FROM revival_notify rn
            JOIN ideas i ON rn.idea_id = i.idea_id
            LEFT JOIN mypage picker ON rn.picker_id = picker.user_id
            WHERE rn.author_id = ?
            ORDER BY rn.created_at DESC
        """, (user_id,)).fetchall()

        # 継承一覧（削除済みアイデアも含む、削除済みフラグ付き）
        inheritance_rows = con.execute("""
            SELECT 
                ii.inheritance_id,
                ii.parent_idea_id,
                ii.child_idea_id,
                ii.add_point,
                ii.add_detail,
                ii.created_at,
                parent_i.title as parent_title,
                parent_i.detail as parent_detail,
                parent_i.category as parent_category,
                parent_u.nickname as parent_nickname,
                child_i.title as child_title,
                child_i.detail as child_detail,
                child_i.category as child_category,
                parent_i.is_deleted as parent_is_deleted
            FROM idea_inheritance ii
            LEFT JOIN ideas parent_i ON ii.parent_idea_id = parent_i.idea_id
            LEFT JOIN mypage parent_u ON ii.parent_user_id = parent_u.user_id
            LEFT JOIN ideas child_i ON ii.child_idea_id = child_i.idea_id
            WHERE ii.child_user_id = ?
            ORDER BY ii.created_at DESC
        """, (user_id,)).fetchall()

        # 融合履歴を取得
        fusion_rows = con.execute("""
            SELECT 
                if.fusion_id,
                if.parent_idea_id_1,
                if.parent_idea_id_2,
                if.parent_idea_id_3,
                if.fused_idea_id,
                if.created_at,
                fused_i.title as fused_title,
                fused_i.detail as fused_detail,
                fused_i.category as fused_category
            FROM idea_fusion if
            LEFT JOIN ideas fused_i ON if.fused_idea_id = fused_i.idea_id
            WHERE if.user_id = ?
            ORDER BY if.created_at DESC
        """, (user_id,)).fetchall()

    # 自分の投稿一覧（削除済みは非表示）
    ideas = []
    for row in idea_rows:
        is_deleted = bool(row[5]) if row[5] is not None else False
        if not is_deleted:  # 削除済みでないものだけ表示
            ideas.append({
                'idea_id': row[0],
                'title': row[1],
                'detail': row[2],
                'category': row[3],
                'created_at': row[4],
                'is_deleted': is_deleted
            })

    # ガチャ結果（削除済みも表示）
    gacha_results = []
    for row in gacha_rows:
        is_deleted = bool(row[6]) if row[6] is not None else False
        gacha_results.append({
            'result_id': row[0],
            'created_at': row[1],
            'idea_id': row[2],
            'idea_title': row[3],
            'detail': row[4],
            'category': row[5],
            'is_deleted': is_deleted
        })

    revival_notifications = []
    for row in revival_rows:
        revival_notifications.append({
            'notify_id': row[0],
            'created_at': row[1],
            'picker_id': row[2],
            'picker_nickname': row[3] if row[3] else '不明なユーザー',
            'picker_icon_path': row[4],
            'idea_title': row[5],
            'category': row[6]
        })

    # 継承一覧（削除済みも表示）
    inheritance_items = []
    for row in inheritance_rows:
        parent_is_deleted = bool(row[13]) if len(row) > 13 and row[13] is not None else False
        inheritance_items.append({
            'inheritance_id': row[0],
            'parent_idea_id': row[1],
            'child_idea_id': row[2],
            'add_point': row[3],
            'add_detail': row[4],
            'created_at': row[5],
            'parent_title': row[6],
            'parent_detail': row[7],
            'parent_category': row[8],
            'parent_nickname': row[9] if row[9] else '不明なユーザー',
            'child_title': row[10],
            'child_detail': row[11],
            'child_category': row[12],
            'parent_is_deleted': parent_is_deleted
        })

    # 融合履歴
    fusion_items = []
    for row in fusion_rows:
        fusion_items.append({
            'fusion_id': row[0],
            'parent_idea_id_1': row[1],
            'parent_idea_id_2': row[2],
            'parent_idea_id_3': row[3],
            'fused_idea_id': row[4],
            'created_at': row[5],
            'fused_title': row[6],
            'fused_detail': row[7],
            'fused_category': row[8]
        })

    return render_template(
        'mypage.html',
        user=user,
        ideas=ideas,
        gacha_results=gacha_results,
        revival_notifications=revival_notifications,
        inheritance_items=inheritance_items,
        fusion_items=fusion_items
    )


@app.route('/notifications/mark-read', methods=['POST'])
@login_required
def mark_notifications_read():
    """通知を既読状態にする（通知パネルを開いたときに呼ばれる）"""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    with get_connection() as con:
        # 該当ユーザーの全未読通知を既読状態に更新
        now = now_jst().strftime('%Y-%m-%d %H:%M:%S')
        con.execute("""
            UPDATE revival_notify 
            SET read_at = ? 
            WHERE author_id = ? AND read_at IS NULL
        """, (now, user_id))
        
        if not using_supabase():
            con.commit()
        
        # 更新後の未読通知数を取得
        unread_count_row = con.execute("""
            SELECT COUNT(*) 
            FROM revival_notify 
            WHERE author_id = ? AND read_at IS NULL
        """, (user_id,)).fetchone()
        
        unread_count = unread_count_row[0] if unread_count_row else 0
    
    return jsonify({'success': True, 'unread_count': unread_count}), 200


@app.route('/ranking')
@login_required
def ranking():
    """投稿数ランキングページ"""
    user_id = session.get('user_id')
    # ユーザーの会社コードを取得
    company_code = get_company_code_by_user_id(user_id) or 'test'
    
    # 期間パラメータを取得（デフォルトは総合）
    period = request.args.get('period', 'all')
    valid_periods = ['all', 'weekly', 'monthly', 'yearly']
    if period not in valid_periods:
        period = 'all'
    
    # 期間別ランキングを取得（制限なし、会社コードでフィルタリング）
    rankings_by_period = {}
    inheritance_rankings_by_period = {}
    for p in valid_periods:
        rankings_by_period[p] = get_ranking_by_period(p, limit=1000, company_code=company_code)
        inheritance_rankings_by_period[p] = get_inheritance_ranking_by_period(p, limit=1000, company_code=company_code)
    
    # 現在選択中のランキング
    current_rankings = rankings_by_period[period]
    current_inheritance_rankings = inheritance_rankings_by_period[period]
    
    current_user_id = session.get('user_id')
    
    # 各期間の順位を計算
    user_ranks_by_period = {}
    for p in valid_periods:
        post_rank = None
        inheritance_rank = None
        
        # 投稿数の順位を取得
        for ranking_item in rankings_by_period[p]:
            if ranking_item['user_id'] == current_user_id:
                post_rank = ranking_item['rank']
                break
        
        # 継承数の順位を取得
        for ranking_item in inheritance_rankings_by_period[p]:
            if ranking_item['user_id'] == current_user_id:
                inheritance_rank = ranking_item['rank']
                break
        
        user_ranks_by_period[p] = {
            'post_rank': post_rank,
            'inheritance_rank': inheritance_rank
        }
    
    # 現在選択中の順位
    current_user_post_rank = user_ranks_by_period[period]['post_rank']
    current_user_inheritance_rank = user_ranks_by_period[period]['inheritance_rank']

    return render_template(
        'ranking.html',
        rankings=current_rankings,
        rankings_by_period=rankings_by_period,
        inheritance_rankings_by_period=inheritance_rankings_by_period,
        current_period=period,
        current_user_id=current_user_id,
        current_user_post_rank=current_user_post_rank,
        current_user_inheritance_rank=current_user_inheritance_rank,
        user_ranks_by_period=user_ranks_by_period
    )


# ==================== イベント関連のルーティング ====================

@app.route('/events')
@login_required
def events():
    """イベント一覧/参加/開催ページ"""
    update_event_statuses()  # イベント状態を更新
    user_id = session['user_id']
    user_name = session.get('nickname', 'ユーザー')
    
    # 日時パース用の関数をインポート
    from relay.db import _parse_datetime
    
    # 参加中のイベント（全て）と公開のイベント（参加していないもののみ）を取得
    all_events = get_all_events()
    public_events = get_public_events()
    
    # 参加中のイベントと公開イベントを分ける
    my_events = []  # 参加中のイベント
    other_events = []  # 公開されているが参加していないイベント
    
    # 参加中のイベントを取得
    for event_row in all_events:
        event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public = event_row
        if is_event_participant(event_id, user_id):
            # 日時が文字列の場合はdatetimeオブジェクトに変換
            start_date = _parse_datetime(start_date)
            end_date = _parse_datetime(end_date)
            created_at = _parse_datetime(created_at)
            
            # 開催者情報を取得
            creator_row = get_user_by_user_id(created_by)
            creator_nickname = creator_row[1] if creator_row else '不明'
            
            my_events.append({
                'event_id': event_id,
                'name': name,
                'start_date': start_date,
                'end_date': end_date,
                'status': status,
                'is_participant': True,
                'created_by': created_by,
                'creator_nickname': creator_nickname,
                'is_public': is_public,
                'created_at': created_at
            })
    
    # 公開されているが参加していないイベントを取得
    for event_row in public_events:
        event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public = event_row
        if not is_event_participant(event_id, user_id):
            # 日時が文字列の場合はdatetimeオブジェクトに変換
            start_date = _parse_datetime(start_date)
            end_date = _parse_datetime(end_date)
            created_at = _parse_datetime(created_at)
            
            # 開催者情報を取得
            creator_row = get_user_by_user_id(created_by)
            creator_nickname = creator_row[1] if creator_row else '不明'
            
            other_events.append({
                'event_id': event_id,
                'name': name,
                'start_date': start_date,
                'end_date': end_date,
                'status': status,
                'is_participant': False,
                'created_by': created_by,
                'creator_nickname': creator_nickname,
                'is_public': is_public,
                'created_at': created_at
            })
    
    return render_template(
        'events.html',
        my_events=my_events,
        other_events=other_events,
        user_name=user_name
    )


@app.route('/events/create', methods=['POST'])
@login_required
def event_create():
    """イベントを作成"""
    user_id = session['user_id']
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '').strip()
    start_date_str = request.form.get('start_date', '').strip()
    end_date_str = request.form.get('end_date', '').strip()
    is_public = request.form.get('is_public') == '1'  # チェックボックスの値
    
    if not name or not password or not start_date_str or not end_date_str:
        flash('すべての項目を入力してください。')
        return redirect(url_for('events'))
    
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%dT%H:%M')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%dT%H:%M')
    except ValueError:
        flash('日時の形式が正しくありません。')
        return redirect(url_for('events'))
    
    if end_date <= start_date:
        flash('終了日時は開始日時より後である必要があります。')
        return redirect(url_for('events'))
    
    event_id = uuid.uuid4().hex
    password_hash = generate_password_hash(password)
    
    create_event(event_id, name, password_hash, start_date, end_date, user_id, is_public)
    
    # 作成者は自動的に参加
    join_event(event_id, user_id)
    
    flash('イベントを作成しました！')
    return redirect(url_for('event_detail', event_id=event_id))


@app.route('/events/join', methods=['POST'])
@login_required
def event_join():
    """イベントに参加（パスワード認証）"""
    user_id = session['user_id']
    password = request.form.get('password', '').strip()
    event_id = request.form.get('event_id', '').strip()
    
    # イベントIDが指定されている場合（既に参加済みの場合の再入場、またはイベント選択から参加）
    if event_id:
        event_row = get_event(event_id)
        if not event_row:
            flash('イベントが見つかりません。')
            return redirect(url_for('events'))
        
        event_id_check, name, password_hash, start_date, end_date, created_at, created_by, status, is_public = event_row
        
        # 既に参加している場合はそのまま入る（パスワード不要）
        if is_event_participant(event_id, user_id):
            flash('イベントページに移動しました。')
            return redirect(url_for('event_detail', event_id=event_id))
        
        # パスワードが提供されている場合はチェックして参加
        if password:
            if check_password_hash(password_hash, password):
                # 参加処理
                if join_event(event_id, user_id):
                    flash(f'{name} に参加しました！')
                    return redirect(url_for('event_detail', event_id=event_id))
                else:
                    flash('参加に失敗しました。')
                    return redirect(url_for('events'))
            else:
                flash('パスワードが正しくありません。')
                return redirect(url_for('events'))
        else:
            flash('パスワードを入力してください。')
            return redirect(url_for('events'))
    
    if not password:
        flash('パスワードを入力してください。')
        return redirect(url_for('events'))
    
    # 全てのイベントをチェック
    all_events = get_all_events()
    matching_events = []
    
    for event_row in all_events:
        event_id_check, name, password_hash, start_date, end_date, created_at, created_by, status, is_public = event_row
        
        # パスワードをチェック
        if check_password_hash(password_hash, password):
            # 既に参加しているイベントは除外
            if not is_event_participant(event_id_check, user_id):
                matching_events.append({
                    'event_id': event_id_check,
                    'name': name,
                    'status': status
                })
    
    # パスワードが一致するイベントがない場合
    if not matching_events:
        flash('パスワードが正しくありません。または既に参加済みです。')
        return redirect(url_for('events'))
    
    # パスワードが一致するイベントが1つの場合、自動参加
    if len(matching_events) == 1:
        event = matching_events[0]
        event_id = event['event_id']
        event_name = event['name']
        
        # 既に参加している場合はそのまま入る
        if is_event_participant(event_id, user_id):
            flash(f'{event_name} に移動しました。')
            return redirect(url_for('event_detail', event_id=event_id))
        
        # 参加処理
        if join_event(event_id, user_id):
            flash(f'{event_name} に参加しました！')
            return redirect(url_for('event_detail', event_id=event_id))
        else:
            flash('参加に失敗しました。')
            return redirect(url_for('events'))
    
    # パスワードが一致するイベントが複数ある場合、選択画面を表示
    # events.htmlで選択フォームを表示するように修正が必要
    flash(f'パスワードが一致するイベントが{len(matching_events)}つ見つかりました。イベントを選択してください。')
    # TODO: イベント選択画面へのリダイレクトまたは、events.htmlで選択UIを表示
    return redirect(url_for('events'))


@app.route('/events/<event_id>')
@login_required
def event_detail(event_id):
    """イベント詳細ページ"""
    update_event_statuses()  # イベント状態を更新
    user_id = session['user_id']
    user_name = session.get('nickname', 'ユーザー')
    
    event_row = get_event(event_id)
    if not event_row:
        flash('イベントが見つかりません。')
        return redirect(url_for('events'))
    
    event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public = event_row
    
    # 日時が文字列の場合はdatetimeオブジェクトに変換
    from relay.db import _parse_datetime
    created_at = _parse_datetime(created_at)
    start_date = _parse_datetime(start_date)
    end_date = _parse_datetime(end_date)
    
    # 開催者情報を取得
    creator_row = get_user_by_user_id(created_by)
    creator_nickname = creator_row[1] if creator_row else '不明'
    is_creator = (user_id == created_by)
    
    # 参加チェック
    if not is_event_participant(event_id, user_id):
        flash('このイベントに参加していません。')
        return redirect(url_for('events'))
    
    # イベントが終了している場合は終了ページへ
    if status == 'ended':
        return redirect(url_for('event_ended', event_id=event_id))
    
    # 参加者一覧
    participants = []
    for p_row in get_event_participants(event_id):
        user_id_p, joined_at, nickname, icon_path = p_row
        participants.append({
            'user_id': user_id_p,
            'nickname': nickname,
            'icon_path': icon_path,
            'joined_at': joined_at
        })
    
    # イベント中のアイデア
    ideas = get_event_ideas(event_id)
    
    # ランキング
    rankings = []
    for rank_row in get_event_ranking(event_id):
        user_id_r, nickname_r, icon_path_r, post_count = rank_row
        rankings.append({
            'user_id': user_id_r,
            'nickname': nickname_r,
            'icon_path': icon_path_r,
            'post_count': post_count
        })
    
    # datetime-localフォーマットに変換（モーダル用）
    start_date_str = start_date.strftime('%Y-%m-%dT%H:%M')
    end_date_str = end_date.strftime('%Y-%m-%dT%H:%M')
    
    event = {
        'event_id': event_id,
        'name': name,
        'start_date': start_date,
        'end_date': end_date,
        'start_date_str': start_date_str,
        'end_date_str': end_date_str,
        'status': status,
        'created_by': created_by,
        'creator_nickname': creator_nickname,
        'created_at': created_at,
        'is_public': is_public,
        'is_creator': is_creator
    }
    
    return render_template(
        'event_detail.html',
        event=event,
        participants=participants,
        rankings=rankings,
        participant_count=len(participants),
        idea_count=len(ideas),
        user_name=user_name
    )


@app.route('/events/<event_id>/ended')
@login_required
def event_ended(event_id):
    """イベント終了ページ"""
    update_event_statuses()  # イベント状態を更新
    user_id = session['user_id']
    user_name = session.get('nickname', 'ユーザー')
    
    event_row = get_event(event_id)
    if not event_row:
        flash('イベントが見つかりません。')
        return redirect(url_for('events'))
    
    event_id, name, password_hash, start_date, end_date, created_at, created_by, status, is_public = event_row
    
    # 日時が文字列の場合はdatetimeオブジェクトに変換
    from relay.db import _parse_datetime
    created_at = _parse_datetime(created_at)
    start_date = _parse_datetime(start_date)
    end_date = _parse_datetime(end_date)
    
    # 開催者情報を取得
    creator_row = get_user_by_user_id(created_by)
    creator_nickname = creator_row[1] if creator_row else '不明'
    is_creator = (user_id == created_by)
    
    # 参加チェック
    if not is_event_participant(event_id, user_id):
        flash('このイベントに参加していません。')
        return redirect(url_for('events'))
    
    # 参加者一覧
    participants = []
    for p_row in get_event_participants(event_id):
        user_id_p, joined_at, nickname, icon_path = p_row
        participants.append({
            'user_id': user_id_p,
            'nickname': nickname,
            'icon_path': icon_path,
            'joined_at': joined_at
        })
    
    # イベント中のアイデア
    ideas = get_event_ideas(event_id)
    
    # ランキング（最終結果）
    rankings = []
    for rank_row in get_event_ranking(event_id):
        user_id_r, nickname_r, icon_path_r, post_count = rank_row
        rankings.append({
            'user_id': user_id_r,
            'nickname': nickname_r,
            'icon_path': icon_path_r,
            'post_count': post_count
        })
    
    event = {
        'event_id': event_id,
        'name': name,
        'start_date': start_date,
        'end_date': end_date,
        'status': status,
        'created_by': created_by,
        'creator_nickname': creator_nickname,
        'created_at': created_at,
        'is_public': is_public,
        'is_creator': is_creator
    }
    
    return render_template(
        'event_ended.html',
        event=event,
        participants=participants,
        rankings=rankings,
        participant_count=len(participants),
        idea_count=len(ideas),
        user_name=user_name
    )


@app.route('/events/<event_id>/edit', methods=['GET', 'POST'])
@login_required
def event_edit(event_id):
    """イベント編集ページ（開催者のみ）"""
    user_id = session['user_id']
    event_row = get_event(event_id)
    
    if not event_row:
        flash('イベントが見つかりません。')
        return redirect(url_for('events'))
    
    event_id_check, name, password_hash, start_date, end_date, created_at, created_by, status, is_public = event_row
    
    # 開催者チェック
    if user_id != created_by:
        flash('イベントの編集は開催者のみ可能です。')
        return redirect(url_for('event_detail', event_id=event_id))
    
    if request.method == 'POST':
        new_name = request.form.get('name', '').strip()
        new_start_date_str = request.form.get('start_date', '').strip()
        new_end_date_str = request.form.get('end_date', '').strip()
        new_is_public = request.form.get('is_public') == '1'
        
        if not new_name or not new_start_date_str or not new_end_date_str:
            flash('すべての項目を入力してください。')
            return redirect(url_for('event_edit', event_id=event_id))
        
        try:
            new_start_date = datetime.strptime(new_start_date_str, '%Y-%m-%dT%H:%M')
            new_end_date = datetime.strptime(new_end_date_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('日時の形式が正しくありません。')
            return redirect(url_for('event_edit', event_id=event_id))
        
        if new_end_date <= new_start_date:
            flash('終了日時は開始日時より後である必要があります。')
            return redirect(url_for('event_edit', event_id=event_id))
        
        # イベント情報を更新
        update_event(event_id, name=new_name, start_date=new_start_date, end_date=new_end_date, is_public=new_is_public)
        
        flash('イベント情報を更新しました。')
        return redirect(url_for('event_detail', event_id=event_id))
    
    # GETリクエストの場合、編集フォームを表示
    user_name = session.get('nickname', 'ユーザー')
    
    # 日時が文字列の場合はdatetimeオブジェクトに変換
    if isinstance(start_date, str):
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                start_date = now_jst()
    if isinstance(end_date, str):
        try:
            end_date = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                end_date = now_jst()
    
    # datetime-localフォーマットに変換
    start_date_str = start_date.strftime('%Y-%m-%dT%H:%M')
    end_date_str = end_date.strftime('%Y-%m-%dT%H:%M')
    
    event = {
        'event_id': event_id,
        'name': name,
        'start_date': start_date,
        'end_date': end_date,
        'start_date_str': start_date_str,
        'end_date_str': end_date_str,
        'is_public': bool(is_public)
    }
    
    return render_template('event_edit.html', event=event, user_name=user_name)


@app.route('/events/<event_id>/delete', methods=['POST'])
@login_required
def event_delete(event_id):
    """イベント削除（開催者のみ）"""
    user_id = session['user_id']
    event_row = get_event(event_id)
    
    if not event_row:
        flash('イベントが見つかりません。')
        return redirect(url_for('events'))
    
    event_id_check, name, password_hash, start_date, end_date, created_at, created_by, status, is_public = event_row
    
    # 開催者チェック
    if user_id != created_by:
        flash('イベントの削除は開催者のみ可能です。')
        return redirect(url_for('event_detail', event_id=event_id))
    
    # イベントを削除
    delete_event(event_id)
    
    flash(f'イベント「{name}」を削除しました。')
    return redirect(url_for('events'))


# ==================== 管理者機能 ====================

@app.route('/admin/companies')
@admin_required
def admin_companies():
    """管理者用会社コード管理ページ"""
    companies = get_all_companies()
    companies_list = []
    for row in companies:
        companies_list.append({
            'company_code': row[0],
            'company_name': row[1],
            'created_at': row[2],
            'created_by': row[3]
        })
    
    return render_template('admin/companies.html', companies=companies_list)


@app.route('/admin/companies/create', methods=['POST'])
@admin_required
def admin_create_company():
    """会社コードを作成"""
    user_id = session['user_id']
    company_code = request.form.get('company_code', '').strip()
    company_name = request.form.get('company_name', '').strip() or None
    
    if not company_code:
        flash('会社コードを入力してください。')
        return redirect(url_for('admin_companies'))
    
    # 会社コードの重複チェック
    existing_company = get_company(company_code)
    if existing_company:
        flash('この会社コードは既に登録されています。')
        return redirect(url_for('admin_companies'))
    
    try:
        create_company(company_code, company_name, user_id)
        flash(f'会社コード「{company_code}」を作成しました。')
    except Exception as e:
        flash(f'会社コードの作成に失敗しました: {str(e)}')
    
    return redirect(url_for('admin_companies'))
