"""Build a verified local checkpoint, optionally retaining exact corpus archives.

Only tracked project files and explicit pinned artifacts are included. Download
caches, environments and local credentials are never discovered or traversed.
This script does not upload, publish or modify a remote repository.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = "docs/certified_analysis/ROUND14_REPORT_ZH.md"
DEFAULT_BASELINE = "b37f5eb1cc6095a8f28ef264485c4b6935e54400"


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def build(destination, archives=None, *, report=DEFAULT_REPORT, baseline=DEFAULT_BASELINE):
    destination = Path(destination).resolve()
    if destination.is_relative_to(ROOT):
        raise ValueError("checkpoint destination must be outside the tracked project")
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    report_path = (ROOT / report).resolve(strict=True)
    if not report_path.is_relative_to(ROOT) or str(report_path.relative_to(ROOT)) not in tracked:
        raise ValueError("checkpoint report must be a tracked project file")
    report_name = str(report_path.relative_to(ROOT))
    if type(baseline) is not str or len(baseline) != 40 or any(c not in "0123456789abcdef" for c in baseline):
        raise ValueError("baseline must be an exact lowercase commit ID")
    if subprocess.check_output(["git", "cat-file", "-t", baseline], cwd=ROOT, text=True).strip() != "commit":
        raise ValueError("baseline must identify a retained local commit")
    destination.mkdir(parents=True, exist_ok=True)
    bundle = destination / "PythonStAn-history.bundle"
    subprocess.run(["git", "bundle", "create", str(bundle), "--all"], cwd=ROOT, check=True)
    subprocess.run(["git", "bundle", "verify", str(bundle)], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files = [("PythonStAn/" + name, ROOT / name) for name in tracked if name]
    files.append((bundle.name, bundle))
    archive_records = []
    if archives is not None:
        sys.path.insert(0, str(ROOT))
        from benchmark.round8_corpus.registry import projects
        archive_root = Path(archives).resolve()
        for record in projects():
            name = record.get("archive_filename", record.get("artifact_filename"))
            if type(name) is not str or Path(name).name != name:
                raise ValueError("pinned artifact must have a plain filename")
            path = archive_root / name
            if path.is_symlink() or path.stat().st_size != record["archive_bytes"] or sha256(path) != record["archive_sha256"]:
                raise ValueError("pinned artifact failed verification: " + record["id"])
            files.append(("corpus_archives/" + name, path))
            archive_records.append({"project_id": record["id"], "path": "corpus_archives/" + name,
                                    "bytes": record["archive_bytes"], "sha256": record["archive_sha256"]})
    readme = destination / "READ_ME_FIRST_ZH.md"
    readme.write_text(
        "# PythonStAn 当前检查点\n\n"
        f"代码：`{revision}`，分支：`{branch}`。\n\n"
        f"项目报告在 `PythonStAn/{report_name}`；"
        "inspector 使用说明在 `PythonStAn/docs/inspector/README_ZH.md`。"
        "具体成功、失败、未完成与能力边界以报告为准。\n\n"
        "`PROJECT_MANIFEST.json` 列出所有文件大小与 SHA-256；manifest 不自引用。"
        "`PythonStAn-history.bundle` 保留本地历史。\n\n"
        "恢复历史：`git clone PythonStAn-history.bundle PythonStAn-with-history`。\n\n"
        "如含 `corpus_archives/`，进入 PythonStAn 后可离线重建固定版本语料：\n\n"
        "```sh\npython -m benchmark.round8_corpus.resume_acquisition "
        "--downloads ../corpus_archives --cache ../round8_corpus "
        "--output /tmp/pythonstan-corpus-recovery.json\n```\n",
        encoding="utf-8")
    files.append((readme.name, readme))
    native_digest = hashlib.sha256()
    for path in sorted((ROOT / "pythonstan").rglob("*.py")):
        native_digest.update(str(path.relative_to(ROOT)).encode() + b"\0")
        native_digest.update(path.read_bytes())
        native_digest.update(b"\0")
    manifest = {"revision": revision, "tree": tree, "branch": branch,
        "core_sha256": native_digest.hexdigest(),
        "baseline_revision": baseline, "current_report": report_name,
        "pinned_corpus_archives": archive_records,
        "files": [{"path": name, "bytes": path.stat().st_size, "sha256": sha256(path)}
                  for name, path in files]}
    manifest_path = destination / "PROJECT_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    archive = destination / "PythonStAn_20261105_project.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as writer:
        for name, path in files:
            compressed = path.suffix in {".gz", ".whl", ".bundle"}
            writer.write(path, name, compress_type=zipfile.ZIP_STORED if compressed else zipfile.ZIP_DEFLATED)
        writer.write(manifest_path, "PROJECT_MANIFEST.json")
    with zipfile.ZipFile(archive) as reader:
        if reader.testzip() is not None:
            raise ValueError("checkpoint CRC verification failed")
        for record in manifest["files"]:
            size, digest = 0, hashlib.sha256()
            with reader.open(record["path"]) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(block)
                    digest.update(block)
            if size != record["bytes"] or digest.hexdigest() != record["sha256"]:
                raise ValueError("checkpoint file verification failed: " + record["path"])
    result = {"revision": revision, "archive": str(archive), "bytes": archive.stat().st_size,
              "sha256": sha256(archive), "verified_files": len(files),
              "pinned_archives": len(archive_records), "crc_and_all_file_hashes_verified": True}
    (destination / "ARCHIVE_VALIDATION.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--archives", type=Path)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.destination, arguments.archives,
                           report=arguments.report, baseline=arguments.baseline)))
