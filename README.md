# 视频号短剧管理系统

微信视频号短剧上线管理 Web 系统，支持多人协作、权限分级、Excel 导入导出。

## 功能概览

- 短剧数据的增删改查、分页、筛选、搜索
- Excel 批量导入，自动去重和冲突检测
- 一键导出当前筛选数据为 Excel
- 账号密码登录，分管理员 / 普通用户两种角色
- 软件激活码管理，可为桌面端签发授权并校验机器绑定
- Docker 一键部署，数据单文件存储，易于迁移

## 权限说明

| 功能 | 管理员 (admin) | 普通用户 (user) |
|------|:-:|:-:|
| 查看列表 | ✅ | ✅ |
| 导出 Excel | ✅ | ✅ |
| 标记"是否上传" | ✅ | ✅ |
| 新增 / 编辑短剧 | ✅ | ❌ |
| 审核录入 | ✅ | ❌ |
| 导入 Excel | ✅ | ❌ |
| 删除 | ✅ | ❌ |
| 用户管理 | ✅ | ❌ |
| 激活码管理 | ✅ | ❌ |

## 短剧字段说明

| 字段 | 说明 |
|------|------|
| 日期 | 上线日期 |
| 原剧名 | 原始剧本名称 |
| 新剧名 | 上线使用的剧名 |
| 集数 | 总集数 |
| 时长(分钟) | 总时长 |
| 是否审核通过 | 是 / 否 |
| 是否上传 | 是 / 否（普通用户可操作） |
| 素材 | 素材描述 |
| 推广语 | 对外推广文案 |
| 简介 | 剧情简介 |
| 公司 | 所属公司 |

## 技术栈

- **后端**：Python 3 + Flask + Gunicorn
- **数据库**：SQLite（单文件，存于 `data/dramas.db`）
- **前端**：Bootstrap 5 + 原生 JavaScript
- **部署**：Docker + Docker Compose

## 快速开始

### 方式一：Docker 部署（推荐）

**前提：** 服务器已安装 Docker 和 Docker Compose

```bash
# 1. 复制环境变量模板
cp .env.example .env.production

# 2. 修改 .env.production 中的密钥
# 建议使用 python3 -c "import secrets; print(secrets.token_hex(32))" 生成随机值

# 3. 一键发布到服务器
./scripts/deploy.sh --host user@your-server --env-file .env.production

# 4. 登录服务器查看日志
ssh user@your-server
cd /opt/short-vedio-manage
docker compose logs -f

# 5. 访问
# http://your-server-ip:8000
```

**常用管理命令：**

```bash
docker compose stop             # 停止
docker compose start            # 启动
docker compose restart          # 重启
docker compose down             # 停止并删除容器（数据不丢失）
docker compose up -d --build    # 更新代码后重新构建启动
```

### 方式二：直接运行（本地开发）

```bash
# 安装依赖
pip3 install flask openpyxl gunicorn werkzeug

# 启动（开发模式）
python3 app.py

# 访问
# http://localhost:8000
```

## 默认账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | admin123 | 管理员 |
| user1 | user123 | 普通用户 |

> **安全提示：** 首次登录后，请立即在「用户管理」页面修改默认密码。

## 配置 Nginx（可选，用于 HTTPS / 域名访问）

取消注释 `docker-compose.yml` 中的 nginx 服务配置，并修改 `nginx.conf`：

```yaml
# docker-compose.yml
nginx:
  image: nginx:1.25-alpine
  volumes:
    - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
  depends_on:
    - app
  ports:
    - "80:80"
```

## Excel 导入说明

### 文件格式要求

支持 `.xlsx` 和 `.xls` 格式，第一行为表头，表头名称须与以下对应：

```
日期 | 原剧名 | 新剧名 | 集数 | 时间(分钟) | 是否审核通过 | 是否上传 | 素材 | 推广语 | 简介 | 公司
```

### 去重规则

导入时以 **原剧名 + 新剧名** 组合作为唯一标识：

- **完全重复**（两个字段均相同）→ 跳过，计入"重复"数量
- **原剧名相同、新剧名不同** → 标记为"冲突"，不导入，在结果页展示供人工判断
- **全新数据** → 正常导入，计入"新增"数量

## 数据迁移

数据全部存储在 `data/dramas.db` 单文件中。

```bash
# 备份
cp data/dramas.db data/dramas_backup_$(date +%Y%m%d).db

# 迁移到新服务器（停服迁移）
docker compose down
scp data/dramas.db user@new-server:/opt/short-vedio-manage/data/
# 在新服务器启动即可
docker compose up -d
```

## 自动化升级到服务器

项目已内置 `scripts/deploy.sh`，适合当前这种单体 Flask + SQLite + Docker Compose 发布方式。

```bash
# 1. 准备生产环境变量
cp .env.example .env.production
vim .env.production

# 2. 执行发布
./scripts/deploy.sh --host root@your-server --env-file .env.production
```

脚本会自动完成：

- 上传最新代码到服务器
- 保留线上 `data/` 目录，不覆盖数据库
- 发布前备份 `data/dramas.db`
- 执行 `docker compose up -d --build --remove-orphans`

## 项目结构

```
short-vedio-manage/
├── app.py              # Flask 后端（全部路由逻辑）
├── requirements.txt    # Python 依赖
├── Dockerfile          # 容器构建配置
├── docker-compose.yml  # 服务编排配置
├── nginx.conf          # Nginx 反向代理配置（可选）
├── data/
│   └── dramas.db      # SQLite 数据库（自动创建）
├── templates/
│   ├── login.html     # 登录页
│   └── index.html     # 主页面
└── static/
    ├── app.js         # 前端逻辑
    └── style.css      # 样式
```

## API 接口

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/dramas` | 登录用户 | 获取短剧列表（支持分页、筛选） |
| POST | `/api/dramas` | 管理员 | 新增短剧 |
| PUT | `/api/dramas/<id>` | 管理员 | 编辑短剧 |
| DELETE | `/api/dramas/<id>` | 管理员 | 删除短剧 |
| POST | `/api/dramas/batch-delete` | 管理员 | 批量删除 |
| PATCH | `/api/dramas/<id>/upload` | 登录用户 | 切换上传状态 |
| POST | `/api/import` | 管理员 | 导入 Excel |
| GET | `/api/export` | 登录用户 | 导出 Excel |
| GET | `/api/companies` | 登录用户 | 获取公司列表 |
| GET | `/api/users` | 管理员 | 获取用户列表 |
| POST | `/api/users` | 管理员 | 新增用户 |
| DELETE | `/api/users/<id>` | 管理员 | 删除用户 |
| PUT | `/api/users/<id>/password` | 管理员 | 修改用户密码 |
| GET | `/api/licenses` | 管理员 | 获取激活码列表 |
| POST | `/api/licenses` | 管理员 | 新建激活码 |
| GET | `/api/licenses/<id>/activations` | 管理员 | 查看设备绑定明细 |
| POST | `/api/licenses/<id>/disable` | 管理员 | 停用激活码 |
| POST | `/api/licenses/<id>/enable` | 管理员 | 启用激活码 |
| POST | `/api/licenses/<id>/unbind` | 管理员 | 解绑指定设备 |
| POST | `/license/activate` | 客户端 | 桌面端激活授权 |
| POST | `/license/verify` | 客户端 | 桌面端联网校验 |

## 桌面端授权对接

桌面端激活服务地址建议配置成：

```text
https://your-domain.com
```

当前已兼容以下路径：

- `/license/activate`
- `/license/verify`
- `/client-api/license/activate`
- `/client-api/license/verify`

部署时请至少配置以下环境变量：

```bash
SECRET_KEY=your-flask-secret
LICENSE_SIGNING_KEY=your-license-signing-secret
```

建议 `LICENSE_SIGNING_KEY` 与 `SECRET_KEY` 不同，并开启 HTTPS。
