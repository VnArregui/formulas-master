"""E7: tiempos y conteos sobre el libro grande (test.xlsx, 405 KB).

Los tiempos por si solos NO discriminan: un motor incremental no tiene por
que ser mucho mas rapido si el cambio afecta a muchas celdas o a las caras.
Por eso E7d cuenta nodos invocados y los compara con el conjunto realmente
afectado por el cambio en este grafo. Los tiempos se reportan como
resultados de estos casos concretos, en esta maquina.
"""
import json
import os
import os.path as osp
import re
import statistics
import sys
import time

sys.path.insert(0, osp.dirname(osp.abspath(__file__)))

import instrument as I  # noqa: E402

RESULTS = osp.join(osp.dirname(osp.abspath(__file__)), 'results', 'latest')
REPS = 3


_RE_CELL = re.compile(r"^'\[[^\]]+\][^']+'!\$?[A-Z]{1,3}\$?[0-9]{1,7}$")


def is_cell_ref(node_id):
    """True solo para una referencia A1 de una hoja concreta.

    Descarta nodos de error (#REF!, #NAME?), nombres definidos
    ('[test.xlsx]'!A) y rangos (A1:B2).
    """
    return isinstance(node_id, str) and bool(_RE_CELL.match(node_id))


def banner(title):
    print('\n' + '=' * 72)
    print(title)
    print('=' * 72)
    sys.stdout.flush()


def timeit(fn, reps=REPS):
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return {'median': statistics.median(samples),
            'min': min(samples), 'max': max(samples),
            'samples': [round(s, 4) for s in samples]}


def report(name, stats):
    print('  %-42s mediana %7.3fs   (min %.3f / max %.3f)'
          % (name, stats['median'], stats['min'], stats['max']))
    sys.stdout.flush()


def main():
    out = {'reps': REPS}
    os.chdir(I.TEST_FILES)
    from formulas.excel import ExcelModel

    banner('E7 - Coste de construir el modelo vs coste de calcular')

    t0 = time.perf_counter()
    m = ExcelModel()
    m.loads('test.xlsx')
    m.add_book('test_link.xlsx')
    t_loads = time.perf_counter() - t0

    t0 = time.perf_counter()
    m.finish()
    t_finish = time.perf_counter() - t0

    dsp = m.dsp
    print('  %-42s %7.3fs' % ('loads (parseo de los libros)', t_loads))
    print('  %-42s %7.3fs' % ('finish (construccion del grafo)', t_finish))
    print('  nodos de datos: %d   nodos funcion: %d'
          % (len(dsp.data_nodes), len(dsp.function_nodes)))
    out['build'] = {'loads': t_loads, 'finish': t_finish,
                    'data_nodes': len(dsp.data_nodes),
                    'function_nodes': len(dsp.function_nodes)}

    # Elegir celdas de entrada REALES: referencias A1 de una hoja concreta,
    # con valor por defecto y sin ninguna funcion que las produzca (hojas del
    # grafo). Se descartan los nodos de error (#REF!, #NAME?) y los nombres
    # definidos, que no son celdas. Se ordenan por grado de salida para que
    # el cambio alimente de verdad al modelo.
    leaves = [k for k in dsp.default_values
              if is_cell_ref(k) and not dsp.dmap.pred.get(k)]
    leaves.sort(key=lambda k: (-len(dsp.dmap.succ.get(k, ())), k))
    print('  celdas de entrada candidatas (hojas del grafo): %d' % len(leaves))
    one, many = leaves[:1], leaves[:20]
    print('  celda usada para "1 celda": %s (alimenta %d nodos directos)'
          % (I.short(one[0]), len(dsp.dmap.succ.get(one[0], ()))))

    banner('E7a - El coste de calculate() depende de cuanto cambia?')
    print('Si fuera incremental, el coste deberia variar con el tamano del')
    print('conjunto afectado (ver E7d); si no, deberia ser similar en los')
    print('cuatro casos. Los tiempos solo son compatibles o no con E7d.')
    print('')

    stats = {}
    stats['calculate() sin inputs'] = timeit(lambda: m.calculate())
    report('calculate() sin inputs', stats['calculate() sin inputs'])

    stats['calculate() repetido'] = timeit(lambda: m.calculate())
    report('calculate() repetido (identico)', stats['calculate() repetido'])

    inputs_1 = {k: 1 for k in one}
    stats['calculate(inputs=1 celda)'] = timeit(
        lambda: m.calculate(inputs=dict(inputs_1)))
    report('calculate(inputs = 1 celda)', stats['calculate(inputs=1 celda)'])

    inputs_20 = {k: 1 for k in many}
    stats['calculate(inputs=20 celdas)'] = timeit(
        lambda: m.calculate(inputs=dict(inputs_20)))
    report('calculate(inputs = 20 celdas)',
           stats['calculate(inputs=20 celdas)'])

    meds = [stats[k]['median'] for k in (
        'calculate() sin inputs', 'calculate() repetido',
        'calculate(inputs=1 celda)', 'calculate(inputs=20 celdas)')]
    spread = (max(meds) - min(meds)) / max(meds)
    print('')
    print('  Dispersion entre las cuatro medianas: %.1f%%' % (spread * 100))
    print('  Lectura: tiempos similares en los cuatro casos; compatible con')
    print('  los conteos de E7d, no concluyente por si solo.')

    banner('E7b - Lo que si reduce el coste: pedir menos salidas')
    computed = [k for k in dsp.data_nodes
                if is_cell_ref(k) and dsp.dmap.pred.get(k)]
    target = sorted(computed)[0]
    # La celda mas profunda del grafo, para no medir solo un caso favorable:
    # dist es la distancia desde START que asigna el algoritmo de schedula.
    sol = m.calculate()
    deep = max((k for k in computed if k in sol.dist),
               key=lambda k: sol.dist[k])
    print('  celda superficial : %s' % I.short(target))
    print('  celda mas profunda: %s (dist %s)' % (I.short(deep),
                                                  sol.dist[deep]))
    print('')
    stats['calculate(outputs=1 celda)'] = timeit(
        lambda: m.calculate(outputs=[target]))
    report('calculate(outputs = celda superficial)',
           stats['calculate(outputs=1 celda)'])
    stats['calculate(outputs=celda profunda)'] = timeit(
        lambda: m.calculate(outputs=[deep]))
    report('calculate(outputs = celda profunda)',
           stats['calculate(outputs=celda profunda)'])
    full = stats['calculate() sin inputs']['median']
    sub = stats['calculate(outputs=1 celda)']['median']
    deep_med = stats['calculate(outputs=celda profunda)']['median']
    print('  En estos dos casos, frente al calculo completo: %.0fx (superficial)'
          ' / %.0fx (profunda)' % (full / sub, full / deep_med))
    print('  (depende de cuantas celdas necesite la salida pedida; no es una')
    print('   cifra general)')

    banner('E7c - compile(): construir el pipe una vez, invocarlo muchas')
    # Comparacion homologa: misma firma (mismas entradas, misma salida) por
    # las dos vias. Se usa la celda profunda, no la superficial, que es
    # practicamente gratis y falsearia el contraste.
    stats['calculate(inputs+outputs, celda profunda)'] = timeit(
        lambda: m.calculate(inputs=dict(inputs_1), outputs=[deep]))
    report('calculate(inputs=1, outputs=profunda)',
           stats['calculate(inputs+outputs, celda profunda)'])
    t0 = time.perf_counter()
    func = m.compile(inputs=one, outputs=[deep])
    t_compile = time.perf_counter() - t0
    print('  %-42s %7.3fs' % ('compile() (coste unico)', t_compile))
    call = timeit(lambda: func(1), reps=5)
    report('invocacion del pipe compilado', call)
    base = stats['calculate(inputs+outputs, celda profunda)']['median']
    print('  En este caso, misma firma por las dos vias: pipe %.0fx mas rapido'
          % (base / call['median']))
    print('  Amortizacion del compile() en este caso: ~%.0f invocaciones'
          % (t_compile / max(base - call['median'], 1e-9)))
    print('')
    print('  El ahorro combina dos efectos que aqui no se separan: no replanificar')
    print('  en cada llamada y no reejecutar la parte que compile() congelo al')
    print('  construir el pipe (ver E6).')

    # ------------------------------------------------------------------ E7d
    # Al final, porque instrumentar anade coste y no debe contaminar tiempos.
    banner('E7d - Conteos en el libro grande frente al conjunto afectado')
    fn_nodes = set(dsp.function_nodes)

    def descendants(starts):
        seen, stack = set(), list(starts)
        while stack:
            n = stack.pop()
            for nxt in dsp.dmap.succ.get(n, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    rec = I.Recorder(dsp)
    counts = {}
    for name, inp, starts in (('sin inputs', None, ()),
                              ('1 celda', inputs_1, one),
                              ('20 celdas', inputs_20, many)):
        affected = descendants(starts) & fn_nodes
        rec.reset()
        m.calculate(inputs=dict(inp) if inp else None)
        ran = rec.executed
        counts[name] = {'affected': len(affected), 'invoked': rec.total,
                        'invoked_unaffected': len(ran - affected)
                        if starts else None}
        print('  %-11s afectados=%-6s invocados=%-6d no afectados invocados=%s'
              % (name, len(affected) if starts else '-', rec.total,
                 counts[name]['invoked_unaffected']
                 if starts else '-'))
    rec.restore()
    print('')
    print('  Con un motor incremental, "no afectados invocados" seria 0.')
    out['counts'] = counts

    out['timings'] = stats
    out['compile'] = {'build': t_compile, 'call': call}
    out['spread_ratio'] = spread
    out['input_cells_used'] = {'one': one, 'twenty': many}
    out['output_cell_used'] = {'shallow': target, 'deep': deep,
                               'deep_dist': repr(sol.dist[deep])}

    os.makedirs(RESULTS, exist_ok=True)
    with open(osp.join(RESULTS, 'timing.json'), 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print('')
    print('Resultados -> research/recalc/results/latest/timing.json')
    return out


if __name__ == '__main__':
    main()
