# 部署文档

## 目录

- [项目结构](#项目结构)
- [环境要求](#环境要求)
- [方式一：Docker 部署（推荐）](#方式一docker-部署推荐)
- [方式一-A：自动化发布升级](#方式一-a自动化发布升级)
- [方式二：直接部署（无 Docker）](#方式二直接部署无-docker)
- [配置 Nginx + HTTPS（可选）](#配置-nginx--https可选)
- [数据备份与迁移](#数据备份与迁移)
- [日常维护](#日常维护)
- [故障排查](#故障排查)

---

## 项目结构

```
short-vedio-manage/
├── app.py                  # Flask 后端（全部逻辑）
├── requirements.txt        # Python 依赖
├── Dockerfile              # 容器构建
├── docker-compose.yml      # 服务编排
├── nginx.conf              # Nginx 反向代理配置
├── data/
│   └── dramas.db          # SQLite 数据库（自动创建）
├── templates/
│   ├── login.html
│   └── index.html
└── static/
    ├── app.js
    └── style.css
```

> **数据只有一个文件**：`data/dramas.db`，迁移时只需拷贝此文件。

---

## 环境要求

### Docker 部署
| 组件 | 最低版本 |
|------|---------|
| Docker | 20.10+ |
| Docker Compose | 2.0+ |
| 操作系统 | Ubuntu 20.04 / CentOS 7 / Debian 11 及以上 |

### 直接部署
| 组件 | 最低版本 |
|------|---------|
| Python | 3.9+ |
| pip | 21.0+ |

---

## 方式一：Docker 部署（推荐）

### 1. 安装 Docker

```bash
# Ubuntu / Debian
curl -fsSL https://get.docker.com | sh

# 验证安装
docker --version
docker compose version
```

### 2. 准备环境变量

```bash
cp .env.example .env.production
```

### 3. 修改 SECRET_KEY 和 LICENSE_SIGNING_KEY（必须）

> ⚠️ 生产环境必须修改，否则 Session 和桌面端授权 token 都存在安全风险。

```bash
vim .env.production
```

将默认值替换为随机字符串：

```bash
# 修改前
SECRET_KEY=replace-with-a-random-secret
LICENSE_SIGNING_KEY=replace-with-a-random-license-signing-key

# 修改后（示例，请自行生成）
SECRET_KEY=aB3xK9mP2qR7vL4nJ8wA3sD6fG1hY5tU0eC
LICENSE_SIGNING_KEY=7d9f4e5a1b2c3d4e5f60718293ab4cd5
```

> 生成随机 Key 的方法：
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(32))"
> ```

### 4. 上传项目并启动服务

```bash
./scripts/deploy.sh --host root@<服务器IP> --env-file .env.production
```

### 5. 验证启动

```bash
# 登录服务器
ssh root@<服务器IP>
cd /opt/short-vedio-manage

# 查看容器状态（Status 应为 Up）
docker compose ps

# 查看启动日志
docker compose logs -f
```

正常输出示例：
```
short-vedio-manage-app-1  running  0.0.0.0:8000->8000/tcp
```

### 方式一-A：自动化发布升级

后续每次升级都可以直接复用下面这条命令：

```bash
./scripts/deploy.sh --host root@<服务器IP> --env-file .env.production
```

脚本会自动：

- 打包当前项目代码，但排除本地数据库、文档和缓存文件
- 通过 SSH 上传到服务器指定目录
- 保留线上 `data/` 数据目录
- 发布前备份线上 `data/dramas.db`
- 执行 `docker compose up -d --build --remove-orphans`

常见用法：

```bash
# 指定服务器目录
./scripts/deploy.sh --host root@<服务器IP> --remote-dir /srv/short-vedio-manage --env-file .env.production

# 指定 SSH 私钥和端口
./scripts/deploy.sh --host deploy@<服务器IP> --identity ~/.ssh/prod_id_ed25519 --port 2222 --env-file .env.production

# 如果服务器已经有 .env，只同步代码
./scripts/deploy.sh --host root@<服务器IP>
```

### 6. 开放防火墙端口

```bash
# Ubuntu (ufw)
ufw allow 8000
ufw reload

# CentOS (firewalld)
firewall-cmd --permanent --add-port=8000/tcp
firewall-cmd --reload
```

> **云服务器（阿里云 / 腾讯云）**：还需在控制台「安全组」中放行 TCP 8000 端口。

### 7. 访问系统

```
http://<服务器IP>:8000
```

默认账号：

| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | admin123 | 管理员 |
| user1 | user123 | 普通用户 |

> ⚠️ 首次登录后立即修改默认密码。

---

## 方式二：直接部署（无 Docker）

适合无法安装 Docker 的环境，使用 Python + Gunicorn 直接运行。

### 1. 安装 Python 依赖

```bash
cd /opt/short-vedio-manage
pip3 install -r requirements.txt
```

### 2. 设置环境变量

```bash
export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
export LICENSE_SIGNING_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 持久化（写入 ~/.bashrc 或 /etc/environment）
echo "export SECRET_KEY=<你的Key>" >> ~/.bashrc
echo "export LICENSE_SIGNING_KEY=<你的LicenseSigningKey>" >> ~/.bashrc
source ~/.bashrc
```

### 3. 启动服务

**临时启动（测试用）：**
```bash
python3 app.py
```

**生产启动（Gunicorn）：**
```bash
gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 60 --daemon app:app
```

### 4. 配置 systemd 开机自启（推荐）

创建服务文件：

```bash
vim /etc/systemd/system/short-vedio.service
```

写入以下内容：

```ini
[Unit]
Description=Short Video Management System
After=network.target

[Service]
User=root
WorkingDirectory=/opt/short-vedio-manage
Environment="SECRET_KEY=<替换为你的Key>"
Environment="LICENSE_SIGNING_KEY=<替换为你的LicenseSigningKey>"
ExecStart=gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 60 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
systemctl daemon-reload
systemctl enable short-vedio
systemctl start short-vedio

# 查看状态
systemctl status short-vedio
```

---

## 配置 Nginx + HTTPS（可选）

有域名时，建议配置 Nginx 反向代理 + Let's Encrypt 免费 HTTPS。桌面端激活码会调用 `/license/activate` 和 `/license/verify`，务必通过 HTTPS 暴露。

### 方案 A：Docker Compose 中启用 Nginx

取消 `docker-compose.yml` 中 nginx 服务的注释：

```yaml
version: '3.8'
services:
  app:
    build: .
    volumes:
      - ${APP_DATA_DIR:-./data}:/app/data
    environment:
      SECRET_KEY: ${SECRET_KEY}
      LICENSE_SIGNING_KEY: ${LICENSE_SIGNING_KEY}
    restart: unless-stopped
    # 不再对外暴露 8000，由 nginx 代理
    expose:
      - "8000"

  nginx:
    image: nginx:1.25-alpine
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - app
    ports:
      - "80:80"
    restart: unless-stopped
```

修改 `nginx.conf` 中的域名：

```nginx
server {
    listen 80;
    server_name your-domain.com;    # ← 替换为你的域名

    client_max_body_size 50m;

    location / {
        proxy_pass http://app:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 方案 B：服务器原生 Nginx + Certbot HTTPS

```bash
# 安装 Nginx 和 Certbot
apt install nginx certbot python3-certbot-nginx -y

# 创建站点配置
cat > /etc/nginx/sites-available/short-vedio << 'EOF'
server {
    listen 80;
    server_name your-domain.com;
    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
EOF

ln -s /etc/nginx/sites-available/short-vedio /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 申请 HTTPS 证书（自动配置 Nginx）
certbot --nginx -d your-domain.com
```

---

## 数据备份与迁移

### 手动备份

```bash
# 备份数据库（带日期戳）
cp /opt/short-vedio-manage/data/dramas.db \
   /opt/short-vedio-manage/data/dramas_$(date +%Y%m%d_%H%M%S).db
```

### 定时自动备份（crontab）

```bash
crontab -e
```

添加以下内容（每天凌晨 3 点备份，保留最近 30 天）：

```cron
0 3 * * * cp /opt/short-vedio-manage/data/dramas.db /opt/backups/dramas_$(date +\%Y\%m\%d).db && find /opt/backups -name "dramas_*.db" -mtime +30 -delete
```

### 迁移到新服务器

```bash
# 1. 停止旧服务器服务
cd /opt/short-vedio-manage
docker compose down

# 2. 打包整个项目目录（含数据库）
tar -czf short-vedio-manage.tar.gz /opt/short-vedio-manage

# 3. 传输到新服务器
scp short-vedio-manage.tar.gz root@<新服务器IP>:/opt/

# 4. 新服务器解压并启动
ssh root@<新服务器IP>
cd /opt && tar -xzf short-vedio-manage.tar.gz
cd short-vedio-manage
docker compose up -d --build
```

> 也可以只迁移数据库：将 `data/dramas.db` 单独拷贝到新服务器对应目录即可。

---

## 日常维护

### Docker 方式常用命令

```bash
cd /opt/short-vedio-manage

# 查看运行状态
docker compose ps

# 查看实时日志
docker compose logs -f

# 查看最近 100 行日志
docker compose logs --tail=100

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 更新代码后重新部署
git pull
docker compose up -d --build
```

### 直接部署常用命令

```bash
# 查看服务状态
systemctl status short-vedio

# 重启服务
systemctl restart short-vedio

# 查看日志
journalctl -u short-vedio -f

# 更新代码后重启
git pull && systemctl restart short-vedio
```

### 查看数据库（可选）

```bash
# 进入容器内查看（Docker 方式）
docker compose exec app python3 -c "
import sqlite3
conn = sqlite3.connect('data/dramas.db')
print('短剧总数:', conn.execute('SELECT COUNT(*) FROM dramas').fetchone()[0])
print('用户总数:', conn.execute('SELECT COUNT(*) FROM users').fetchone()[0])
"
```

---

## 故障排查

### 容器无法启动

```bash
# 查看详细错误日志
docker compose logs app

# 常见原因：
# 1. 端口 8000 被占用
lsof -i :8000

# 2. data 目录权限问题
chmod 755 /opt/short-vedio-manage/data
```

### 页面打不开

```bash
# 1. 确认容器在运行
docker compose ps

# 2. 确认端口监听
ss -tlnp | grep 8000

# 3. 确认防火墙已放行
ufw status | grep 8000
# 或
firewall-cmd --list-ports
```

### 登录后跳回登录页（Session 失效）

原因：`SECRET_KEY` 为空或每次重启变化。

```bash
# 检查环境变量是否正确设置
docker compose exec app env | grep SECRET_KEY
```

确保 `docker-compose.yml` 中 `SECRET_KEY` 为固定值，不要使用动态生成的值。

当前推荐做法：把固定值写入服务器上的 `.env` 文件，由 `docker-compose.yml` 读取。

### 导入 Excel 失败

- 确认文件格式为 `.xlsx` 或 `.xls`
- 确认第一行表头与系统要求一致（含「原剧名」和「新剧名」列）
- 查看日志获取详细报错：`docker compose logs -f`

### 磁盘空间不足

```bash
# 查看磁盘使用
df -h

# 清理 Docker 无用镜像
docker image prune -f

# 清理旧备份文件
find /opt/backups -name "dramas_*.db" -mtime +30 -delete
```

---

## 端口与服务说明

| 服务 | 端口 | 说明 |
|------|------|------|
| Gunicorn (Flask) | 8000 | 应用服务，直接访问或经 Nginx 代理 |
| Nginx（可选） | 80 / 443 | 反向代理，HTTPS 终结 |

---

## 配置速查

| 配置项 | 位置 | 说明 |
|--------|------|------|
| SECRET_KEY | `.env` | Session 加密密钥，必须修改 |
| 数据库文件 | `data/dramas.db` | SQLite 数据库，自动创建 |
| 对外端口 | `.env` → `APP_PORT` | 默认 8000，可改为 80 |
| 上传文件大小限制 | `nginx.conf` → client_max_body_size | 默认 50MB |
| Gunicorn workers | `Dockerfile` → CMD | 默认 2 个工作进程 |
