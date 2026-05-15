# Deployment Guide — Telegram Inspection Bot

Panduan deploy bot di VPS yang sama dengan container Frappe ERPNext.

## Arsitektur

```
┌─────────────────────────────────────────────────────────────────┐
│  VPS                                                            │
│                                                                 │
│  ┌─── frappe_network (bridge) ─────────────────────────────┐   │
│  │                                                          │   │
│  │  frappe-docker.yml:                                      │   │
│  │    frontend (:8080)                                      │   │
│  │    backend (:8000)  ◄── bot konek ke sini                │   │
│  │    db, redis-cache, redis-queue, scheduler, etc.         │   │
│  │                                                          │   │
│  │  docker-compose.yml (insbot):                            │   │
│  │    bot (:8443)  ◄── Frappe webhook POST ke sini          │   │
│  │                                                          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─── default network (insbot) ────┐                            │
│  │  bot ←→ redis (internal)        │                            │
│  └─────────────────────────────────┘                            │
└─────────────────────────────────────────────────────────────────┘
```

Bot terhubung ke dua network:
- **frappe_network** (external) — agar bisa akses `backend:8000` (Frappe REST API)
- **default** (internal insbot) — untuk komunikasi bot ↔ Redis sendiri

## Prasyarat

- VPS sudah menjalankan Frappe/ERPNext via `frappe-docker.yml` dengan network `frappe_network`
- Docker & Docker Compose v2 terinstall
- Git terinstall di VPS
- Akses SSH ke VPS

## Langkah 1: Clone Repository ke VPS

```bash
ssh user@your-vps-ip

cd /opt
git clone https://github.com/krisbimantara/insbot.git
cd insbot
```

## Langkah 2: Pastikan frappe_network Sudah Ada

```bash
# Cek network Frappe sudah running
docker network ls | grep frappe_network

# Jika belum ada (Frappe belum start), start Frappe dulu:
# cd /path/to/frappe && docker compose -f frappe-docker.yml up -d
```

> **Penting:** Nama network harus persis `frappe_network`. Jika Frappe compose file-mu
> menggunakan project name (mis. `myproject_frappe_network`), cek dengan:
> ```bash
> docker network ls
> ```
> Lalu sesuaikan nama di `docker-compose.yml` bagian `networks.frappe_network.name`.

## Langkah 3: Konfigurasi Environment

```bash
cp .env.example .env
nano .env
```

Isi nilai berikut:

| Variable | Nilai | Keterangan |
|----------|-------|-----------|
| `FRAPPE_URL` | `http://backend:8000` | Nama container Frappe backend di frappe_network |
| `FRAPPE_API_KEY` | `xxxxxxxx` | API Key dari User Frappe (User → API Access → Generate Keys) |
| `FRAPPE_API_SECRET` | `xxxxxxxx` | API Secret dari User Frappe |
| `TELEGRAM_BOT_TOKEN` | `123456:ABC...` | Token dari @BotFather |
| `REDIS_URL` | `redis://redis:6379/0` | Biarkan default (Redis internal bot) |
| `WEBHOOK_SHARED_SECRET` | `random_string` | Secret yang sama dengan Server Script Frappe |
| `WEBHOOK_HOST` | `0.0.0.0` | Biarkan default |
| `WEBHOOK_PORT` | `8443` | Biarkan default |

### Catatan tentang FRAPPE_URL

Karena bot sudah join `frappe_network`, bot bisa langsung akses container `backend` Frappe:

```env
FRAPPE_URL=http://backend:8000
```

Ini lebih reliable daripada pakai IP host karena langsung lewat Docker DNS internal.

## Langkah 4: Build dan Jalankan

```bash
docker compose up -d --build
```

Cek status:
```bash
docker compose ps
```

Output yang diharapkan:
```
NAME                    STATUS              PORTS
inspection-bot          Up (healthy)        0.0.0.0:8443->8443/tcp
inspection-bot-redis    Up (healthy)        6379/tcp
```

## Langkah 5: Verifikasi

### Test healthz
```bash
curl http://localhost:8443/healthz
# Expected: {"status":"ok"}
```

### Test webhook (simulasi dari Frappe)
```bash
curl -X POST http://localhost:8443/webhook/inspection-request \
  -H "Content-Type: application/json" \
  -H "X-Inspection-Webhook-Secret: YOUR_SECRET_HERE" \
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
# Expected: OK (status 200)
```

### Test koneksi ke Frappe dari container bot
```bash
docker exec inspection-bot python -c "
import asyncio, aiohttp
async def test():
    async with aiohttp.ClientSession() as s:
        r = await s.get('http://backend:8000/api/method/frappe.ping')
        print(r.status, await r.text())
asyncio.run(test())
"
# Expected: 200 {"message":"pong"}
```

## Langkah 6: Konfigurasi Webhook di Frappe

Di Frappe, buat Server Script atau hook yang mengirim POST ke bot saat admin menekan "Request Inspeksi":

**URL webhook (dari dalam Docker network):**
```
http://inspection-bot:8443/webhook/inspection-request
```

**Header yang harus dikirim:**
```
Content-Type: application/json
X-Inspection-Webhook-Secret: <sama dengan WEBHOOK_SHARED_SECRET di .env>
```

**Body JSON:**
```json
{
  "event": "inspection_requested",
  "motor_tarikan": "PJ-001",
  "nopol": "B 1234 XYZ",
  "merk": "Honda",
  "model": "Beat",
  "tahun": "2022",
  "warna": "Merah",
  "tipe_inspeksi": "Inspeksi",
  "inspector_chat_id": "123456789"
}
```

## Langkah 7: Expose Webhook ke Internet (Opsional)

Hanya diperlukan jika Frappe berada di server berbeda atau perlu monitoring dari luar.

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

### Opsi B: Hanya internal (recommended)

Karena Frappe dan bot di VPS yang sama dan sudah share network, tidak perlu expose ke internet. Hapus port mapping di `docker-compose.yml` jika tidak butuh akses dari luar:

```yaml
services:
  bot:
    # hapus bagian ports jika tidak perlu akses dari luar
    # ports:
    #   - "8443:8443"
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
docker compose logs -f bot          # Bot logs (realtime)
docker compose logs --tail=100 bot  # Last 100 lines
docker compose logs -f redis        # Redis logs
```

### Restart

```bash
docker compose restart bot
```

### Stop

```bash
docker compose down          # Stop containers (data Redis tetap)
docker compose down -v       # Stop + hapus volume Redis (data session hilang!)
```

## Troubleshooting

| Masalah | Diagnosa | Solusi |
|---------|----------|--------|
| Bot tidak bisa konek ke Frappe | `docker exec inspection-bot curl http://backend:8000` | Pastikan bot join `frappe_network` dan Frappe sudah running |
| `frappe_network not found` | `docker network ls` | Start Frappe dulu, atau buat manual: `docker network create frappe_network` |
| Redis connection refused | `docker compose ps` | Pastikan redis container healthy |
| Webhook 403 Forbidden | Cek header `X-Inspection-Webhook-Secret` | Samakan secret di `.env` dan Server Script Frappe |
| Bot tidak merespons di Telegram | Cek `TELEGRAM_BOT_TOKEN` | Pastikan token valid, bot belum di-block user |
| Container restart loop | `docker compose logs bot` | Biasanya env var wajib belum diisi di `.env` |
| `network frappe_network declared as external, but could not be found` | Frappe belum start | Start Frappe: `docker compose -f frappe-docker.yml up -d` |
