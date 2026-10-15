"""Reproducible local build. No application data is included in source archives."""
from __future__ import annotations

import hashlib
import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main(package_only=False):
    package = ROOT / 'release/随行音乐转换器'
    if not package_only:
        # Collect into a build-only directory: PyInstaller --noconfirm must never
        # recursively remove a user's portable folder, settings or converted music.
        stage_root = ROOT / 'build/portable'
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(stage_root),
                        '--workpath', str(ROOT / 'build'), str(ROOT / 'CarMusic.spec')], cwd=ROOT, check=True)
        stage = stage_root / '随行音乐转换器'
        package.mkdir(parents=True, exist_ok=True)
        for name in ('CarMusic.exe', 'CarMusicCLI.exe'):
            target = package / name
            if target.exists():
                try:
                    with target.open('r+b'):
                        pass
                except PermissionError as exc:
                    raise RuntimeError('请先关闭正在运行的随行音乐转换器，再重新构建。现有输出不会被删除。') from exc
        internal = package / '_internal'
        if internal.exists():
            if not internal.resolve().is_relative_to(ROOT.resolve()):
                raise RuntimeError('拒绝清理工作区之外的依赖目录。')
            shutil.rmtree(internal)
        shutil.copytree(stage / '_internal', internal)
        for name in ('CarMusic.exe', 'CarMusicCLI.exe'):
            shutil.copy2(stage / name, package / name)
    for name in ('README.md', 'THIRD_PARTY.md', 'LICENSE'):
        shutil.copy2(ROOT / name, package / name)
    shutil.copytree(ROOT / 'licenses', package / 'licenses', dirs_exist_ok=True)
    # Keep the private validation outputs and client databases out of the application package.
    source = package / '应用源代码.zip'
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as archive:
        includes = ['main.py', 'build_release.py', 'CarMusic.spec', 'requirements.txt', 'requirements-dev.txt',
                    'README.md', 'THIRD_PARTY.md', 'LICENSE', '.gitignore']
        for name in includes:
            archive.write(ROOT / name, name)
        for directory in ('car_music', 'assets', 'licenses', 'tests'):
            for path in (ROOT / directory).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                    archive.write(path, path.relative_to(ROOT))
    target = ROOT / 'release/随行音乐转换器-Windows-x64.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        # Allowlist deliverables: never include settings, converted songs or reports
        # that a user may have saved inside their existing portable folder.
        for name in ('CarMusic.exe', 'CarMusicCLI.exe', 'README.md', 'THIRD_PARTY.md', 'LICENSE', '应用源代码.zip'):
            path = package / name
            archive.write(path, path.relative_to(package.parent))
        for directory in ('_internal', 'licenses'):
            for path in (package / directory).rglob('*'):
                if path.is_file():
                    archive.write(path, path.relative_to(package.parent))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(f'{digest}  {target.name}\n', encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size / 1024**2:.1f} MB)', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--package-only', action='store_true', help='仅重新整理现有 EXE 的文档、源码及压缩包')
    main(parser.parse_args().package_only)
