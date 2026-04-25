
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


## 产品功能

- 支持 jpg / png / webp / gif
- 支持批量拖拽上传
- 支持压缩前后在线对比
- 使用 Docker 开发与部署
- 后端使用 Python + FastAPI
- 前端使用 React + Vite
- 前后端分离，但对外只暴露一个端口

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
当前仓库的 Docker 镜像中默认也包含：
- `pngquant`（PNG 视觉无损量化）
- `zopflipng`（PNG 无损优化）
- `cwebp`（WebP lossless / near-lossless / lossy）
- `cjpeg` / `jpegtran`（JPEG 高质量压缩与无损优化）

如果你修改了镜像，请进入容器确认：

```bash
docker compose exec backend which gifsicle
docker compose exec backend which pngquant
docker compose exec backend which cwebp
docker compose exec backend which cjpeg
docker compose exec backend which jpegtran
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

### 3. 自动构建 Docker 镜像

仓库已增加 GitHub Actions 自动构建流程，风格与 `zhaoolee/notes` 保持一致：

- 推送 `dev` 分支时，自动构建并发布 Docker Hub `zhaoolee/bbduck:dev`
- 推送 `main` 分支时，自动构建并发布 Docker Hub `zhaoolee/bbduck:latest`
- 推送 `v*` tag 时，会额外发布同名版本标签
- 所有构建都会附带一个 `sha-<commit>` 标签，便于回滚
- 也支持在 GitHub Actions 页面手动触发一次构建

GitHub 仓库需要提前配置：

- `secrets.DOCKERHUB_USERNAME`
- `secrets.DOCKERHUB_TOKEN`
- 可选：`vars.DOCKERHUB_REPOSITORY`（不填时默认使用 `bbduck`）

#### 如何配置 Docker Hub 自动发布

```bash
# 1. 设置 Docker Hub 用户名
gh secret set DOCKERHUB_USERNAME --repo zhaoolee/bbduck

# 2. 设置 Docker Hub Access Token
gh secret set DOCKERHUB_TOKEN --repo zhaoolee/bbduck

# 3. 设置镜像仓库名（可选，不设时默认 bbduck）
gh variable set DOCKERHUB_REPOSITORY --body 'bbduck' --repo zhaoolee/bbduck

# 4. 检查是否配置成功
gh secret list --repo zhaoolee/bbduck
gh variable list --repo zhaoolee/bbduck
```

拉取示例：

```bash
docker pull zhaoolee/bbduck:dev
docker pull zhaoolee/bbduck:latest
```

### 4. 常用自测命令

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
获取当前支持格式、质量阈值、压缩 profile、最小收益阈值等运行配置。

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



## 首页默认评价图片

首页在用户未上传图片时，会优先请求后端 `GET /api/evaluation-images` 作为默认展示队列。
前端会优先展示已打包的首页评测图；只有接口失败或确认没有评测图时，才会回退到内置 `example` 示例。

默认目录：

```text
backend/data/evaluation-images
backend/data/evaluation-compressed
```

`backend/data/evaluation-images` 存放原图，`backend/data/evaluation-compressed` 存放对应的已打包压缩图；两个目录都会随 Docker 镜像一起打包。

Docker Compose 开发模式会自动启动 `evaluation-watcher` 服务，监听原图目录变化并自动刷新打包压缩图：

```bash
docker compose up --build
```

开发时把图片放进 `backend/data/evaluation-images`，新增、删除、修改后都会自动重新生成 `backend/data/evaluation-compressed`，不需要手动重启后端。

如果用 `docker run` 跑生产镜像，可以这样挂载：

```bash
docker run -d --rm --name bbduck -p 28642:8000 \
  -v /absolute/path/to/evaluation-images:/app/data/evaluation-images \
  -v /absolute/path/to/evaluation-compressed:/app/data/evaluation-compressed \
  zhaoolee/bbduck:latest
```

可通过环境变量覆盖：

```bash
BBDUCK_EVALUATION_IMAGES_DIR=/absolute/path/to/evaluation-images
BBDUCK_EVALUATION_COMPRESSED_DIR=/absolute/path/to/evaluation-compressed
```

把原图按文件名编号放进去即可，例如：

```text
backend/data/evaluation-images/00001.jpg
backend/data/evaluation-images/00002.png
backend/data/evaluation-images/00003.gif
backend/data/evaluation-images/00004.webp
```

对应的打包压缩图放在：

```text
backend/data/evaluation-compressed/00001.webp
backend/data/evaluation-compressed/00002.png
backend/data/evaluation-compressed/00003.gif
backend/data/evaluation-compressed/00004.webp
```

支持格式：`jpg`、`jpeg`、`png`、`gif`、`webp`。程序会忽略其他文件、目录和隐藏文件，并按文件名中的编号自然升序展示。接口会优先匹配同 stem 的打包压缩图，例如 `00001.png` 会优先查找 `backend/data/evaluation-compressed/00001.*`，其次查找 `00001.compressed.*`，并通过安全静态接口 `/api/evaluation-compressed/{file_name}` 返回。只有对应打包压缩图缺失时，才会退回运行时压缩兜底。

非 Docker 本地运行可直接启动 watcher：

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/watch_evaluation_assets.py
```

如果只想手动构建一次，也可以继续使用：

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/build_evaluation_assets.py
```

## 设计目标

1. Python 作为主语言，负责压缩编排、质量评估、REST API。
2. 当前默认压缩模式为 `visual-lossless`，目标是在肉眼几乎看不出差异的前提下尽量缩小体积；当候选收益过低或质量风险过高时，直接回退原图。
3. 压缩算法优先使用成熟的开源工具链：
   - JPEG: jpegtran + cjpeg + Pillow fallback
   - PNG: pngquant（高质量量化）+ zopflipng + Pillow optimize fallback
   - WebP: cwebp（lossless / near-lossless / high-quality lossy）+ Pillow fallback
   - GIF: gifsicle + Pillow fallback
4. 使用 SSIM / PSNR 评估压缩前后的视觉差异，并针对不同 profile 使用不同阈值。
5. 当前支持三种压缩 profile：
   - `safe`：更保守，优先无损或超高质量候选
   - `visual-lossless`：默认模式，接近 PP鸭的视觉无损压缩路线
   - `aggressive`：更追求体积，但仍会经过质量阈值筛选
6. 开发环境通过 Vite 代理 `/api` 到 FastAPI，只暴露前端端口。
7. 生产环境使用 FastAPI 直接托管前端静态产物，实现单容器单端口部署。
