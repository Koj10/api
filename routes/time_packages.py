from .main_routes import *
import base64
import datetime
from utils import get_computer_zone_by_token, resolve_package_price

def is_package_active(package):
    """Прайс клуба, время клуба = UTC+5.

    Будни 08:00–14:00 — только утренний час (дневной).
    3 и 5 часов (вечерний) — с 08:00 до 22:00.
    Обычный час (бесконечный) — будни 14:00–22:00, выходные 08:00–22:00.
    Ночь — 22:00–08:00.
    """
    if package.get("is_active") in (0, False, "0"):
        return False

    now = datetime.datetime.now()
    current_time = (now + datetime.timedelta(hours=5)).time()
    is_weekday_today = now.weekday() < 5
    is_night = current_time >= datetime.time(22, 0) or current_time < datetime.time(8, 0)
    is_morning = datetime.time(8, 0) <= current_time < datetime.time(14, 0)

    if package["is_weekend"] == 0 and not is_weekday_today:
        return False
    if package["is_weekend"] == 1 and is_weekday_today:
        return False

    period = package.get("time_period")
    if period == "дневной":
        return is_weekday_today and is_morning
    if period == "вечерний":
        return not is_night
    if period == "ночной":
        return is_night
    if period == "бесконечный":
        if is_night:
            return False
        if is_weekday_today and is_morning:
            return False
        return True
    return False

@api.route('/time_packages', methods=['GET'])
def time_packages():
    pc_token = request.args.get('pc_token')
    zone = get_computer_zone_by_token(pc_token)
    packages = SQL_request(
        "SELECT * FROM time_packages WHERE is_active = 1",
        fetch="all",
    )

    filtered_packages = []

    for package in packages:
        if not is_package_active(package):
            package['is_active'] = 2
        if 'image' in package:
            del package['image']
        package['display_price'] = resolve_package_price(package, zone)
        package['price_zone'] = zone
        filtered_packages.append(package)

    return jsonify(filtered_packages), 200

@api.route('/time_packages/<int:package_id>', methods=['GET'])
def time_package(package_id):
    package = SQL_request("SELECT * FROM time_packages WHERE id = ?", (package_id,), fetch='one')

    if not package:
        return jsonify({"error": "Package not found"}), 404

    if 'image' in package:
        del package['image']

    if package.get("is_active") not in (0, False, "0") and not is_package_active(package):
        package["is_active"] = 2

    package['display_price'] = resolve_package_price(package, "regular")

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

    package = SQL_request(
        "SELECT * FROM time_packages WHERE id = ?",
        (product_id,),
        fetch="one",
    )
    if not package or not is_package_active(package):
        return jsonify({"error": "Этот пакет сейчас недоступен"}), 403

    pc_token = data.get("pc_token")
    zone = get_computer_zone_by_token(pc_token)
    message, code = buy_products(g.user, product_id, type_product, quality, zone=zone)
    return jsonify(message), code