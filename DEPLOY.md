# Guide de déploiement — Facturix

Ce guide décrit le déploiement de Facturix en production sur un serveur Linux avec **Gunicorn** (WSGI) et **nginx** (reverse proxy), géré par **systemd**.

---

## Prérequis

- Serveur Linux (Debian/Ubuntu ou RHEL/Oracle Linux)
- Python 3.9+
- nginx
- Accès sudo

---

## 1. Créer un utilisateur dédié

```bash
sudo useradd -r -s /bin/false -d /opt/facturix facturix
```

---

## 2. Déployer les fichiers

```bash
# Créer le répertoire applicatif
sudo mkdir -p /opt/facturix/uploads_temp
sudo mkdir -p /opt/facturix/archive_files

# Cloner ou copier les sources
sudo git clone https://github.com/votre-org/facturix.git /opt/facturix
# — ou bien copier manuellement les fichiers —

# Permissions
sudo chown -R facturix:facturix /opt/facturix
```

---

## 3. Environnement Python virtuel

```bash
sudo python3 -m venv /opt/facturix/venv
sudo /opt/facturix/venv/bin/pip install --upgrade pip
sudo /opt/facturix/venv/bin/pip install -r /opt/facturix/requirements.txt
```

---

## 4. Premier lancement (initialisation de la base)

```bash
sudo -u facturix /opt/facturix/venv/bin/python /opt/facturix/app.py
# Ctrl+C dès que l'application démarre — la base facturix.db est créée
```

La base SQLite est initialisée avec les mappings et règles par défaut au premier démarrage.

---

## 5. Service systemd

Créer le fichier `/etc/systemd/system/facturix.service` :

```ini
[Unit]
Description=Facturix — Contrôle factures électroniques Factur-X
After=network.target

[Service]
Type=simple
User=facturix
Group=facturix
WorkingDirectory=/opt/facturix
ExecStart=/opt/facturix/venv/bin/gunicorn \
    --bind 127.0.0.1:5000 \
    --workers 3 \
    --worker-class sync \
    --timeout 120 \
    --access-logfile /var/log/facturix/access.log \
    --error-logfile /var/log/facturix/error.log \
    app:app
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Créer le répertoire de logs et activer le service :

```bash
sudo mkdir -p /var/log/facturix
sudo chown facturix:facturix /var/log/facturix

sudo systemctl daemon-reload
sudo systemctl enable facturix
sudo systemctl start facturix

# Vérifier le statut
sudo systemctl status facturix
```

---

## 6. Configuration nginx

Créer `/etc/nginx/conf.d/facturix.conf` (ou `/etc/nginx/sites-available/facturix`) :

```nginx
server {
    listen 80;
    server_name facturix.exemple.fr;  # adapter au domaine

    # Taille max des uploads (PDF volumineux)
    client_max_body_size 100M;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    # Fichiers statiques servis directement par nginx
    location /static/ {
        alias /opt/facturix/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
```

Activer et recharger nginx :

```bash
# Debian/Ubuntu
sudo ln -s /etc/nginx/sites-available/facturix /etc/nginx/sites-enabled/
# RHEL/Oracle Linux : le fichier est déjà dans conf.d/, pas besoin de lien

sudo nginx -t          # vérifier la configuration
sudo systemctl reload nginx
```

---

## 7. HTTPS avec Certbot (recommandé)

```bash
sudo apt install certbot python3-certbot-nginx   # Debian/Ubuntu
# ou
sudo dnf install certbot python3-certbot-nginx   # RHEL

sudo certbot --nginx -d facturix.exemple.fr
```

Certbot modifie automatiquement la config nginx pour rediriger HTTP → HTTPS.

---

## 8. Vérification

```bash
# Statut Gunicorn
sudo systemctl status facturix

# Logs en temps réel
sudo journalctl -u facturix -f

# Test de l'API
curl -s http://localhost:5000/api/stats/db-info | python3 -m json.tool
```

---

## 9. Mise à jour

```bash
cd /opt/facturix

# Récupérer les mises à jour
sudo -u facturix git pull

# Mettre à jour les dépendances si requirements.txt a changé
sudo /opt/facturix/venv/bin/pip install -r requirements.txt

# Redémarrer le service
sudo systemctl restart facturix
```

---

## 10. Sauvegarder la base de données

La base `facturix.db` (SQLite) contient tous les mappings, règles métier et l'historique des contrôles. Il est recommandé de la sauvegarder régulièrement :

```bash
# Snapshot quotidien (ajouter dans crontab)
sqlite3 /opt/facturix/facturix.db ".backup /opt/facturix/backups/facturix_$(date +%Y%m%d).db"

# Garder les 30 derniers jours
find /opt/facturix/backups/ -name "*.db" -mtime +30 -delete
```

---

## Variables d'environnement (optionnel)

| Variable | Valeur par défaut | Rôle |
|---|---|---|
| `FLASK_SECRET_KEY` | valeur aléatoire générée | Clé de chiffrement des sessions Flask |
| `FACTURIX_DB_PATH` | `./facturix.db` | Chemin vers la base SQLite |
| `FACTURIX_UPLOAD_FOLDER` | `./uploads_temp` | Dossier temporaire pour les uploads |

Pour définir la clé secrète en production, ajouter dans le service systemd :

```ini
[Service]
Environment="FLASK_SECRET_KEY=votre-cle-aleatoire-longue"
```

---

## Architecture réseau recommandée

```
Internet
   │
   ▼
[nginx :443]  ──── TLS/HTTPS ────  Client navigateur
   │
   │  proxy_pass localhost:5000
   ▼
[Gunicorn :5000]  ──  3 workers sync
   │
   ▼
[Flask / app.py]
   │
   ├── facturix.db  (SQLite)
   └── archive_files/  (PDFs/RDIs archivés)
```

---

*Pour le déploiement Windows (développement local), lancer simplement `python app.py`.*
