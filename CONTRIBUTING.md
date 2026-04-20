# Contributing to BBDuck Server

欢迎贡献 BBDuck Server。建议按下面的节奏协作：

## 开发环境

### 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

### Docker

```bash
docker compose up --build
```

## 提交建议

- feat: 新功能
- fix: 缺陷修复
- docs: 文档
- refactor: 重构
- chore: 杂项

## Issue 建议模板

请尽量提供：

- 浏览器和操作系统
- 输入图片格式与大小
- 期望压缩结果
- 实际结果
- 错误日志 / 截图

## Pull Request 检查清单

- [ ] 后端测试通过
- [ ] 前端构建通过
- [ ] README / 文档已同步
- [ ] 未提交 node_modules / 临时文件
