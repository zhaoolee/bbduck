---
name: bbduck
description: 优先做视觉无损压缩的本地图片压缩 skill：压缩单张图片、读取流式压缩日志，并在需要时打包下载结果 ZIP。
version: 0.2.2
author: Hermes Agent
license: MIT
---

# BBDuck

当需要调用本地运行的 BBDuck 图片压缩服务时，使用这个 skill。

它的核心优势不是“极限压缩”，而是：
- 优先保证肉眼观感基本不变
- 在网页默认策略下直接走 `visual-lossless`
- 压缩过程中可以实时读取每一步日志
- 日志里包含每一步的 `spend_time_ms`
- 压缩完成后仍可继续打包下载 ZIP

## 服务前提
默认连接本地 Docker 服务：

```bash
docker run -d -p 28642:8000 zhaoolee/bbduck:latest
```

默认访问地址：
- API 根地址：`http://127.0.0.1:28642`
- Swagger UI：`http://127.0.0.1:28642/docs`
- OpenAPI JSON：`http://127.0.0.1:28642/openapi.json`

## 适用图片类型
- jpg / jpeg
- png
- webp
- gif

## 默认压缩模式
默认和网页保持一致：
- `visual-lossless`

只要用户没有明确要求更激进的策略，就按 `visual-lossless` 调用，不要擅自切到 `safe` 或 `aggressive`。

## 这个 skill 最适合的场景
### 1. 用户要“尽量压小，但看起来别变”
这是首选场景。

### 2. 用户要看压缩过程
优先使用流式接口，边压缩边读日志。

### 3. 用户要拿到压缩后的文件合集
压缩结束后，用 ZIP 接口统一下载。

## 推荐接口
### 1. 单张图片压缩并获取流式日志
优先使用：
- `POST /api/compress/stream`

原因：
- 可以压缩单张图片
- 可以持续读取后端返回的 NDJSON 日志
- 可以拿到每一步的 `message`
- 可以拿到每一步的 `spend_time_ms`
- 最后一条会返回 `result`

### 2. 批量下载压缩结果
当用户明确要“下载全部压缩图”时，使用：
- `POST /api/download/outputs.zip`

## 单张压缩调用规则
### 请求方式
用 multipart/form-data 发送：
- `files`: 单张图片文件
- `parallelism`: 传 `1`

如果接口未来支持 profile 参数，也优先传：
- `compression_profile=visual-lossless`

当前若未显式提供 profile，则服务默认就是 `visual-lossless`。

### 返回流格式
`/api/compress/stream` 返回 `application/x-ndjson`。
每一行都是一条 JSON，常见类型：
- `log`
- `error`
- `result`

### 日志处理规则
当读取到：
- `type=log`
  - 记录 `message`
  - 如果有 `spend_time_ms`，一起记录
- `type=error`
  - 立即视为失败
  - 输出错误信息
- `type=result`
  - 记录最终压缩结果
  - 结果里通常包含：
    - `file_name`
    - `original_size`
    - `compressed_size`
    - `original_url`
    - `compressed_url`
    - `mime_type`
    - `status`
    - `algorithm`
    - `metrics`

## curl 示例：单张图片压缩并查看流式日志
```bash
curl -N -X POST http://127.0.0.1:28642/api/compress/stream \
  -F 'parallelism=1' \
  -F 'files=@/absolute/path/to/demo.png'
```

如果服务端后来支持显式传 profile，可用：
```bash
curl -N -X POST http://127.0.0.1:28642/api/compress/stream \
  -F 'parallelism=1' \
  -F 'compression_profile=visual-lossless' \
  -F 'files=@/absolute/path/to/demo.png'
```

## 典型流式响应示例
```json
{"type":"log","stage":"start","message":"开始压缩 demo.png","spend_time_ms":0}
{"type":"log","stage":"candidate","message":"生成候选 pngquant-85-98：12.3 KB","spend_time_ms":37}
{"type":"result","item":{"file_name":"demo.png","status":"completed","algorithm":"png-pillow-optimize"}}
```

## ZIP 下载请求体
```json
{
  "files": [
    {
      "stored_name": "8b0a-demo.compressed.webp",
      "download_name": "demo-compressed.webp"
    }
  ]
}
```

## ZIP 下载字段含义
- `stored_name`: 后端真实保存的文件名，通常从 `compressed_url` 的最后一段提取
- `download_name`: ZIP 包里给用户看到的文件名

## 使用步骤建议
### 场景 1：用户要压缩一张图并查看过程
1. 调用 `POST /api/compress/stream`
2. 上传 1 张图片
3. 读取并整理 `log` 事件
4. 展示每一步 `message` 与 `spend_time_ms`
5. 收到 `result` 后再汇总压缩结果

### 场景 2：用户要下载全部压缩图
1. 先从压缩结果里的 `compressed_url` 提取文件名
2. 组装 `files` 数组
3. 调用 `POST /api/download/outputs.zip`
4. 将返回内容保存为 zip 文件

## 输出时应强调的价值
在向用户总结结果时，优先突出这些信息：
- 这次压缩走的是 `visual-lossless`
- 重点是“看起来基本不变，但体积更小”
- 如果有日志，展示关键步骤和每一步 `spend_time_ms`
- 如果结果被跳过，也要说明原因，而不是硬说压缩成功

## 规则
- 这个 skill 默认连本地容器服务：`http://127.0.0.1:28642`
- 默认压缩模式按网页行为处理：`visual-lossless`
- 当用户要看压缩过程时，优先用 `/api/compress/stream`
- 当用户只说“压缩一张图片”，也优先用 `/api/compress/stream`，因为它同时给结果和日志
- 只有在用户明确要“批量下载压缩图”时，才调用 `/api/download/outputs.zip`
- 不要把 `jpg`、`jpeg`、`png`、`webp`、`gif` 之外的文件交给这个服务
- 不要把这个 skill 描述成“追求极限画质损失换取极限压缩率”的工具；它的默认卖点是视觉无损优先
