"""Instrumentacion para medir que ejecuta `formulas` en cada `calculate()`.

Dos contadores independientes y redundantes:

* `instrument(dsp)` envuelve `node['function']` de cada nodo funcion del
  Dispatcher. Cubre CellWrapper, sh.bypass, RangesAssembler e
  InvRangesAssembler de forma uniforme.
* `patch_cell_wrapper()` parchea `formulas.cell.CellWrapper.__call__`, unico
  punto de entrada de toda evaluacion de formula (formulas/cell.py:32).

Si ambos coinciden sobre las celdas de formula, la instrumentacion no sesga.
"""
import collections
import functools
import os.path as osp
import re

import schedula as sh

HERE = osp.dirname(osp.abspath(__file__))
TEST_FILES = osp.normpath(osp.join(HERE, '..', '..', 'test', 'test_files'))


class Counted:
    """Delega en la funcion original y cuenta cada invocacion."""

    def __init__(self, fn, key, counter):
        object.__setattr__(self, '__wrapped__', fn)
        object.__setattr__(self, '_key', key)
        object.__setattr__(self, '_counter', counter)
        try:
            functools.update_wrapper(self, fn)
        except Exception:
            pass

    def __call__(self, *args, **kwargs):
        self._counter[self._key] += 1
        return self.__wrapped__(*args, **kwargs)

    def __getattr__(self, name):
        # schedula y formulas introspeccionan la funcion (name, inputs, dsp...)
        return getattr(object.__getattribute__(self, '__wrapped__'), name)

    def __repr__(self):
        return 'Counted(%r)' % (self.__wrapped__,)


class Recorder:
    """Contador de ejecuciones por nodo funcion de un Dispatcher."""

    def __init__(self, dsp):
        self.dsp = dsp
        self.counter = collections.Counter()
        self.wrapped = []
        for node_id, node in dsp.function_nodes.items():
            fn = node.get('function')
            if fn is None or isinstance(fn, Counted):
                continue
            node['function'] = Counted(fn, node_id, self.counter)
            self.wrapped.append(node_id)

    def reset(self):
        self.counter.clear()

    def snapshot(self):
        return dict(self.counter)

    @property
    def total(self):
        return sum(self.counter.values())

    @property
    def executed(self):
        return set(self.counter)

    def restore(self):
        for node_id in self.wrapped:
            node = self.dsp.function_nodes[node_id]
            if isinstance(node['function'], Counted):
                node['function'] = node['function'].__wrapped__
        self.wrapped = []


_CELL_COUNTER = collections.Counter()
_ORIGINAL_CELL_CALL = None


def patch_cell_wrapper():
    """Contador independiente sobre CellWrapper.__call__ (formulas/cell.py:32)."""
    global _ORIGINAL_CELL_CALL
    from formulas.cell import CellWrapper
    if _ORIGINAL_CELL_CALL is not None:
        return _CELL_COUNTER
    _ORIGINAL_CELL_CALL = CellWrapper.__call__

    @functools.wraps(_ORIGINAL_CELL_CALL)
    def counting_call(self, *args, **kwargs):
        _CELL_COUNTER[getattr(self, '__name__', repr(self))] += 1
        return _ORIGINAL_CELL_CALL(self, *args, **kwargs)

    CellWrapper.__call__ = counting_call
    return _CELL_COUNTER


def unpatch_cell_wrapper():
    global _ORIGINAL_CELL_CALL
    if _ORIGINAL_CELL_CALL is None:
        return
    from formulas.cell import CellWrapper
    CellWrapper.__call__ = _ORIGINAL_CELL_CALL
    _ORIGINAL_CELL_CALL = None


def cell_counter():
    return _CELL_COUNTER


def ref(dsp, cell):
    """Resuelve 'A2' al id de nodo real, que incluye fichero y hoja.

    Los ids se normalizan a mayusculas e incorporan el directorio
    (formulas/tokens/operand.py:233), asi que no se pueden fijar a mano.
    """
    suffix = '!%s' % cell.upper()
    hits = [k for k in dsp.data_nodes
            if isinstance(k, str) and k.upper().endswith(suffix)]
    if len(hits) != 1:
        raise KeyError('ref(%r) -> %d coincidencias: %r' % (cell, len(hits),
                                                            sorted(hits)[:10]))
    return hits[0]


def unwrap(value):
    """Desenvuelve un Ranges a su escalar; deja pasar el resto."""
    from formulas.ranges import Ranges
    if isinstance(value, Ranges):
        flat = value.value.ravel()
        return flat[0] if flat.size == 1 else value.value.tolist()
    return value


def solution_values(solution):
    """dict node_id -> escalar, filtrando los sh.Token (p.ej. sh.SELF)."""
    return {k: unwrap(v) for k, v in solution.items()
            if not isinstance(k, sh.Token)}


_RE_QUAL = re.compile(r"'\[[^\]]*\][^']*'!")


def short(node_id):
    """Quita el prefijo '[fichero]HOJA'! de cualquier id, para leerlo mejor."""
    return _RE_QUAL.sub('', str(node_id))


def labeler(dsp):
    """Devuelve f(node_id) -> etiqueta legible.

    A un nodo funcion lo nombra por la celda que produce (su unico output),
    que es mucho mas claro que su expresion completa.
    """
    fn = dsp.function_nodes

    def label(node_id):
        node = fn.get(node_id)
        if node:
            outs = list(node.get('outputs') or ())
            if len(outs) == 1:
                return short(outs[0])
            if outs:
                return '+'.join(map(short, outs))
        return short(node_id)

    return label
