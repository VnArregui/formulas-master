"""E1-E5: que ejecuta `formulas` en cada `calculate()`.

Cada experimento declara sus predicciones ANTES de ejecutar, para que el
diseno pueda refutar la hipotesis y no solo confirmarla.
"""
import json
import os
import os.path as osp
import sys

sys.path.insert(0, osp.dirname(osp.abspath(__file__)))

import instrument as I  # noqa: E402

RESULTS = osp.join(osp.dirname(osp.abspath(__file__)), 'results', 'latest')


def descendants(dsp, node_id):
    """Nodos alcanzables hacia adelante desde node_id en el grafo dmap."""
    seen, stack = set(), [node_id]
    while stack:
        n = stack.pop()
        for s in dsp.dmap.succ.get(n, ()):
            if s not in seen:
                seen.add(s)
                stack.append(s)
    return seen


def ancestors(dsp, node_id):
    """Nodos de los que node_id depende, hacia atras en dmap."""
    seen, stack = set(), [node_id]
    while stack:
        n = stack.pop()
        for p in dsp.dmap.pred.get(n, ()):
            if p not in seen:
                seen.add(p)
                stack.append(p)
    return seen


def rec_original(node):
    """La funcion real de un nodo, aunque este envuelta por Counted."""
    fn = node.get('function')
    return getattr(fn, '__wrapped__', fn) if isinstance(fn, I.Counted) else fn


def inner_recorders(dsp):
    """Instrumenta el DispatchPipe interno de cada celda de formula.

    Cada CellWrapper envuelve un DispatchPipe con sus propios nodos (los
    operadores y funciones de la formula). Contarlos distingue 'se invoco el
    nodo de la celda' de 'se volvio a calcular su contenido'.
    """
    from formulas.cell import CellWrapper
    recs = []
    for node in dsp.function_nodes.values():
        fn = rec_original(node)
        if isinstance(fn, CellWrapper) and hasattr(fn.func, 'dsp'):
            recs.append(I.Recorder(fn.func.dsp))
    return recs


def build_model():
    os.chdir(I.TEST_FILES)
    from formulas.excel import ExcelModel
    return ExcelModel().loads('excel.xlsx').finish()


def banner(title):
    print('\n' + '=' * 72)
    print(title)
    print('=' * 72)


def main():
    """Deshace el parche global de CellWrapper aunque algo falle, para no
    contaminar los experimentos que se ejecuten despues (p.ej. los tiempos)."""
    try:
        return _main()
    finally:
        I.unpatch_cell_wrapper()


def _main():
    out = {}
    m = build_model()
    dsp = m.dsp
    rec = I.Recorder(dsp)
    cells = I.patch_cell_wrapper()

    def R(c):
        return I.ref(dsp, c)

    lab = I.labeler(dsp)

    fn_nodes = set(dsp.function_nodes)

    out['model'] = {
        'data_nodes': len(dsp.data_nodes),
        'function_nodes': len(fn_nodes),
        'instrumented': len(rec.wrapped),
    }

    # ------------------------------------------------------------- grafo
    # La clasificacion afectado / no afectado se deriva del grafo REGISTRADO
    # (dsp.dmap), no de una lectura manual de las formulas.
    banner('Grafo de dependencias registrado (nodo funcion: entradas -> salida)')
    graph = {}
    for k in sorted(fn_nodes, key=lab):
        node = dsp.function_nodes[k]
        ins = [I.short(i) for i in node.get('inputs') or ()]
        outs = [I.short(o) for o in node.get('outputs') or ()]
        graph[lab(k)] = {'inputs': ins, 'outputs': outs,
                         'function': type(rec_original(node)).__name__}
        print('  %-8s %-18s %s -> %s' % (lab(k), graph[lab(k)]['function'],
                                        ', '.join(ins), ', '.join(outs)))
    out['graph'] = graph

    # ---------------------------------------------------------------- E1
    banner('E1 - Hay cache de resultados entre llamadas?')
    print('Prediccion CON cache : la 2a llamada identica ejecuta 0 nodos.')
    print('Prediccion SIN cache : la 2a llamada ejecuta lo mismo que la 1a.')
    inner = inner_recorders(dsp)

    def inner_total():
        return sum(r.total for r in inner)

    def reset_all():
        rec.reset()
        cells.clear()
        for r in inner:
            r.reset()

    reset_all()
    m.calculate()
    n1, s1_nodes, c1, i1 = (rec.total, rec.executed, sum(cells.values()),
                            inner_total())
    reset_all()
    m.calculate()
    n2, s2_nodes, c2, i2 = (rec.total, rec.executed, sum(cells.values()),
                            inner_total())
    print('')
    print('                              R1     R2')
    print('Nodos del modelo invocados  : %-6d %d' % (n1, n2))
    print('Contador CellWrapper        : %-6d %d' % (c1, c2))
    inner_nodes = sum(len(r.wrapped) for r in inner)
    print('Nodos internos de las celdas: %-6d %d   (de %d operadores/funciones'
          ' en %d celdas)' % (i1, i2, inner_nodes, len(inner)))
    print('Mismos nodos invocados      : %s' % (s1_nodes == s2_nodes))
    print('')
    print('El contador interno mide los operadores y funciones DENTRO de cada')
    print('formula: si la invocacion de la celda devolviera un valor guardado,')
    print('en R2 seria 0. No distingue memoizacion por argumento de funciones')
    print('auxiliares concretas (p.ej. xdate), que no interviene en este libro.')
    veredicto = ('SIN reutilizacion de resultados entre llamadas'
                 if (n2, c2, i2) == (n1, c1, i1) else
                 'inesperado: R1=%s R2=%s' % ((n1, c1, i1), (n2, c2, i2)))
    print('VEREDICTO -> %s' % veredicto)
    out['E1'] = {'r1': n1, 'r2': n2, 'cellwrapper_r1': c1, 'cellwrapper_r2': c2,
                 'inner_r1': i1, 'inner_r2': i2,
                 'inner_cells_instrumented': len(inner),
                 'inner_nodes_total': inner_nodes,
                 'same_nodes': s1_nodes == s2_nodes,
                 'cache_between_calls': n2 == 0 or i2 == 0}

    # ---------------------------------------------------------------- E2
    banner('E2 - Recalcula solo lo afectado por el input que cambia?')
    out['E2'] = {}
    for cell, value in (('D2', 9), ('A2', 3)):
        node = R(cell)
        affected = descendants(dsp, node) & fn_nodes
        unaffected = fn_nodes - affected
        print('')
        print('--- input %s = %s ---' % (cell, value))
        print('Afectados (descendientes de %s en el grafo registrado): %d'
              % (cell, len(affected)))
        print('   %s' % sorted(map(lab, affected)))
        print('No afectados: %d' % len(unaffected))
        print('   %s' % sorted(map(lab, unaffected)))
        print('Prediccion INCREMENTAL   : solo se invocan afectados')
        print('Prediccion NO INCREMENTAL: se invocan tambien no afectados')
        rec.reset()
        m.calculate(inputs={node: value})
        ran = rec.executed
        ran_unaffected = ran & unaffected
        ran_affected = ran & affected
        print('')
        print('Invocaciones reales           : %d' % rec.total)
        print('  afectados invocados         : %d  %s'
              % (len(ran_affected), sorted(map(lab, ran_affected))))
        print('  NO afectados invocados      : %d  %s'
              % (len(ran_unaffected), sorted(map(lab, ran_unaffected))))
        verdict = 'INCREMENTAL' if not ran_unaffected else 'NO INCREMENTAL'
        print('VEREDICTO -> %s' % verdict)
        out['E2'][cell] = {
            'affected_count': len(affected),
            'affected': sorted(map(lab, affected)),
            'unaffected': sorted(map(lab, unaffected)),
            'executed_total': rec.total,
            'executed_affected': sorted(map(lab, ran_affected)),
            'executed_unaffected': sorted(map(lab, ran_unaffected)),
            'verdict': verdict,
        }

    # ---------------------------------------------------------------- E3
    banner('E3 - Identidad y estado de la Solution')
    sol_a = m.calculate()
    len_a = len(sol_a)
    sol_b = m.calculate()
    print('sol_a is sol_b                 : %s' % (sol_a is sol_b))
    print('dsp.solution is sol_b          : %s' % (dsp.solution is sol_b))
    print('sol_a sigue poblada tras sol_b : %s (%d claves, antes %d)'
          % (len(sol_a) == len_a, len(sol_a), len_a))
    print('')
    print('Cada calculate() abre una Solution nueva y machaca dsp.solution;')
    print('la anterior queda huerfana, no se reutiliza.')
    out['E3'] = {'same_object': sol_a is sol_b,
                 'dsp_solution_is_last': dsp.solution is sol_b,
                 'previous_untouched': len(sol_a) == len_a}

    # ---------------------------------------------------------------- E4
    banner('E4 - Como decide entonces que ejecutar?')
    rec.reset()
    sol_nc = m.calculate(no_call=True)
    no_call_exec = rec.total
    reachable = {k for k in sol_nc if k in dsp.data_nodes}
    rec.reset()
    sol_full = m.calculate()
    executed = rec.executed
    computed = {k for k in sol_full if k in dsp.data_nodes}
    print('calculate(no_call=True) ejecuta : %d nodos' % no_call_exec)
    print('Nodos de datos alcanzables      : %d' % len(reachable))
    print('Nodos de datos calculados       : %d' % len(computed))
    print('Alcanzables == calculados       : %s' % (reachable == computed))
    print('Nodos funcion ejecutados en R1  : %d' % len(executed))

    node_a2 = R('A2')
    dv = dsp.default_values.get(node_a2, {})
    sol_in = m.calculate(inputs={node_a2: 3})
    print('')
    print('initial_dist del default de A2  : %r' % (dv.get('initial_dist'),))
    print('dist de A2 sin input            : %r' % (sol_full.dist.get(node_a2),))
    print('dist de A2 con input            : %r' % (sol_in.dist.get(node_a2),))
    print('')
    print('El input gana al default POR DISTANCIA, no por marcar celdas sucias.')
    out['E4'] = {
        'no_call_executions': no_call_exec,
        'reachable_data_nodes': len(reachable),
        'computed_data_nodes': len(computed),
        'reachable_equals_computed': reachable == computed,
        'executed_function_nodes': len(executed),
        'A2_default_initial_dist': repr(dv.get('initial_dist')),
        'A2_dist_without_input': repr(sol_full.dist.get(node_a2)),
        'A2_dist_with_input': repr(sol_in.dist.get(node_a2)),
    }

    # ---------------------------------------------------------------- E5
    banner('E5 - Poda por outputs: reduce trabajo, pero no es incremental')
    c4 = R('C4')
    needed = ancestors(dsp, c4) & fn_nodes
    not_needed = fn_nodes - needed
    print('Cierre real de dependencias de C4: %d nodos funcion de %d.'
          % (len(needed), len(fn_nodes)))
    print('No hacen falta para C4: %s' % sorted(map(lab, not_needed)))
    print('Prediccion: se ejecutan los necesarios, se omiten los demas.')
    rec.reset()
    m.calculate(outputs=[c4])
    first, ran1 = rec.total, rec.executed
    rec.reset()
    m.calculate(outputs=[c4])
    second = rec.total
    print('')
    print('calculate(outputs=[C4]) 1a vez  : %d ejecuciones' % first)
    print('calculate(outputs=[C4]) 2a vez  : %d ejecuciones' % second)
    print('calculate() completo            : %d ejecuciones' % n1)
    print('Nodos ejecutados: %s' % sorted(map(lab, ran1)))
    print('')
    print('La reduccion la dirige la SALIDA pedida, no el input que cambia,')
    print('y repetir la misma llamada no ahorra nada (%d == %d).'
          % (first, second))
    skipped = s1_nodes - ran1
    print('Omitidos respecto al calculo completo: %s' % sorted(map(lab, skipped)))
    out['E5'] = {'first': first, 'second': second, 'full': n1,
                 'needed_for_C4': len(needed),
                 'nodes': sorted(map(str, ran1)),
                 'skipped': sorted(map(str, skipped))}

    # --------------------------------------------------------------- check
    banner('Control: la instrumentacion no altera los resultados')
    sol = m.calculate()
    names = ('B2', 'B3', 'B4', 'C2', 'C3', 'C4', 'D3', 'D4')
    got = {c: I.unwrap(sol[R(c)]) for c in names}
    expected = {'B2': 8, 'B3': 2, 'B4': 8, 'C2': 5, 'C3': 12, 'C4': 35,
                'D3': 2, 'D4': 3}
    ok = all(abs(float(got[k]) - v) < 1e-4 for k, v in expected.items())
    for k in names:
        print('  %-3s esperado %-5s obtenido %s' % (k, expected[k], got[k]))
    print('')
    print('Coinciden con los valores del libro: %s' % ok)
    out['control'] = {'values': {k: float(v) for k, v in got.items()},
                      'matches_workbook': ok}

    os.makedirs(RESULTS, exist_ok=True)
    with open(osp.join(RESULTS, 'counts.json'), 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print('')
    print('Resultados -> research/recalc/results/latest/counts.json')
    return out


if __name__ == '__main__':
    main()
