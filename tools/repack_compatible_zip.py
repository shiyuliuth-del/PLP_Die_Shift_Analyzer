"""Rewrap a verified source ZIP using stored entries and explicit file types.

All file payloads, including the original SHA256 manifest, stay byte-identical.
The output has explicit directories, no encryption, no ZIP64 or data descriptor,
and no Deflate streams. This is an archive variant, not a software version bump.
"""
import argparse
import hashlib
import json
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath
import zipfile


def repack(source, output, root=None):
    source,output=Path(source),Path(output)
    if source.resolve()==output.resolve():raise ValueError('Output must differ from source')
    if root is not None and (not root or '/' in root or '\\' in root or root in ('.','..')):raise ValueError('Invalid root folder')
    with zipfile.ZipFile(source) as archive:
        broken=archive.testzip()
        if broken is not None:raise ValueError('Source CRC failed: '+broken)
        files={};source_roots=set()
        for entry in archive.infolist():
            path=PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename:raise ValueError('Invalid archive path')
            source_roots.add(path.parts[0])
            if entry.is_dir():continue
            relative=PurePosixPath(*path.parts[1:]).as_posix()
            if relative=='.' or relative in files:raise ValueError('Missing root or duplicate path')
            files[relative]=archive.read(entry)
        if len(source_roots)!=1:raise ValueError('Expected a single source root')
    root=root or next(iter(source_roots)).removesuffix('_Source')
    manifest=json.loads(files['SHA256SUMS.json'])
    for name,digest in manifest.items():
        if hashlib.sha256(files[name]).hexdigest()!=digest:raise ValueError('Manifest mismatch: '+name)
    folders={root+'/'}
    for name in files:
        parent=PurePosixPath(root,name).parent
        while parent.as_posix()!='.':folders.add(parent.as_posix()+'/');parent=parent.parent
    stamp=datetime.now().timetuple()[:6]
    def info(name,directory=False):
        item=zipfile.ZipInfo(name,stamp);item.create_system=3
        item.create_version=20;item.extract_version=20
        item.compress_type=zipfile.ZIP_STORED
        # Preserve actual file types and portable low-byte DOS flags. The
        # Unix creator also avoids old Info-ZIP OEM conversion of UTF-8 names.
        item.external_attr=((stat.S_IFDIR|0o755)<<16)|0x10 if directory else ((stat.S_IFREG|0o644)<<16)|0x20
        return item
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as archive:
        for name in sorted(folders):archive.writestr(info(name,True),b'')
        for name,data in sorted(files.items()):archive.writestr(info(root+'/'+name),data)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for name,data in files.items():assert archive.read(root+'/'+name)==data
        for item in archive.infolist():
            assert item.compress_type==zipfile.ZIP_STORED and item.create_system==3
            assert (stat.S_ISDIR if item.is_dir() else stat.S_ISREG)(item.external_attr>>16)
            assert not item.extra and not item.flag_bits&9  # no encryption or data descriptor
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(digest+'  '+output.name+'\n',encoding='ascii')
    return dict(status='PASS',source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                output_sha256=digest,output_size=output.stat().st_size,file_count=len(files),
                unchanged_payloads=True,method='ZIP Store, explicit regular-file/directory types and DOS flags, UTF-8 names where required, no ZIP64')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--root');args=parser.parse_args()
    print(json.dumps(repack(args.source,args.output,args.root),ensure_ascii=False,indent=2))
