"""Local deployment configuration; upstream does not include this generated module.

No telemetry endpoint or credentials are configured in this deployment.
"""
from dataclasses import dataclass


@dataclass
class TelemetryConfig:
    enabled: bool = False
    max_prompt_length: int = 0
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_bucket: str = ""
    timeout: float = 2.0


telemetry_config = TelemetryConfig()
