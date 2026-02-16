#!/bin/bash
set -e
APP_DIR="/opt/facturx"
echo "Mise a jour de Factur-X..."
sudo cp facturx_webapp_v11.py $APP_DIR/
sudo chown facturx:facturx $APP_DIR/facturx_webapp_v11.py
sudo systemctl restart facturx
echo "OK - Mise a jour effectuee."
sudo systemctl status facturx --no-pager
