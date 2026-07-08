from cashback import claim_cashback, cashback_profile_fields
from .main_routes import *


@api.route("/loyalty/cashback/claim", methods=["POST"])
@auth_decorator()
def loyalty_cashback_claim():
    result, error = claim_cashback(g.user["id"])
    if error:
        return jsonify({"error": error}), 400
    return jsonify(
        {
            "message": "Кешбэк переведён на баланс",
            **result,
        }
    ), 200
