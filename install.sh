#!/bin/bash
set -e
echo "======================================================"
echo "  INSTALLATION FACTUR-X V11 - Oracle Linux"
echo "======================================================"

APP_DIR="/opt/facturx"
SERVICE_USER="facturx"
PORT=5000
SERVER_IP=$(hostname -I | awk '{print $1}')

# 1. Dependances systeme
echo "[1/6] Installation des dependances systeme..."
if command -v dnf &>/dev/null; then
    sudo dnf install -y python3 python3-pip nginx
else
    sudo yum install -y python3 python3-pip nginx
fi

# 2. Utilisateur dedie
echo "[2/6] Creation de l utilisateur $SERVICE_USER..."
sudo useradd -r -s /bin/false -d $APP_DIR $SERVICE_USER 2>/dev/null || echo "  -> Utilisateur deja existant"

# 3. Deploiement des fichiers
echo "[3/6] Deploiement des fichiers..."
sudo mkdir -p $APP_DIR/uploads_temp
sudo cp facturx_webapp_v11.py $APP_DIR/
for f in mapping_v5_simple.json mapping_v5_groupee.json; do
    if [ -f "$f" ]; then
        sudo cp "$f" $APP_DIR/
        echo "  -> $f copie"
    else
        echo "  -> ATTENTION : $f absent, a copier manuellement dans $APP_DIR"
    fi
done

# 4. Environnement Python virtuel
echo "[4/6] Installation des dependances Python..."
sudo python3 -m venv $APP_DIR/venv
sudo $APP_DIR/venv/bin/pip install --upgrade pip --quiet
sudo $APP_DIR/venv/bin/pip install flask PyPDF2 lxml openpyxl gunicorn --quiet

sudo chown -R $SERVICE_USER:$SERVICE_USER $APP_DIR

# 5. Service systemd
echo "[5/6] Creation du service systemd..."
sudo tee /etc/systemd/system/facturx.service > /dev/null << SERVICE
[Unit]
Description=Factur-X V11 - Application de controle
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:$PORT --timeout 120 --access-logfile $APP_DIR/access.log --error-logfile $APP_DIR/error.log facturx_webapp_v11:app
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable facturx
sudo systemctl start facturx

# 6. Nginx reverse proxy
echo "[6/6] Configuration Nginx..."
sudo tee /etc/nginx/conf.d/facturx.conf > /dev/null << NGINX
server {
    listen 80;
    server_name _;
    client_max_body_size 50M;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 120s;
    }
}
NGINX

sudo nginx -t && sudo systemctl enable nginx && sudo systemctl restart nginx

# Pare-feu
if systemctl is-active --quiet firewalld; then
    echo "  -> Ouverture port 80 dans firewalld..."
    sudo firewall-cmd --permanent --add-service=http
    sudo firewall-cmd --reload
fi

# SELinux
if command -v getenforce &>/dev/null && [ "$(getenforce)" != "Disabled" ]; then
    echo "  -> SELinux : autorisation proxy reseau..."
    sudo setsebool -P httpd_can_network_connect 1
fi

echo ""
echo "======================================================"
echo "  INSTALLATION TERMINEE !"
echo "  Application : http://$SERVER_IP"
echo "  Logs        : $APP_DIR/error.log"
echo "  Commandes   :"
echo "    sudo systemctl status facturx"
echo "    sudo journalctl -u facturx -f"
echo "======================================================"
