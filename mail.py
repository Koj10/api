import json
import logging
import os
import socket
import smtplib
import ssl
import urllib.error
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from paths import load_app_env

load_app_env()

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
HTTP_TIMEOUT = 15
SMTP_CONNECT_TIMEOUT = int(os.getenv("SMTP_CONNECT_TIMEOUT", "8"))


def _normalize_secret(value):
    if not value:
        return ""
    cleaned = value.strip().strip('"').strip("'")
    return cleaned.replace(" ", "")


def _mail_settings():
    provider = (os.getenv("EMAIL_PROVIDER") or "auto").strip().lower()
    from_email = (os.getenv("FROM_EMAIL") or os.getenv("SMTP_USER") or "").strip()
    from_name = (os.getenv("FROM_NAME") or "GameSense").strip()
    brevo_key = _normalize_secret(os.getenv("BREVO_API_KEY"))

    if provider == "auto":
        if brevo_key and from_email:
            provider = "brevo"
        else:
            provider = "smtp"

    return {
        "provider": provider,
        "from_email": from_email,
        "from_name": from_name,
        "brevo_key": brevo_key,
        "smtp": _smtp_config(),
    }


def _smtp_config(port_override=None):
    port_raw = os.getenv("SMTP_PORT", "587") or "587"
    try:
        port = int(port_override if port_override is not None else port_raw)
    except ValueError:
        logging.error("SMTP_PORT имеет неверное значение: %r", port_raw)
        port = 587

    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    from_email = (os.getenv("FROM_EMAIL") or smtp_user or "").strip()

    return {
        "server": (os.getenv("SMTP_SERVER") or "smtp.gmail.com").strip(),
        "port": port,
        "user": smtp_user,
        "password": _normalize_secret(os.getenv("SMTP_PASSWORD")),
        "from_email": from_email,
    }


def _smtp_configured(cfg):
    return all([cfg["server"], cfg["user"], cfg["password"], cfg["from_email"]])


def _brevo_configured(settings):
    return bool(settings["brevo_key"] and settings["from_email"])


def _parse_http_error_body(raw):
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:500]
    if isinstance(data, dict):
        message = data.get("message") or data.get("error") or data.get("detail")
        if message:
            return str(message)
    return raw[:500]


def _http_post_json(url, headers, payload, timeout=HTTP_TIMEOUT):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)


def _http_get_json(url, headers, timeout=HTTP_TIMEOUT):
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)


def _brevo_error_detail(raw_detail):
    detail = raw_detail or "неизвестная ошибка Brevo"
    if "unrecognised ip" in detail.lower():
        return (
            "Brevo отклонил запрос: IP сервера не в списке разрешённых. "
            "Добавьте IP VPS: https://app.brevo.com/security/authorised_ips"
        )
    return detail


def _send_via_brevo(settings, to_email, subject, text_body, html_body):
    payload = {
        "sender": {"name": settings["from_name"], "email": settings["from_email"]},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": text_body or "",
    }
    if html_body:
        payload["htmlContent"] = html_body

    try:
        _http_post_json(
            BREVO_API_URL,
            {"api-key": settings["brevo_key"]},
            payload,
        )
        return True, None
    except urllib.error.HTTPError as exc:
        detail = _parse_http_error_body(exc.read().decode("utf-8", errors="replace"))
        return False, _brevo_error_detail(detail)
    except urllib.error.URLError as exc:
        return False, f"Brevo API: сеть недоступна — {exc.reason}"


def _ipv4_socket(host, port, timeout):
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    if not infos:
        raise OSError(f"IPv4-адрес для {host} не найден")
    return socket.create_connection(infos[0][4], timeout)


class SMTPIPv4(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        return _ipv4_socket(host, port, timeout)


class SMTPSSLIPv4(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        sock = _ipv4_socket(host, port, timeout)
        return self.context.wrap_socket(sock, server_hostname=host)


def _open_smtp(cfg, timeout=None):
    if timeout is None:
        timeout = SMTP_CONNECT_TIMEOUT
    if cfg["port"] == 465:
        return SMTPSSLIPv4(cfg["server"], cfg["port"], timeout=timeout)
    return SMTPIPv4(cfg["server"], cfg["port"], timeout=timeout)


def _ports_to_try(primary_port):
    if primary_port == 465:
        return (465, 587)
    if primary_port == 587:
        return (587, 465)
    return (primary_port,)


def _smtp_error_detail(cfg, port, error):
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return (
            f"SMTP: ошибка авторизации для {cfg['user']}. "
            "Для Gmail используйте пароль приложения: https://myaccount.google.com/apppasswords"
        )
    if isinstance(error, (TimeoutError, OSError)):
        return (
            f"SMTP: не удалось подключиться к {cfg['server']}:{port} ({type(error).__name__}). "
            "На VPS часто блокируют порты 587/465 — используйте Brevo (EMAIL_PROVIDER=brevo)."
        )
    return f"SMTP: отправка не удалась — {error}"


def _send_via_smtp(cfg, to_email, subject, text_body, html_body):
    msg = MIMEMultipart()
    msg["From"] = cfg["from_email"]
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body or "", "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    last_error = None
    last_port = cfg["port"]
    for port in _ports_to_try(cfg["port"]):
        last_port = port
        attempt_cfg = {**cfg, "port": port}
        try:
            with _open_smtp(attempt_cfg) as server:
                if port != 465:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(attempt_cfg["user"], attempt_cfg["password"])
                server.sendmail(attempt_cfg["from_email"], [to_email], msg.as_string())
            return True, port, None
        except smtplib.SMTPAuthenticationError as exc:
            return False, port, exc
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            last_error = exc
            logging.error(
                "SMTP: не удалось подключиться к %s:%s — %s: %s",
                cfg["server"],
                port,
                type(exc).__name__,
                exc,
            )
    return False, last_port, last_error


def _smtp_connection_error(error):
    return isinstance(error, (TimeoutError, OSError))


def send_email(to_email, subject, text_body, html_body=None):
    """Отправляет письмо. SMTP или Brevo API. Возвращает (успех, текст_ошибки или None)."""
    if not to_email:
        logging.error("send_email: пустой адрес получателя")
        return False, "Пустой адрес получателя"

    settings = _mail_settings()
    provider = settings["provider"]

    if provider == "brevo":
        if not _brevo_configured(settings):
            return False, "Brevo не настроен: нужны BREVO_API_KEY и FROM_EMAIL в .env"
        ok, error = _send_via_brevo(settings, to_email, subject, text_body, html_body)
        if ok:
            logging.info("Письмо отправлено на %s через Brevo API (тема: %s)", to_email, subject)
        return ok, error

    cfg = settings["smtp"]
    if not _smtp_configured(cfg):
        if _brevo_configured(settings):
            ok, error = _send_via_brevo(settings, to_email, subject, text_body, html_body)
            if ok:
                logging.info("Письмо отправлено на %s через Brevo API (тема: %s)", to_email, subject)
            return ok, error
        return False, "Почта не настроена: SMTP_* или BREVO_API_KEY в .env"

    try:
        ok, port, error = _send_via_smtp(cfg, to_email, subject, text_body, html_body)
        if ok:
            logging.info(
                "Письмо отправлено на %s через SMTP %s:%s (тема: %s)",
                to_email,
                cfg["server"],
                port,
                subject,
            )
            return True, None

        if _smtp_connection_error(error) and _brevo_configured(settings):
            logging.warning("SMTP недоступен, пробуем Brevo API для %s", to_email)
            brevo_ok, brevo_error = _send_via_brevo(settings, to_email, subject, text_body, html_body)
            if brevo_ok:
                logging.info("Письмо отправлено на %s через Brevo API (fallback)", to_email)
                return True, None
            return False, brevo_error or _smtp_error_detail(cfg, port, error)

        detail = _smtp_error_detail(cfg, port, error)
        logging.error(detail)
        return False, detail
    except Exception as exc:
        logging.exception("Неожиданная ошибка отправки email на %s", to_email)
        return False, f"{type(exc).__name__}: {exc}"


def send_test_email(to_email):
    return send_email(
        to_email,
        "GameSense — тест почты",
        "Если вы видите это письмо, отправка работает.",
        "<p>Если вы видите это письмо, отправка работает.</p>",
    )


def email_provider_name():
    return _mail_settings()["provider"]


def _check_brevo(settings):
    if not _brevo_configured(settings):
        return False, "Brevo: нужны BREVO_API_KEY и FROM_EMAIL"
    try:
        account = _http_get_json(
            "https://api.brevo.com/v3/account",
            {"api-key": settings["brevo_key"]},
            timeout=15,
        )
        account_email = account.get("email", "ok")
        return True, f"Brevo API OK (аккаунт: {account_email}, from: {settings['from_email']})"
    except urllib.error.HTTPError as exc:
        detail = _parse_http_error_body(exc.read().decode("utf-8", errors="replace"))
        return False, _brevo_error_detail(detail)
    except urllib.error.URLError as exc:
        return False, f"Brevo API: {exc.reason}"


def check_email_connection():
    settings = _mail_settings()
    provider = settings["provider"]

    if provider == "brevo":
        return _check_brevo(settings)

    cfg = settings["smtp"]
    if not _smtp_configured(cfg):
        if _brevo_configured(settings):
            return _check_brevo(settings)
        return False, "SMTP: не хватает SMTP_USER, SMTP_PASSWORD, FROM_EMAIL"

    last_error = None
    last_port = cfg["port"]
    for port in _ports_to_try(cfg["port"]):
        last_port = port
        attempt_cfg = {**cfg, "port": port}
        try:
            with _open_smtp(attempt_cfg) as server:
                if port != 465:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(attempt_cfg["user"], attempt_cfg["password"])
            return True, f"SMTP {cfg['server']}:{port} — OK, from: {cfg['from_email']}"
        except smtplib.SMTPAuthenticationError as exc:
            return False, _smtp_error_detail(cfg, port, exc)
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            last_error = exc

    if last_error and _smtp_connection_error(last_error) and _brevo_configured(settings):
        ok, detail = _check_brevo(settings)
        if ok:
            return True, f"SMTP недоступен, но Brevo fallback OK. {detail}"
        return False, detail

    if last_error is None:
        return False, "SMTP: не удалось подключиться"
    return False, _smtp_error_detail(cfg, last_port, last_error)


check_smtp_connection = check_email_connection
