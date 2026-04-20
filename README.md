# bbduck-server

BBDuck Server 是一个面向开源社区的图片压缩服务脚手架，目标是做成“Web 版 PP鸭”，并明确以“保真优先”为产品定位：

- 支持 jpg / png / webp / gif
- 支持批量拖拽上传
- 支持压缩前后在线对比
- 使用 Docker 开发与部署
- 后端使用 Python + FastAPI
- 前端使用 React + Vite
- 前后端分离，但对外只暴露一个端口

## 设计目标

1. Python 作为主语言，负责压缩编排、质量评估、REST API。
2. 压缩算法优先使用成熟的开源工具链：
   - JPEG: mozJPEG
   - PNG: zopflipng
   - WebP: cwebp / Pillow fallback
   - GIF: gifsicle
3. 使用 SSIM / PSNR 评估压缩前后的视觉差异。
4. 如果某轮压缩后的质量低于阈值，则自动轮换下一组策略。
5. 开发环境通过 Vite 代理 `/api` 到 FastAPI，只暴露前端端口。
6. 生产环境使用 FastAPI 直接托管前端静态产物，实现单容器单端口部署。

## 项目结构

```
bbduck-server/
├── backend/                  # FastAPI 服务
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── services/
│   │   └── main.py
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                 # React + Vite 客户端
│   ├── src/
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
├── docs/
│   ├── architecture.md
│   └── implementation-plan.md
├── docker-compose.yml        # 开发编排
└── Dockerfile                # 生产镜像（单端口）
```

## 开发模式

### 1. Docker 开发

开发和联调统一强制走 Docker：

- `frontend` 容器运行 Vite
- `backend` 容器运行 FastAPI
- 浏览器访问 Vite
- 前端通过 `/api` 访问后端，Vite 负责代理
- 开发阶段只暴露 `5173`

```bash
docker compose up --build
```

打开：

- http://localhost:5173

#### GIF 压缩依赖

GIF 高压缩率依赖 `gifsicle`。
当前仓库的 Docker 镜像中应包含它；如果你修改了镜像，请进入容器确认：

```bash
docker compose exec backend which gifsicle
docker compose exec backend gifsicle --version | head -n 1
```

### 2. Docker 部署

生产构建同样强制走 Docker，使用根目录 `Dockerfile`：

- 先构建前端静态文件
- 再构建 Python 运行镜像
- FastAPI 同时提供 API 和前端静态资源
- 对外只暴露 `8000`

```bash
docker build -t bbduck-server .
docker run --rm -p 8000:8000 bbduck-server
```

打开：

- http://localhost:8000

### 3. 常用自测命令

后端测试：

```bash
cd backend
source .venv/bin/activate
PYTHONPATH=. pytest tests/test_pipeline.py -q
```

前端构建检查：

```bash
cd frontend
npm run build
```

本地 API 压缩验证（在容器已启动时）：

```bash
curl -s -X POST \
  -F 'parallelism=1' \
  -F 'files=@/absolute/path/to/sample.gif' \
  http://127.0.0.1:8000/api/compress
```

## API 草案

### `GET /api/health`
检查服务是否启动。

### `GET /api/config`
获取当前支持格式、质量阈值、压缩策略。

### `POST /api/compress`
批量上传图片并返回压缩结果：

- 原图信息
- 压缩后文件信息
- 压缩率
- SSIM
- PSNR
- 预览 URL

### `GET /api/files/{file_name}`
读取压缩后静态文件，供前端对比预览。

## 前端交互草案

- 批量拖拽上传
- 上传队列
- 单张图片压缩状态
- 压缩结果卡片
- 左右滑块对比（类似 PP鸭）
- 支持查看原图 / 压缩图尺寸、体积、压缩率、SSIM、PSNR

## 美术风格

视觉方向参考：

- https://github.com/zhaoolee/daodejing

建议采用：

- 大留白 + 东方极简
- 黑白灰为主 + 青绿色点缀
- 卡片式层次 + 柔和阴影
- 中文优先排版

## 当前状态

当前仓库已完成：

- 项目目录初始化
- FastAPI 基础 API
- React + Vite 前端骨架
- Docker 开发 / 部署骨架
- 压缩管线接口与质量评估骨架
- 详细架构文档和实现计划

下一步建议：

1. 安装 mozjpeg / zopflipng / gifsicle 等系统依赖
2. 完善真实压缩策略与回退逻辑
3. 为前端对比组件加入拖动蒙版与缩放
4. 增加任务队列、缓存、限流与历史记录
