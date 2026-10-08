"""Ejecuta toda la bateria y regenera research/recalc/results/latest/.

    .venv/Scripts/python research/recalc/run_all.py

Los conteos (E1-E6) son deterministas y deben repetirse identicos entre
ejecuciones. Los tiempos (E7) no lo son: por eso se reportan medianas.
"""
import os
import os.path as osp
import sys
import time

HERE = osp.dirname(osp.abspath(__file__))
sys.path.insert(0, HERE)

import exp_compile  # noqa: E402
import exp_counts  # noqa: E402
import exp_timing  # noqa: E402
import probe_api  # noqa: E402
import provenance  # noqa: E402

STEPS = [
    ('PROV   codigo y entorno medidos', provenance.main),
    ('E1-E5  conteo de ejecuciones', exp_counts.main),
    ('E6     compile() / DispatchPipe', exp_compile.main),
    ('E7     tiempos sobre test.xlsx', exp_timing.main),
    ('API    funcionamiento y alcance', probe_api.main),
]


def main():
    cwd = os.getcwd()
    failures = []
    for name, fn in STEPS:
        print('\n\n' + '#' * 72)
        print('# %s' % name)
        print('#' * 72)
        sys.stdout.flush()
        t0 = time.perf_counter()
        try:
            fn()
        except Exception as ex:  # no abortar el resto de la bateria
            failures.append((name, ex))
            print('!! FALLO: %s: %s' % (type(ex).__name__, ex))
        finally:
            os.chdir(cwd)
        print('\n[%s completado en %.1fs]' % (name, time.perf_counter() - t0))

    print('\n' + '=' * 72)
    if failures:
        print('FALLOS: %d' % len(failures))
        for name, ex in failures:
            print('  %s -> %s: %s' % (name, type(ex).__name__, ex))
        return 1
    print('Todo OK. Resultados en research/recalc/results/latest/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
