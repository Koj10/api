import logging
import os
import socket
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from paths import load_app_env

load_app_env()


def _smtp_config():
    port_raw = os.getenv("SMTP_PORT", "587") or "587"
    try:
        port = int(port_raw)
    except ValueError:
        logging.error("SMTP_PORT имеет неверное значение: %r", port_raw)
        port = 2000

    smtp_user = os.getenv("SMTP_USER")
    from_email = os.getenv("FROM_EMAIL") or smtp_user

    return {
        "server": os.getenv("SMTP_SERVER"),
        "port": port,
        "user": smtp_user,
        "password": os.getenv("SMTP_PASSWORD"),
        "from_email": from_email,
    }


def _smtp_configured(cfg):
    return all([cfg["server"], cfg["user"], cfg["password"], cfg["from_email"]])


def _ipv4_socket(host, port, timeout):
    """Подключение только по IPv4 — на VPS часто нет маршрута до IPv6."""
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    if not infos:
        raise OSError(f"IPv4-адрес для {host} не найден")
    return socket.create_connection(infos[0][4], timeout)


class SMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        if self.debuglevel > 0:
            self._print_debug("connect (IPv4):", (host, port))
        return _ipv4_socket(host, port, timeout)


class SMTP_SSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        if self.debuglevel > 0:
            self._print_debug("connect (IPv4 SSL):", (host, port))
        sock = _ipv4_socket(host, port, timeout)
        return self.context.wrap_socket(sock, server_hostname=host)


def _open_smtp(cfg, timeout=30):
    if cfg["port"] == 465:
        return SMTP_SSL(cfg["server"], cfg["port"], timeout=timeout)
    return SMTP(cfg["server"], cfg["port"], timeout=timeout)


def send_email(to_email, subject, text_body, html_body=None):
    if not to_email:
        logging.error("send_email: пустой адрес получателя")
        return False

    cfg = _smtp_config()
    if not _smtp_configured(cfg):
        missing = [
            name
            for name, key in (
                ("SMTP_SERVER", "server"),
                ("SMTP_USER", "user"),
                ("SMTP_PASSWORD", "password"),
                ("FROM_EMAIL", "from_email"),
            )
            if not cfg[key]
        ]
        logging.error("SMTP не настроен. Не заданы: %s", ", ".join(missing))
        return False

    msg = MIMEMultipart()
    msg["From"] = cfg["from_email"]
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body or "", "plain"))

    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        with _open_smtp(cfg) as server:
            if cfg["port"] != 465:
                server.ehlo()
                server.starttls()
                server.ehlo()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from_email"], [to_email], msg.as_string())
        logging.info("Письмо отправлено на %s (тема: %s)", to_email, subject)
        return True
    except smtplib.SMTPAuthenticationError as exc:
        logging.error(
            "SMTP: ошибка авторизации для %s — проверьте SMTP_USER и пароль приложения Gmail: %s",
            cfg["user"],
            exc,
        )
        return False
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        logging.error(
            "SMTP: не удалось отправить письмо на %s через %s:%s — %s: %s",
            to_email,
            cfg["server"],
            cfg["port"],
            type(exc).__name__,
            exc,
        )
        return False
    except Exception:
        logging.exception("Неожиданная ошибка отправки email на %s", to_email)
        return False


def check_smtp_connection():
    """Проверка подключения и авторизации SMTP без отправки письма."""
    cfg = _smtp_config()
    if not _smtp_configured(cfg):
        missing = [
            name
            for name, key in (
                ("SMTP_SERVER", "server"),
                ("SMTP_USER", "user"),
                ("SMTP_PASSWORD", "password"),
                ("FROM_EMAIL", "from_email"),
            )
            if not cfg[key]
        ]
        return False, f"Не заданы переменные: {', '.join(missing)}"

    try:
        with _open_smtp(cfg, timeout=15) as server:
            if cfg["port"] != 465:
                server.ehlo()
                server.starttls()
                server.ehlo()
            server.login(cfg["user"], cfg["password"])
        return True, f"Подключение к {cfg['server']}:{cfg['port']} (IPv4) успешно"
    except smtplib.SMTPAuthenticationError as exc:
        return False, f"Ошибка авторизации Gmail: {exc}"
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
