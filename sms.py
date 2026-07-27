import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from paths import load_app_env

load_app_env()

HTTP_TIMEOUT = 30


def _normalize_secret(value):
    if not value:
        return ""
    return value.strip().strip('"').strip("'")


def _sms_settings():
    provider = (os.getenv("SMS_PROVIDER") or "auto").strip().lower()
    api_key = _normalize_secret(os.getenv("SMS_API_KEY") or os.getenv("SMSRU_API_ID"))
    sender = (os.getenv("SMS_SENDER") or "GameSense").strip()

    if provider == "auto":
        provider = "smsru" if api_key else "console"

    return {
        "provider": provider,
        "api_key": api_key,
        "sender": sender,
    }


def _provider_configured(settings):
    provider = settings["provider"]
    if provider == "console":
        return True
    if provider == "smsru":
        return bool(settings["api_key"])
    return False


def _send_via_console(phone, text):
    logging.warning("SMS (console): %s — %s", phone, text)
    return True, None


def _send_via_smsru(settings, phone, text):
    digits = phone.lstrip("+")
    params = urllib.parse.urlencode(
        {
            "api_id": settings["api_key"],
            "to": digits,
            "msg": text,
            "json": "1",
        }
    )
    url = f"https://sms.ru/sms/send?{params}"
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        raw = response.read().decode("utf-8")
        data = json.loads(raw) if raw else {}

    if data.get("status") == "OK":
        return True, None

    status_code = data.get("status_code")
    detail = data.get("status_text") or str(data)
    return False, f"SMS.ru: {status_code or 'error'} — {detail}"


def send_sms(phone, text):
    """Отправляет SMS. Возвращает (успех, текст_ошибки или None)."""
    if not phone:
        return False, "Пустой номер телефона"
    if not text:
        return False, "Пустой текст SMS"

    settings = _sms_settings()
    if not _provider_configured(settings):
        return False, "SMS не настроен. Задайте SMS_API_KEY в .env"

    provider = settings["provider"]
    try:
        if provider == "console":
            return _send_via_console(phone, text)

        if provider == "smsru":
            return _send_via_smsru(settings, phone, text)

        return False, f"Неизвестный SMS_PROVIDER: {provider}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        message = f"SMS API ({provider}) HTTP {exc.code}: {detail}"
        logging.error(message)
        return False, message
    except urllib.error.URLError as exc:
        message = f"SMS API ({provider}): сеть недоступна — {exc.reason}"
        logging.error(message)
        return False, message
    except Exception as exc:
        logging.exception("Неожиданная ошибка отправки SMS на %s", phone)
        return False, f"{type(exc).__name__}: {exc}"


def sms_provider_name():
    return _sms_settings()["provider"]


def check_sms_connection():
    settings = _sms_settings()
    provider = settings["provider"]

    if not _provider_configured(settings):
        return False, f"Провайдер {provider}: не хватает переменных в .env"

    if provider == "console":
        return True, "SMS_PROVIDER=console — коды пишутся в лог (для разработки)"

    if provider == "smsru":
        params = urllib.parse.urlencode(
            {"api_id": settings["api_key"], "json": "1"}
        )
        url = f"https://sms.ru/my/balance?{params}"
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
        if data.get("status") == "OK":
            balance = data.get("balance", "?")
            return True, f"SMS.ru доступен (баланс: {balance} ₽)"
        return False, f"SMS.ru: {data.get('status_text') or data}"

    return False, f"Неизвестный провайдер: {provider}"
