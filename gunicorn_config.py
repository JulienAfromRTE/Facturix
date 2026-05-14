# Configuration Gunicorn pour Facturix
# Lancer avec : gunicorn -c gunicorn_config.py app:app

import multiprocessing

# --- Réseau ---
bind = "0.0.0.0:5000"

# --- Workers ---
# 2-4 workers selon le serveur (règle = nb CPU x 2 + 1)
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"

# --- Timeouts ---
# Augmenté à 120s pour laisser le temps au traitement PDF/XML
timeout = 120
graceful_timeout = 30
keepalive = 5

# --- Taille des requêtes ---
# Autoriser des fichiers PDF jusqu'à 50 Mo
limit_request_line = 8190
limit_request_fields = 10000  # 500 factures × 3 champs = 1500 minimum ; marge pour les gros batchs

# --- Logs ---
loglevel = "info"
accesslog = "-"   # stdout
errorlog  = "-"   # stderr
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(D)sµs'

# --- Rechargement automatique en dev (désactiver en prod) ---
reload = False
