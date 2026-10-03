#!/usr/bin/env bash
# Подготовка сервера Ubuntu 24.04 под сервис «Цифровой дизайнер презентаций».
#
# Запускается от root на сервере; повторный запуск безопасен. Ставит то, что
# не ставится через pip: LibreOffice (PDF и превью слайдов), шрифты, Caddy
# (HTTPS и пароль перед страницей), пользователя сервиса, каталоги, подкачку
# и экран. Код и окружение Python выкладывает deploy/deploy.sh.
#
# Адрес сервера, пароль и ключ модели в репозиторий не попадают: они живут
# только на сервере, в /etc/dpd/ и /etc/caddy/Caddyfile.
#
#   DPD_DOMAIN=имя.сервера DPD_LOGIN=expert bash setup-server.sh
#
# Без DPD_DOMAIN берётся имя вида 1-2-3-4.sslip.io: публичный DNS, который
# отвечает адресом, записанным в самом имени, — сертификат Let's Encrypt
# выпускается без покупки домена. Пароль генерируется, если не задан
# DPD_PASSWORD, и сохраняется в /etc/dpd/credentials (только для root).

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

APP_ROOT=/opt/dpd
DATA_DIR=/var/lib/dpd
CONF_DIR=/etc/dpd
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== Пакеты"
apt-get update -q
apt-get install -y -q --no-install-recommends \
    python3-venv python3-pip git curl ca-certificates gnupg ufw \
    debian-keyring debian-archive-keyring apt-transport-https \
    libreoffice-impress libreoffice-core \
    fontconfig fonts-liberation fonts-liberation2 fonts-dejavu-core \
    fonts-crosextra-carlito fonts-crosextra-caladea fonts-lato \
    fonts-open-sans fonts-noto-core fonts-wine

# Arial и Times New Roman: Liberation с теми же метриками уже стоит, этот
# пакет скачивает оригиналы с SourceForge. Сбой загрузки — не повод
# останавливаться.
echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" \
    | debconf-set-selections
apt-get install -y -q ttf-mscorefonts-installer || echo "!! шрифты Microsoft не скачались, остаются Liberation"
fc-cache -f >/dev/null

echo "== Caddy"
if ! command -v caddy >/dev/null; then
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
        > /etc/apt/sources.list.d/caddy-stable.list
    chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -q
    apt-get install -y -q caddy
fi

echo "== Подкачка"
# 4 ГБ памяти на страницу, LibreOffice и параллельные запросы к модели —
# впритык; подкачка не даёт ядру убить процесс посреди сборки.
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== Пользователь и каталоги"
id dpd >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin dpd
mkdir -p "$APP_ROOT" "$DATA_DIR" "$CONF_DIR"
chown dpd:dpd "$DATA_DIR"
chmod 750 "$CONF_DIR"
chgrp dpd "$CONF_DIR"

# Файл окружения сервиса. Ключ модели дописывает владелец (или deploy.sh) —
# здесь только то, что не секрет.
ENV_FILE="$CONF_DIR/dpd.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
DPD_OUTPUT_DIR=$DATA_DIR
# LLM_API_KEY=...
# Провайдер модели недоступен из страны сервера — запросы к нему через прокси:
# HTTPS_PROXY=http://127.0.0.1:10809
EOF
fi
chown root:dpd "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "== Сервис страницы"
install -m 644 "$HERE/dpd.service" /etc/systemd/system/dpd.service
systemctl daemon-reload
systemctl enable dpd.service >/dev/null

echo "== HTTPS и пароль"
if [ -z "${DPD_DOMAIN:-}" ]; then
    ip="$(curl -s -4 --max-time 10 https://ifconfig.me)"
    DPD_DOMAIN="${ip//./-}.sslip.io"
fi
DPD_LOGIN="${DPD_LOGIN:-expert}"
CRED_FILE="$CONF_DIR/credentials"
if [ -z "${DPD_PASSWORD:-}" ] && [ -f "$CRED_FILE" ]; then
    DPD_PASSWORD="$(sed -n 's/^password=//p' "$CRED_FILE")"
fi
# Не `tr </dev/urandom | head`: под pipefail SIGPIPE у tr роняет скрипт.
DPD_PASSWORD="${DPD_PASSWORD:-$(python3 -c 'import secrets; print("".join(secrets.choice("abcdefghkmnpqrstuvwxyz23456789") for _ in range(12)))')}"
umask 077
printf 'url=https://%s\nlogin=%s\npassword=%s\n' "$DPD_DOMAIN" "$DPD_LOGIN" "$DPD_PASSWORD" > "$CRED_FILE"
umask 022
chown root:root "$CRED_FILE"
chmod 600 "$CRED_FILE"

hash="$(caddy hash-password --plaintext "$DPD_PASSWORD")"
sed -e "s|__DOMAIN__|$DPD_DOMAIN|" -e "s|__LOGIN__|$DPD_LOGIN|" -e "s|__HASH__|$hash|" \
    "$HERE/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl enable caddy >/dev/null
systemctl reload-or-restart caddy

echo "== Экран"
# Страница слушает только 127.0.0.1; снаружи — SSH и Caddy.
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "Готово: https://$DPD_DOMAIN — логин и пароль в $CRED_FILE"
