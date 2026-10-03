#!/usr/bin/env bash
# Выложить на сервер версию из git и перезапустить страницу.
#
#   bash deploy/deploy.sh [ssh-имя сервера]      # по умолчанию dpd-vps
#
# Выкладывается ровно закоммиченное (`git archive HEAD`), без незакоммиченных
# правок и файлов из .gitignore: сервис, код и видео должны быть одной версии,
# и её номер лежит на сервере в /opt/dpd/app/REVISION. Окружение Python
# ставится по scripts/requirements.txt, как на машине разработки.

set -euo pipefail

HOST="${1:-dpd-vps}"
REV="$(git rev-parse --short HEAD)"

echo "== Выкладка $REV на $HOST"
git archive --format=tar HEAD | ssh "$HOST" "
    set -e
    rm -rf /opt/dpd/app.new
    mkdir -p /opt/dpd/app.new
    tar -x -C /opt/dpd/app.new
    echo $REV > /opt/dpd/app.new/REVISION
"

ssh "$HOST" '
    set -e
    [ -d /opt/dpd/venv ] || python3 -m venv /opt/dpd/venv
    /opt/dpd/venv/bin/pip install -q --upgrade pip
    /opt/dpd/venv/bin/pip install -q -r /opt/dpd/app.new/scripts/requirements.txt
    rm -rf /opt/dpd/app.old
    [ -d /opt/dpd/app ] && mv /opt/dpd/app /opt/dpd/app.old
    mv /opt/dpd/app.new /opt/dpd/app
    /opt/dpd/venv/bin/pip install -q --no-deps -e /opt/dpd/app
    systemctl restart dpd.service
    sleep 5
    systemctl is-active dpd.service
    curl -s -o /dev/null -w "страница отвечает: %{http_code}\n" http://127.0.0.1:8501/_stcore/health
'
