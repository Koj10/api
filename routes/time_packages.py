from .main_routes import *
import base64
import datetime

def is_package_active(package):
    now = datetime.datetime.now()
    current_time = (now + datetime.timedelta(hours=5)).time()
    is_weekday_today = now.weekday() < 5  # Пн-Пт: 0-4

    if package['is_weekend'] == 0 and not is_weekday_today:
        return False
    elif package['is_weekend'] == 1 and is_weekday_today:
        return False
    else:
        period = package.get('time_period')
        start_time = None
        end_time = None

        if period == "дневной":
            start_time = datetime.time(8, 0)
            end_time = datetime.time(17, 0)
        elif period == "вечерний":
            start_time = datetime.time(17, 0)
            end_time = datetime.time(22, 0)
        elif period == "ночной":
            # Ночной период: 22:00 - 08:00
            if datetime.time(22, 0) <= current_time or current_time < datetime.time(8, 0):
                return True
            else:
                return False

        if start_time is not None and end_time is not None:
            if not (start_time <= current_time <= end_time):
                return False

        return True  # Все условия выполнены

@api.route('/time_packages', methods=['GET'])
def time_packages():
    packages = SQL_request("SELECT * FROM time_packages", fetch='all')  # Получаем все пакеты

    now = datetime.datetime.now()
    filtered_packages = []

    for package in packages:
        if not is_package_active(package):
            package['is_active'] = 2
        if 'image' in package:
            del package['image']
        filtered_packages.append(package)

    return jsonify(filtered_packages), 200

@api.route('/time_packages/<int:package_id>', methods=['GET'])
def time_package(package_id):
    package = SQL_request("SELECT * FROM time_packages WHERE id = ?", (package_id,), fetch='one')

    if not package:
        return jsonify({"error": "Package not found"}), 404

    if 'image' in package:
        del package['image']

    if not is_package_active(package):
        package['is_active'] = 2

    return jsonify(package), 200


@api.route('/buy/<string:type_product>', methods=['POST'])
@auth_decorator()
def buy_product(type_product):
    data = request.get_json()
    product_id = data.get('id')
    quality = data.get('quality')

    protducts = ["time_packages"]
    if type_product not in protducts:
        return jsonify({"error": "Даннный продукт не найден"}), 400
    else:
        product = SQL_request(f"SELECT * FROM {type_product} WHERE id = ?", (product_id,), fetch='one')
        message, code = buy_products(g.user, product_id, type_product, quality)
        return jsonify(message), code