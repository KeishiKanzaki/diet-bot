"""Loaded by the existing Render start command: gunicorn app:app."""
import os

bind = '0.0.0.0:' + os.getenv('PORT', '5000')
workers = 1
threads = 4
timeout = 90
accesslog = '-'
errorlog = '-'
