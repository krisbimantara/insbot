# Deployment Guide — Telegram Inspection Bot

Panduan deploy bot di VPS yang sama dengan container Frappe ERPNext.

## Prasyarat

- VPS sudah menjalankan Frappe/ERPNext via Docker
- Docker & Docker Compose terinstall
- Git terinstall di VPS
- Akses SSH ke VPS

## Langkah 1: Clone Repository ke VPS

```bash
ssh user@your-vps-ip

# Buat direktori untuk bot (di luar folder Frappe)
cd /opt
git clone https://github.com/YOUR_USERNAME/insbot.git
cd insbot
```

## Langkah 2: Konfigurasi Environment

```bash
# Copy template dan isi nilai yang sesuai
cp .env.example .env
nano .env
```

Isi nilai berikut:

| Variable | Keterangan |
|----------|-----------|
| `FRAPPE_URL` | URL Frappe internal, mis. `http://frappe-web:8069` atau `http://host.docker.internal:8069` jika Frappe di host network |
| `FRAPPE_API_KEY` | API Key dari User Frappe (User → API Access → Generate Keys) |
| `FRAPPE_API_SECRET` | API Secret dari User Frappe |
| `TELEGRAM_BOT_TOKEN` | Token dari @BotFather |
| `REDIS_URL` | Biarkan default `redis://redis:6379/0` (Redis internal bot) |
| `WEBHOOK_SHARED_SECRET` | Secret yang sama dengan yang dikonfigurasi di Server Script Frappe |
| `WEBHOOK_PORT` | Port webhook, default `8443` |

### Koneksi ke Frappe di Docker Network yang Sama

Jika Frappe berjalan di Docker network tertentu (mis. `frappe_network`), tambahkan network tersebut ke `docker-compose.yml` bot:

```yaml
services:
  bot:
    # ... konfigurasi existing ...
    networks:
      - default
      - frappe_network

networks:
  frappe_network:
    external: true
```

Dengan ini, `FRAPPE_URL` bisa menggunakan nama container Frappe langsung, mis:
```
FRAPPE_URL=http://frappe-web:8069
```

**Alternatif** jika tidak ingin join network Frappe:
```
FRAPPE_URL=http://host.docker.internal:8069
```
atau gunakan IP internal VPS:
```
FRAPPE_URL=http://172.17.0.1:8069
```

## Langkah 3: Build dan Jalankan

```bash
# Build image dan start containers
docker compose up -d --build

# Cek status
docker compose ps

# Cek logs
docker compose logs -f bot
```

## Langkah 4: Verifikasi

```bash
# Test healthz endpoint
curl http://localhost:8443/healthz
# Expected: {"status":"ok"}

# Test webhook endpoint (dari VPS)
curl -X POST http://localhost:8443/webhook/inspection-request \
  -H "Content-Type: application/json" \
  -H "X-Inspection-Webhook-Secret: YOUR_SECRET" \
  -d '{
    "event": "inspection_requested",
    "motor_tarikan": "TEST-001",
    "nopol": "B 1234 TEST",
    "merk": "Honda",
    "model": "Beat",
    "tahun": "2024",
    "warna": "Merah",
    "tipe_inspeksi": "Inspeksi",
    "inspector_chat_id": "YOUR_TELEGRAM_ID"
  }'
```

## Langkah 5: Konfigurasi Frappe Webhook

Di Frappe, buat Server Script atau Webhook yang mengirim POST ke bot saat tombol "Request Inspeksi" ditekan:

```
URL: http://inspection-bot:8443/webhook/inspection-request
```

(Gunakan nama container `inspection-bot` jika sudah join network yang sama)

Atau jika bot diakses via port mapping:
```
URL: http://localhost:8443/webhook/inspection-request
```

## Langkah 6: Expose Webhook ke Internet (Opsional)

Jika Frappe berada di server berbeda atau perlu akses dari luar:

### Opsi A: Nginx Reverse Proxy

Tambahkan di konfigurasi Nginx VPS:

```nginx
server {
    listen 443 ssl;
    server_name bot.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location /webhook/ {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /healthz {
        proxy_pass http://127.0.0.1:8443;
    }
}
```

### Opsi B: Traefik (jika sudah dipakai Frappe)

Tambahkan labels di `docker-compose.yml`:

```yaml
services:
  bot:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.insbot.rule=Host(`bot.yourdomain.com`)"
      - "traefik.http.services.insbot.loadbalancer.server.port=8443"
```

## Maintenance

### Update Bot

```bash
cd /opt/insbot
git pull origin main
docker compose up -d --build
```

### Lihat Logs

```bash
docker compose logs -f bot          # Bot logs
docker compose logs -f redis        # Redis logs
docker compose logs --tail=100 bot  # Last 100 lines
```

### Restart

```bash
docker compose restart bot
```

### Stop

```bash
docker compose down          # Stop containers
docker compose down -v       # Stop + hapus volume Redis (data hilang!)
```

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| Bot tidak bisa konek ke Frappe | Cek `FRAPPE_URL`, pastikan network Docker terhubung |
| Redis connection refused | Pastikan container redis sudah running: `docker compose ps` |
| Webhook 403 Forbidden | Cek `WEBHOOK_SHARED_SECRET` sama di bot dan Frappe |
| Bot tidak merespons di Telegram | Cek `TELEGRAM_BOT_TOKEN`, pastikan bot belum di-block user |
| Container restart loop | Cek logs: `docker compose logs bot`, biasanya env var missing |
