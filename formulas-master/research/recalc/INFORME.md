# `formulas`: cómo recalcula y hasta dónde llega

Informe de análisis de la biblioteca [`formulas`](https://github.com/vinci1it2000/formulas),
que parsea y compila fórmulas de Excel a código Python.

**Parte I** responde la pregunta concreta: *si cambia una celda de entrada, ¿cómo determina
`formulas` qué debe ejecutar?* **Parte II** documenta el funcionamiento general y el alcance
real de la biblioteca.

Todo lo que se afirma aquí está medido contra el código y el entorno identificados abajo, no
copiado de la documentación. Los scripts que producen cada cifra están en esta carpeta y las
salidas crudas de la corrida citada en `results/reference/`.

## Código y entorno medidos

**Código.** El árbol de `formulas` es una instantánea sin metadatos de git (carpeta
`formulas-master`), así que **no hay commit que registrar**. Se identifica así:

| | |
|---|---|
| Versión declarada | `1.3.4`, `__updated__ = "2026-03-11 18:35:00"` (`formulas/_version.py`) |
| Instalación | editable (`pip install -e .`): el código ejecutado es el de este árbol |
| Huella SHA-256 del árbol | `94acf93e3137aa68408bf5d0b2238e34eda959e97c50eb2a58d351bbb50a9c9d` |
| Ficheros con huella | 49 (paquete `formulas/`, `setup.py`, `setup.cfg`, `requirements/`, test y libros usados) |
| Modificaciones locales | ninguna posterior a la extracción (por fecha de modificación) |

El manifiesto fichero a fichero está en `results/reference/source_manifest.txt`. La comprobación de
modificaciones es por fecha: detecta cambios hechos **después** de extraer la instantánea, no
diferencias que la instantánea ya trajera respecto a la release 1.3.4 publicada. Esa
equivalencia no está comprobada.

**Entorno** (lista completa en `results/reference/pip_freeze.txt`):

| Componente | Versión |
|---|---|
| Python | 3.13.5 (Windows 11) |
| `schedula` | **1.6.15** |
| `numpy` | 2.5.3 |
| `numpy-financial` | 1.1.0 |
| `openpyxl` | 3.1.5 |
| `scipy` | 1.18.1 |

Los números de línea de schedula se refieren a la 1.6.15, leída en el `site-packages` del venv.

---

# Parte I — Recálculo

## Resumen

**`formulas` no hace recálculo incremental.** Cada `calculate()` invoca todos los nodos
alcanzables del grafo, dependan o no del input que cambió, y no reutiliza resultados de llamadas
anteriores en función de qué cambió.

Lo que sí se reutiliza entre llamadas es el **modelo**: el grafo de dependencias, las celdas
parseadas y los libros cargados. Construirlo es lo caro (~30 s en el libro grande de pruebas) y
se paga una vez.

Hay dos formas de reutilización de valores que existen y que **no** son recálculo incremental,
porque ninguna depende de qué celda cambió:

1. **`compile()` congela valores**: al construir el pipe calcula una vez la parte del submodelo
   que no depende de los inputs declarados y la fija como valor por defecto (E6).
2. **Memoización por argumento** en algunas funciones auxiliares (`xdate`, `_text2datetime`):
   si se llaman con los mismos argumentos, devuelven el resultado guardado. El nodo de la celda
   se sigue invocando (§1.3).

| Afirmación | Veredicto | Evidencia principal |
|---|---|---|
| Reutiliza el modelo entre llamadas | **Cierto** | `loads`+`finish` no se repiten |
| Reutiliza resultados entre llamadas a `calculate()` | **Falso** en lo medido | E1 (nodos del modelo y operadores internos), E7d |
| Invoca sólo las celdas afectadas por el cambio | **Falso** | E2, E7d |
| Puede calcular menos que el modelo completo | **Cierto, por otra vía** | E5: lo dirige `outputs=`, no el input que cambió |
| `compile()` reutiliza valores | **Cierto, pero fijos** | E6: congela la parte independiente de los inputs al compilar |
| El pipe compilado reutiliza algo según el argumento que cambie | **Falso** | E6: mismos 3 nodos en todas las invocaciones |

## 1. Qué hace `calculate()`

### 1.1 El recorrido del código

`ExcelModel.calculate()` delega directamente en schedula
([`formulas/excel/__init__.py:169-170`](../../formulas/excel/__init__.py#L169)):

```python
def calculate(self, *args, **kwargs):
    return self.dsp.dispatch(*args, **kwargs)
```

`Dispatcher.dispatch()` (`schedula/dispatcher.py:1719-1736`) hace tres cosas:

```python
        if not no_call:
            if shrink:  # Pre shrink.
                dsp = self.shrink_dsp(inputs, outputs, inputs_dist, wildcard)
            elif outputs:
                dsp = self.get_sub_dsp_from_workflow(
                    outputs, self.dmap, reverse=True, blockers=inputs,
                    wildcard=wildcard
                )

        # Initialize.
        self.solution = sol = self.solution.__class__(
            dsp, inputs, outputs, wildcard, inputs_dist, no_call, rm_unused_nds,
            _wait_in, full_name=sol_name, verbose=verbose
        )

        # Dispatch.
        sol._run(stopper=stopper, executor=executor)
```

1. Si hay `outputs`, recorta el grafo **hacia atrás** desde ellos (base de E5).
2. Construye una `Solution` nueva. Al inicializarse llama a `_clean_set()`
   (`schedula/utils/sol.py:177-189`, desde `_init_workflow`, línea 194), que empieza con
   `self.clear()` y reinicia `workflow`, `dist`, `seen`, `_visited` y `_pipe`.
3. `_run()` (`sol.py:307-340`) es un Dijkstra sobre una cola de prioridad (`heappop(fringe)`):
   parte de los datos disponibles y avanza al nodo más cercano, ejecutando cada nodo función que
   alcanza.

En el núcleo de schedula (`dispatcher.py`, `utils/sol.py`, `utils/dsp.py`, `utils/base.py`) no hay
`lru_cache`, `cached_property` ni memoización; comprobado por grep. El `cached_ids` de
`sol.py:226-237` es un diccionario local de `_run` que memoiza una búsqueda de id dentro de una
sola ejecución.

### 1.2 Por qué esto no basta

Que se cree una `Solution` nueva sólo descarta que se reutilice **esa** `Solution`. La
reutilización podría esconderse en otros sitios: dentro de las funciones de los nodos, en cachés
de módulo, o en estado que el propio `dsp` conserve entre llamadas. Por eso la conclusión no
descansa en el recorrido del código, sino en **medir qué se ejecuta** (§2), a dos niveles:

- **nodos del modelo**: cada nodo función del grafo de `ExcelModel`;
- **nodos internos de cada celda**: cada celda es un `CellWrapper` que envuelve su propio
  `DispatchPipe`, con un nodo por operador o función de la fórmula. Contarlos distingue *"se
  invocó la celda"* de *"se volvió a calcular su contenido"*: si la invocación devolviera un
  valor guardado, el contador interno se quedaría en 0.

### 1.3 Reutilización que sí existe en `formulas`

Revisadas todas las `lru_cache` del paquete, la mayoría cachean configuración o parsing
(`_default_filter`, `_get_date_parser`, `_units`, `_range2parts`, `_index2col`, `get_functions`…).
Tres merecen mención:

| Caché | Qué guarda | Alcance |
|---|---|---|
| `xdate(year, month, day)` ([`functions/date.py:49`](../../formulas/functions/date.py#L49)) | el número de serie de una fecha (función `DATE`) | proceso, 128 entradas |
| `_text2datetime(date_text)` ([`functions/date.py:71`](../../formulas/functions/date.py#L71)) | el parseo de un texto a fecha | proceso, 128 entradas |
| `check(value)` ([`functions/__init__.py:360`](../../formulas/functions/__init__.py#L360)) | resultado de un criterio tipo `COUNTIF` | se crea en cada llamada: no sobrevive |

Las dos primeras **sí reutilizan valores entre llamadas a `calculate()`**, indexados por
argumento. No son recálculo incremental: no saben qué celda cambió, el nodo de la celda se sigue
invocando, y sólo ahorran el cálculo interno de esa función concreta cuando se repiten los
mismos argumentos. El libro de E1–E6 no usa `DATE` ni fechas en texto, así que no intervienen en
esos conteos.

## 2. Evidencia empírica

Modelo de prueba: `test/test_files/excel.xlsx`, hoja `DATA`. Valores y fórmulas:

```
A2 = 2   A3 = 6   A4 = 5   D2 = 1
B2 = A2+A3          C2 = B2/B3+D2       D3 = 1+D2
B3 = B2-A3          C3 = C2*A2+D3       D4 = 1+D3
B4 = MAX(A3:A4,B2)  C4 = B3^C2+D4
```

Las dependencias **no** forman cadenas independientes: `C2` usa `D2`, `C3` usa `A2` y `D3`, y
`C4` usa `B3`, `C2` y `D4`. La clasificación afectado/no afectado de cada experimento no sale de
leer estas fórmulas, sino del **grafo registrado** en `dsp.dmap`, que `exp_counts.py` vuelca así:

| Nodo | Tipo | Entradas → salida |
|---|---|---|
| `B2` | `CellWrapper` | `A2, A3 → B2` |
| `B3` | `CellWrapper` | `A3, B2 → B3` |
| `B4` | `CellWrapper` | `A3:A4, B2 → B4` |
| `C2` | `CellWrapper` | `B2, B3, D2 → C2` |
| `C3` | `CellWrapper` | `A2, C2, D3 → C3` |
| `C4` | `CellWrapper` | `B3, C2, D4 → C4` |
| `D3` | `CellWrapper` | `D2 → D3` |
| `D4` | `CellWrapper` | `D3 → D4` |
| `INPUT_A`, `INPUT_B`, `INPUT_C` | `CellWrapper` | `A2`, `A3`, `A4` → nombre definido |
| `A2`, `A3`, `A4` | bypass inverso | nombre definido → celda |
| `A3:A4` | `RangesAssembler` | `A3, A4 → A3:A4` |
| `A3+A4` | `InvRangesAssembler` | `A3:A4 → A3, A4` |

16 nodos función, 20 nodos de datos. Se instrumentan con dos contadores independientes: uno
envuelve `node['function']` en el dispatcher y otro parchea `CellWrapper.__call__`. Un tercero
cuenta los operadores internos de cada celda.

### E1 — Ninguna reutilización entre dos llamadas idénticas

| | R1 · `calculate()` | R2 · idéntica |
|---|---|---|
| Nodos del modelo invocados | 12 | **12** |
| Contador `CellWrapper` | 11 | **11** |
| Operadores internos ejecutados (de 11 en total) | 11 | **11** |

El conjunto de nodos invocados es el mismo en las dos llamadas. Si la segunda reutilizara
resultados de la primera, algún contador bajaría; ninguno lo hace, tampoco el interno.

### E2 — Se invocan también las celdas no afectadas

| Input | Afectados según el grafo | Invocados | Afectados invocados | **No afectados invocados** |
|---|---|---|---|---|
| `D2 = 9` | `C2`, `C3`, `C4`, `D3`, `D4` | 12 | 5 | **7**: `A3:A4`, `B2`, `B3`, `B4`, `INPUT_A`, `INPUT_B`, `INPUT_C` |
| `A2 = 3` | `A2`, `B2`, `B3`, `B4`, `C2`, `C3`, `C4`, `INPUT_A` | 12 | 7 | **5**: `A3:A4`, `D3`, `D4`, `INPUT_B`, `INPUT_C` |

`D2` alimenta directamente `D3` y `C2`, y a través de ellas `D4`, `C3` y `C4`. Al cambiarla se
vuelven a ejecutar `B2`, `B3` y `B4`, que no dependen de `D2` por ningún camino. Con un motor
incremental la última columna sería 0.

Esta tabla coincide con la de la primera corrida del experimento (los mismos 7 nodos, entonces
registrados con sus ids completos en `counts.json`); lo que se corrigió después fue la
descripción en prosa, que atribuía a `D2` sólo la cadena `D3 → D4 → C4`.

### E3 — La `Solution` anterior se descarta

```
sol_a is sol_b                 : False
dsp.solution is sol_b          : True
sol_a sigue poblada tras sol_b : True (20 claves)
```

Cada llamada devuelve un objeto nuevo y sobrescribe `dsp.solution`. Por sí solo esto no
demuestra que no haya reutilización (§1.2); sí explica una trampa práctica: `write()` y
`compare()` sin `solution=` explícito usan la última solución, o lanzan **otro** dispatch
completo ([`excel/__init__.py:181`](../../formulas/excel/__init__.py#L181)). Por eso el CLI
guarda el retorno y lo pasa a mano ([`formulas/cli.py:653-655`](../../formulas/cli.py#L653)).

### E4 — Cómo se decide qué ejecutar

```
calculate(no_call=True) ejecuta : 0 nodos
Nodos de datos alcanzables      : 20
Nodos de datos calculados       : 20
Alcanzables == calculados       : True
```

Lo que se ejecuta coincide con lo alcanzable desde los datos disponibles.

Un input gana al valor del fichero **por distancia**, no porque se marque nada como obsoleto.
`Cell.add` registra el valor del fichero con `initial_dist=sh.inf(1, 0)`
([`formulas/cell.py:157-160`](../../formulas/cell.py#L157)), a distancia infinita de orden 1; un
input pasado a `calculate` entra a distancia 0:

```
initial_dist del default de A2  : inf(inf=1, num=0)
dist de A2 sin input            : inf(inf=1, num=0.0)
dist de A2 con input            : 0.0
```

### E5 — Pedir menos salidas reduce trabajo, pero es otra cosa

| Llamada | Nodos invocados |
|---|---|
| `calculate()` completo | 12 |
| `calculate(outputs=[C4])` 1ª vez | **10** |
| `calculate(outputs=[C4])` 2ª vez | **10** |

Lo omitido es `{B4, C3}`, justo el complemento del cierre de dependencias de `C4` en el grafo
registrado (14 de 16 nodos función). La poda la dirige la **salida pedida**, no el **input que
cambió**, y repetir la llamada no ahorra nada.

### E6 — `compile()`: reutiliza valores al compilar, nada entre invocaciones

`ExcelModel.compile(inputs, outputs)`
([`excel/__init__.py:726-754`](../../formulas/excel/__init__.py#L726)) **sí reutiliza valores**:

```python
        res = dsp()                                  # calcula lo que no depende de los inputs

        dsp = dsp.get_sub_dsp_from_workflow(
            outputs, graph=dsp.dmap, reverse=True, blockers=res,
            wildcard=False
        )                                            # corta el grafo en lo ya calculado

        for k, v in res.items():
            if k in dsp.data_nodes and k not in dsp.default_values:
                dsp.set_default_value(k, v.value)    # y lo congela como valor por defecto
```

Con la firma `inputs=(A2, B3)`, `outputs=(C2, C4)`:

```
Valores copiados del fichero        : D2 = 1, A3 = 6
Valores CALCULADOS y congelados     : D4 = 3.0
Nodos función que quedan en el pipe : B2, C2, C4
```

`D4` se calculó una vez al compilar y se reutiliza en todas las invocaciones. Es reutilización
fija: no depende de qué argumento cambie en cada llamada.

**Entre invocaciones**, en cambio:

```
llamada 1  args=(3, 1)  ejecuciones=3  [B2, C2, C4]
llamada 2  args=(3, 1)  ejecuciones=3  [B2, C2, C4]
llamada 3  args=(3, 1)  ejecuciones=3  [B2, C2, C4]
llamada 4  args=(3, 2)  ejecuciones=3  [B2, C2, C4]   <- sólo cambia B3
llamada 5  args=(5, 2)  ejecuciones=3  [B2, C2, C4]
llamada 6  args=(7, 4)  ejecuciones=3  [B2, C2, C4]
llamada 7  args=(3, 1)  ejecuciones=3  [B2, C2, C4]
```

Repetir argumentos no ahorra nada, y en la llamada 4, que sólo cambia `B3`, se vuelve a ejecutar
`B2` (`= A2 + A3`, no depende de `B3`). `DispatchPipe.__call__` (heredado de `SubDispatchPipe`,
`schedula/utils/dsp.py:1521-1542`) recorre `self.pipe` entero en cada llamada, y reutiliza el
mismo objeto `Solution` in-place (`dsp.py:1623-1626`); de ahí la advertencia de thread-safety de
su docstring.

**Contraste con `calculate()` con la misma firma**, medido sobre un modelo aparte:

```
calculate(inputs, outputs) : 8 nodos  [A3:A4, B2, C2, C4, D3, D4, INPUT_B, INPUT_C]
pipe compilado             : 3 nodos  [B2, C2, C4]
```

La diferencia es lo que `compile()` congeló (`D4`) o dejó fuera al podar (`D3`, `A3:A4`,
`INPUT_B`, `INPUT_C`). El pipe hace menos trabajo por dos motivos que este experimento no separa:
no replanifica en cada llamada y no reejecuta la parte congelada.

### E7 — Tiempos y conteos sobre el libro grande

`test/test_files/test.xlsx` (405 KB) + `test_link.xlsx`: 32 121 nodos de datos, 27 680 de
función. Medianas de 3 repeticiones (5 para el pipe) en esta máquina, en la corrida completa de
`run_all.py`. Entre corridas los tiempos varían del orden de un 5 %; los conteos no varían.

**Construcción del modelo:** `loads` 29,2 s · `finish` 0,97 s.

#### E7d — Conteos (la evidencia que discrimina)

Celda de entrada: `'[test.xlsx]EXTRA'!AB1`, elegida por ser la hoja del grafo con más sucesores
directos (149). Para 20 celdas, las 20 primeras de esa misma ordenación.

| Input | Nodos función afectados según el grafo | Invocados | **No afectados invocados** |
|---|---|---|---|
| ninguno | — | 25 274 | — |
| `AB1` | 149 | 25 274 | **25 125** |
| 20 celdas | 2 966 | 25 274 | **22 308** |

Cambiar una celda que afecta a 149 nodos invoca los mismos 25 274 que no cambiar nada.

#### E7a — Tiempos de `calculate()`

| Llamada | Mediana |
|---|---|
| sin inputs | 12,74 s |
| repetida idéntica | 12,86 s |
| `inputs` = 1 celda | 12,91 s |
| `inputs` = 20 celdas | 13,17 s |

Dispersión entre las cuatro medianas: 3,3 % (0,43 s), menor que el rango mín.–máx. de cada
serie (entre 2,3 y 2,8 s). Los tiempos son **compatibles** con los conteos de E7d, pero no los sustituyen:
por sí solos no prueban que no haya incrementalidad, porque lo que ganaría un motor incremental
dependería de cuántas celdas afectara el cambio y de lo caras que fueran. Aquí el cambio de una
celda afecta al 0,5 % de los nodos función.

#### E7b/E7c — Acotar el grafo, en estos casos concretos

| Llamada | Mediana |
|---|---|
| `calculate(outputs=[A1 de una hoja])` — salida sin dependencias | 0,14 ms |
| `calculate(outputs=[CORE!U5])` — la salida más profunda del grafo | 6,5 ms |
| `calculate(inputs={AB1}, outputs=[CORE!U5])` | 6,5 ms |
| `compile(inputs=[AB1], outputs=[CORE!U5])` — una vez | 4,28 s |
| invocación del pipe compilado | 0,042 ms |

En este caso el pipe es unas 156 veces más rápido que `calculate` con la misma firma, y el coste
de `compile()` se recupera a partir de unas 660 invocaciones. Son cifras de estas dos celdas en
esta máquina: dependen de cuántas celdas necesite la salida pedida y de qué parte congele
`compile()`, y no deben leerse como generales.

## 3. Conclusión de la Parte I

Cuando cambias una celda de entrada y llamas a `calculate()`:

1. `formulas` delega en `schedula`, que abre una `Solution` vacía y ejecuta un Dijkstra sobre el
   grafo desde los datos disponibles: los valores por defecto del fichero más tus `inputs`.
2. Se invocan **todos** los nodos función alcanzables, dependan o no de la celda que cambió, y se
   recalcula su contenido (E1, E2, E7d).
3. Tu input prevalece sobre el valor del fichero porque entra a distancia 0 frente al
   `sh.inf(1, 0)` del valor por defecto; no hay marcado de celdas obsoletas.
4. Si pasas `outputs`, el grafo se poda antes hacia atrás desde esas salidas.

Formulación propuesta para documentar la herramienta:

> `formulas` construye un grafo de dependencias entre celdas y lo ejecuta con schedula. El
> modelo se reutiliza entre llamadas, así que el parseo y la construcción del grafo se pagan una
> vez. Cada `calculate()` ejecuta de nuevo todas las celdas alcanzables: no hay recálculo
> incremental ni reutilización de resultados según qué celda cambió. Para reducir trabajo hay que
> acotar el grafo explícitamente (`outputs=`, `from_ranges`) o compilar un `DispatchPipe` con
> `compile()`, que además calcula una vez y congela la parte que no depende de sus inputs.
> Algunas funciones auxiliares (fechas) memoizan por argumento.

**Límites de esta conclusión.** Vale para el código identificado arriba sobre schedula 1.6.15.
Los conteos cubren un libro pequeño completo y uno grande con dos elecciones de entrada; no se
midió con otros libros ni con modos de ejecución asíncronos de schedula (`executor`). Todo el
comportamiento de ejecución depende de schedula: con otra versión habría que repetir al menos E1,
E2, E6 y E7d.

---

# Parte II — Funcionamiento y alcance

## 4. De una fórmula a una función Python

El pipeline no construye un árbol de sintaxis para interpretarlo después: **construye
directamente un grafo de schedula** y lo envuelve como función.

```
texto  →  tokens  →  shunting-yard  →  sh.Dispatcher  →  DispatchPipe
```

1. **Tokenizado.** `Parser.filters` ([`formulas/parser.py:32-35`](../../formulas/parser.py#L32))
   define la gramática como una lista ordenada de tipos que se prueban en cascada:
   `Error, String, Number, Lambda, Range, OperatorToken, Separator, Function, Array,
   Parenthesis, Intersect`.
2. **Ordenación.** Cada `Token.ast()` implementa su parte del *shunting-yard*. Las precedencias
   reproducen las de Excel ([`formulas/tokens/operator.py:24-28`](../../formulas/tokens/operator.py#L24)):
   `:` y espacio = 8, unarios = 7, `%` = 6, `^` = 5, `*` `/` = 4, `+` `-` = 3, `&` = 2,
   comparadores = 1.
3. **Construcción del grafo.** `AstBuilder.append()`
   ([`formulas/builder.py:56-87`](../../formulas/builder.py#L56)) reduce en notación polaca
   inversa: por cada operador o función saca sus argumentos de la pila y añade un
   `dsp.add_function(...)`. Los operandos se registran como nodos de datos.
4. **Compilación.** `AstBuilder.compile()` ejecuta el grafo una vez con `COMPILING=True`
   (plegado de constantes), lo poda hacia atrás desde la raíz y devuelve un `DispatchPipe`.

Verificado ejecutándolo:

```python
>>> tokens, builder = formulas.Parser().ast('=(1 + 1) + B3 / A2')
>>> func = builder.compile()
>>> list(func.inputs)          # ['A2', 'B3']
>>> func(1, 5)                 # Array(7.0, dtype=object)
>>> type(func)                 # schedula.utils.dsp.DispatchPipe
```

`ast()` devuelve una **tupla** `(tokens, builder)`; de ahí el `[1]` en los ejemplos de la
documentación.

## 5. Del libro Excel al grafo

```
loads → load → add_book → add_sheet → add_cell        (carga)
finish → complete → anchors → assemble → solve_circular → inverse_references
```

`finish()` ([`excel/__init__.py:597-608`](../../formulas/excel/__init__.py#L597)) orquesta cinco
fases y hay que llamarlo antes de `calculate`:

| Fase | Qué hace |
|---|---|
| `complete()` | Cierre transitivo: carga bajo demanda celdas, hojas y libros referenciados pero aún no compilados. Ante un fichero o referencia inexistente inyecta `#REF!` con un warning. |
| `anchors()` | Resuelve los nodos ancla (ids terminados en `#`: rangos abiertos y referencias derramadas). |
| `assemble()` | Crea un `RangesAssembler` por cada rango sin productor: la función que junta N celdas sueltas en un array (`A1:A10`). |
| `solve_circular()` | Sólo si `circular=True`. Ver §8. |
| `inverse_references()` | Añade los `sh.bypass` inversos para que fijar un nombre definido propague a la celda subyacente. |

Clases implicadas ([`formulas/cell.py`](../../formulas/cell.py)):

| Clase | Rol |
|---|---|
| `Cell` | Una celda del libro. Guarda su `Ranges`, su contexto y, si es fórmula, el `AstBuilder`. |
| `Ref` | Un *defined name* global. Subclase de `Cell` sin geometría propia y sin filtro de salida. |
| `CellWrapper` | Envuelve el `DispatchPipe` de una celda; traduce argumentos y convierte `NotImplementedError` en `#NAME?`. |
| `RangesAssembler` | N celdas → 1 rango. |
| `InvRangesAssembler` | 1 rango → N celdas. Permite que fijar un rango como input actualice sus celdas. |

**Por qué la solución contiene objetos `Ranges` y no escalares.** El nodo de datos de salida de
cada celda lleva un filtro `format_output` ([`cell.py:71-72`](../../formulas/cell.py#L71)) que
envuelve el resultado en un `Ranges`, y las funciones de Excel están envueltas con
`wrap_ranges_func` ([`functions/__init__.py:163-171`](../../formulas/functions/__init__.py#L163)),
que desenvuelve al entrar. El grafo transporta valor **y** geometría, que es lo que `write()`
necesita para volcar cada resultado en su celda.

Para leer un valor:

```python
v.value.ravel()[0] if isinstance(v, Ranges) else v
```

y hay que filtrar las claves `sh.Token`: la solución incluye `sh.SELF`, que apunta al propio
dispatcher.

## 6. API pública

`formulas/__init__.py` usa import perezoso (PEP 562): un `__getattr__` a nivel de módulo que
importa el submódulo sólo al pedir el símbolo. Verificado:

```
IMPORT_ALL=False -> nada materializado : True
IMPORT_ALL=True  -> todo materializado : True
```

`__all__` tiene **18 nombres**: `BOOK`, `CELL`, `CIRCULAR`, `DIV`, `ERR_CIRCULAR`, `ExcelModel`,
`NA`, `NAME`, `NULL`, `NUM`, `Parser`, `REF`, `Ranges`, `SHEETS`, `SUBMODULES`, `VALUE`,
`XlError`, `get_functions`.

Métodos de `ExcelModel` ([`formulas/excel/__init__.py`](../../formulas/excel/__init__.py)):

| Grupo | Método | Línea | Qué hace |
|---|---|---|---|
| Carga | `loads(*files)` | 226 | Carga N ficheros; devuelve `self` (encadenable). |
| | `load(file)` | 231 | `add_book` y procesado de todas las hojas. |
| | `add_book(...)` | 264 | Abre el libro, lo cachea, resuelve enlaces externos y nombres definidos. |
| | `add_sheet(...)` | 312 | Registra la hoja; extrae fórmulas de array y rangos de fórmula. |
| | `add_cell(...)` | 381 | Añade la celda al grafo si no estaba ya. |
| | `add_references(...)` | 210 | Procesa los *defined names* globales. |
| Construcción | `complete(stack)` | 451 | Cierre transitivo de dependencias. |
| | `from_ranges(*ranges)` | 236 | `complete(ranges)`: modelo **parcial** desde las celdas de salida. |
| | `anchors()` | 437 | Resuelve nodos ancla. |
| | `assemble(compact)` | 557 | Crea los `RangesAssembler`. |
| | `solve_circular()` | 756 | Detecta y trata ciclos. |
| | `inverse_references()` | 576 | Bypass inversos de los nombres definidos. |
| | `finish(...)` | 597 | Orquesta las cinco fases. |
| Uso | `calculate(...)` | 169 | `dsp.dispatch(...)`. Devuelve una `Solution` (dict). |
| | `compile(inputs, outputs)` | 726 | Sub-modelo con firma fija → `DispatchPipe` (ver E6). |
| | `write(books, solution, dirpath)` | 656 | Vuelca la solución a libros openpyxl. |
| | `compare(...)` | 172 | Diff contra libros de referencia con tolerancias. |
| | `to_dict()` / `from_dict(...)` | 610 / 634 | Serialización JSON del modelo. |

`ExcelModel.__call__` delega en `calculate`; por eso en los tests se ve `xl_mdl({...})`.

**`Ranges`** ([`formulas/ranges.py`](../../formulas/ranges.py)) representa un conjunto de
rectángulos de celdas con sus valores opcionales. Sus operadores reproducen los de Excel:
`+` = expansión (`:`), `|` = unión (la coma), `&`/`intersect` = intersección (el espacio),
`-` = diferencia.

## 7. Catálogo de funciones

```
claves registradas en get_functions() : 645
de ellas marcadas not_implemented     : 0
hoja COVERAGE de test.xlsx            : 483 de 536 (90.1%)
```

Las dos cifras miden cosas distintas:

- **645** son las claves registradas en el `defaultdict` que devuelve `get_functions()`,
  incluidos alias (`_xlfn.*`, `__XLUDF.*`), funciones de compatibilidad y helpers internos
  (`ARRAY`, `ARRAYROW`). Ninguna vale `not_implemented`: ése es el *default_factory* y sólo
  aparece al pedir un nombre ausente.
- **483 de 536 (90,1 %)** es la cobertura declarada del catálogo oficial de Excel. Su fuente es la
  hoja `COVERAGE` de `test/test_files/test.xlsx`, y `test/test_readme.py:29-42` comprueba que el
  README coincida con ella.

Categorías **al 0 %**: AUTOMATION (0/3), CUBE (0/7), DATABASE (0/12, todas las `D*`), WEB (0/3).
Parciales: INFORMATION 16/22, LOOKUP 33/40, MATH & TRIG 71/80, TEXT 44/50. Al 100 %:
COMPATIBILITY, DATE & TIME, ENGINEERING, FINANCIAL, LOGICAL, STATISTICAL, OPERATORS. Que
FINANCIAL figure al 100 % significa que las funciones existen; en este entorno algunas fallan con
ciertos argumentos (§10).

Sí están soportados `LAMBDA`, `LET`, los arrays dinámicos y `UNIQUE`/`SORT`/`SORTBY`/`PIVOTBY`.

**Funciones propias.** `get_functions()` devuelve un `defaultdict` mutable y cacheado, así que
añadir una clave la registra para todo el proceso. Verificado:

```python
>>> formulas.get_functions()['MYFUNC'] = lambda x, y: 1 + y + x
>>> formulas.Parser().ast('=MYFUNC(1, 2)')[1].compile()()
4
```

> **Cuidado:** al ser un `defaultdict`, leer `fns['NOEXISTE']` **inserta** la clave con
> `not_implemented` y contamina el diccionario cacheado. Hay que comprobar con `'NOEXISTE' in fns`.

### Función desconocida: el resultado depende de la vía

| Vía | Resultado de `=NOEXISTE(1,2)` |
|---|---|
| Celda de un `ExcelModel` | `#NAME?` |
| `Parser().ast(...).compile()` | **lanza `DispatcherError`** |

El `#NAME?` lo produce `CellWrapper.__call__`
([`cell.py:32-40`](../../formulas/cell.py#L32)), que sólo envuelve fórmulas **de celda**:

```python
    def __call__(self, *args, **kwargs):
        try:
            return self.func(...)
        except sh.DispatcherError as ex:
            if isinstance(ex.ex, NotImplementedError):
                return Error.errors['#NAME?']
            raise ex
```

Una función compilada directamente con `Parser` no pasa por ahí y propaga la excepción.

## 8. Errores y referencias circulares

**8 errores soportados** (`formulas/tokens/operand.py:26-37`), en el orden que usa `ERROR.TYPE`:

```
#NULL!, #DIV/0!, #VALUE!, #REF!, #NAME?, #NUM!, #N/A, #GETTING_DATA
```

No soportados: `#SPILL!`, `#CALC!`, `#FIELD!`, `#BLOCKED!`. `ERR_CIRCULAR` (`#CIRC!`) es un valor
interno que no pertenece a `Error.errors` y cuyo `str()` es `'0'`: al escribir el libro, la celda
circular sale como `0`, como en Excel con el cálculo iterativo desactivado.

**Referencias circulares.** `finish(circular=...)` vale **`False` por defecto**. Medido sobre
`circular.xlsx`:

| | Nodos de datos sin resolver | Nodos con `#CIRC!` |
|---|---|---|
| `finish()` | **62** | 0 |
| `finish(circular=True)` | 1 | **38** |

Sin el flag, los nodos del ciclo quedan **ausentes** de la solución, sin excepción, sin error en
la celda y sin aviso: `sol[ref]` da `KeyError` y `write()` no escribe esas celdas. Es la trampa más
fácil de pasar por alto de la biblioteca.

Con el flag, `solve_circular()` ([`excel/__init__.py:756-788`](../../formulas/excel/__init__.py#L756))
aplica Tarjan y Johnson (`formulas/excel/cycle.py`, implementación propia sin networkx) con dos
estrategias: si alguna función del ciclo sabe romperlo (un `IF` que no evalúa la rama circular),
reencamina la arista por el nodo `CIRCULAR`; si no, marca los nodos de datos del ciclo con
`ERR_CIRCULAR`.

**No hay cálculo iterativo**: ni número máximo de iteraciones ni épsilon de convergencia. Un
ciclo genuino nunca converge a un valor.

## 9. Formatos, CLI y API web

| Formato | API Python | CLI |
|---|---|---|
| `.xlsx` | Sí | Sí |
| `.ods` | Sí (verificado: 2 841 nodos) | Sí |
| `.json` (vía `from_dict`) | Sí | Sí |
| `.xlsm` | Sin comprobar (openpyxl lo lee; no hay fixture en el repo) | **No**: `Unsupported input file` |
| `.xls` (BIFF) | No | **No**: `Unsupported input file` |
| `.csv` | No | No |

La CLI filtra por extensión en [`formulas/cli.py:220-227`](../../formulas/cli.py#L220) y sólo
admite `.xlsx`, `.ods` y `.json`. Los enlaces externos se limitan a `.xlsx`
(`excel/__init__.py:290-299`): un enlace a `.xlsm`, `.xls` u `.ods` se descarta en silencio.

**Comandos de la CLI**, verificados con `formulas --help`:

| Comando | Qué hace |
|---|---|
| `build` | Exporta un modelo JSON portable, podado a las salidas pedidas. |
| `calc` | Calcula, individual o por lotes (`--batch`, `--processes`). |
| `test` | Compara los resultados calculados con el libro de referencia. |
| `serve` | Levanta el servidor Flask. |

**No existen `run` ni `plot`.** El grafo se dibuja desde Python con
`xl_model.dsp.plot(view=True)`, funcionalidad de schedula.

**API web** (`formulas/app.py`): `GET /api/health`, `GET /api/model`, `POST /api/calculate` y la
GUI en `/`. El modelo se construye **una vez al arrancar**: el servidor trabaja sobre un modelo
fijo, sin recarga en caliente ni subida de ficheros.

## 10. Limitaciones y cosas que conviene saber

### Lo que queda fuera del modelo

Del libro sólo se extraen **valor y fórmula** de cada celda. Quedan fuera macros y VBA, tablas
dinámicas, formato condicional, gráficos, validación de datos, *slicers*, Power Query/Power Pivot
y el formato de celda. El README no lo declara (no tiene sección de limitaciones, y el paquete no
contiene ningún `TODO`/`FIXME`); se deduce de la estructura: el lector extiende
`openpyxl.reader.excel.ExcelReader`, que no interpreta `vbaProject.bin`, y `_book2dict` sólo lee
`cell.value`.

### Enmascaramiento de errores y un fallo observado en este entorno

`wrap_func` ([`functions/__init__.py:144-160`](../../formulas/functions/__init__.py#L144))
captura cualquier `Exception` y devuelve `#VALUE!`, así que un fallo de implementación no se
distingue de un `#VALUE!` legítimo de Excel.

En este entorno la suite de tests del repo falla:

```
Ran 6 tests in 66.422s
FAILED (failures=1)
```

`test_excel_model` encuentra 12 diferencias en la hoja FINANCIAL. Once celdas dan `#VALUE!`
en lugar de un número: `CUMIPMT` en `S13:X13`, `CUMPRINC` en `S14:X14` (las dos comparten la
implementación `xcumipmt`) y `PPMT` en `Y41`. La duodécima, `IRR` en `Y26`, da `-1.0` donde se
esperaba `#NUM!`. El fallo se reproduce con la suite original, sin ninguno de los scripts
de este informe cargado.

**Causa aislada para `CUMIPMT` y `PPMT`.** Quitando el enmascaramiento aparece:

```
File "formulas/functions/financial.py", line 1352, in xcumipmt
    res = func(rate, per, nper, pv, fv=0, when=type)
ValueError: cannot convert float NaN to integer
```

y se reproduce **sin `formulas`**, llamando directamente a `numpy_financial`:

| Llamada | Resultado |
|---|---|
| `npf.ipmt(6, [3,4,5], 5.5, 125000, fv=0, when=1)` | `ValueError: cannot convert float NaN to integer` |
| `npf.ipmt(6.0, [3,4,5], 5.5, 125000, fv=0, when=1)` | `[-107027.2, -106318.8, -101360.0]` |
| `npf.ppmt(1, 3, 33, 10000, 0, 1)` | `ValueError` |
| `npf.ppmt(1.0, 3, 33, 10000, 0, 1)` | `-2.33e-06` |

El disparador es un **`rate` entero**. Las tasas de las once celdas llegan como enteros: `4`,
`6`, `7` y `9` en `D:G` de las filas 13 y 14; `'26/1/1900'` en `I13`/`I14`, que `_text2num`
convierte en `26`; y `True` en `J41`. `xcumipmt` y `xppmt` los pasan a `numpy_financial` sin
convertirlos a `float`, y dentro de `npf.ipmt`, `_value_like(ipmt_array, np.nan)` intenta crear un
`NaN` con el dtype entero del array.

Por la vía pública de `formulas` el efecto es directo:

| Llamada | Resultado |
|---|---|
| `get_functions()['CUMIPMT'](6, 5.5, 125000, 3, 5, 1)` | `#VALUE!` |
| `get_functions()['CUMIPMT'](6.0, 5.5, 125000, 3, 5, 1)` | `-314705.9605595653` |
| valor guardado por Excel en `S13` | `-314705.9605595599` |

Con la tasa en `float`, el resultado coincide con Excel. Lo mismo ocurre con
`CUMPRINC(6.0, …)` = `-6729.8395` frente a `S14` = `-6729.8395`.

Qué **no** está establecido:

- **Que sea una regresión.** Que el mismo código pase en otras combinaciones de `numpy` y
  `numpy-financial` exigiría compararlo en otro entorno, y no se ha hecho. El fallo se describe
  como fallo en este entorno.
- **La causa de `Y26`** (`=IRR(F27:H27, J26)`, con `F27=4`, `G27=5`, `H27` vacía y `J26=True`):
  `formulas` devuelve `-1.0` donde Excel da `#NUM!`. No lanza la excepción anterior; es otro
  mecanismo, no investigado.

### Empaquetado roto

```
extras en setup.py         : all, cli, dev, excel, plot
ficheros en requirements/  : all, base, cli, dev, excel, plot, rtd, test
primera linea de plot.pip  : '-r web.pip'
requirements/web.pip existe: False
```

`requirements/plot.pip` referencia un `web.pip` inexistente, así que
`pip install -r requirements/{plot,all,dev}.pip` falla. Además `README.rst:107` anuncia
`pip install formulas[web]`, un extra que `setup.py` no define (el que corresponde es `[cli]`).

### Otras

- **Recursión** en `cycle._strong_connect`: un grafo de dependencias muy profundo puede provocar
  `RecursionError`.
- **Ids de nodo sensibles al formato.** Se normalizan a mayúsculas y llevan el directorio:
  `'<dir>[<FICHERO.XLSX>]<HOJA>'!<REF>`. Cargar el mismo libro con ruta relativa o absoluta da ids
  distintos. Conviene buscarlos en `dsp.data_nodes` en vez de escribirlos a mano (como hace
  `ref()` en `instrument.py`).
- **`compile()` comparte estado con el modelo.** `shrink_dsp` reutiliza los mismos diccionarios
  de atributos de nodo en el modelo y en el pipe (en E6, los de `B2`, `C2` y `C4`). Modificar la
  función de un nodo del pipe la modifica también en el modelo.

---

# Reproducir

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -e . -r requirements/excel.pip -r requirements/test.pip click click_log flask
```

```bash
.venv/Scripts/python research/recalc/run_all.py
```

| Script | Contenido |
|---|---|
| `provenance.py` | Manifiesto SHA-256 del código, `pip freeze`, versión declarada. |
| `instrument.py` | Contadores de ejecución, parche de `CellWrapper`, helper `ref()`. |
| `exp_counts.py` | Grafo registrado, E1–E5. |
| `exp_compile.py` | E6. |
| `exp_timing.py` | E7 (tiempos y conteos en el libro grande). |
| `probe_api.py` | Comprobaciones ejecutables de la Parte II. |
| `results/reference/` | Salidas de la corrida citada en este informe (no se sobrescriben). |
| `results/latest/` | Salidas de cada nueva ejecución de `run_all.py` (no versionadas). |

## Validez de las mediciones

1. **Conteos deterministas.** E1–E6 y E7d salen idénticos entre corridas. Los tiempos no, y por
   eso se dan medianas y se tratan como apoyo, no como prueba.
2. **Contadores independientes.** Uno envuelve `node['function']`, otro parchea
   `CellWrapper.__call__` y un tercero cuenta los operadores internos de cada celda. Coinciden
   entre sí (11 = 11 en E1) y con el total de operadores del modelo.
3. **La instrumentación no altera los resultados.** Con los contadores activos, los valores
   coinciden con los guardados en el propio `excel.xlsx` (`B2=8`, `B3=2`, `B4=8`, `C2=5`,
   `C3=12`, `C4=35`, `D3=2`, `D4=3`), y los de E6 con los del README para `func(3, 1)` →
   `[10.0, 4.0]`.
4. **Clasificación desde el grafo.** Afectado significa "descendiente en `dsp.dmap`", calculado por
   el script, no deducido leyendo fórmulas.
5. **Predicciones antes de ejecutar.** Cada experimento imprime qué esperaría ver con y sin
   incrementalidad antes de medir.

### Correcciones respecto a la versión anterior de este informe

Tras una revisión se corrigió lo siguiente:

- **Dependencias de `D2`.** La prosa decía que `D2` sólo alimentaba `D3 → D4 → C4`, pero
  `C2 = B2/B3+D2`, así que también afecta a `C2` y, a través de ella, a `C3` y `C4`. La
  clasificación del script venía del grafo y ya era correcta; se ha corregido la explicación y se
  ha contrastado con el `counts.json` original.
- **Argumento de los cuatro atributos.** Se decía que `ExcelModel` sólo tiene cuatro atributos y
  que "no hay dónde guardar una caché". No prueba nada: el estado podría estar dentro de `dsp` o
  de otros objetos. Se ha retirado; la evidencia es el recorrido del código más los conteos,
  ahora también dentro de cada celda.
- **`compile()`.** Se decía que "reutiliza el plan, no los valores". Es falso: congela al compilar
  los valores de la parte independiente de los inputs (`D4` en E6).
- **Las `lru_cache` de `formulas`.** Se decía que eran todas de parsing. `xdate` y
  `_text2datetime` memoizan resultados por argumento.
- **Contraste de E6.** Se publicó que `calculate` con la misma firma ejecutaba 5 nodos. Eran 8: al
  instrumentar el pipe se envolvieron también `B2`, `C2` y `C4` en el modelo, porque comparten
  diccionario de nodo, y esos tres se contaron en el contador equivocado. Ahora se mide sobre un
  modelo aparte.
- **Tiempos contaminados.** `exp_counts.py` no deshacía su parche de `CellWrapper`, así que en
  `run_all.py` los tiempos de E7 se midieron con un contador activo. Ahora se restaura al terminar
  y todas las cifras de este informe son de una corrida limpia.
- **Alcance de los tiempos.** Se afirmaba que un motor incremental sería "órdenes de magnitud" más
  rápido. No tiene por qué; dependería del conjunto afectado. Los tiempos se presentan ahora como
  compatibles con los conteos de E7d, y las aceleraciones como resultados de casos concretos.
- **Fallo FINANCIAL.** Se atribuía a una regresión de dependencias sin comparar entornos. Ahora se
  describe como fallo en este entorno, con la causa inmediata aislada.
- **Selección de entradas en E7** (corrección de la versión anterior, que se mantiene): la
  selección automática elegía nodos de error (`#REF!`, `#NAME?`) y nombres definidos en lugar de
  celdas, y E7c comparaba contra una salida trivial.
