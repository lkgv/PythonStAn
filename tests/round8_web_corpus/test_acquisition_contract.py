"""Data-only acquisition, archive boundaries and exact frozen rehydration."""
import hashlib
import io
import tarfile
from pathlib import Path

import pytest
from benchmark.round8_corpus import acquire_web as acquisition


def archive(path, members):
    with tarfile.open(path, 'w:gz') as target:
        for name, payload in members:
            info=tarfile.TarInfo(name)
            info.size=len(payload)
            target.addfile(info,io.BytesIO(payload))


def test_complete_source_extraction_does_not_execute_target(tmp_path):
    marker=tmp_path/'executed'
    source=f'open({str(marker)!r}, "w").write("bad")\n'.encode()
    packed=tmp_path/'source.tar.gz'
    archive(packed,[('dist/pkg/__init__.py',source),('dist/LICENSE',b'license')])
    acquisition.extract(packed,tmp_path/'source')
    assert (tmp_path/'source/dist/pkg/__init__.py').read_bytes()==source
    assert not marker.exists()


def test_parent_archive_path_is_rejected_before_write(tmp_path):
    packed=tmp_path/'source.tar.gz'
    archive(packed,[('../escape.py',b'pass')])
    with pytest.raises(ValueError,match='unsafe'):
        acquisition.extract(packed,tmp_path/'source')
    assert not (tmp_path/'escape.py').exists()


def test_source_symlink_is_rejected(tmp_path):
    packed=tmp_path/'source.tar.gz'
    with tarfile.open(packed,'w:gz') as target:
        info=tarfile.TarInfo('dist/link');info.type=tarfile.SYMTYPE;info.linkname='../../escape'
        target.addfile(info)
    with pytest.raises(ValueError,match='unsafe'):
        acquisition.extract(packed,tmp_path/'source')


def test_frozen_entry_contract_survives_offline_rehydration(tmp_path,monkeypatch):
    downloads=tmp_path/'downloads';downloads.mkdir()
    packed=downloads/'pkg-1.0.tar.gz'
    archive(packed,[('pkg-1.0/pkg/__init__.py',b'def entry():\n    return 1\n'),('pkg-1.0/LICENSE',b'license')])
    metadata=tmp_path/'metadata';metadata.mkdir();monkeypatch.setattr(acquisition,'HERE',metadata)
    def forbid_network(url):raise AssertionError('Frozen cached archive must not use network')
    monkeypatch.setattr(acquisition,'fetch',forbid_network)
    frozen={'id':'pkg','package':'pkg','version':'1.0','category':'fixture',
            'source_url':'https://invalid.example/archive','archive_filename':packed.name,
            'archive_sha256':hashlib.sha256(packed.read_bytes()).hexdigest(),
            'archive_bytes':packed.stat().st_size,'entry_points':['pkg.entry'],
            'entry_records':[{'qualified_name':'pkg.entry','line':1}]}
    record=acquisition.acquire(('pkg','pkg','pkg','fixture'),tmp_path/'cache',downloads,frozen)
    assert record['entry_points']==['pkg.entry']
    assert record['entry_records']==frozen['entry_records']
    assert record['python_files']==1 and record['target_code_imported'] is False
    assert record['license_paths']==['LICENSE']
    assert (metadata/'inventory/pkg.json').exists()


def test_tampered_cached_source_cannot_rewrite_frozen_inventory(tmp_path,monkeypatch):
    downloads=tmp_path/'downloads';downloads.mkdir()
    packed=downloads/'pkg-1.0.tar.gz'
    archive(packed,[('pkg-1.0/pkg/__init__.py',b'def entry():\n    return 1\n'),('pkg-1.0/LICENSE',b'license')])
    metadata=tmp_path/'metadata';metadata.mkdir();monkeypatch.setattr(acquisition,'HERE',metadata)
    frozen={'id':'pkg','package':'pkg','version':'1.0','category':'fixture',
            'source_url':'https://invalid.example/archive','archive_filename':packed.name,
            'archive_sha256':hashlib.sha256(packed.read_bytes()).hexdigest(),
            'archive_bytes':packed.stat().st_size,'entry_points':['pkg.entry']}
    cache=tmp_path/'cache'
    record=acquisition.acquire(('pkg','pkg','pkg','fixture'),cache,downloads,frozen)
    inventory=metadata/record['source_inventory_file'];original=inventory.read_bytes()
    record['source_inventory_sha256']=hashlib.sha256(original).hexdigest()
    (cache/record['project_root']/'pkg/__init__.py').write_text('tampered = True\n')
    with pytest.raises(ValueError,match='cached runtime sources'):
        acquisition.acquire(('pkg','pkg','pkg','fixture'),cache,downloads,record)
    assert inventory.read_bytes()==original
