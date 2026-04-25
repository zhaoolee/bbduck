# BBDuck

![](./frontend/public/bbduck-logo.png)

BBDuck 是面向开源社区的图片压缩工具，以“视觉无损优先”为产品亮点，开源版 PP鸭，支持 skill 调用。

![](./README.assets/74c834d486cf06717739fb2859a3c675f47d229b3d2fc3af9ec3b7468c87dcf4.png)

**开源地址** https://github.com/zhaoolee/bbduck

![](./README.assets/a69790085cd22d8aac0cd11da718c4ec17bf731f6dc23f7639473b0068593cdb.png)

**在线体验地址**：https://bbduck.v2fy.com/ （我的服务器CPU很弱，压缩的速度不够快，如果是私有化部署的机器，压缩速度会非常快）

![](./README.assets/60d5a948b27bc859b728179861f4053d25f8b264e1e8c6bf1e6c3926ccdfd697.png)

压缩**1980 × 17703**的长便签后，文件体积减少了 **60.93%**，也就是压缩后只有原来的**四成左右**，但文字依然非常清晰

![](./README.assets/0d46673e4a9eae0d05e3c482e1ae610bf366bc533e992a3f2c42577f5d4be9dd.png)

![](./README.assets/b2dc0568163541e2d6920a879a10304c6946f06125dcb682f6c27054d2185e91.png)

![](./README.assets/683a5ce3156c65205d178cda15ae5ca687150eeded659aec5da22af2c2a74fbd.gif)

## 本地部署

```bash
docker run -d --restart unless-stopped --name bbduck -p 28642:8000 zhaoolee/bbduck:latest
```

打开 http://127.0.0.1:28642 即可使用。

## SKILL 调用方法

skill 地址：
https://clawhub.ai/zhaoolee/bbduck

可直接在 Hermes 中使用：

```text
从clawhub安装 https://clawhub.ai/zhaoolee/bbduck 用来优化本地的图片尺寸, 使用
https://bbduck.v2fy.com/api/evaluation-images/00001.png 进行测试
```

![](./README.assets/64e37b20888cd9026f5eefaf51dc7d876146ac476e035c1d5122f10d20bddb3a.png)

通过**Hermes**运行的效果（**OpenClaw**同理，不过Hermes排版更有品，我就用Hermes截图了）

![](./README.assets/33378726b66cf41cf1bf43a5cd65125c632f90fd8e38e0523621f10bc3e57f75.png)
