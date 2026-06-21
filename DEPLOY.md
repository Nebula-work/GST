# Deploying on EC2 (one domain, nginx)

Run the whole app — static frontend **and** FastAPI backend — on a single EC2
box behind nginx, on one domain (e.g. `https://gst.sysmos.org`). Because the
frontend and API share an origin, there's **no CORS** and the frontend calls a
relative `/api`. No Amplify, no Node process in production (nginx serves the
exported static files).

```
Browser ──HTTPS──> nginx ┬─ /        -> /var/www/gst        (static frontend)
                         └─ /api/*   -> 127.0.0.1:8011      (uvicorn / FastAPI)
```

**Prerequisites on the box:** nginx, Python 3.10+, Node 18+ (the EC2 already has
nvm), and a subdomain you can point at the instance in Route 53.

---

## 1. Clone the repo
```bash
cd /home/ubuntu
git clone https://github.com/Nebula-work/GST.git gst   # or the SSH URL
cd gst
```

## 2. Backend — FastAPI as a systemd service
```bash
cd /home/ubuntu/gst/backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
# sample files are committed (backend/sample_data/), nothing to generate

sudo cp /home/ubuntu/gst/deploy/gst-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gst-backend
curl -s http://127.0.0.1:8011/api/health      # -> {"status":"ok"}
```
> Edit the paths in `gst-backend.service` first if you cloned somewhere else.

## 3. Frontend — build the static export, serve from nginx
```bash
cd /home/ubuntu/gst/frontend
nvm use 20 || nvm install 20
npm ci
npm run build          # production build -> API calls go to relative /api
sudo mkdir -p /var/www/gst
sudo rm -rf /var/www/gst/*
sudo cp -r out/* /var/www/gst/
```

## 4. nginx site
```bash
sudo cp /home/ubuntu/gst/deploy/nginx-gst.conf /etc/nginx/sites-available/gst.sysmos.org
# edit `server_name` (and `root` if different) inside that file
sudo ln -sf /etc/nginx/sites-available/gst.sysmos.org /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 5. DNS + HTTPS
1. **Route 53:** add an `A` record `gst.sysmos.org` → the instance's public/Elastic IP.
2. Once it resolves, get the certificate (auto-edits nginx to add the 443 block + HTTP→HTTPS redirect):
   ```bash
   sudo certbot --nginx -d gst.sysmos.org
   ```

Open `https://gst.sysmos.org` — upload two files (or "Load sample files") and reconcile.

---

## Updating after a `git pull`
```bash
cd /home/ubuntu/gst && git pull

# if backend changed:
cd backend && .venv/bin/pip install -r requirements.txt && sudo systemctl restart gst-backend

# if frontend changed:
cd ../frontend && npm ci && npm run build \
  && sudo rm -rf /var/www/gst/* && sudo cp -r out/* /var/www/gst/
```

## Troubleshooting
- **502 Bad Gateway on /api** → backend isn't running: `sudo systemctl status gst-backend`, `journalctl -u gst-backend -n 50`.
- **413 Request Entity Too Large** → raise `client_max_body_size` in the nginx site.
- **Frontend loads but uploads fail** → check the browser Network tab; calls should go to `/api/...` on the same domain (not localhost).

---

*Alternative: host the frontend on AWS Amplify instead (see `amplify.yml`) and
keep the backend here — but then you're on two origins and need
`NEXT_PUBLIC_API_BASE` + `CORS_ALLOW_ORIGINS`. The single-box setup above is
simpler.*
