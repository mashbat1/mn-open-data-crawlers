import subprocess
import unittest
from unittest.mock import patch
import ci


class GitHubOutageTests(unittest.TestCase):
    def test_outage_is_not_an_absent_release(self):
        failure = subprocess.CompletedProcess([], 1, "", "HTTP 502: Server Error")
        with patch.object(ci.subprocess, "run", return_value=failure), patch.object(ci.time, "sleep"):
            with self.assertRaises(SystemExit):
                ci.release_exists("wip-audit")

    def test_transient_read_failure_recovers(self):
        responses = [subprocess.CompletedProcess([], 1, "", "HTTP 502: Server Error"),
                     subprocess.CompletedProcess([], 0, "release exists", "")]
        with patch.object(ci.subprocess, "run", side_effect=responses), patch.object(ci.time, "sleep"):
            self.assertTrue(ci.release_exists("wip-audit"))

    def test_real_not_found_allows_creation(self):
        failure = subprocess.CompletedProcess([], 1, "", "release not found")
        with patch.object(ci.subprocess, "run", return_value=failure):
            self.assertFalse(ci.release_exists("wip-new"))
