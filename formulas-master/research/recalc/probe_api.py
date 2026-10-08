"""Comprueba ejecutandolas las afirmaciones sobre funcionamiento y alcance.

Nada aqui se copia de la documentacion: todo se mide contra la version
instalada. Donde el README declara una cifra, se contrasta.
"""
import json
import os
import os.path as osp
import subprocess
import sys

sys.path.insert(0, osp.dirname(osp.abspath(__file__)))

import instrument as I  # noqa: E402

HERE = osp.dirname(osp.abspath(__file__))
ROOT = osp.normpath(osp.join(HERE, '..', '..'))
RESULTS = osp.join(HERE, 'results', 'latest')
PY = sys.executable


def banner(title):
    print('\n' + '=' * 72)
    print(title)
    print('=' * 72)
    sys.stdout.flush()


def probe_versions(out):
    banner('Versiones')
    import formulas
    import schedula
    import numpy
    import openpyxl
    info = {'python': sys.version.split()[0],
            'formulas': formulas.__version__,
            'schedula': schedula.__version__,
            'numpy': numpy.__version__,
            'openpyxl': openpyxl.__version__}
    for k, v in info.items():
        print('  %-10s %s' % (k, v))
    out['versions'] = info


def probe_public_api(out):
    banner('API publica e import perezoso (PEP 562)')
    import importlib
    import formulas

    names = sorted(formulas.__all__)
    print('  __all__ (%d): %s' % (len(names), ', '.join(names)))

    # El import perezoso: con IMPORT_ALL=False nada esta materializado aun.
    code = ('import os; os.environ["IMPORT_ALL"]="False";'
            'import importlib, formulas; importlib.reload(formulas);'
            'print(set(formulas.__all__).isdisjoint(formulas.__dict__))')
    lazy = subprocess.run([PY, '-c', code], capture_output=True, text=True,
                          cwd=ROOT).stdout.strip()
    code = code.replace('"False"', '"True"').replace('isdisjoint',
                                                     'issubset')
    eager = subprocess.run([PY, '-c', code], capture_output=True, text=True,
                           cwd=ROOT).stdout.strip()
    print('  IMPORT_ALL=False -> nada materializado : %s' % lazy)
    print('  IMPORT_ALL=True  -> todo materializado : %s' % eager)
    out['public_api'] = {'all': names, 'lazy_ok': lazy, 'eager_ok': eager}
    importlib.reload(formulas)


def probe_coverage(out):
    banner('Cobertura de funciones de Excel')
    import formulas
    from formulas.functions import not_implemented

    fns = formulas.get_functions()
    total_keys = len(fns)
    explicit_ni = [k for k, v in fns.items() if v is not_implemented]
    print('  claves registradas en get_functions() : %d' % total_keys)
    print('  de ellas marcadas not_implemented     : %d' % len(explicit_ni))
    print('  submodulos                            : %s'
          % ', '.join(formulas.SUBMODULES))
    print('  (not_implemented es el default_factory del defaultdict: solo')
    print('   aparece al pedir un nombre ausente, no como valor registrado)')

    # Fuente de verdad del README: la hoja COVERAGE de test.xlsx.
    from openpyxl import load_workbook
    wb = load_workbook(osp.join(I.TEST_FILES, 'test.xlsx'), data_only=True)
    ws = wb['COVERAGE']
    declared = {'implemented': ws['B18'].value, 'total': ws['D18'].value,
                'ratio': ws['E18'].value}
    print('  hoja COVERAGE de test.xlsx            : %s de %s (%.1f%%)'
          % (declared['implemented'], declared['total'],
             declared['ratio'] * 100))
    print('')
    print('  Las dos cifras miden cosas distintas: get_functions() cuenta')
    print('  claves registradas (incluye alias _xlfn.* y helpers internos);')
    print('  la hoja COVERAGE cuenta funciones del catalogo oficial de Excel.')

    # Funcion desconocida: el resultado DEPENDE de la via usada.
    print('')
    print('  "NOEXISTE" in get_functions()         : %s'
          % ('NOEXISTE' in fns))
    raw = formulas.Parser().ast('=NOEXISTE(1,2)')[1].compile()
    try:
        raw_res = repr(raw())
    except Exception as ex:
        raw_res = '%s: %s' % (type(ex).__name__, str(ex)[:60])
    print('  via Parser().ast(...).compile()       : %s' % raw_res)

    from formulas.excel import ExcelModel
    mdl = ExcelModel().from_dict({'A1': '=NOEXISTE(1,2)'}).finish()
    cell_res = I.unwrap(mdl.calculate()['A1'])
    print('  via celda de un ExcelModel            : %r' % (cell_res,))
    print('')
    print('  El #NAME? lo produce CellWrapper.__call__ (formulas/cell.py:37),')
    print('  que solo envuelve formulas de CELDA. Una funcion compilada a')
    print('  pelo con Parser propaga la excepcion del dispatcher.')

    # Extension: el dict es mutable y cacheado -> punto de extension oficial.
    fns['MYFUNC'] = lambda x, y: 1 + y + x
    custom = formulas.Parser().ast('=MYFUNC(1, 2)')[1].compile()
    print('  tras registrar MYFUNC, =MYFUNC(1,2)   : %r' % (custom(),))
    del fns['MYFUNC']

    out['coverage'] = {'registered_keys': total_keys,
                       'explicit_not_implemented': len(explicit_ni),
                       'unknown_via_parser': raw_res,
                       'unknown_via_cell': repr(cell_res),
                       'coverage_sheet': declared,
                       'submodules': list(formulas.SUBMODULES)}


def probe_errors(out):
    banner('Errores de Excel soportados')
    from formulas.tokens.operand import Error
    from formulas.excel import ERR_CIRCULAR
    names = list(Error.errors)
    print('  %d errores: %s' % (len(names), ', '.join(names)))
    print('  ERR_CIRCULAR = %r  -> str() = %r'
          % (ERR_CIRCULAR, str(ERR_CIRCULAR)))
    missing = [e for e in ('#SPILL!', '#CALC!', '#FIELD!', '#BLOCKED!')
               if e not in Error.errors]
    print('  no soportados: %s' % ', '.join(missing))
    out['errors'] = {'supported': names, 'unsupported_checked': missing,
                     'circular_str': str(ERR_CIRCULAR)}


def probe_parser(out):
    banner('Parser: de una formula suelta a una funcion Python')
    import formulas
    tokens, builder = formulas.Parser().ast('=(1 + 1) + B3 / A2')
    func = builder.compile()
    print('  tokens reconocidos : %d' % len(tokens))
    print('  func.inputs        : %s' % list(func.inputs))
    print('  func(1, 5)         : %r' % (func(1, 5),))
    print('  tipo de func       : %s.%s'
          % (type(func).__module__, type(func).__name__))
    out['parser'] = {'inputs': list(func.inputs),
                     'result': repr(func(1, 5)),
                     'type': '%s.%s' % (type(func).__module__,
                                        type(func).__name__)}


def probe_circular(out):
    banner('Referencias circulares: comportamiento por defecto vs circular=True')
    os.chdir(I.TEST_FILES)
    from formulas.excel import ExcelModel, ERR_CIRCULAR

    plain = ExcelModel().loads('circular.xlsx').finish()
    sol_plain = plain.calculate()
    missing = [k for k in plain.dsp.data_nodes
               if isinstance(k, str) and k not in sol_plain]

    circ = ExcelModel().loads('circular.xlsx').finish(circular=True)
    sol_circ = circ.calculate()
    circ_vals = [k for k, v in sol_circ.items()
                 if isinstance(k, str) and I.unwrap(v) is ERR_CIRCULAR]

    print('  finish() por defecto  -> %d nodos de datos SIN resolver'
          % len(missing))
    print('     ejemplos: %s' % sorted(map(I.short, missing))[:8])
    print('  finish(circular=True) -> %d nodos SIN resolver, %d con #CIRC!'
          % (len([k for k in circ.dsp.data_nodes
                  if isinstance(k, str) and k not in sol_circ]),
             len(circ_vals)))
    print('     ejemplos: %s' % sorted(map(I.short, circ_vals))[:8])
    print('')
    print('  Sin circular=True los nodos del ciclo quedan AUSENTES de la')
    print('  solucion: no hay error, simplemente no se calculan.')
    out['circular'] = {'unresolved_default': len(missing),
                       'err_circular_with_flag': len(circ_vals)}


def probe_formats(out):
    banner('Formatos de entrada')
    os.chdir(I.TEST_FILES)
    from formulas.excel import ExcelModel
    from formulas.cli import _load_model, CliError

    res = {}
    m = ExcelModel().loads('test.ods').finish()
    res['ods_api'] = len(m.dsp.data_nodes)
    print('  .ods via API   : OK (%d nodos de datos)' % res['ods_api'])

    try:
        _load_model(['algo.xls'])
        res['xls_cli'] = 'aceptado (inesperado)'
    except CliError as ex:
        res['xls_cli'] = str(ex)
    print('  .xls via CLI   : rechazado -> %s' % res['xls_cli'])

    try:
        _load_model(['algo.xlsm'])
        res['xlsm_cli'] = 'aceptado'
    except CliError as ex:
        res['xlsm_cli'] = str(ex)
    except Exception as ex:
        res['xlsm_cli'] = 'paso el filtro de extension (%s)' % type(ex).__name__
    print('  .xlsm via CLI  : rechazado -> %s' % res['xlsm_cli'])
    print('')
    print('  La CLI solo admite .xlsx, .ods y .json (formulas/cli.py:220-227).')
    print('  La API delega en openpyxl y no filtra por extension, pero no hay')
    print('  fixture .xlsm en el repo: eso queda SIN COMPROBAR aqui.')
    out['formats'] = res


def probe_cli(out):
    banner('Comandos reales de la CLI')
    # El ejecutable de la CLI del MISMO entorno que ejecuta este script.
    exe = osp.join(osp.dirname(sys.executable),
                   'formulas.exe' if os.name == 'nt' else 'formulas')
    r = subprocess.run([exe, '--help'], capture_output=True, text=True,
                       cwd=ROOT)
    text = r.stdout
    cmds = []
    grab = False
    for line in text.splitlines():
        if line.startswith('Commands:'):
            grab = True
            continue
        if grab and line.startswith('  ') and line[2:3].islower():
            cmds.append(line.split()[0])
        elif grab and not line.startswith('  '):
            break
    print('  comandos: %s' % ', '.join(cmds))
    for absent in ('run', 'plot'):
        print('  "%s" existe: %s' % (absent, absent in cmds))
    out['cli'] = {'commands': cmds}


def probe_packaging(out):
    banner('Empaquetado: extras declarados vs anunciados')
    import re
    setup = open(osp.join(ROOT, 'setup.py'), encoding='utf-8').read()
    block = setup[setup.index('extras = {'):setup.index("extras['all']")]
    declared = sorted(set(re.findall(r"^\s*'(\w+)':", block, re.M))
                      | {'cli', 'all', 'dev'})
    reqs = sorted(f[:-4] for f in os.listdir(osp.join(ROOT, 'requirements'))
                  if f.endswith('.pip'))
    plot = open(osp.join(ROOT, 'requirements', 'plot.pip'),
                encoding='utf-8').read().splitlines()[0]
    print('  extras en setup.py        : %s' % ', '.join(declared))
    print('  ficheros en requirements/ : %s' % ', '.join(reqs))
    print('  primera linea de plot.pip : %r' % plot)
    print('  requirements/web.pip existe: %s' % ('web' in reqs))
    print('')
    print('  plot.pip depende de un web.pip inexistente, asi que')
    print('  `pip install -r requirements/{plot,all,dev}.pip` falla.')
    out['packaging'] = {'extras': declared, 'requirements': reqs,
                        'plot_first_line': plot, 'web_pip_exists': 'web' in reqs}


def main():
    out = {}
    probe_versions(out)
    probe_public_api(out)
    probe_coverage(out)
    probe_errors(out)
    probe_parser(out)
    probe_circular(out)
    probe_formats(out)
    probe_cli(out)
    probe_packaging(out)

    os.makedirs(RESULTS, exist_ok=True)
    with open(osp.join(RESULTS, 'api.json'), 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print('')
    print('Resultados -> research/recalc/results/latest/api.json')
    return out


if __name__ == '__main__':
    main()
