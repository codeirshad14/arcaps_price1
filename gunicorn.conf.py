"""
Gunicorn settings for the live server.

Gunicorn reads this file by itself when it is started from this folder (the
folder holding manage.py), so the systemd service needs no change. Anything
given on gunicorn's own command line still wins over what is here.

Why it exists: left to its defaults gunicorn runs ONE worker handling ONE
request at a time. Every screen and every customer's phone then queues
behind whoever is currently being served — a slow mobile connection, an
admin export — and from the outside that looks exactly like "the tab keeps
loading" and "with several users it hangs".

The bind address is deliberately not set: it belongs to the service that
starts gunicorn and to the nginx config that points at it.
"""

import multiprocessing

# The usual rule of thumb, capped so a big machine does not open more
# PostgreSQL connections than it needs.
workers = min(multiprocessing.cpu_count() * 2 + 1, 9)

# Threads let each worker serve several requests at once. Almost all the time
# here is spent waiting on the database or the network, which threads overlap
# well — and every thread holds its own pooled database connection.
worker_class = "gthread"
threads = 4

# A request is a few milliseconds of work. One running for a minute is stuck,
# and holding a worker on it only makes everybody else wait.
timeout = 60
graceful_timeout = 30

# Lets a phone fetch the page, its CSS, JS and pictures over one connection
# instead of opening a new one for each.
keepalive = 5

# Recycle workers now and then, so a slow leak never builds up over weeks of
# uptime. The jitter stops them all restarting at once.
max_requests = 1000
max_requests_jitter = 100
