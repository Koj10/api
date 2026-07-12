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
RESEND_API_URL = "https://api.resend.com/emails"
HTTP_TIMEOUT = 30


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
    resend_key = _normalize_secret(os.getenv("RESEND_API_KEY"))

    if provider == "auto":
        if brevo_key:
            provider = "brevo"
        elif resend_key:
            provider = "resend"
        else:
            provider = "smtp"

    return {
        "provider": provider,
        "from_email": from_email,
        "from_name": from_name,
        "brevo_key": brevo_key,
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
        "server": (os.getenv("SMTP_SERVER") or "").strip(),
        "port": port,
        "user": smtp_user,
        "password": _normalize_secret(os.getenv("SMTP_PASSWORD")),
        "from_email": from_email,
    }


def _provider_configured(settings):
    provider = settings["provider"]
    if provider == "brevo":
        return bool(settings["brevo_key"] and settings["from_email"])
    if provider == "resend":
        return bool(settings["resend_key"] and settings["from_email"])
    if provider == "smtp":
        cfg = settings["smtp"]
        return all([cfg["server"], cfg["user"], cfg["password"], cfg["from_email"]])
    return False


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


def _send_via_brevo(settings, to_email, subject, text_body, html_body):
    payload = {
        "sender": {"name": settings["from_name"], "email": settings["from_email"]},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": text_body or "",
    }
    if html_body:
        payload["htmlContent"] = html_body

    _http_post_json(
        BREVO_API_URL,
        {"api-key": settings["brevo_key"]},
        payload,
    )


def _send_via_resend(settings, to_email, subject, text_body, html_body):
    payload = {
        "from": f'{settings["from_name"]} <{settings["from_email"]}>',
        "to": [to_email],
        "subject": subject,
        "text": text_body or "",
    }
    if html_body:
        payload["html"] = html_body

    _http_post_json(
        RESEND_API_URL,
        {"Authorization": f'Bearer {settings["resend_key"]}'},
        payload,
    )


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


def _open_smtp(cfg, timeout=30):
    if cfg["port"] == 465:
        return SMTPSSLIPv4(cfg["server"], cfg["port"], timeout=timeout)
    return SMTPIPv4(cfg["server"], cfg["port"], timeout=timeout)


def _ports_to_try(primary_port):
    if primary_port == 465:
        return (465, 587)
    if primary_port == 587:
        return (587, 465)
    return (primary_port,)


def _send_via_smtp(cfg, to_email, subject, text_body, html_body):
    msg = MIMEMultipart()
    msg["From"] = cfg["from_email"]
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body or "", "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    last_error = None
    for port in _ports_to_try(cfg["port"]):
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
    return False, cfg["port"], last_error


def send_email(to_email, subject, text_body, html_body=None):
    if not to_email:
        logging.error("send_email: пустой адрес получателя")
        return False

    settings = _mail_settings()
    if not _provider_configured(settings):
        logging.error(
            "Почта не настроена для провайдера %s. Проверьте .env (BREVO_API_KEY / RESEND_API_KEY / SMTP_*)",
            settings["provider"],
        )
        return False

    provider = settings["provider"]
    try:
        if provider == "brevo":
            _send_via_brevo(settings, to_email, subject, text_body, html_body)
            logging.info("Письмо отправлено на %s через Brevo API (тема: %s)", to_email, subject)
            return True

        if provider == "resend":
            _send_via_resend(settings, to_email, subject, text_body, html_body)
            logging.info("Письмо отправлено на %s через Resend API (тема: %s)", to_email, subject)
            return True

        if provider == "smtp":
            ok, port, error = _send_via_smtp(
                settings["smtp"], to_email, subject, text_body, html_body
            )
            if ok:
                logging.info(
                    "Письмо отправлено на %s через SMTP %s:%s (тема: %s)",
                    to_email,
                    settings["smtp"]["server"],
                    port,
                    subject,
                )
                return True
            if isinstance(error, smtplib.SMTPAuthenticationError):
                logging.error("SMTP: ошибка авторизации для %s: %s", settings["smtp"]["user"], error)
            else:
                logging.error("SMTP: отправка на %s не удалась: %s", to_email, error)
            return False

        logging.error("Неизвестный EMAIL_PROVIDER: %s", provider)
        return False
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logging.error(
            "Email API (%s): HTTP %s — %s",
            provider,
            exc.code,
            detail[:500],
        )
        return False
    except urllib.error.URLError as exc:
        logging.error("Email API (%s): сеть недоступна — %s", provider, exc.reason)
        return False
    except Exception:
        logging.exception("Неожиданная ошибка отправки email на %s", to_email)
        return False


def email_provider_name():
    return _mail_settings()["provider"]


def check_email_connection():
    """Проверка настроек и доступности выбранного провайдера почты."""
    settings = _mail_settings()
    provider = settings["provider"]

    if not _provider_configured(settings):
        return False, f"Провайдер {provider}: не хватает переменных в .env"

    try:
        if provider == "brevo":
            account = _http_get_json(
                "https://api.brevo.com/v3/account",
                {"api-key": settings["brevo_key"]},
                timeout=15,
            )
            email = account.get("email", "ok")
            return True, f"Brevo API доступен (аккаунт: {email}, from: {settings['from_email']})"

        if provider == "resend":
            _http_get_json(
                "https://api.resend.com/domains",
                {"Authorization": f'Bearer {settings["resend_key"]}'},
                timeout=15,
            )
            return True, f"Resend API доступен (from: {settings['from_email']})"

        if provider == "smtp":
            cfg = settings["smtp"]
            last_error = None
            for port in _ports_to_try(cfg["port"]):
                attempt_cfg = {**cfg, "port": port}
                try:
                    with _open_smtp(attempt_cfg, timeout=15) as server:
                        if port != 465:
                            server.ehlo()
                            server.starttls(context=ssl.create_default_context())
                            server.ehlo()
                        server.login(attempt_cfg["user"], attempt_cfg["password"])
                    return True, f"SMTP {cfg['server']}:{port} (IPv4) — OK"
                except smtplib.SMTPAuthenticationError as exc:
                    return False, f"SMTP: ошибка авторизации на порту {port}: {exc}"
                except (smtplib.SMTPException, OSError, TimeoutError) as exc:
                    last_error = exc
            return False, f"{type(last_error).__name__}: {last_error}"

        return False, f"Неизвестный провайдер: {provider}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return False, f"{provider} API HTTP {exc.code}: {detail[:300]}"
    except urllib.error.URLError as exc:
        return False, f"{provider} API: {exc.reason}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# Обратная совместимость для старых импортов
check_smtp_connection = check_email_connection
