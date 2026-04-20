# BBDuck Server 架构设计

## 1. 项目定位

BBDuck Server 是一个面向浏览器的图片压缩服务，核心体验参考 PP鸭：

- 批量上传
- 进度可视化
- 结果列表
- 压缩前后对比
- 质量指标可解释

同时整体的审美会参考 `zhaoolee/daodejing` 的简洁、东方、克制的表达方式。

## 2. 总体架构

### 开发架构

```
Browser
  ↓
Vite Dev Server :5173
  ├─ 提供 React 页面
  └─ 代理 /api → FastAPI:8000
                 ↓
              Compression Pipeline
                 ↓
         mozJPEG / zopflipng / cwebp / gifsicle
```

特点：

- 对开发者只暴露 1 个端口：5173
- 前后端解耦，开发体验好
- 可以热更新前端与后端

### 生产架构

```
Browser
  ↓
FastAPI :8000
  ├─ /api/* → REST API
  ├─ /assets/* → React build 静态资源
  └─ /* → index.html
```

特点：

- 单镜像部署
- 单端口对外服务
- 容器化简单，适合自托管与工作流调用

## 3. 后端模块划分

### `app/main.py`
负责：

- 初始化 FastAPI
- 注册 API 路由
- 挂载静态目录
- 为 SPA 提供 fallback 路由

### `app/core/config.py`
负责：

- 读取环境变量
- 统一管理阈值
- 统一管理目录位置
- 声明支持格式和默认策略

### `app/api/routes.py`
负责：

- `health` 健康检查
- `config` 配置读取
- `compress` 批量压缩入口
- 文件静态输出

### `app/services/compress.py`
负责：

- 保存上传文件
- 根据格式挑选压缩策略
- 轮换算法组合
- 计算体积变化
- 返回压缩结果 DTO

### `app/services/metrics.py`
负责：

- 计算 SSIM
- 计算 PSNR
- 判断是否低于阈值

## 4. 压缩策略设计

### 4.1 JPEG

首选：mozJPEG

可轮换参数：

- quality=82
- quality=76
- quality=70

回退策略：

- Pillow JPEG optimize

### 4.2 PNG

首选：zopflipng

可轮换参数：

- `--iterations=15`
- `--iterations=50`
- palette 优化

回退策略：

- Pillow optimize=True

### 4.3 WebP

首选：cwebp

可轮换参数：

- quality=85
- quality=80
- quality=75

回退策略：

- Pillow WebP 编码

### 4.4 GIF

首选：gifsicle

可轮换参数：

- `-O2`
- `-O3`

回退策略：

- 保守复制 / Pillow 再编码

## 5. 质量评估策略

每次生成候选压缩结果后：

1. 读取原图与候选图
2. 对齐尺寸 / 色彩空间
3. 计算：
   - SSIM
   - PSNR
4. 如果：
   - `ssim >= threshold`
   - `psnr >= threshold`
   则视为可接受
5. 如果未达标，则轮换下一策略
6. 从达标候选里选择最小文件
7. 如果都不达标，返回“最保守候选”或原图

## 6. 前端信息架构

页面建议拆成 4 个区块：

1. Hero 区
   - 项目标题
   - 一句核心价值
   - 支持格式说明

2. Upload Dropzone
   - 拖拽提示
   - 点击补传
   - 批量上传说明

3. Queue / Result List
   - 原文件名
   - 原体积 / 压缩后体积
   - 压缩率
   - SSIM / PSNR
   - 状态标记

4. Compare Viewer
   - 左右图层
   - 中线滑块
   - 原图 / 压缩图信息条

## 7. UI 风格建议

从 `daodejing` 借鉴，而不是照搬：

- 背景：暖白 / 米白
- 文本：深灰黑
- 点缀：鸭绿 / 青色
- 组件圆角：中等
- 阴影：非常轻
- 动效：缓入缓出、不过度弹跳

关键词：

- 安静
- 呼吸感
- 留白
- 古典与现代混合

## 8. 扩展路线

### 8.1 能力增强

- EXIF 保留 / 移除策略
- 无损 / 有损模式切换
- SVG / AVIF 支持
- 历史任务与批量下载 ZIP

### 8.2 工程增强

- Redis + RQ / Celery 异步任务
- 数据库存储历史记录
- 对象存储（S3 / OSS / COS）
- Prometheus 指标
- OpenTelemetry tracing

### 8.3 产品增强

- 质量优先 / 体积优先预设
- 自定义 SSIM / PSNR 阈值
- 登录系统与 API Token
- 可嵌入第三方工作流
