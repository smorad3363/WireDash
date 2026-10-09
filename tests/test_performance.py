#!/usr/bin/env python3
"""Guard performance changes, including real non-blocking MB/s sampling."""
from pathlib import Path
import ast
import unittest


class PerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config_path = Path("src/modules/WireguardConfiguration.py")
        cls.config_source = cls.config_path.read_text()
        cls.tree = ast.parse(cls.config_source)

    def test_traffic_api_does_not_sleep(self):
        conf = next(n for n in self.tree.body
                    if isinstance(n, ast.ClassDef) and n.name == "WireguardConfiguration")
        sample = next(n for n in conf.body
                      if isinstance(n, ast.FunctionDef) and n.name == "getRealtimeTrafficUsage")
        self.assertFalse(any(isinstance(n, ast.Call)
                             and isinstance(n.func, ast.Attribute)
                             and n.func.attr == "sleep"
                             for n in ast.walk(sample)))
        self.assertIn("time.monotonic()", ast.get_source_segment(self.config_source, sample))

    def test_reuse_peers_and_restricted_objects(self):
        for path in ("src/modules/WireguardConfiguration.py",
                     "src/modules/AmneziaConfiguration.py"):
            data = Path(path).read_text()
            self.assertIn("peer.refreshFromRow(row)", data)
            self.assertIn("cached.get(row[", data)
        peer = Path("src/modules/Peer.py").read_text()
        self.assertIn("def refreshFromRow(self, row)", peer)
        self.assertIn("datetime.datetime.now()", peer)
        self.assertNotIn("getJobs()", peer.split("def refreshFromRow(self, row)")[1].split("def metered_usage")[0])

    def test_ui_trims_config_and_bounds_chart(self):
        data = Path("src/dashboard.py").read_text()
        read_model = Path("src/modules/ConfigurationReadModel.py").read_text()
        self.assertIn('if k != "configuration"', read_model)
        self.assertIn('"configurationInfo": configuration', read_model)
        self.assertIn('configuration_info_payload(WireguardConfigurations[configurationName])', data)
        ui = Path("src/static/app/src/components/configurationComponents/peerList.vue").read_text()
        self.assertIn("document.hidden", ui)
        graph = Path("src/static/app/src/components/configurationComponents/peerListComponents/peerDataUsageCharts.vue").read_text()
        self.assertIn("MAX_REALTIME_POINTS = 120", graph)
        self.assertIn("animation: false", graph)
        self.assertIn("document.hidden", graph)


if __name__ == "__main__":
    unittest.main()
