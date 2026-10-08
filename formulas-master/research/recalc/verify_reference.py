"""Compara results/latest/ con results/reference/ en todo lo determinista.

    .venv/Scripts/python research/recalc/verify_reference.py

Se exige igualdad en:
* counts.json, compile.json, api.json : completos.
* timing.json                         : solo los conteos de E7d y el numero
                                        de nodos funcion. Los tiempos varian
                                        entre maquinas y corridas.
* source_manifest.txt                 : huellas SHA-256 del codigo medido.

Sale con codigo 0 si todo coincide y 1 si hay alguna diferencia.
"""
import json
import os.path as osp
import sys

HERE = osp.dirname(osp.abspath(__file__))
REF = osp.join(HERE, 'results', 'reference')
NEW = osp.join(HERE, 'results', 'latest')


def load(base, name):
    with open(osp.join(base, name), encoding='utf-8') as f:
        return json.load(f)


def diff(a, b, path=''):
    """Lista de rutas donde a y b difieren."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append('%s/%s: falta en %s' % (
                    path, k, 'referencia' if k not in a else 'nueva'))
            else:
                out.extend(diff(a[k], b[k], '%s/%s' % (path, k)))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(diff(x, y, '%s[%d]' % (path, i)))
        return out
    return [] if a == b else ['%s: %r != %r' % (path or '/', a, b)]


def manifest(base):
    with open(osp.join(base, 'source_manifest.txt'), encoding='utf-8') as f:
        rows = [line.split() for line in f if not line.startswith('#')]
    # sha256  fecha  hora  ruta  -> se ignora la fecha (cambia al copiar)
    return {r[3]: r[0] for r in rows if len(r) >= 4}


def main():
    problems = {}
    for name in ('counts.json', 'compile.json', 'api.json'):
        problems[name] = diff(load(REF, name), load(NEW, name))

    ref_t, new_t = load(REF, 'timing.json'), load(NEW, 'timing.json')
    problems['timing.json (conteos E7d)'] = diff(ref_t['counts'],
                                                new_t['counts'])
    problems['timing.json (nodos funcion)'] = diff(
        ref_t['build']['function_nodes'], new_t['build']['function_nodes'])
    problems['source_manifest.txt'] = diff(manifest(REF), manifest(NEW))

    failed = False
    for name, found in problems.items():
        status = 'OK' if not found else 'DIFERENCIAS (%d)' % len(found)
        print('%-30s %s' % (name, status))
        for line in found[:10]:
            print('    ' + line)
        failed |= bool(found)

    print('')
    print('Tiempos (informativos, no se comparan):')
    for k in ('calculate() sin inputs', 'calculate(inputs=1 celda)'):
        print('  %-28s referencia %.2fs   nueva %.2fs' % (
            k, ref_t['timings'][k]['median'], new_t['timings'][k]['median']))
    print('')
    print('RESULTADO: %s' % ('reproduce la referencia' if not failed
                             else 'NO reproduce la referencia'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
