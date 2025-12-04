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
)
from relay.content_moderation import check_content
import uuid
from datetime import datetime
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
MAX_POST_LENGTH = 280


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
    length = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ('F', 'W'):
            length += 2
        else:
            length += 1
    return length


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            next_url = request.url
            return redirect(url_for('login', next=next_url))
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
        now = datetime.now()
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
    
    # 期間別ランキングを取得（各期間トップ5）
    rankings_by_period = {}
    for p in valid_periods:
        rankings_by_period[p] = get_ranking_by_period(p, limit=5)
    
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
    
    with get_connection() as con:
        idea_row = con.execute(
            "SELECT idea_id, title, detail, category, user_id, created_at FROM ideas WHERE idea_id = ?",
            (idea_id,)
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
        flash('追加したポイントは全角32文字（半角64文字）以内で入力してください。')
        return redirect(url_for('inheritance_form', idea_id=idea_id))

    with get_connection() as con:
        # 既存の継承レコードがあるか確認
        existing = con.execute(
            "SELECT inheritance_id FROM idea_inheritance WHERE parent_idea_id = ? AND child_user_id = ? AND child_idea_id IS NULL",
            (parent_idea_id, user_id)
        ).fetchone()

        inheritance_id = str(uuid.uuid4())
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

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
        flash('追加したポイントは全角32文字（半角64文字）以内で入力してください。')
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
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
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
            return redirect(url_for('inheritance_form', idea_id=idea_id))
        
        if is_thin_content:
            print(f"[継承投稿処理] 内容が薄い投稿として拒否されました: {reason}")
            flash(f'内容が不十分なため、投稿できませんでした。{reason if reason else "もう少し詳しく説明してください。"}')
            return redirect(url_for('inheritance_form', idea_id=idea_id))
        
        print("[継承投稿処理] AI判定を通過しました。投稿を保存します...")

        # アイデアを登録
        con.execute(
            "INSERT INTO ideas (idea_id, title, detail, category, user_id, created_at, inheritance_flag) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (child_idea_id, child_title, add_detail, child_category, user_id, created_at, True)
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
    now = datetime.now()
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
            WHERE ii.inheritance_id = ?
        """, (inheritance_id,)).fetchone()
        
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


@app.route('/post', methods=['POST'])
def post():
    if 'user_id' not in session:
        return redirect(url_for('login', next=url_for('form')))

    title = request.form['title']
    detail = request.form['detail']
    category = request.form['category']

    if calculate_text_length(title) > MAX_TITLE_LENGTH:
        flash(
            f'タイトルは全角{MAX_TITLE_LENGTH // 2}文字（半角{MAX_TITLE_LENGTH}文字）以内で入力してください。'
        )
        return redirect(url_for('form'))

    if calculate_text_length(detail) > MAX_POST_LENGTH:
        flash(
            f'アイデアの詳細は全角{MAX_POST_LENGTH // 2}文字（半角{MAX_POST_LENGTH}文字）以内で入力してください。'
        )
        return redirect(url_for('form'))

    # AI判定を実行
    print("\n[投稿処理] AI判定を開始します...")
    is_inappropriate, is_thin_content, reason = check_content(title, detail, category)
    
    if is_inappropriate:
        print(f"[投稿処理] 不適切な投稿として拒否されました: {reason}")
        flash(f'不適切な内容が含まれているため、投稿できませんでした。{reason if reason else ""}')
        return redirect(url_for('form'))
    
    if is_thin_content:
        print(f"[投稿処理] 内容が薄い投稿として拒否されました: {reason}")
        flash(f'内容が不十分なため、投稿できませんでした。{reason if reason else "もう少し詳しく説明してください。"}')
        return redirect(url_for('form'))
    
    print("[投稿処理] AI判定を通過しました。投稿を保存します...")

    with get_connection() as con:
        idea_id = str(uuid.uuid4())
        user_id = session['user_id']
        created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # inheritance_flagはデフォルトでFalse（SQLiteの場合は0）
        inheritance_flag = False
        con.execute(
            "INSERT INTO ideas (idea_id, title, detail, category, user_id, created_at, inheritance_flag) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [idea_id, title, detail, category, user_id, created_at, inheritance_flag]
        )
        if not using_supabase():
            con.commit()
    
    # イベント中に投稿した場合、イベントに関連付ける
    active_events = get_active_events()
    now = datetime.now()
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
            "SELECT user_id FROM ideas WHERE idea_id = ?",
            (idea_id,)
        ).fetchone()

        if not idea_row or idea_row[0] != user_id:
            flash('指定した投稿を削除できません。')
            return redirect(url_for('mypage'))

        cur.execute("DELETE FROM gacha_result WHERE idea_id = ?", (idea_id,))
        cur.execute("DELETE FROM ideas WHERE idea_id = ?", (idea_id,))
        con.commit()

    flash('投稿を削除しました。')
    return redirect(url_for('mypage'))


@app.route('/posts/<idea_id>')
@login_required
def post_view(idea_id):
    user_id = session['user_id']

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
            WHERE i.idea_id = ?
            """,
            (idea_id,)
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
        'email': request.form.get('email', '').strip() if request.method == 'POST' else ''
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
            created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            insert_user(user_id, nickname, password_hash, email, icon_path, created_at)
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

    item = fetch_random_item(
        exclude_user_id=current_user_id,
        category=category
    )

    if not item:
        session['last_gacha_idea_id'] = None
        flash('現在引けるアイデアがありません。')
        return redirect(url_for('result', category=category))

    idea_id = item[0]
    author_id = item[4]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

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

        idea_rows = con.execute(
            "SELECT idea_id, title, detail, category, created_at FROM ideas WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()

        gacha_rows = con.execute("""
            SELECT gr.result_id, gr.created_at, i.idea_id, i.title, i.detail, i.category
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
                child_i.category as child_category
            FROM idea_inheritance ii
            LEFT JOIN ideas parent_i ON ii.parent_idea_id = parent_i.idea_id
            LEFT JOIN mypage parent_u ON ii.parent_user_id = parent_u.user_id
            LEFT JOIN ideas child_i ON ii.child_idea_id = child_i.idea_id
            WHERE ii.child_user_id = ?
            ORDER BY ii.created_at DESC
        """, (user_id,)).fetchall()

    ideas = []
    for row in idea_rows:
        ideas.append({
            'idea_id': row[0],
            'title': row[1],
            'detail': row[2],
            'category': row[3],
            'created_at': row[4]
        })

    gacha_results = []
    for row in gacha_rows:
        gacha_results.append({
            'result_id': row[0],
            'created_at': row[1],
            'idea_id': row[2],
            'idea_title': row[3],
            'detail': row[4],
            'category': row[5]
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

    inheritance_items = []
    for row in inheritance_rows:
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
            'child_category': row[12]
        })

    return render_template(
        'mypage.html',
        user=user,
        ideas=ideas,
        gacha_results=gacha_results,
        revival_notifications=revival_notifications,
        inheritance_items=inheritance_items
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
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
    # 期間パラメータを取得（デフォルトは総合）
    period = request.args.get('period', 'all')
    valid_periods = ['all', 'weekly', 'monthly', 'yearly']
    if period not in valid_periods:
        period = 'all'
    
    # 期間別ランキングを取得（制限なし、全ユーザー表示）
    rankings_by_period = {}
    inheritance_rankings_by_period = {}
    for p in valid_periods:
        rankings_by_period[p] = get_ranking_by_period(p, limit=1000)  # 実質的に全件取得
        inheritance_rankings_by_period[p] = get_inheritance_ranking_by_period(p, limit=1000)
    
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
                start_date = datetime.now()
    if isinstance(end_date, str):
        try:
            end_date = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                end_date = datetime.now()
    
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
