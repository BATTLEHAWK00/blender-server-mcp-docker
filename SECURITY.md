# 安全说明

服务仅面向可信客户端。`execute_blender_code` 可在容器用户权限下执行任意 Python，
所有客户端共享 Blender 场景；文件工具的会话路径隔离不等于操作系统隔离。

默认仅绑定 localhost。远程部署应设置强随机 `API_TOKEN`、正确的
`MCP_ALLOWED_HOSTS`，并通过 HTTPS 代理访问。不要将容器内 addon 端口对外发布。
不设置 Token 会关闭 HTTP 鉴权；健康检查无需鉴权。

报告漏洞时使用仓库 Security 页的私密报告功能（若已启用），不要在公开 Issue
粘贴 Token、私有模型或部署配置。若未启用，请先联系仓库维护者获取私密报告渠道。
