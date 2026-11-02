"""Validate the collaboration entry points and source hygiene, not human review."""
from pathlib import Path
import ast
import re
import subprocess
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[1]
IMAGES={'.png','.jpg','.jpeg','.gif','.webp','.bmp','.tif','.tiff','.svg','.ico','.pdf'}
DOCUMENTS=['AGENTS.md','Agent.md','CONTRIBUTING.md','README.md',
           'docs/DESIGN_REQUIREMENTS.md','docs/Agents.md','docs/BASELINE_1102.md','docs/KNOWN_ISSUES.md']

def main():
    tree=ast.parse((ROOT/'die_shift_studio/__init__.py').read_text(encoding='utf-8'))
    version=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=='__version__' for t in n.targets))
    assert re.fullmatch(r'V[1-9][0-9]*',version),version
    documents=DOCUMENTS+[f'docs/{version}_使用说明.md','.github/pull_request_template.md']
    for name in documents:
        path=ROOT/name;assert path.is_file(),f'Missing required guide: {name}'
        text=path.read_text(encoding='utf-8');assert text.strip(),name
        for target in re.findall(r'\]\(([^)]+)\)',text):
            target=target.strip('<>').split('#',1)[0]
            if not target or '://' in target or target.startswith('mailto:'):continue
            local=(path.parent/unquote(target)).resolve()
            assert local.is_relative_to(ROOT),f'Non-portable guide link: {name}: {target}'
            # package_release generates this record after isolated validation.
            if local==ROOT/'docs/package_verification.json':continue
            assert local.exists(),f'Broken guide link: {name}: {target}'
    assert (ROOT/'README.md').read_text(encoding='utf-8').splitlines()[0].endswith(version)
    assert f'docs/{version}_使用说明.md' in (ROOT/'AGENTS.md').read_text(encoding='utf-8')
    assert not (ROOT/'SHA256SUMS.json').exists() or not (ROOT/'.git').exists(), 'A repository-root release manifest becomes stale; generate it inside the source ZIP.'
    tracked=[]
    if (ROOT/'.git').exists():
        tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode('utf-8').split('\0')
        tracked=[p for p in tracked if p]
        bad=[p for p in tracked if any(v in Path(p).parts for v in ('__pycache__','logs','.venv','node_modules'))
             or Path(p).suffix.lower() in ('.pyc','.pyo','.log','.zip','.exe')]
        assert not bad,f'Runtime/build files tracked: {bad}'
        pictures=[p for p in tracked if Path(p).suffix.lower() in IMAGES]
        if pictures:
            raw=subprocess.check_output(['git','check-attr','-z','export-ignore','--',*pictures],cwd=ROOT).decode('utf-8').split('\0')
            for i in range(0,len(raw)-1,3):assert raw[i+2]=='set',f'Image must be export-ignore: {raw[i]}'
    print(f'CONTRIBUTION_CHECK_PASS: {version}, {len(documents)} portable guide/template files, {len(tracked)} tracked paths checked')
    print('This checks discoverability and file hygiene; it does not attest that an Agent read the guides or that runtime algorithms are defect-free.')

if __name__=='__main__':main()
