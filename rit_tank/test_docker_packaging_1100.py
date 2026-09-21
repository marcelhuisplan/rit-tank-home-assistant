"""Offline regression test for release 11.00: Dockerfile must ship every
runtime module that app.py imports, so the Home Assistant add-on image can
actually start. No Docker, Home Assistant or network calls are used."""
import re
import unittest
from pathlib import Path

REQUIRED_RUNTIME_MODULES = [
    'app.py',
    'pdf_report.py',
    'google_places.py',
    'routing.py',
    'home_assistant.py',
    'trips.py',
    'assistant.py',
]


class DockerfileRuntimeModulesTests(unittest.TestCase):
    """Ensure the Home Assistant Docker image contains all modular runtime files."""

    def setUp(self):
        self.root = Path(__file__).parent
        self.dockerfile = (self.root / 'Dockerfile').read_text(encoding='utf-8')

    def test_dockerfile_copies_every_runtime_module_to_app(self):
        copy_lines = [line for line in self.dockerfile.splitlines() if line.strip().startswith('COPY')]
        for module in REQUIRED_RUNTIME_MODULES:
            matches = [
                line for line in copy_lines
                if re.search(rf'(?<![\w.]){re.escape(module)}(?![\w.])', line) and '/app' in line
            ]
            self.assertTrue(
                matches,
                f"Dockerfile mist een COPY-regel die {module} naar /app kopieert; "
                "de add-on kan hierdoor niet opstarten."
            )

    def test_all_app_py_local_imports_are_shipped_in_dockerfile(self):
        app_source = (self.root / 'app.py').read_text(encoding='utf-8')
        local_module_stems = {p.stem for p in self.root.glob('*.py')}
        imported_stems = set(re.findall(r'^\s*(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_]*)', app_source, re.MULTILINE))
        local_imports = imported_stems & local_module_stems
        shipped = {module[:-3] for module in REQUIRED_RUNTIME_MODULES}
        missing = local_imports - shipped - {'app'}
        self.assertFalse(
            missing,
            f"app.py importeert lokale module(s) {missing} die niet in de Dockerfile "
            "naar /app worden gekopieerd."
        )


if __name__ == '__main__':
    unittest.main()
