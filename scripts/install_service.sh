#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "This installer must be run as root." >&2
    exit 1
fi

PROJECT_ROOT=/opt/jarvis
ENV_DIRECTORY=/etc/jarvis
ENV_FILE=/etc/jarvis/jarvis.env
KEY_DIRECTORY=/etc/jarvis/keys
HOSTS_FILE=/etc/jarvis/hosts.yaml
KNOWN_HOSTS_FILE=/etc/jarvis/known_hosts
UNIT_TARGET=/etc/systemd/system/jarvis.service

if ! getent group jarvis >/dev/null; then
    groupadd --system jarvis
fi

if ! getent passwd jarvis >/dev/null; then
    useradd \
        --system \
        --gid jarvis \
        --no-create-home \
        --home-dir /nonexistent \
        --shell /usr/sbin/nologin \
        jarvis
fi

install -d -o root -g root -m 0750 "${ENV_DIRECTORY}"
install -d -o root -g jarvis -m 0750 "${KEY_DIRECTORY}"
if [[ ! -e ${ENV_FILE} ]]; then
    install -o root -g root -m 0600 \
        "${PROJECT_ROOT}/.env.example" "${ENV_FILE}"
fi
chown root:root "${ENV_FILE}"
chmod 0600 "${ENV_FILE}"

# Remote monitoring files are intentionally never created or populated here.
# If an administrator installed them, enforce the documented read-only access.
for monitoring_file in "${HOSTS_FILE}" "${KNOWN_HOSTS_FILE}"; do
    if [[ -e ${monitoring_file} ]]; then
        chown root:jarvis "${monitoring_file}"
        chmod 0640 "${monitoring_file}"
    fi
done
find "${KEY_DIRECTORY}" -maxdepth 1 -type f \
    -exec chown root:jarvis {} + \
    -exec chmod 0640 {} +

install -d -o jarvis -g jarvis -m 0750 \
    "${PROJECT_ROOT}/logs" "${PROJECT_ROOT}/data"
install -o root -g root -m 0644 \
    "${PROJECT_ROOT}/config/jarvis.service" "${UNIT_TARGET}"

systemctl daemon-reload

if "${PROJECT_ROOT}/venv/bin/python" \
    "${PROJECT_ROOT}/scripts/check_config.py"; then
    echo "Configuration valid. Service is installed but not started."
    echo "Start it explicitly with: systemctl start jarvis"
else
    echo "Configuration invalid. Service was not started." >&2
    exit 1
fi
