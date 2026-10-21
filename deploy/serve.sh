#!/bin/sh
set -e
# Migrations and the baseline taxonomy, in one place. See the command's own
# docstring for what it does and why the fixture is safe to load every time.
python manage.py deploy
exec gunicorn --pythonpath src --workers 2 --bind 0.0.0.0:8000 \
  --access-logfile - volusiahd.wsgi:application
