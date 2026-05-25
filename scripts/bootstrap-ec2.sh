#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

sudo apt-get update
sudo apt-get install -y ca-certificates curl git

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi

if ! docker compose version >/dev/null 2>&1; then
  sudo apt-get install -y docker-compose-plugin || true
fi

sudo usermod -aG docker "$USER"
sudo mkdir -p /opt/right-route-ocr
sudo chown -R "$USER":"$USER" /opt/right-route-ocr

echo "EC2 bootstrap complete."
echo "Log out and back in to apply docker group membership."
echo "Then push to main to trigger CI/CD deployment."
