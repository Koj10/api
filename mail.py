import logging
import os
import socket
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from paths import load_app_env

load_app_env()


def _normalize_secret(value):
    if not value:
        return ""
    cleaned = value.strip().strip('"').strip("'")
    return cleaned.replace(" ", "")


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
    """Отправляет письмо через SMTP (Gmail). Возвращает (успех, текст_ошибки или None)."""
    if not to_email:
        logging.error("send_email: пустой адрес получателя")
        return False, "Пустой адрес получателя"

    cfg = _smtp_config()
    if not _smtp_configured(cfg):
        logging.error(
            "SMTP не настроен. Проверьте .env: SMTP_SERVER, SMTP_USER, SMTP_PASSWORD, FROM_EMAIL"
        )
        return False, "Почта не настроена на сервере (SMTP)"

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
        if isinstance(error, smtplib.SMTPAuthenticationError):
            detail = (
                f"SMTP: ошибка авторизации для {cfg['user']}. "
                "Для Gmail используйте пароль приложения: https://myaccount.google.com/apppasswords"
            )
            logging.error("%s: %s", detail, error)
            return False, detail
        detail = f"SMTP: отправка не удалась — {error}"
        logging.error(detail)
        return False, detail
    except Exception as exc:
        logging.exception("Неожиданная ошибка отправки email на %s", to_email)
        return False, f"{type(exc).__name__}: {exc}"


def send_test_email(to_email):
    return send_email(
        to_email,
        "GameSense — тест почты",
        "Если вы видите это письмо, отправка через SMTP работает.",
        "<p>Если вы видите это письмо, отправка через SMTP работает.</p>",
    )


def email_provider_name():
    return "smtp"


def check_email_connection():
    """Проверка настроек и доступности SMTP."""
    cfg = _smtp_config()
    if not _smtp_configured(cfg):
        return False, "SMTP: не хватает переменных в .env (SMTP_USER, SMTP_PASSWORD, FROM_EMAIL)"

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
            return True, f"SMTP {cfg['server']}:{port} (IPv4) — OK, from: {cfg['from_email']}"
        except smtplib.SMTPAuthenticationError as exc:
            return False, (
                f"SMTP: ошибка авторизации на порту {port}: {exc}. "
                "Для Gmail нужен пароль приложения, не обычный пароль."
            )
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            last_error = exc

    return False, f"{type(last_error).__name__}: {last_error}"


check_smtp_connection = check_email_connection
