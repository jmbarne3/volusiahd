#!/bin/sh
set -e
python manage.py migrate --noinput
exec gunicorn --pythonpath src --workers 2 --bind 0.0.0.0:8000 \
  --access-logfile - volusiahd.wsgi:application
