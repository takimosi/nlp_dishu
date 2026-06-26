# Public Deployment

Target: one Ubuntu 22.04 server with Docker Compose.

## 1. Open the security group

In the cloud console, allow inbound traffic:

- TCP 22 from your IP, for SSH.
- TCP 80 from `0.0.0.0/0`, for the public demo site.

## 2. Install Docker on the server

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
docker compose version
```

## 3. Clone and configure

```bash
git clone https://github.com/takimosi/nlp_dishu.git
cd nlp_dishu
cp .env.example .env
nano .env
```

Put the real key in `.env`:

```env
DEEPSEEK_API_KEY=your-real-key
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
FLASK_SECRET_KEY=change-me
```

Never commit `.env`.

## 4. Start

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100
```

The public URL is:

```text
http://112.124.68.145/
```

## 5. Update

```bash
git pull
docker compose up -d --build
```
