# Сервис на сервере

Страница та же, что `streamlit run ui/app.py` на машине разработчика, но доступна по ссылке HTTPS и закрыта логином и паролем (задача T-74). Адрес сервера, пароль и ключ модели в репозиторий не попадают: они живут только на сервере.

```text
браузер ──HTTPS + логин/пароль──▶ Caddy :443 ──▶ Streamlit 127.0.0.1:8501 (dpd.service)
                                                     │
                                                     ├─▶ LibreOffice: PDF и превью слайдов
                                                     └─▶ модель по API (через прокси, если нужно)
```

| Что | Где на сервере |
|---|---|
| Код — ровно закоммиченная версия | `/opt/dpd/app`, номер коммита в `/opt/dpd/app/REVISION` |
| Окружение Python | `/opt/dpd/venv`, по `scripts/requirements.txt` |
| Результаты прогонов, копии шаблонов (`DPD_OUTPUT_DIR`) | `/var/lib/dpd` |
| Ключ модели, прокси | `/etc/dpd/dpd.env` |
| Ссылка, логин и пароль для экспертов | `/etc/dpd/credentials` (только root) |
| Сервис страницы | `systemctl status dpd`, журнал — `journalctl -u dpd` |
| HTTPS и пароль | `/etc/caddy/Caddyfile`, сертификат Let's Encrypt выпускается и продлевается сам |

Сервис и Caddy поднимаются сами после перезагрузки сервера. Снаружи открыты только порты 22, 80 и 443.

## Развернуть с нуля

Сервер — Ubuntu 24.04 (в ней Python 3.12, как у проекта), 2 vCPU, 4 ГБ памяти, диск от 40 ГБ, вход по SSH-ключу от root.

```bash
# 1. С машины разработчика: файлы установки на сервер и установка
scp deploy/setup-server.sh deploy/Caddyfile deploy/dpd.service СЕРВЕР:/root/deploy/
ssh СЕРВЕР 'bash /root/deploy/setup-server.sh'     # DPD_DOMAIN=имя — свой домен вместо sslip.io

# 2. Ключ модели — строкой LLM_API_KEY=... в /etc/dpd/dpd.env на сервере

# 3. Код и окружение Python; повторять после каждого изменения кода
bash deploy/deploy.sh СЕРВЕР
```

`setup-server.sh` ставит LibreOffice, шрифты (Liberation, Carlito и Caladea — замены Calibri и Cambria с теми же метриками, Arial и Times New Roman, Tahoma из Wine, Lato, Noto), Caddy, подкачку 2 ГБ, экран и пользователя `dpd` — страница работает от него, а не от root, и код ей только для чтения. Без своего домена берётся имя вида `1-2-3-4.sslip.io`: публичный DNS отвечает адресом, записанным в самом имени, и сертификат выпускается без покупки домена. Пароль генерируется и сохраняется в `/etc/dpd/credentials`; повторный запуск скрипта безопасен и пароль не меняет.

`deploy.sh` выкладывает `git archive HEAD`, а не рабочий каталог: на сервер попадает только закоммиченное, без файлов из `.gitignore`, поэтому корпоративные шаблоны второго трека и `.env` туда не уходят — шаблон эксперт загружает на странице сам.

## Провайдер модели недоступен из страны сервера

OpenRouter отвечает адресам в России `403 Access denied by security policy` (проверено 2026-10-03 с сервера Selectel), хотя Hugging Face, DeepInfra, Novita и Cloud.ru с того же сервера отвечают. Код это не затрагивает: клиент модели — `httpx`, и он идёт через прокси из переменной `HTTPS_PROXY` в `/etc/dpd/dpd.env`:

```text
HTTPS_PROXY=http://127.0.0.1:10809
NO_PROXY=127.0.0.1,localhost
```

На нашем сервере прокси — клиент Xray (`systemctl status xray`, конфигурация `/usr/local/etc/xray/config.json`), подключённый к VPN владельца; выходы — серверы VPN вне России, самый быстрый выбирается сам, недоступный обходится. Конфигурация несёт ключи VPN и в репозиторий не входит. Без прокси работает всё, кроме модели: сборка по своему плану, три варианта, аудит, три формата.

Другой путь — провайдер, доступный из страны сервера: адрес и имя модели задаются переменными `LLM_BASE_URL`, `LLM_MODEL`, `VLM_BASE_URL`, `VLM_MODEL` в том же файле, без правки кода (README, «Переменные окружения»). Параметры из `extra` в `configs/models.yaml` понимает OpenRouter; у другого провайдера генерацию нужно проверить живым прогоном.

## Проверить

```bash
ssh СЕРВЕР 'systemctl is-active dpd caddy xray; cat /opt/dpd/app/REVISION'
ssh СЕРВЕР 'cd /opt/dpd/app && set -a && . /etc/dpd/dpd.env && set +a && sudo -E -u dpd /opt/dpd/venv/bin/python scripts/check_provider.py'
curl -s -o /dev/null -w '%{http_code}\n' https://АДРЕС/          # 401 — сервер жив и просит пароль
```
