# 第三方组件

- `vendor/blender-mcp/` 来自 https://github.com/ahujasid/blender-mcp，
  MIT License，Copyright (c) 2025 Siddharth Ahuja。完整许可见
  `vendor/blender-mcp/LICENSE`，固定提交与补丁见 `vendor/blender-mcp/UPSTREAM.md`。
- 容器下载并包含 Blender 官方二进制；Blender 及其组件适用各自许可，许可文件随
  官方发行包保留在 `/opt/blender`，官方许可说明：https://www.blender.org/about/license/ 。
- Python、Debian 及基础镜像中的依赖保留各自许可，不能将整个容器视作单一 MIT 软件。

本项目自身代码尚未指定开源许可证；上游 MIT 许可证仅适用于上述 vendored 组件。
