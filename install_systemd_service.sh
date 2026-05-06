#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="ers-v2.service"
PROJECT_DIR="/home/pi/ers_v2"
SERVICE_SOURCE="${PROJECT_DIR}/ers-v2.service.example"
SERVICE_TARGET="/etc/systemd/system/${SERVICE_NAME}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root: sudo bash ${PROJECT_DIR}/install_systemd_service.sh" >&2
    exit 1
fi

if [[ ! -f "${SERVICE_SOURCE}" ]]; then
    echo "Service example not found: ${SERVICE_SOURCE}" >&2
    exit 1
fi

echo "Installing ${SERVICE_NAME}"
cp "${SERVICE_SOURCE}" "${SERVICE_TARGET}"
chmod 0644 "${SERVICE_TARGET}"

echo "Reloading systemd"
systemctl daemon-reload

echo "Enabling ${SERVICE_NAME}"
systemctl enable "${SERVICE_NAME}"

echo "Starting ${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "Done."
echo "Status:  systemctl status ${SERVICE_NAME}"
echo "Logs:    journalctl -u ${SERVICE_NAME} -f"
