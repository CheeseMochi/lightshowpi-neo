"""
Configuration management for LightShowPi Neo API.

Extends the existing configuration_manager to add API-specific
settings while maintaining backward compatibility.
"""

import os
import sys
import logging
from typing import Optional

# Add parent directory to path to import existing modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..', 'py'))

from configuration_manager import Configuration

log = logging.getLogger(__name__)


class APIConfig:
    """
    Extended configuration for API mode.

    Wraps the existing Configuration class and adds API-specific
    settings from the [api] section of config files.
    """

    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize API configuration.

        Args:
            config_file: Optional path to config file (defaults to overrides.cfg)
        """
        self.cm = Configuration(param_config=config_file)

        # Load API settings
        self.enabled = self._get_bool('api', 'enabled', False)
        self.host = self._get_str('api', 'host', '0.0.0.0')
        self.port = self._get_int('api', 'port', 8000)
        self.cloud_url = self._get_str('api', 'cloud_url', '')
        self.cloud_key = self._get_str('api', 'cloud_key', '')
        self.db_path = self._get_str('api', 'db_path', '')

        # Load client settings (for client Pis)
        self.client_id = self._get_str('client', 'client_id', '')
        self.client_key = self._get_str('client', 'client_key', '')
        self.server_host = self._get_str('client', 'server_host', '')
        self.server_port = self._get_int('client', 'server_port', 8001)

        # Expose underlying configuration manager
        self.config = self.cm.config
        self.lightshow = self.cm.lightshow
        self.hardware = self.cm.hardware
        self.audio_processing = self.cm.audio_processing

        log.info(f"API configuration loaded (enabled={self.enabled}, port={self.port})")

    def _get_bool(self, section: str, key: str, default: bool) -> bool:
        """Get boolean config value with fallback."""
        try:
            return self.cm.config.getboolean(section, key)
        except Exception:
            return default

    def _get_str(self, section: str, key: str, default: str) -> str:
        """Get string config value with fallback."""
        try:
            return self.cm.config.get(section, key)
        except Exception:
            return default

    def _get_int(self, section: str, key: str, default: int) -> int:
        """Get integer config value with fallback."""
        try:
            return self.cm.config.getint(section, key)
        except Exception:
            return default

    def is_server(self) -> bool:
        """
        Check if this Pi is configured as a server.

        Returns:
            bool: True if server mode
        """
        return self.cm.network.mode == "server" or self.cm.network.mode == ""

    def is_client(self) -> bool:
        """
        Check if this Pi is configured as a client.

        Returns:
            bool: True if client mode
        """
        return self.cm.network.mode == "client"

    def update_state(self, key: str, value: str):
        """
        Update state file.

        Args:
            key: State key
            value: State value
        """
        self.cm.update_state(key, value)

    def get_state(self, key: str, default: str = "") -> str:
        """
        Get state value.

        Args:
            key: State key
            default: Default value if key not found

        Returns:
            str: State value
        """
        return self.cm.get_state(key, default)

    def reload_state(self):
        """Reload state from disk."""
        self.cm.load_state()


def get_api_config() -> APIConfig:
    """
    Get API configuration instance.

    Returns:
        APIConfig: Configuration instance
    """
    return APIConfig()


if __name__ == "__main__":
    # Test configuration loading
    logging.basicConfig(level=logging.INFO)
    config = get_api_config()
    print(f"API Enabled: {config.enabled}")
    print(f"API Port: {config.port}")
    print(f"Is Server: {config.is_server()}")
    print(f"Is Client: {config.is_client()}")
