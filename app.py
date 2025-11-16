from flask import Flask, render_template, request, redirect, session, url_for
from flask_socketio import SocketIO, join_room, leave_room, emit
from models import db, User, ChatRoom
import os, datetime, random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secretkey123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'
db.init_app(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

user_colors = {}  # кольори користувачів
room_users = {}   # учасники кімнат

# Ініціалізація БД та папки logs
with app.app_context():
    db.create_all()
    os.makedirs('logs', exist_ok=True)

# Авторизація 
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        user = User.query.filter_by(username=username).first()
        if user:
            session['user_id'] = user.id
            return redirect(url_for('password'))
        else:
            session['new_user'] = username
            return redirect(url_for('register'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    username = session.get('new_user')
    if not username:
        return redirect(url_for('login'))
    if request.method == 'POST':
        password = request.form['password']
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id
        return redirect(url_for('chat'))
    return render_template('register.html', username=username)

@app.route('/password', methods=['GET', 'POST'])
def password():
    user = None
    if 'user_id' in session:
        user = User.query.get(session.get('user_id'))
    error = None
    if request.method == 'POST':
        password_input = request.form['password']
        if user and user.check_password(password_input):
            return redirect(url_for('chat'))
        else:
            error = "Невірний пароль"
    return render_template('password.html', username=user.username if user else '', error=error)

# Чат 
@app.route('/chat')
def chat():
    user = None
    if 'user_id' in session:
        user = User.query.get(session.get('user_id'))
    if not user:
        return redirect(url_for('login'))
    if user.id not in user_colors:
        user_colors[user.id] = "#%06x" % random.randint(0, 0xFFFFFF)
    rooms = ChatRoom.query.all()
    return render_template('chat.html', username=user.username, rooms=rooms, color=user_colors[user.id])

# Видалення кімнати (тільки admin)
@app.route('/delete_room/<int:room_id>', methods=['POST'])
def delete_room(room_id):
    user = None
    if 'user_id' in session:
        user = User.query.get(session.get('user_id'))
    if not user or user.username != 'admin':
        return "Доступ заборонено", 403

    room = ChatRoom.query.get(room_id)
    if room:
        db.session.delete(room)
        db.session.commit()
        
        socketio.emit('room_deleted', {'room_id': room_id}, broadcast=True)
    return '', 204

# WebSocket Events
@socketio.on('create_room')
def handle_create_room(data):
    room_name = data.get('room')
    username = data.get('username')
    if not room_name or not username:
        return
    existing = ChatRoom.query.filter_by(name=room_name).first()
    if not existing:
        user = User.query.filter_by(username=username).first()
        if user:
            new_room = ChatRoom(name=room_name, owner_id=user.id)
            db.session.add(new_room)
            db.session.commit()
            emit('room_created', {'room': room_name, 'room_id': new_room.id}, broadcast=True)

@socketio.on('join')
def handle_join(data):
    username = data.get('username')
    room = data.get('room')
    if not username or not room:
        return
    join_room(room)
    if room not in room_users:
        room_users[room] = {}

    color = "#%06x" % random.randint(0, 0xFFFFFF)
    user = User.query.filter_by(username=username).first()
    if user:
        color = user_colors.get(user.id, color)
        if user.id not in user_colors:
            user_colors[user.id] = color

    room_users[room][username] = color

    # Історія повідомлень
    history = []
    log_path = f'logs/{room}.log'
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            history = f.read().splitlines()

    emit('chat_history', {'history': history}, room=request.sid)
    log_message(room, f"{username} приєднався до чату")
    emit('message', {'msg': f"{username} приєднався до чату"}, room=room)
    emit('update_users', {'users': [{'username': u, 'color': c} for u, c in room_users[room].items()]}, room=room)

@socketio.on('leave')
def handle_leave(data):
    username = data.get('username')
    room = data.get('room')
    if not username or not room:
        return
    leave_room(room)
    if room in room_users and username in room_users[room]:
        del room_users[room][username]
    log_message(room, f"{username} покинув чат")
    emit('message', {'msg': f"{username} покинув чат"}, room=room)
    emit('update_users', {'users': [{'username': u, 'color': c} for u, c in room_users.get(room, {}).items()]}, room=room)

@socketio.on('send_message')
def handle_message(data):
    username = data.get('username')
    room = data.get('room')
    msg = data.get('msg')
    if not username or not room or not msg:
        return
    log_message(room, f"{username}: {msg}")
    emit('message', {'msg': f"{username}: {msg}"}, room=room)

# Логування 
def log_message(room, message):
    with open(f'logs/{room}.log', 'a', encoding='utf-8') as f:
        f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    socketio.run(app, debug=True)
