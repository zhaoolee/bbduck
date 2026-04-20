# BBDuck Server Implementation Plan

> For Hermes: Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 搭建一个支持 jpg/png/webp/gif 压缩、批量拖拽上传、在线对比预览、Docker 开发与部署的开源项目。

**Architecture:** 开发环境由 React + Vite 作为唯一对外入口，代理 FastAPI API；生产环境由 FastAPI 托管前端构建产物并暴露单端口。压缩能力通过 Python 调度多个开源图像压缩器，并用 SSIM / PSNR 做质量校验。

**Tech Stack:** Python 3.11, FastAPI, Pillow, scikit-image, React, Vite, TypeScript, Docker

---

## Task 1: 初始化目录与配置

**Objective:** 建立单仓库结构与基础配置。

**Files:**
- Create: `backend/`
- Create: `frontend/`
- Create: `docs/`
- Create: `docker-compose.yml`
- Create: `Dockerfile`
- Create: `README.md`

**Steps:**
1. 创建 monorepo 目录结构。
2. 写入 `.gitignore`。
3. 写入 Docker 开发与部署脚手架。
4. 写入 README，明确“单端口 + 前后端分离”模式。

## Task 2: 建立 FastAPI 基础 API

**Objective:** 提供健康检查、配置读取、批量压缩入口。

**Files:**
- Create: `backend/app/main.py`
- Create: `backend/app/api/routes.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/schemas.py`
- Test: `backend/tests/test_api.py`

**Steps:**
1. 先写 `health` 与 `config` 的测试。
2. 运行 pytest，确认失败。
3. 写最小 FastAPI 实现。
4. 再补上 `compress` 路由的接口结构。

## Task 3: 建立压缩管线骨架

**Objective:** 为不同格式准备统一的压缩策略接口。

**Files:**
- Create: `backend/app/services/compress.py`
- Create: `backend/app/services/metrics.py`
- Test: `backend/tests/test_pipeline.py`

**Steps:**
1. 为格式校验、策略选择、结果结构先写测试。
2. 实现策略注册表。
3. 实现候选压缩结果比较逻辑。
4. 接入 SSIM / PSNR 骨架。

## Task 4: 建立 React + Vite 前端

**Objective:** 提供上传入口、状态列表、对比预览。

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`

**Steps:**
1. 初始化 Vite React TypeScript 项目。
2. 配置 `/api` 代理到 backend 容器。
3. 写拖拽上传 UI。
4. 写结果卡片与对比组件。

## Task 5: 打通前后端联调

**Objective:** 让上传、压缩、展示形成闭环。

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `backend/app/api/routes.py`
- Modify: `backend/app/services/compress.py`

**Steps:**
1. 前端提交 `FormData`。
2. 后端接收批量文件。
3. 返回结果 JSON。
4. 前端渲染对比区。

## Task 6: 生产部署单端口化

**Objective:** 使用一个 Docker 镜像完成前端静态资源托管与 API 服务。

**Files:**
- Modify: `Dockerfile`
- Modify: `backend/app/main.py`

**Steps:**
1. 使用多阶段构建产出前端 dist。
2. 将 dist 拷贝到 Python 运行镜像。
3. FastAPI 挂载静态目录和 fallback 路由。
4. 验证对外只暴露一个端口。

## Task 7: 完善文档与开源准备

**Objective:** 让项目适合公开发布。

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`

**Steps:**
1. 补充运行截图。
2. 补充贡献指南。
3. 补充 Roadmap。
4. 准备 GitHub Actions / License / Issue Templates。
