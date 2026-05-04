from __future__ import annotations

import io
import json
import subprocess
import sys
import textwrap
from pathlib import Path


class TestRenderReportCli:
    def _fixture(self):
        return {
            "metadata": {
                "table_id": "p.d.t",
                "num_rows": 100,
                "num_bytes_human": "100 B",
                "schema_field_count": 1,
                "table_type": "TABLE",
                "modified": "2026-05-03T11:30:00+00:00",
                "schema": [{"name": "id", "type": "INT64", "mode": "REQUIRED", "description": ""}],
            },
            "checks": {
                "duplicate_keys": {
                    "status": "complete",
                    "rows": [{"duplicate_excess_rows": 5, "duplicate_key_groups": 2}],
                },
            },
            "warnings": [],
        }

    def test_writes_markdown(self, tmp_path, monkeypatch, render_report_module):
        in_path = tmp_path / "in.json"
        in_path.write_text(json.dumps(self._fixture()))
        out_md = tmp_path / "out.md"

        monkeypatch.setattr(sys, "argv", ["render_report.py", "--input", str(in_path), "--output-md", str(out_md)])
        rc = render_report_module.main()
        assert rc == 0
        text = out_md.read_text(encoding="utf-8")
        assert "p.d.t" in text
        assert "Rows: 100" in text

    def test_also_writes_html_when_flag_set(self, tmp_path, monkeypatch, render_report_module):
        in_path = tmp_path / "in.json"
        in_path.write_text(json.dumps(self._fixture()))
        out_md = tmp_path / "out.md"
        out_html = tmp_path / "out.html"

        monkeypatch.setattr(
            sys,
            "argv",
            ["render_report.py", "--input", str(in_path), "--output-md", str(out_md),
             "--output-html", str(out_html), "--theme", "dark"],
        )
        rc = render_report_module.main()
        assert rc == 0
        html = out_html.read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>")
        # dark-only theme — no media query
        assert "@media (prefers-color-scheme: dark)" not in html

    def test_expectation_failure_exits_3(self, tmp_path, monkeypatch, render_report_module):
        in_path = tmp_path / "in.json"
        in_path.write_text(json.dumps(self._fixture()))
        out_md = tmp_path / "out.md"

        monkeypatch.setattr(
            sys,
            "argv",
            ["render_report.py", "--input", str(in_path), "--output-md", str(out_md),
             "--expect-zero-duplicates"],
        )
        rc = render_report_module.main()
        assert rc == 3
        # report file is still written
        assert out_md.exists()

    def test_expectation_pass_exits_0(self, tmp_path, monkeypatch, render_report_module):
        in_path = tmp_path / "in.json"
        in_path.write_text(json.dumps(self._fixture()))
        out_md = tmp_path / "out.md"

        monkeypatch.setattr(
            sys,
            "argv",
            ["render_report.py", "--input", str(in_path), "--output-md", str(out_md),
             "--expect-min-rows", "50"],
        )
        rc = render_report_module.main()
        assert rc == 0

    def test_stdin_input(self, tmp_path, monkeypatch, render_report_module):
        out_md = tmp_path / "out.md"
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(self._fixture())))
        monkeypatch.setattr(sys, "argv", ["render_report.py", "--input", "-", "--output-md", str(out_md)])
        rc = render_report_module.main()
        assert rc == 0
        assert "p.d.t" in out_md.read_text(encoding="utf-8")

    def test_output_matches_main_script_renderer(self, tmp_path, monkeypatch, render_report_module, render_module, serialize_module):
        """Path A and Path B must produce byte-identical Markdown for the same report dict."""
        report = self._fixture()
        in_path = tmp_path / "in.json"
        in_path.write_text(json.dumps(report))
        out_md = tmp_path / "out.md"

        monkeypatch.setattr(sys, "argv", ["render_report.py", "--input", str(in_path), "--output-md", str(out_md)])
        render_report_module.main()
        via_render_report = out_md.read_text(encoding="utf-8")

        # Direct call to the same renderer that the bundled script also uses.
        via_renderer = render_module.make_markdown(serialize_module.serialize(report))
        assert via_render_report == via_renderer

    def test_runs_without_google_cloud_bigquery_installed(self, tmp_path):
        """Path A invariant: render_report.py must not require google-cloud-bigquery.

        Spawned in a subprocess so conftest.py's stubs do not leak in. The script
        is expected to run cleanly even though `google` is not importable in the
        child process (we explicitly block it via a meta-path finder).
        """
        in_path = tmp_path / "in.json"
        in_path.write_text(json.dumps(self._fixture()))
        out_md = tmp_path / "out.md"

        script = Path(__file__).parent.parent / "scripts" / "render_report.py"
        block_google = textwrap.dedent(
            """
            import sys
            class _BlockGoogle:
                def find_spec(self, name, path=None, target=None):
                    if name == 'google' or name.startswith('google.'):
                        raise ModuleNotFoundError(f"blocked by test: {name}")
                    return None
            sys.meta_path.insert(0, _BlockGoogle())
            sys.modules.pop('google', None)
            sys.modules.pop('google.cloud', None)
            sys.modules.pop('google.cloud.bigquery', None)
            """
        ).strip()
        runner = (
            f"{block_google}\n"
            f"import runpy\n"
            f"from pathlib import Path\n"
            f"script_path = {str(script)!r}\n"
            # Mimic what Python does for `python scripts/render_report.py`:
            # add the script's directory to sys.path so sibling-module imports work.
            f"sys.path.insert(0, str(Path(script_path).parent))\n"
            f"sys.argv = [script_path, '--input', {str(in_path)!r}, '--output-md', {str(out_md)!r}]\n"
            f"runpy.run_path(script_path, run_name='__main__')\n"
        )
        stdout_path = tmp_path / "stdout.txt"
        stderr_path = tmp_path / "stderr.txt"
        with open(stdout_path, "w", encoding="utf-8") as so, open(stderr_path, "w", encoding="utf-8") as se:
            rc = subprocess.call(
                [sys.executable, "-c", runner],
                stdin=subprocess.DEVNULL,
                stdout=so,
                stderr=se,
            )
        stderr = stderr_path.read_text(encoding="utf-8")
        assert rc == 0, f"stdout: {stdout_path.read_text(encoding='utf-8')}\nstderr: {stderr}"
        assert out_md.exists()
        assert "p.d.t" in out_md.read_text(encoding="utf-8")
