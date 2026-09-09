# Blender Server — 原生 Blender MCP 容器

[English](README.md) | **简体中文**

在一个容器中运行 Blender 5.2.1、虚拟显示 Xvfb，以及 [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) 的原生 MCP 服务和配套 addon。无需在客户端机器上安装或手动启动 Blender 插件。

架构：HTTP MCP 客户端 → 原生 MCP 服务 → 容器内 addon → 常驻 Blender。虚拟显示负责 GUI/视口，Cycles 渲染使用 NVIDIA GPU。原生 addon 要求 GUI 事件循环，因此这里不使用 `blender -b`。

## 快速开始

需要 Linux x86_64、Docker Compose v2、NVIDIA 驱动和 NVIDIA Container Toolkit。

```bash
cp .env.example .env
mkdir -p work
# 编辑 .env：设置 API_TOKEN、GPU_ID，以及 id -u / id -g 对应的 APP_UID / APP_GID
docker compose up -d --build
curl --fail http://127.0.0.1:21849/health
```

MCP 地址为 `http://127.0.0.1:21849/mcp`。远程访问时配置 `BIND_HOST`、
`MCP_ALLOWED_HOSTS` 和强随机 `API_TOKEN`；通过 HTTPS 反向代理提供外部访问。
addon 的 9876 端口仅在容器内监听。`.env`、`work/` 和
`mcp-client.local.json` 不提交到仓库或镜像。

## 客户端接入

通用 HTTP MCP 客户端配置（字段依客户端而定）：

```json
{
  "mcpServers": {
    "blender-server": {
      "url": "http://127.0.0.1:21849/mcp",
      "headers": {"Authorization": "Bearer <.env 中的 API_TOKEN>"}
    }
  }
}
```

删除或停用客户端之前的 `uvx blender-mcp` stdio 配置，使用上述远程 HTTP 配置，重新连接后刷新工具列表。否则旧服务仍会尝试连接客户端本机的 Blender，报 `Could not connect to Blender`。

## 原生工具

完整工具列表通过 MCP `tools/list` 获取，包括 `get_addon_status`、`get_scene_info`、`get_object_info`、`execute_blender_code`、`get_viewport_screenshot`，以及上游的素材检索、导入和生成工具。第三方素材/生成服务需单独启用对应集成并配置 API Key；没有替用户开通这些服务。上游遥测通过环境变量关闭。

例如调用 `execute_blender_code` 创建对象：

```json
{"code":"import bpy\nbpy.ops.mesh.primitive_cube_add(location=(2, 0, 0))"}
```

常驻 Blender 默认使用 Cycles 和 `AUTO` GPU 选择（优先 OptiX，然后 CUDA，不自动退回 CPU）。具体后端取决于宿主机驱动和 GPU。保存场景和渲染图像时使用 `/work/...`，宿主机对应 `./work/...`。内存中的场景不会自动保存，容器重启会回到初始场景；需要保存时调用 `bpy.ops.wm.save_as_mainfile(filepath='/work/scene.blend')`。

原生执行工具是同步调用，上游 socket 超时为 180 秒。长渲染可使用保存的 `.blend` 文件另行批处理。多个客户端共享同一个场景，应避免同时修改。任意 Python 执行具有容器用户权限，仅供可信客户端使用。

## MCP 文件上传与下载

原生 MCP 入口额外注册三个工具，沿用 `/mcp` 的 Bearer 鉴权，无需另调 REST 接口：

| 工具 | 用途 |
| --- | --- |
| `upload_file(path, data_base64, offset=0, overwrite=false)` | 上传文件或追加分块，每块解码后最多 1 MiB |
| `download_file(path, offset=0, length=65536)` | 下载 Base64 数据，默认每块 64 KiB，最多 1 MiB |
| `list_files(path=".", offset=0, limit=100)` | 分页浏览目录，返回文件路径、类型和大小 |

路径相对于当前会话目录 `/work/sessions/<session-id>/files/`，也接受该会话目录内的绝对路径；禁止跨会话路径和符号链接。上传自动创建父目录，已有文件需显式 `overwrite=true` 才能从头覆盖。

服务使用有状态 Streamable HTTP。客户端先发送 `initialize`，保存响应头中的 `Mcp-Session-Id`，再发送 `notifications/initialized`；后续请求都携带此 ID 和原有 Bearer Token。标准 MCP 客户端会自动处理。会话 ID 由服务端生成，不接受客户端自行创建的 ID；请像凭据一样保管它。同一 Token 下的隔离依赖会话 ID 的保密性。

每个会话独立存储，同名文件不会冲突。`list_files` 返回当前目录的 `absolute_path`，可让 Blender 将场景和渲染输出保存到该目录，再通过 `download_file` 下载。旧 `/work` 文件不自动迁移，也不在文件工具的可访问范围内。

服务启动时扫描一次，此后每 **4 小时**清理一次连续 **4 小时没有请求**且无请求执行中的会话目录（因此通常在闲置 4–8 小时后删除）。请求完成会更新持久化活动时间，上传、下载及正在执行的同步 Blender 请求期间不会清理该会话。清理仅处理 `sessions` 下符合服务端 ID 格式的目录，不删除旧 `/work` 文件，也不跟随会话目录符号链接。删除的文件不可恢复，重要结果应及时下载。

会话空闲超时或服务重启后，客户端需重新初始化；新会话不能访问旧会话文件，旧目录按上述过期策略回收。客户端另行启动、已经脱离 MCP 请求的后台 Blender 任务不计入活动请求，长任务应使用持久目录或在任务期间维持会话请求。

隔离范围是文件工具。`execute_blender_code` 仍可执行任意 Python，多个客户端仍共享 Blender 场景；这不是针对不可信客户端的容器或操作系统级隔离。

例如通过 MCP `tools/call` 上传 `hello.txt`：

```json
{"name":"upload_file","arguments":{"path":"hello.txt","data_base64":"aGVsbG8K"}}
```

下载同一文件：

```json
{"name":"download_file","arguments":{"path":"hello.txt"}}
```

工具结果中的 JSON 包含 `data_base64`，客户端将其 Base64 解码后写入本地文件。MCP 服务无法直接写入客户端磁盘，需要客户端具备本地文件读写能力。

大文件上传时，将原始文件按不超过 1 MiB 分块，分别进行 Base64 编码；首块 `offset=0`，后续块使用上一次结果的 `next_offset`。追加偏移必须等于当前文件大小，同一文件应顺序调用。每块成功后立即写入文件，全部上传完成后再让 Blender 使用返回的 `absolute_path`；中断会留下已上传部分，可从当前大小继续追加。

下载时持续使用返回的 `next_offset`，按顺序解码拼接，直到 `eof=true`；期间不要修改源文件。目录浏览在 `next_offset` 非空时继续翻页。`.blend`、纹理、渲染图和压缩包都按原始字节传输，不自动解压。

更新现有部署需重新构建并启动容器（先保存内存中的 Blender 场景），然后让 MCP 客户端重新连接并刷新工具列表：

```bash
docker compose up -d --build
```

## 构建与运行

宿主机需 Linux x86_64、Docker Compose v2、NVIDIA 驱动和 NVIDIA Container Toolkit。无需宿主机 Blender/Python 依赖。

```bash
# 新安装才复制；已有 .env 时保留现有网络和 Token 配置
cp .env.example .env
# 将 APP_UID / APP_GID 设为 id -u / id -g 的输出
mkdir -p work
docker compose build
docker compose up -d
```

也可以直接构建镜像：

```bash
docker build --build-arg APP_UID="$(id -u)" --build-arg APP_GID="$(id -g)" -t blender-server:local .
```

Debian 默认使用官方源；可用 `APT_MIRROR` 构建参数指定镜像站。Python 锁文件目前使用 USTC 源。Blender 官方发行包由 SHA256 校验，基础镜像来自 GHCR。可用 `--build-arg ALL_PROXY` 传递已有下载代理；代理不写入运行环境。上游代码及少量本地补丁放在 `vendor/blender-mcp`，固定提交和 MIT 许可证见该目录。更新时需一起更新 MCP 源码与 addon。

| 配置 | 说明 |
| --- | --- |
| `GPU_ID` | 宿主机 GPU 序号，默认 0 |
| `APP_UID` / `APP_GID` | 容器用户，需能写入 `work` |
| `BIND_HOST` / `PORT` | 默认绑定 127.0.0.1，端口 21849 |
| `API_TOKEN` | HTTP MCP Bearer 鉴权；健康检查除外 |
| `MCP_ALLOWED_HOSTS` | NAT 对外访问 IP/域名与端口白名单 |
| Origin / CORS | 允许所有来源；实际请求仍要求 Bearer Token |

启动器监督 Xvfb、Blender 和 HTTP 服务，任一进程退出即结束容器，由 Compose 重启。通过 `docker compose logs -f` 查看日志。

## 测试

无需 GPU 的单元测试和代码检查：

```bash
uv sync --locked
uv run --locked ruff check blender_server tests scripts
uv run --locked pytest -q
```


通过本地客户端配置中的地址测试原生工具、addon、场景操作、截图及 GPU 渲染：

```bash
python3 scripts/native_smoke.py
```

此测试读取本地客户端配置，在场景中创建 `MCP_E2E_Cube`，生成 `work/native-viewport.png`、`work/native-render.png` 和 `work/native-e2e.blend`。

先前的自定义渲染 API 代码仍保留在 `blender_server/app.py`，当前镜像入口已切换到原生 MCP；旧 `/jobs` 接口和 `scripts/smoke.py` 不再用于当前部署。

## GitHub 镜像发布

[镜像 workflow](.github/workflows/image.yml) 在 `main` 推送、`v*` 标签和手动触发时运行，
PR 也会构建验证，但不登录 registry 或推送镜像。发布前必须通过单元测试和 lint。
镜像地址自动取当前仓库名：`ghcr.io/<owner>/<repository>`（小写），仅支持 `linux/amd64`。
构建采用 Buildx 和 GitHub Actions 缓存，配置参考 [Docker 官方 action](https://github.com/docker/build-push-action)。

| 触发 | 镜像标签 |
| --- | --- |
| `main` 推送 | `main`、`edge`、`sha-<commit>` |
| `v1.2.3` 标签 | `1.2.3`、`1.2`、`latest`、`sha-<commit>` |
| `v1.2.3-rc.1` 标签 | `1.2.3-rc.1`、`sha-<commit>`，不更新 `latest` |
| 手动触发 | 当前分支或版本标签及 `sha-<commit>` |

使用仓库自带的 `GITHUB_TOKEN`，无需新增 registry 密钥；仓库/组织需允许 Actions
写入 Packages。首次发布后按需在 GitHub Package 设置中调整可见性；私有镜像拉取需登录 GHCR。
首次正式发布前可使用 `edge`，`latest` 仅由正式版本标签生成。

使用预构建镜像，在 `.env` 设置 `BLENDER_SERVER_IMAGE=ghcr.io/<owner>/<repository>:edge`，然后：

```bash
docker compose pull
docker compose up -d --no-build
```

预构建镜像用户固定为 UID/GID 1000，需保证 `work/` 对其可写；`.env` 中的
`APP_UID` / `APP_GID` 仅影响本地构建。升级前保存内存场景，升级后重新连接客户端。

## 仓库结构与贡献

- `blender_server/`：服务入口、文件会话管理和保留的 REST 渲染实现。
- `vendor/blender-mcp/`：固定版本的上游 MCP 和 addon，含补丁记录和许可证。
- `tests/`：无需 Blender/GPU 的单元测试。
- `scripts/`：连接真实部署的 smoke 脚本。
- `.github/`：CI、镜像构建和维护模板。

开发与发布流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全边界见
[SECURITY.md](SECURITY.md)，第三方许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
