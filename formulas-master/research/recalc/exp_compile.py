"""E6: que reutiliza `compile()` -> schedula.DispatchPipe.

`ExcelModel.compile()` (formulas/excel/__init__.py:726-754) SI reutiliza
valores: ejecuta una vez el submodelo sin los inputs declarados
(`res = dsp()`), corta el grafo en lo ya calculado (`blockers=res`) y congela
esos resultados como valores por defecto (`set_default_value`). La parte que
no depende de los inputs se calcula una sola vez, al compilar.

La pregunta de este experimento es la otra: ENTRE invocaciones del pipe, ¿se
reutiliza algo segun que argumento haya cambiado? Si no, el numero de nodos
ejecutados por invocacion es constante aunque solo cambie un argumento.
"""
import json
import os
import os.path as osp
import sys

sys.path.insert(0, osp.dirname(osp.abspath(__file__)))

import instrument as I  # noqa: E402

RESULTS = osp.join(osp.dirname(osp.abspath(__file__)), 'results', 'latest')


def banner(title):
    print('\n' + '=' * 72)
    print(title)
    print('=' * 72)


def main():
    out = {}
    os.chdir(I.TEST_FILES)
    from formulas.excel import ExcelModel

    m = ExcelModel().loads('excel.xlsx').finish()
    dsp = m.dsp

    def R(c):
        return I.ref(dsp, c)

    banner('E6 - compile() / DispatchPipe: que se reutiliza y cuando')

    inputs = [R('A2'), R('B3')]
    outputs = [R('C2'), R('C4')]
    # compile() hace un dispatch interno para congelar valores: instrumentar
    # DESPUES, para no contaminar el conteo ni romper los isinstance internos.
    func = m.compile(inputs=inputs, outputs=outputs)
    fdsp = func.dsp
    lab = I.labeler(fdsp)
    print('Tipo devuelto por compile(): %s.%s'
          % (type(func).__module__, type(func).__name__))
    print('Firma: inputs=(A2, B3)  outputs=(C2, C4)')

    # --- 1. Lo que compile() congela UNA VEZ --------------------------------
    file_defaults = set(dsp.default_values)
    frozen, from_file = {}, {}
    for k, dv in fdsp.default_values.items():
        if not isinstance(k, str):
            continue
        val = I.unwrap(dv.get('value'))
        (from_file if k in file_defaults else frozen)[I.short(k)] = val
    print('')
    print('1) Valores por defecto del pipe compilado')
    print('   copiados del fichero         : %s' % from_file)
    print('   CALCULADOS y congelados al   : %s' % frozen)
    print('   compilar (no estan en el fichero)')
    print('   Nodos funcion que quedan     : %s'
          % sorted(map(lab, fdsp.function_nodes)))
    print('   (compile() SI reutiliza valores: los de la parte que no depende')
    print('    de los inputs declarados, calculados una vez y fijados.)')

    # --- 2. Entre invocaciones ---------------------------------------------
    rec = I.Recorder(fdsp)
    print('')
    print('2) Entre invocaciones del pipe')
    print('   Prediccion SI se reutilizara segun el argumento que cambia:')
    print('     repetir argumentos ejecuta 0 nodos; cambiar solo B3 no')
    print('     reejecuta B2 (B2 = A2 + A3, no depende de B3).')
    print('   Prediccion NO: todas las invocaciones ejecutan los mismos nodos.')
    print('')

    runs = []
    calls = [(3, 1), (3, 1), (3, 1), (3, 2), (5, 2), (7, 4), (3, 1)]
    for i, args in enumerate(calls, 1):
        rec.reset()
        res = func(*args)
        vals = [I.unwrap(v) for v in res]
        ran = sorted(map(lab, rec.executed))
        runs.append({'args': list(args), 'executions': rec.total,
                     'nodes': ran, 'C2': float(vals[0]), 'C4': float(vals[1])})
        print('   llamada %d  args=%-7s ejecuciones=%d %-16s C2=%-7.4g C4=%.6g'
              % (i, args, rec.total, ran, vals[0], vals[1]))

    only_b3 = runs[3]
    b2_rerun = 'B2' in only_b3['nodes']
    counts = {r['executions'] for r in runs}
    constant = len(counts) == 1
    print('')
    print('   Llamada 4 cambia solo B3 y B2 se reejecuta: %s' % b2_rerun)
    print('   VEREDICTO -> %s' % (
        'entre invocaciones no se reutiliza nada: el pipe se recorre entero '
        'cambie lo que cambie' if constant and b2_rerun else
        'revisar: conteos %s, B2 reejecutado=%s' % (sorted(counts), b2_rerun)))

    # Identidad de la solucion: DispatchPipe la sobreescribe in-place.
    func(3, 1)
    sol_1 = func.solution
    func(5, 2)
    sol_2 = func.solution
    print('')
    print('   func.solution es el mismo objeto entre llamadas: %s' % (
        sol_1 is sol_2))
    print('   (se reescribe in-place; de ahi la nota de thread-safety del')
    print('    docstring de DispatchPipe)')

    # --- 3. Contraste con calculate() con la misma firma --------------------
    # OJO: shrink_dsp comparte los dicts de atributos de nodo entre el modelo
    # y el pipe (B2, C2, C4 son el MISMO objeto en ambos), asi que el
    # Recorder del pipe tambien envolvio esos nodos en `m`. Medir aqui sobre
    # `m` los contaria en el contador equivocado. Se usa un modelo aparte.
    shared = sorted(lab(k) for k, n in fdsp.function_nodes.items()
                    if n is dsp.function_nodes.get(k))
    m2 = ExcelModel().loads('excel.xlsx').finish()
    rec_full = I.Recorder(m2.dsp)
    lab_full = I.labeler(m2.dsp)
    rec_full.reset()
    m2.calculate(inputs={I.ref(m2.dsp, 'A2'): 3, I.ref(m2.dsp, 'B3'): 1},
                 outputs=[I.ref(m2.dsp, 'C2'), I.ref(m2.dsp, 'C4')])
    full_nodes = sorted(map(lab_full, rec_full.executed))
    extra = sorted(set(full_nodes) - set(runs[0]['nodes']))
    print('')
    print('3) Misma firma via calculate(inputs, outputs)')
    print('   calculate ejecuta : %d  %s' % (rec_full.total, full_nodes))
    print('   el pipe ejecuta   : %d  %s' % (runs[0]['executions'],
                                             runs[0]['nodes']))
    print('   diferencia        : %s' % extra)
    print('   (lo que compile() congelo -D4- o dejo fuera al podar el grafo)')
    print('   nodos compartidos modelo/pipe por shrink_dsp: %s' % shared)
    print('')
    print('   El pipe hace MENOS trabajo por dos motivos: no replanifica en cada')
    print('   llamada (el orden ya esta en func.pipe) y no reejecuta la parte')
    print('   congelada. Este experimento no separa cuanto aporta cada uno.')

    out['E6'] = {
        'type': '%s.%s' % (type(func).__module__, type(func).__name__),
        'pipe_len': len(func.pipe),
        'pipe_function_nodes': sorted(map(lab, fdsp.function_nodes)),
        'defaults_from_file': {k: repr(v) for k, v in from_file.items()},
        'frozen_at_compile': {k: repr(v) for k, v in frozen.items()},
        'runs': runs,
        'constant_executions': constant,
        'b2_rerun_when_only_b3_changes': b2_rerun,
        'solution_reused_in_place': sol_1 is sol_2,
        'calculate_executions': rec_full.total,
        'calculate_nodes': full_nodes,
        'extra_in_calculate': extra,
        'nodes_shared_with_model': shared,
    }

    os.makedirs(RESULTS, exist_ok=True)
    with open(osp.join(RESULTS, 'compile.json'), 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print('')
    print('Resultados -> research/recalc/results/latest/compile.json')
    return out


if __name__ == '__main__':
    main()
