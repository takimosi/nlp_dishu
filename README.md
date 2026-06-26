# 地书世界公网演示项目

本项目已经部署到公网服务器，可通过浏览器直接访问演示页面。

公网访问地址：

```text
http://112.124.68.145/
```

建议使用 Chrome、Edge 或 Safari 浏览器访问。第一次打开时，三维场景、图片和静态资源需要加载，页面可能会等待几秒。

## 公网访问操作说明

进入公网地址后，首页会展示三维场景和多个功能入口。用户可以根据需要进入不同模块体验。

### 1. 地书日记 / 公寓交互

点击首页中的地书日记或公寓相关入口，进入交互页面。

用户可以输入或选择事件内容，系统会将输入内容处理成结构化事件，并匹配对应的地书符号或图标。部分功能会调用大模型接口，因此生成结果时可能需要等待几秒。

### 2. 图标书 Boook 页面

点击图标书相关入口后，可以进入图标组合与解释页面。

用户可以选择多个图标组成一个图标序列，系统会根据图标序列理解含义，并生成自然语言回应。该功能依赖服务器端的大模型 API Key，Key 不会暴露在浏览器中。

### 3. 多人游戏模块

点击游戏入口后，可以选择不同游戏模式。

常见操作流程：

```text
创建房间 -> 获得房间码 -> 其他用户输入房间码加入 -> 开始互动
```

房主创建房间后，将房间码发送给其他参与者。其他用户在自己的浏览器中打开同一个公网地址，进入对应游戏模式，输入房间码即可加入。

多人测试时，建议使用不同设备、不同浏览器，或无痕窗口分别模拟不同玩家。

### 4. 访问注意事项

- 如果页面第一次加载较慢，等待几秒即可。
- 如果三维场景未显示，可以刷新页面重试。
- 如果大模型相关功能无响应，可能是 API Key、额度、网络或服务端接口暂时异常。
- 如果多人房间加入失败，先确认房间码是否正确，再刷新页面重试。

## 服务器运行与维护

本项目当前使用阿里云 ECS + Docker Compose 部署。只要 ECS 实例保持运行、Docker 容器保持 `Up`、安全组开放 `80` 端口，其他用户就可以通过公网地址访问。

进入服务器后，可以用以下命令查看容器状态：

```bash
cd ~/nlp_dishu
docker compose ps
```

正常情况下应看到以下服务处于 `Up` 状态：

```text
web
chain-server
diary-server
```

查看最近日志：

```bash
cd ~/nlp_dishu
docker compose logs --tail=100
```

持续查看日志：

```bash
cd ~/nlp_dishu
docker compose logs -f --tail=100
```

重启服务：

```bash
cd ~/nlp_dishu
docker compose restart
```

更新代码后重新构建并启动：

```bash
cd ~/nlp_dishu
git pull
docker compose up -d --build
```

停止服务：

```bash
cd ~/nlp_dishu
docker compose down
```

注意：项目使用 `docker compose up -d` 后会在服务器后台运行，退出 SSH 或关闭 Workbench 不会影响公网访问。

## 环境变量

项目根目录需要 `.env` 文件保存私密配置。该文件不会提交到 GitHub。

服务器上可由示例文件复制生成：

```bash
cp .env.example .env
nano .env
```

配置内容示例：

```env
DEEPSEEK_API_KEY=your-real-key
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
FLASK_SECRET_KEY=change-me
```

不要将真实 API Key 提交到仓库。

## 本地运行说明

如果需要在本地开发或调试，可以使用项目自带的本地启动脚本。

先在本地创建 `.env`：

```powershell
copy .env.example .env
```

然后填写本地使用的 DeepSeek API Key。

启动本地服务：

```powershell
python start.py
```

本地启动后会运行多个服务：

```text
主世界静态页面：http://localhost:8080
地书日记服务：http://localhost:8000
多人游戏服务：http://localhost:5001
```

本地调试时，如果端口被占用，需要先关闭占用端口的程序，或修改启动脚本中的端口配置。

## 项目结构

主要文件和目录：

```text
index.html                         主世界入口页面
Boook/book.html                    图标书 / 咖啡馆交互页面
Boook/cartoon.glb                  首页三维场景模型
game/                              多人游戏后端与页面模板
diary/                             地书日记 / 公寓交互后端与页面
deploy/                            Docker 与 Nginx 部署配置
docker-compose.yml                 服务器 Docker Compose 编排文件
.env.example                       环境变量示例
DEPLOY.md                          部署命令备忘
```

## 模型与大文件说明

首页三维场景模型 `Boook/cartoon.glb` 已经包含在仓库中，可以随代码一起部署。

NLP 语义模型目录不会提交到 GitHub：

```text
game/models/
```

如果需要启用完整语义模型能力，需要将模型目录单独上传到服务器：

```text
/root/nlp_dishu/game/models/paraphrase-multilingual-MiniLM-L12-v2/
```

当前部署版本即使不上传该 NLP 模型，也可以完成主要公网演示流程；部分语义评分或语义检索功能会降级运行。

## 部署方案简述

当前公网部署采用：

```text
GitHub 托管代码
+ 阿里云 ECS 公网服务器
+ Docker Compose 启动服务
+ Nginx 统一对外提供 HTTP 访问
```

Nginx 负责监听公网 `80` 端口，并将请求分发给不同服务：

- 静态页面和资源由 `web` 服务提供。
- 多人游戏接口由 `chain-server` 服务提供。
- 地书日记、公寓交互和大模型代理接口由 `diary-server` 服务提供。

这样浏览器只需要访问一个公网地址，不需要直接暴露多个后端端口。
