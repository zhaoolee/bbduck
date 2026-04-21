# BBDuck

![](./frontend/public/bbduck-logo.jpg)

BBDuck 是面向开源社区的图片压缩工具，以“视觉无损优先”为产品亮点，开源版 PP鸭，支持 skill 调用。

## 本地部署

```bash
docker run -d --rm --name bbduck -p 28642:8000 zhaoolee/bbduck:latest
```

打开 http://127.0.0.1:28642 即可使用。

## skill 调用方法

skill 地址：
https://clawhub.ai/zhaoolee/bbduck

可直接在 Hermes 中使用：

```text
从clawhub安装 https://clawhub.ai/zhaoolee/bbduck 用来优化本地的图片尺寸
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
