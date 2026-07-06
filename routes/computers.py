from .main_routes import *

@api.route('/pc/register', methods=['GET'])
def pc_register():
    token = generate_pc_token()
    SQL_request("INSERT INTO computers (token, status) VALUES (?, 'ремонт')", (token,), fetch=None)
    return jsonify({"token":token}), 200

@api.route('/pc/status', methods=['POST'])
@auth_decorator()
def edit_status():
    unlock_status = ["занят", 'активен']
    data = request.get_json()
    req_token = data.get('token')
    status = data.get('status')
    time_str = data.get('time')
    time = None
    if time_str:
        try:
            dt = datetime.strptime(time_str, '%Y-%m-%dT%H:%M')
            dt = dt - timedelta(hours=5)
            time = dt.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            time = None

    if getattr(g, 'user', None):
        token = req_token
    else:
        token = g.computer['token']

    computer = SQL_request("SELECT * FROM computers WHERE token = ?", params=(token,), fetch='one')
    if computer is None:
        return jsonify({"error":"Компьютер не найден"}), 404

    if status == "активен":
        time = None
        user_id = None
        SQL_request(
            "UPDATE computers SET status = ?, time_active = ?, user_active = ?, "
            "session_started_at = NULL, session_duration_minutes = NULL WHERE token = ?",
            params=(status, time, user_id, token),
            fetch="none",
        )
        return jsonify({"message": "Статус изменён"}), 200
    elif getattr(g, 'user', None) and g.user.get('role') == 'user':
        if status not in unlock_status:
            return jsonify({"error":"Доступ запрещён"}), 403
        user_id = g.user['id']
    else:
        user_id = computer.get('user_active')

    try:
        SQL_request(
            "UPDATE computers SET status = ?, time_active = ?, user_active = ? WHERE token = ?",
            params=(status, time, user_id, token),
            fetch="none",
        )
        return jsonify({"message":"Статус изменён"}), 200
    except Exception as e:
        logging.error(e)
        return jsonify({"error":"Неправильный запрос"}), 403

@api.route('/pc/status/<pc_token>', methods=['GET'])
def get_status(pc_token):
    computer = SQL_request("SELECT * FROM computers WHERE token = ?", params=(pc_token,), fetch='one')
    if computer is None:
        return jsonify({"error":"Компьютер не найден"}), 404
    return jsonify({"message":computer}), 200

@api.route('/pc', methods=['GET'])
@auth_decorator()
def get_pc():
    if getattr(g, 'user', None):
        filtered_computers = []
        computers = SQL_request("SELECT * FROM computers WHERE number_pc IS NOT NULL", fetch='all')
    
        for computer in computers:
            if 'token' in computer and g.user['role'] == 'user':
                del computer['token']
            filtered_computers.append(computer)
        return jsonify(filtered_computers), 200

    elif getattr(g, 'computer', None):
        token = g.computer['token']
        computer = SQL_request("SELECT * FROM computers WHERE token = ?", (token,), fetch='one')

        if computer:
            return jsonify(computer), 200
        else:
            return jsonify({"error":"Компьютер не найден"}), 403

@api.route('/pc/<computer_id>', methods=['GET'])
@auth_decorator()
def get_one_pc(computer_id):
    try:
        computer = SQL_request("SELECT * FROM computers WHERE id = ?", (computer_id,), fetch='one')
        if g.user['role'] != 'admin':
            del computer['token']
        return jsonify(computer), 200
    except:
        return jsonify({"error":"Компьютер не найден"}), 404