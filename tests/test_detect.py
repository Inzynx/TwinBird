from __future__ import annotations

import json
from pathlib import Path

from twinbird import detect


class TestParsePrimaryConfig:
    def test_object_management_url(self, tmp_path: Path) -> None:
        cfg = tmp_path / "default.json"
        cfg.write_text(
            json.dumps(
                {
                    "ManagementURL": {"Scheme": "https", "Host": "api.netbird.io:443"},
                    "AdminURL": {"Scheme": "https", "Host": "app.netbird.io:443"},
                    "WgIface": "wt0",
                    "WgPort": 51820,
                    "PrivateKey": "secret",
                }
            )
        )
        info = detect.parse_primary_config(cfg)
        assert info is not None
        assert info.management_url == "https://api.netbird.io:443"
        assert info.admin_url == "https://app.netbird.io:443"
        assert info.wg_iface == "wt0"
        assert info.wg_port == 51820

    def test_string_management_url(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"ManagementURL": "https://self.example.com:443"}))
        info = detect.parse_primary_config(cfg)
        assert info is not None
        assert info.management_url == "https://self.example.com:443"
        assert info.wg_iface is None
        assert info.wg_port is None

    def test_corrupt_returns_none(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text("{not json")
        assert detect.parse_primary_config(cfg) is None


class TestDetectPrimary:
    def test_none_when_no_candidates(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            detect, "primary_config_candidates", lambda: [tmp_path / "missing.json"]
        )
        assert detect.detect_primary() is None
        assert detect.detected_management_url() is None

    def test_reads_first_existing(self, monkeypatch, tmp_path: Path) -> None:
        cfg = tmp_path / "default.json"
        cfg.write_text(
            json.dumps({"ManagementURL": {"Scheme": "https", "Host": "h:443"}})
        )
        monkeypatch.setattr(detect, "primary_config_candidates", lambda: [cfg])
        assert detect.detected_management_url() == "https://h:443"
