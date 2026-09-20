import io
import stat
import zipfile

import pytest

from src.backup.archive import BackupArchive
from src.backup.collectors import CosmosBackupCollector
from src.backup.export_service import ExportService
from src.backup.models import ExportRequest

from .user_backup_fakes import populated_cosmos


def _archive():
    service = ExportService(CosmosBackupCollector(populated_cosmos()))
    request = ExportRequest()
    request.preview_fingerprint = service.preview(request).selection_fingerprint
    return service.export(request).archive


def test_archive_round_trips_and_checksums_are_verified():
    parsed = BackupArchive().read(_archive())
    assert parsed.manifest["archive_version"] == 1
    assert len(parsed.sections) == 7


def test_duplicate_and_traversal_entries_are_rejected():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("../manifest.json", b"{}")
    with pytest.raises(ValueError):
        BackupArchive().read(raw.getvalue())


def test_checksum_tampering_is_rejected():
    original = _archive()
    source = zipfile.ZipFile(io.BytesIO(original))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "data/accounts.json":
                data = data.replace(b"Demo", b"Evil")
            target.writestr(info, data)
    with pytest.raises(ValueError, match="Checksum"):
        BackupArchive().read(output.getvalue())


@pytest.mark.parametrize("renamer", [
    lambda name, index: name.upper() if index == 1 else name,
    lambda name, index: "../manifest.json" if index == 0 else name,
])
def test_case_collision_or_unsafe_name_is_rejected(renamer):
    source = zipfile.ZipFile(io.BytesIO(_archive()))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w") as target:
        for index, info in enumerate(source.infolist()):
            target.writestr(renamer(info.filename, index), source.read(info.filename))
    with pytest.raises(ValueError):
        BackupArchive().read(output.getvalue())


def test_symlink_entry_is_rejected_before_json_use():
    source = zipfile.ZipFile(io.BytesIO(_archive()))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w") as target:
        for index, info in enumerate(source.infolist()):
            replacement = zipfile.ZipInfo(info.filename)
            replacement.external_attr = (
                (stat.S_IFLNK | 0o777) << 16 if index == 1 else info.external_attr
            )
            target.writestr(replacement, source.read(info.filename))
    with pytest.raises(ValueError, match="symlink"):
        BackupArchive().read(output.getvalue())


@pytest.mark.parametrize("file_type", [
    stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFDIR,
])
def test_every_non_regular_zip_entry_type_is_rejected(file_type):
    source = zipfile.ZipFile(io.BytesIO(_archive()))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w") as target:
        for index, info in enumerate(source.infolist()):
            replacement = zipfile.ZipInfo(info.filename)
            replacement.create_system = 3
            replacement.external_attr = (
                (file_type | 0o600) << 16 if index == 1 else info.external_attr
            )
            target.writestr(replacement, source.read(info.filename))
    with pytest.raises(ValueError, match="non-regular"):
        BackupArchive().read(output.getvalue())
