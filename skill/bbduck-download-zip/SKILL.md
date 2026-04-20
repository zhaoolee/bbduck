---
name: bbduck-download-zip
description: 当用户要批量下载压缩后的图片时，调用 BBDuck Server 的 ZIP 打包接口。
version: 0.1.0
author: Hermes Agent
license: MIT
---

# BBDuck ZIP 下载

当用户想“批量下载压缩后的图片”时，使用这个 skill。

## 只做一件事
调用一个接口：`POST /api/download/outputs.zip`

## 接口文档
- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

## 请求体
```json
{
  "files": [
    {
      "stored_name": "8b0a...-demo.compressed.webp",
      "download_name": "demo-compressed.webp"
    }
  ]
}
```

## 字段含义
- `stored_name`: 后端真实保存的文件名，通常从 `compressed_url` 的最后一段提取
- `download_name`: ZIP 包里给用户看到的文件名

## 调用步骤
1. 先从压缩结果里的 `compressed_url` 提取文件名
2. 组装 `files` 数组
3. 发送 `POST /api/download/outputs.zip`
4. 将返回结果当作 `application/zip` 文件保存

## curl 示例
```bash
curl -X POST http://127.0.0.1:8000/api/download/outputs.zip \
  -H 'Content-Type: application/json' \
  -o bbduck-compressed-images.zip \
  -d '{
    "files": [
      {
        "stored_name": "8b0a-demo.compressed.webp",
        "download_name": "demo-compressed.webp"
      }
    ]
  }'
```

## 规则
- 不要一次讲太多接口
- 只在“批量下载压缩图”这个场景使用这个 skill
- 如果用户只是想压缩图片，不要调用这个 skill
