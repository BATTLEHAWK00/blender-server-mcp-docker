# 贡献指南

使用 Python 3.11+ 和 uv，在仓库根目录执行：

```bash
uv sync --locked
uv run --locked ruff check blender_server tests scripts
uv run --locked pytest -q
```

单元测试无需 GPU。真实部署验证需 NVIDIA GPU，按 README 启动容器后配置
本地 `mcp-client.local.json`，再执行 `python3 scripts/native_smoke.py`。
该脚本会修改场景并写入文件，使用测试实例。旧 `scripts/smoke.py` 仅适用于保留的 REST 服务。

修改依赖时同步更新 `pyproject.toml` 和 `uv.lock`（`uv lock`）。
修改 vendored 源码时更新 `vendor/blender-mcp/UPSTREAM.md`，保留上游许可。
修改 Blender 版本时同时更新 Dockerfile 中的版本、系列和 SHA256，并验证真实 GPU 渲染。
不要提交凭据、`.env`、客户端本地配置或 `work/` 产物。

PR 说明问题、行为变化和验证结果。CI 会运行 lint、单元测试和镜像构建。
维护者发布版本时推送语义化标签，例如 `v0.1.0`；确认镜像 workflow 成功后发布 GitHub Release。
`main` 发布 `edge`，正式版本发布 `latest`，预发布版本不会覆盖 `latest`。
