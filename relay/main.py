from relay import app
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
)
from relay.db import (
    fetch_random_item,
    get_connection,
    get_user_by_email,
    get_user_by_user_id,
    insert_user,
    get_user_tickets,
    add_user_tickets,
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
)
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
    with get_connection() as con:
        db_items = con.execute(
            """
            SELECT 
                i.idea_id,
                i.title,
                i.detail,
                i.category,
                i.user_id,
                i.created_at,
                u.nickname
            FROM ideas i
            LEFT JOIN mypage u ON i.user_id = u.user_id
            ORDER BY i.created_at DESC
            """
        ).fetchall()

    items = []

    for row in db_items:
        items.append({
            'idea_id': row[0],
            'title': row[1],
            'detail': row[2],
            'category': row[3],
            'user_id': row[4],
            'created_at': row[5],
            'nickname': row[6]
        })
    
    user_name = session['nickname']

    return render_template(
        'index.html',
        items=items,
        user_name=user_name
    )

@app.route('/form')
@login_required
def form():
    return render_template(
        'form.html'
    )

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
        con.commit()
    
    # イベント中に投稿した場合、イベントに関連付ける
    active_events = get_active_events()
    now = datetime.now()
    for event_row in active_events:
        event_id_e, name_e, password_hash_e, start_date_e, end_date_e, created_at_e, created_by_e, status_e = event_row
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
    new_tickets = add_user_tickets(user_id, 1)
    session['tickets'] = new_tickets

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
                u.icon_path
            FROM ideas i
            LEFT JOIN mypage u ON i.user_id = u.user_id
            WHERE i.idea_id = ?
            """,
            (idea_id,)
        ).fetchone()

    if not row:
        flash('投稿が見つかりませんでした。')
        return redirect(url_for('mypage'))

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
            session['tickets'] = 1  # 初回登録時にチケット1枚付与
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
            session['user_id'] = user_row[0]
            session['nickname'] = user_row[1]
            session['email'] = user_row[3]
            session['icon_path'] = user_row[4] if len(user_row) > 4 else None
            session['tickets'] = user_row[6] if len(user_row) > 6 else 0

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
    tickets = get_user_tickets(user_id) if user_id else 0
    session['tickets'] = tickets  # セッションも更新
    return render_template("gacha.html", selected_category=selected_category, tickets=tickets)

# ランダムに1つのアイテムを表示するルート
@app.route('/result')
@login_required
def result():
    idea = None
    idea_id = session.pop('last_gacha_idea_id', None)
    user_id = session.get('user_id')
    tickets = get_user_tickets(user_id) if user_id else 0
    session['tickets'] = tickets  # セッションも更新

    if idea_id:
        with get_connection() as con:
            idea = con.execute(
                "SELECT idea_id, title, detail, category, user_id, created_at FROM ideas WHERE idea_id = ?",
                (idea_id,)
            ).fetchone()

    return render_template("result.html", item=idea, tickets=tickets)

# ガチャを回して結果ページにリダイレクトするルート
@app.route('/spin')
@login_required
def spin():
    current_user_id = session.get('user_id')
    category = request.args.get('category')  # 💡カテゴリを取得

    # チケットチェック
    tickets = get_user_tickets(current_user_id)
    if tickets < 1:
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

    # チケットを1枚消費
    new_tickets = add_user_tickets(current_user_id, -1)
    session['tickets'] = new_tickets

    with get_connection() as con:
        con.execute(
            "INSERT INTO gacha_result (result_id, user_id, idea_id, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), current_user_id, idea_id, now)
        )
        if author_id and author_id != current_user_id:
            con.execute(
                "INSERT INTO revival_notify (notify_id, idea_id, author_id, picker_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), idea_id, author_id, current_user_id, now)
            )
        con.commit()

    session['last_gacha_idea_id'] = idea_id

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

    return render_template(
        'mypage.html',
        user=user,
        ideas=ideas,
        gacha_results=gacha_results,
        revival_notifications=revival_notifications
    )


@app.route('/ranking')
@login_required
def ranking():
    """投稿数ランキングページ"""
    with get_connection() as con:
        # 投稿数でユーザーをランキング（投稿数が多い順）
        ranking_rows = con.execute("""
            SELECT 
                u.user_id,
                u.nickname,
                u.icon_path,
                COUNT(i.idea_id) as post_count
            FROM mypage u
            LEFT JOIN ideas i ON u.user_id = i.user_id
            GROUP BY u.user_id, u.nickname, u.icon_path
            HAVING COUNT(i.idea_id) > 0
            ORDER BY post_count DESC, u.created_at ASC
        """).fetchall()

    rankings = []
    for rank, row in enumerate(ranking_rows, start=1):
        rankings.append({
            'rank': rank,
            'user_id': row[0],
            'nickname': row[1],
            'icon_path': row[2],
            'post_count': row[3]
        })

    current_user_id = session.get('user_id')
    current_user_rank = None
    for ranking_item in rankings:
        if ranking_item['user_id'] == current_user_id:
            current_user_rank = ranking_item['rank']
            break

    return render_template(
        'ranking.html',
        rankings=rankings,
        current_user_id=current_user_id,
        current_user_rank=current_user_rank
    )


# ==================== イベント関連のルーティング ====================

@app.route('/events')
@login_required
def events():
    """イベント一覧/参加/開催ページ"""
    update_event_statuses()  # イベント状態を更新
    user_id = session['user_id']
    user_name = session.get('nickname', 'ユーザー')
    
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