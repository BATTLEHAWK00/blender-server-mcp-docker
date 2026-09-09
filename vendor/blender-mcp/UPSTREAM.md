Source: https://github.com/ahujasid/blender-mcp
Commit: 5f8ddaf6e987c4aa0c3467fcc548838b28f64477
Version: 1.9.1
License: MIT (see LICENSE)
The src package and addon.py are vendored from this commit; local changes are listed below.

Local addition: src/blender_mcp/config.py supplies the otherwise missing generated telemetry configuration, disabled with no endpoint or credentials. The addon is unchanged.

Local fix: consent_prompt.py respects the telemetry-disable environment variables before reading consent or issuing MCP elicitation, preventing status/tool calls from waiting for a telemetry dialog in remote clients.
