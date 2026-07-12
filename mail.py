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
        "server": (os.getenv("SMTP_SERVER") or "").strip(),
        "port": port,
        "user": smtp_user,
        "password": _normalize_secret(os.getenv("SMTP_PASSWORD")),
        "from_email": from_email,
    }


def _smtp_configured(cfg):
    return all([cfg["server"], cfg["user"], cfg["password"], cfg["from_email"]])


def _ipv4_socket(host, port, timeout):
    """Подключение только по IPv4 — на VPS/Docker часто нет маршрута до IPv6."""
    infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    if not infos:
        raise OSError(f"IPv4-адрес для {host} не найден")
    return socket.create_connection(infos[0][4], timeout)


class SMTPIPv4(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        if self.debuglevel > 0:
            self._print_debug("connect (IPv4):", (host, port))
        return _ipv4_socket(host, port, timeout)


class SMTPSSLIPv4(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        if self.debuglevel > 0:
            self._print_debug("connect (IPv4 SSL):", (host, port))
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


def _connect_and_send(cfg, msg, to_email, timeout=30):
    last_error = None

    for port in _ports_to_try(cfg["port"]):
        attempt_cfg = {**cfg, "port": port}
        try:
            with _open_smtp(attempt_cfg, timeout=timeout) as server:
                if port != 465:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                server.login(attempt_cfg["user"], attempt_cfg["password"])
                server.sendmail(attempt_cfg["from_email"], [to_email], msg.as_string())
            if port != cfg["port"]:
                logging.info(
                    "SMTP: отправка прошла через резервный порт %s (основной %s недоступен)",
                    port,
                    cfg["port"],
                )
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

    if len(cfg["password"]) < 10:
        logging.warning(
            "SMTP_PASSWORD короткий (%s символов). "
            "Если пароль Gmail с пробелами — укажите его в кавычках в .env",
            len(cfg["password"]),
        )

    msg = MIMEMultipart()
    msg["From"] = cfg["from_email"]
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body or "", "plain"))

    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        ok, port, error = _connect_and_send(cfg, msg, to_email)
        if ok:
            logging.info("Письмо отправлено на %s через %s:%s (тема: %s)", to_email, cfg["server"], port, subject)
            return True

        if isinstance(error, smtplib.SMTPAuthenticationError):
            logging.error(
                "SMTP: ошибка авторизации для %s — проверьте SMTP_USER и пароль приложения Gmail: %s",
                cfg["user"],
                error,
            )
            return False

        logging.error(
            "SMTP: все попытки отправки на %s не удались (%s:%s, fallback включён): %s",
            to_email,
            cfg["server"],
            cfg["port"],
            error,
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
            return True, f"Подключение к {cfg['server']}:{port} (IPv4) успешно"
        except smtplib.SMTPAuthenticationError as exc:
            return False, f"Ошибка авторизации Gmail на порту {port}: {exc}"
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            last_error = exc

    return False, f"{type(last_error).__name__}: {last_error}"
