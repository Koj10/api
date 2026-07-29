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

RESEND_API_URL = "https://api.resend.com/emails"
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
    resend_key = _normalize_secret(os.getenv("RESEND_API_KEY"))

    if provider == "auto":
        if resend_key and from_email:
            provider = "resend"
        else:
            provider = "smtp"

    return {
        "provider": provider,
        "from_email": from_email,
        "from_name": from_name,
        "resend_key": resend_key,
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


def _resend_configured(settings):
    return bool(settings["resend_key"] and settings["from_email"])


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


def _http_headers(extra=None):
    headers = {
        "User-Agent": "GameSense-Mail/1.0",
        "Accept": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _http_post_json(url, headers, payload, timeout=HTTP_TIMEOUT):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={**_http_headers(headers), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)


def _http_get_json(url, headers, timeout=HTTP_TIMEOUT):
    request = urllib.request.Request(url, headers=_http_headers(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)


def _resend_error_detail(raw_detail):
    detail = raw_detail or "неизвестная ошибка Resend"
    lowered = detail.lower()
    if "1010" in lowered:
        return (
            "Resend/Cloudflare отклонил запрос (1010). "
            "Обновите API до последней версии mail.py или проверьте RESEND_API_KEY в контейнере."
        )
    if "domain" in lowered and ("verify" in lowered or "verified" in lowered):
        return (
            f"{detail} "
            "Добавьте и подтвердите домен в Resend → Domains, "
            "или для теста используйте FROM_EMAIL=onboarding@resend.dev"
        )
    return detail


def _send_via_resend(settings, to_email, subject, text_body, html_body):
    payload = {
        "from": f'{settings["from_name"]} <{settings["from_email"]}>',
        "to": [to_email],
        "subject": subject,
        "text": text_body or "",
    }
    if html_body:
        payload["html"] = html_body

    try:
        _http_post_json(
            RESEND_API_URL,
            {"Authorization": f'Bearer {settings["resend_key"]}'},
            payload,
        )
        return True, None
    except urllib.error.HTTPError as exc:
        detail = _parse_http_error_body(exc.read().decode("utf-8", errors="replace"))
        return False, _resend_error_detail(detail)
    except urllib.error.URLError as exc:
        return False, f"Resend API: сеть недоступна — {exc.reason}"


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
            "Gmail — пароль приложения; Yandex — пароль приложения в настройках почты."
        )
    if isinstance(error, (TimeoutError, OSError)):
        return (
            f"SMTP: не удалось подключиться к {cfg['server']}:{port} ({type(error).__name__}). "
            "На VPS часто блокируют 587/465 — используйте Resend (EMAIL_PROVIDER=resend)."
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
    """Отправляет письмо через Resend API или SMTP. Возвращает (успех, ошибка)."""
    if not to_email:
        logging.error("send_email: пустой адрес получателя")
        return False, "Пустой адрес получателя"

    settings = _mail_settings()
    provider = settings["provider"]

    if provider == "resend":
        if not _resend_configured(settings):
            return False, "Resend не настроен: нужны RESEND_API_KEY и FROM_EMAIL в .env"
        ok, error = _send_via_resend(settings, to_email, subject, text_body, html_body)
        if ok:
            logging.info("Письмо отправлено на %s через Resend (тема: %s)", to_email, subject)
        return ok, error

    cfg = settings["smtp"]
    if not _smtp_configured(cfg):
        if _resend_configured(settings):
            ok, error = _send_via_resend(settings, to_email, subject, text_body, html_body)
            if ok:
                logging.info("Письмо отправлено на %s через Resend (тема: %s)", to_email, subject)
            return ok, error
        return False, "Почта не настроена: SMTP_* или RESEND_API_KEY в .env"

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

        if _smtp_connection_error(error) and _resend_configured(settings):
            logging.warning("SMTP недоступен, пробуем Resend для %s", to_email)
            resend_ok, resend_error = _send_via_resend(
                settings, to_email, subject, text_body, html_body
            )
            if resend_ok:
                logging.info("Письмо отправлено на %s через Resend (fallback)", to_email)
                return True, None
            return False, resend_error or _smtp_error_detail(cfg, port, error)

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


def _check_resend(settings):
    if not _resend_configured(settings):
        return False, "Resend: нужны RESEND_API_KEY и FROM_EMAIL"
    try:
        _http_get_json(
            "https://api.resend.com/domains",
            {"Authorization": f'Bearer {settings["resend_key"]}'},
            timeout=15,
        )
        return True, (
            f"Resend API OK (from: {settings['from_email']}). "
            "Проверка отправки: /mail-check?test=ваш@email.com"
        )
    except urllib.error.HTTPError as exc:
        detail = _parse_http_error_body(exc.read().decode("utf-8", errors="replace"))
        return False, _resend_error_detail(detail)
    except urllib.error.URLError as exc:
        return False, f"Resend API: {exc.reason}"


def check_email_connection():
    settings = _mail_settings()
    provider = settings["provider"]

    if provider == "resend":
        return _check_resend(settings)

    cfg = settings["smtp"]
    if not _smtp_configured(cfg):
        if _resend_configured(settings):
            return _check_resend(settings)
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

    if last_error and _smtp_connection_error(last_error) and _resend_configured(settings):
        ok, detail = _check_resend(settings)
        if ok:
            return True, f"SMTP недоступен, но Resend OK. {detail}"
        return False, detail

    if last_error is None:
        return False, "SMTP: не удалось подключиться"
    return False, _smtp_error_detail(cfg, last_port, last_error)


check_smtp_connection = check_email_connection
