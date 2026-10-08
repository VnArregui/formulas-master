# Análisis de recálculo y alcance de `formulas`

Material reproducible de un análisis de la biblioteca
[`formulas`](https://github.com/vinci1it2000/formulas), que parsea y compila fórmulas de Excel a
código Python. Responde una pregunta concreta, *si cambia una celda de entrada, ¿cómo determina
`formulas` qué debe ejecutar?*, y documenta el funcionamiento y el alcance de la biblioteca.

**El informe completo está en
[`formulas-master/research/recalc/INFORME.md`](formulas-master/research/recalc/INFORME.md).**

## Resultado principal

En la instantánea estudiada de `formulas` 1.3.4, ejecutada sobre `schedula` 1.6.15, cada llamada
a `calculate()` invoca todos los nodos alcanzables del grafo de dependencias, independientemente
de qué celda haya cambiado: no hay recálculo incremental. El modelo (grafo, celdas parseadas,
libros cargados) sí se reutiliza entre llamadas. `compile()` calcula una vez y congela la parte
que no depende de sus inputs, pero entre invocaciones también ejecuta siempre los mismos nodos.

Las limitaciones y el detalle de cada experimento están en el informe.

## Qué contiene

```
formulas-master/                  instantánea de formulas 1.3.4 tal como se analizó (EUPL)
  formulas/                       código de la biblioteca
  test/                           tests y libros del proyecto (usados como modelos de prueba)
  research/recalc/                el análisis
    INFORME.md                    informe
    instrument.py                 contadores de ejecución
    exp_counts.py                 E1–E5 · grafo registrado, caché, afectados, poda por outputs
    exp_compile.py                E6    · compile() / DispatchPipe
    exp_timing.py                 E7    · tiempos y conteos en el libro grande
    probe_api.py                  comprobaciones de la Parte II del informe
    provenance.py                 huellas SHA-256 del código y versiones del entorno
    run_all.py                    ejecuta todo
    verify_reference.py           compara una corrida nueva con la de referencia
    results/reference/            salidas de la corrida citada (no se sobrescriben)
    results/latest/               salidas de nuevas corridas (no versionadas)
requirements-lock.txt             versiones exactas del entorno de la corrida citada
CITATION.cff                      cómo citar este material
```

### Sobre la instantánea de `formulas`

Se recibió como carpeta `formulas-master` **sin metadatos de git**, así que no hay un commit de
origen que citar. Se identifica por la versión declarada (`1.3.4`, `__updated__ = 2026-03-11`) y
por la huella SHA-256 de sus ficheros:

```
94acf93e3137aa68408bf5d0b2238e34eda959e97c50eb2a58d351bbb50a9c9d
```

El manifiesto fichero a fichero está en `results/reference/source_manifest.txt`, y
`provenance.py` recalcula la huella. No está verificado que la instantánea sea idéntica a la
release 1.3.4 publicada en PyPI.

`formulas` y `schedula` son obra de Vincenzo Arcidiacono (© European Commission, JRC) y se
distribuyen bajo la licencia EUPL; ver `formulas-master/LICENSE.txt`. La instantánea se incluye
sin modificaciones para permitir la reproducción exacta.

## Reproducir

Requisitos: Python 3.13 en Windows (es el entorno de la corrida citada; en otros sistemas los
conteos deberían coincidir, pero no se ha comprobado).

**1. Crear el entorno e instalar las versiones exactas.**

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements-lock.txt
```

```bash
.venv/Scripts/python -m pip install -e formulas-master --no-deps
```

En Windows, crea el entorno en una ruta corta: con rutas largas la instalación de `lxml` falla
por el límite de 260 caracteres (`OSError: [Errno 2] No such file or directory`).

No uses `pip install formulas[all]` ni `requirements/{plot,all,dev}.pip` del proyecto: dependen
de un `requirements/web.pip` que no existe y la instalación falla (documentado en el informe,
§10).

**2. Ejecutar la batería** (unos 10 minutos; la mayor parte es E7 sobre el libro grande):

```bash
.venv/Scripts/python formulas-master/research/recalc/run_all.py
```

**3. Comparar con la referencia.**

```bash
.venv/Scripts/python formulas-master/research/recalc/verify_reference.py
```

Exige igualdad exacta en todos los conteos (E1–E6 y E7d), en las comprobaciones de la API y en
las huellas del código. Los tiempos se muestran pero no se comparan, porque dependen de la
máquina.

## Cómo citar

Usa los metadatos de [`CITATION.cff`](CITATION.cff) (GitHub ofrece "Cite this repository"). Al
citar la conclusión sobre recálculo, conviene acotarla a la instantánea y a la versión de
`schedula`, por ejemplo:

> En la instantánea de `formulas` 1.3.4 estudiada (huella `94acf93e…`), ejecutada sobre
> `schedula` 1.6.15, cada llamada a `calculate()` invoca todos los nodos alcanzables con
> independencia de la celda modificada.

## Elaboración

El análisis, los scripts y el informe se elaboraron con asistencia de un modelo de IA (Claude,
de Anthropic) y fueron revisados por el autor; la sección "Correcciones" del informe recoge los
errores detectados en esa revisión.
