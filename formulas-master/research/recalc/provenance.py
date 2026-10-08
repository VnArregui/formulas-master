"""Identifica exactamente el codigo y el entorno medidos.

El arbol de `formulas` es una instantanea sin metadatos de git (carpeta
`formulas-master`), asi que no hay commit que registrar. En su lugar:

* results/latest/source_manifest.txt : SHA-256 de cada fichero del paquete y de los
  ficheros de prueba usados, con su fecha de modificacion.
* results/latest/pip_freeze.txt      : versiones exactas del venv.
* results/latest/provenance.json     : resumen (version declarada, huella global,
  ficheros modificados despues de la extraccion).

Con la instalacion editable (`pip install -e .`) el codigo ejecutado es el de
este arbol, por lo que el manifiesto es la identificacion relevante.
"""
import datetime
import hashlib
import json
import os
import os.path as osp
import subprocess
import sys

HERE = osp.dirname(osp.abspath(__file__))
ROOT = osp.normpath(osp.join(HERE, '..', '..'))
RESULTS = osp.join(HERE, 'results', 'latest')

TRACKED = ['formulas', 'setup.py', 'setup.cfg', 'requirements',
           osp.join('test', 'test_excel.py'),
           osp.join('test', 'test_files', 'excel.xlsx'),
           osp.join('test', 'test_files', 'test.xlsx'),
           osp.join('test', 'test_files', 'test_link.xlsx'),
           osp.join('test', 'test_files', 'circular.xlsx'),
           osp.join('test', 'test_files', 'test.ods')]


def iter_files():
    for entry in TRACKED:
        path = osp.join(ROOT, entry)
        if osp.isfile(path):
            yield path
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = sorted(d for d in dirnames if d != '__pycache__')
            for name in sorted(filenames):
                if not name.endswith('.pyc'):
                    yield osp.join(dirpath, name)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def analysis_repo():
    """Commit del repositorio git que contiene este analisis, si existe."""
    def git(*args):
        r = subprocess.run(['git', *args], capture_output=True, text=True,
                           cwd=HERE)
        return r.stdout.strip() if r.returncode == 0 else None
    top = git('rev-parse', '--show-toplevel')
    if not top:
        return None
    return {'toplevel': top,
            'commit': git('rev-parse', 'HEAD') or '(sin commits)',
            'dirty': bool(git('status', '--porcelain'))}


def main():
    os.makedirs(RESULTS, exist_ok=True)
    rows, mtimes = [], []
    for path in iter_files():
        rel = osp.relpath(path, ROOT).replace(os.sep, '/')
        mtime = osp.getmtime(path)
        mtimes.append((mtime, rel))
        rows.append((rel, sha256(path), mtime))

    tree = hashlib.sha256(
        ''.join('%s %s\n' % (rel, digest) for rel, digest, _ in rows).encode()
    ).hexdigest()

    with open(osp.join(RESULTS, 'source_manifest.txt'), 'w',
              encoding='utf-8') as f:
        f.write('# sha256  mtime  path   (huella global: %s)\n' % tree)
        for rel, digest, mtime in rows:
            stamp = datetime.datetime.fromtimestamp(mtime).isoformat(
                sep=' ', timespec='seconds')
            f.write('%s  %s  %s\n' % (digest, stamp, rel))

    freeze = subprocess.run([sys.executable, '-m', 'pip', 'freeze'],
                            capture_output=True, text=True).stdout
    with open(osp.join(RESULTS, 'pip_freeze.txt'), 'w', encoding='utf-8') as f:
        f.write(freeze)

    # La extraccion fija un mtime comun; cualquier fichero posterior seria
    # una modificacion local.
    base = min(m for m, _ in mtimes)
    later = sorted(rel for m, rel in mtimes if m - base > 120)

    sys.path.insert(0, ROOT)
    from formulas import _version
    import schedula
    info = {
        # La instantanea de formulas no trae commit de origen; el repositorio
        # de analisis que la contiene si (puede no existir aun).
        'snapshot_upstream_commit': None,
        'analysis_repo': analysis_repo(),
        'declared_version': _version.__version__,
        'declared_updated': _version.__updated__,
        'files_hashed': len(rows),
        'tree_sha256': tree,
        'modified_after_extraction': later,
        'install_mode': 'editable (pip install -e .)',
        'schedula': schedula.__version__,
        'python': sys.version.split()[0],
    }
    with open(osp.join(RESULTS, 'provenance.json'), 'w') as f:
        json.dump(info, f, indent=2)

    repo = info['analysis_repo']
    print('Commit de origen instantanea: ninguno (sin metadatos de git upstream)')
    print('Repo de analisis            : %s' % (
        '%s%s' % (repo['commit'], ' + cambios sin commit' if repo['dirty']
                  else '') if repo else 'no es un repositorio git'))
    print('Version declarada           : %s (%s)'
          % (info['declared_version'], info['declared_updated']))
    print('Ficheros con huella         : %d' % len(rows))
    print('Huella global del arbol     : %s' % tree)
    print('Modificados tras extraer    : %s' % (later or 'ninguno'))
    print('')
    print('-> results/latest/source_manifest.txt, pip_freeze.txt, provenance.json')
    return info


if __name__ == '__main__':
    main()
