# Otimização de Velocidade do Auto-ARROWS

Este documento analisa onde o bot perde tempo e lista, por ordem de impacto, as
formas de o tornar mais rápido. Abrange as duas fases críticas:

1. **Velocidade por jogada** — o ciclo observação → decisão → toque → confirmação.
2. **Criação da grelha completa** — o *stitching* de tabuleiros maiores que o ecrã.

> Todas as referências de tempo são estimativas baseadas no código atual e na
> documentação existente (`capture.py:2` refere ~2400 ms para `screencap -p`).

---

## 1. Contexto

- Ecrã do dispositivo: **1220 × 2712 px** (vertical).
- Banda útil do tabuleiro: `[TOP_MARGIN, height - BOTTOM_MARGIN]` = `[400, 2412]`,
  ou seja 2012 px de altura (`stitch.py:25-26`).
- O bot é conservador por design (anti-penalização): confirma sempre que a seta
  saiu antes de continuar, o que multiplica o número de capturas.

### Orçamento de tempo típico de uma jogada (`bot.py:93-165`)

| Etapa | Custo aproximado | Onde |
| :--- | :--- | :--- |
| Delay fixo pós-toque | 0.30 s | `bot.py:132` |
| Captura para confirmar saída da seta | **~2.4 s** | `bot.py:136` → `capture.get_frame` |
| Deteção (cabeças + ocupação) | 0.05–0.3 s | `bot.py:98-101` |
| **Total** | **~2.8 s/jogada** | |

O custo é dominado por **uma única captura de ecrã**. Se o tabuleiro for maior que
o ecrã, ainda há o custo do *stitching* inicial (ver secção 4).

---

## 2. Diagnóstico: onde se perde tempo

### 2.1. Captura de ecrã (gargalo principal)

- `capture.get_frame()` (`capture.py:108`) chama `adb_client.screenshot()`
  (`adb_client.py:78`), que executa `adb exec-out screencap -p`.
- O `-p` obriga o dispositivo a **codificar um PNG** (e o PC a descodificá-lo),
  o que domina o custo (~2400 ms documentados em `capture.py:2`).
- O stream rápido H.264 (`screenrecord` → FIFO → OpenCV) existe mas está
  **desligado** por fiabilidade (`USE_STREAM = False`, `capture.py:23`), porque
  podia devolver frames congelados (`capture.py:21-22`).
- Cada `_adb()` (`adb_client.py:15`) arranca um **novo processo** e faz o
  *handshake* com o servidor adb, repetido centenas de vezes por nível.

### 2.2. Esperas fixas (`time.sleep`)

| Local | Valor | Observação |
| :--- | :--- | :--- |
| `bot.py:132` | `delay` 0.3 s | sempre, mesmo que a seta já tenha saído |
| `bot.py:135-141` | até 6 × 0.25 s | polling de confirmação |
| `bot.py:56-69` (`wait_for_stable`) | 0.25 s × até 6 s | espera por frame estável |
| `bot.py:257` (`ensure_visible`) | 0.5 s × até 14 | por pan |
| `stitch.py:93,128,149` | `settle` 0.5 s | por pan do *stitching* |
| `play_grid.py:95,314` | 0.5 s / `delay` 0.3 s | pan e pós-toque |

Somados, os sleeps podem representar **30–50%** do tempo total, mesmo quando a
condição esperada já se verificou.

### 2.3. Deteção redundante e não vetorizada

- `vision.build_occupancy` (`vision.py:462`) percorre **todas as células em
  Python** (loops aninhados) e, por dentro, recalcula `foreground_mask`.
- `foreground_mask` é recomputada em vários sítios: `build_occupancy`
  (`vision.py:477`), `detect_head_pixels` (`vision.py:379`), `board_is_cut`
  (`bot.py:170`), `board_box_foreground` (`vision.py:271`), `update_global`
  (`bot.py:192`). São 4–6 passes sobre a imagem inteira **por frame**.
- `detect_head_pixels` (`vision.py:364`) aplica *distance transform* e
  `cv2.matchTemplate` **4× por pico** (`vision.py:353-360`), em imagem completa.
- `play_level` refaz tudo a cada jogada; `play_grid.relocate` a cada pan e a cada
  jogada; `stitch.save_frame_grids` + `snap_offsets` + `merge` re-detetam cada
  frame várias vezes.
- `grid_stitch.align` (`grid_stitch.py:162`) é uma busca **brute-force** de
  deslocamentos e `refine_positions` (`grid_stitch.py:302`) faz 3 iterações
  sobre todos os frames.

### 2.4. Criação da grelha completa (*stitching*)

- `capture_grid` (`stitch.py:111`) faz pans de **400 px** com `settle=0.5` e uma
  captura de ~2.4 s cada, em raster 2D (até 8 passos por eixo).
- Ir ao canto superior esquerdo (`stitch.py:123-134`) + varrer ~3 bandas ≈
  **1–2 minutos só de captura**, antes de qualquer processamento.
- Depois há `save_frame_grids` (grid + heads por frame), `snap_offsets`
  (deteta pontos/fase por frame outra vez) e `merge` (loops Python por célula).
- `play_grid.build_model` re-extrai a grelha de todos os frames com
  `stitch_all`, que ainda corre `refine_positions`.

---

## 3. Otimizações por ordem de impacto

| # | Otimização | Ganho esperado | Risco | Esforço |
| :-: | :--- | :--- | :--- | :--- |
| 1 | Raw `screencap` sem `-p` | **5–10× na captura** | Baixo | Baixo |
| 2 | Conexão adb persistente | remove *spawn* por comando | Baixo | Baixo |
| 3 | Reativar/corrigir stream H.264 (ou scrcpy/minicap) | captura ~10–30 ms | Médio | Médio |
| 4 | Esperas adaptativas em vez de sleeps | 30–50% do tempo de jogada | Baixo | Baixo |
| 5 | `foreground_mask` 1× por frame + cache | evita 4–6 passes/frame | Baixo | Baixo |
| 6 | `build_occupancy` vetorizada | grande em grelhas grandes | Médio | Médio |
| 7 | Deteção incremental pós-tap | poupa deteção *full-board* | Médio | Médio |
| 8 | Stitching: passo maior + captura em paralelo | menos frames, menos espera | Médio | Médio |
| 9 | Reutilizar grid/fase/heads por frame | ~2–3× no merge | Baixo | Baixo |
| 10 | Meia resolução + `cv2.setNumThreads` | 2–4× na visão | Médio | Médio |
| 11 | `align` por FFT / janela menor; refine condicional | acelera stitching | Médio | Médio |

### 3.1. Captura rápida (otimizações 1–3)

**1. Raw screencap (maior ganho, menor risco).**
Em vez de `screencap -p`, usar `adb exec-out screencap` sem `-p`. O dispositivo
devolve o *framebuffer* cru: cabeçalho (width, height, formato) + pixels RGBA.
Decodificar com `np.frombuffer` + `reshape` + `cv2.cvtColor` evita toda a
codificação/descodificação PNG. Deve ser implementado como *fallback* para
`screencap -p`, pois o formato varia entre dispositivos/versões.

**2. Conexão adb persistente.**
Reutilizar o servidor/ligação (ex.: `adbutils`/`ppadb`, ou um `adb shell`
interativo mantido aberto) evita o custo de arrancar um processo por cada
`tap`/`screenshot`/`swipe`. Requer `adb_client.py` a manter um *socket* em vez de
`subprocess.run` por chamada.

**3. Stream de vídeo.**
O H.264 via `screenrecord` está desligado por poder congelar. Alternativas mais
robustas: **scrcpy** (servidor no dispositivo) ou **minicap**. Em qualquer caso,
manter sempre o último frame e um *watchdog* que deteta frames repetidos e faz
*restart* do stream (o `Stream.restart` em `capture.py:72` já existe).

### 3.2. Esperas adaptativas (otimização 4)

- Substituir o `delay` fixo (`bot.py:132`) por um polling rápido (30–50 ms) que
  verifica a ocupação da célula tocada, usando o último frame disponível.
- `wait_for_stable` (`bot.py:56-69`): usar intervalo de 0.05–0.1 s e comparar
  frames **em baixa resolução** (ex.: reduzir para 1/4 antes do diff).
- Pan settle (`stitch.py:93`): em vez de 0.5 s fixos, detetar estabilidade por
  comparação de dois frames consecutivos de baixa resolução.
- `ensure_visible` (`bot.py:219-265`): reduzir sleeps e confirmar o pan pela
  estabilidade em vez de tempo fixo.

### 3.3. Visão vetorizada e com cache (otimizações 5, 6, 10)

- **Cache de máscara:** calcular `foreground_mask` uma vez por frame e passá-la
  como argumento a `build_occupancy`, `detect_head_pixels`, etc. Alternativa:
  `functools.lru_cache` com chave `id(frame)`/`frame.__array_interface__`.
- **`build_occupancy` vetorizada:** usar `cv2.integral` (imagem integral) para
  obter a soma de primeiro plano por célula em bloco, ou reestruturar a máscara
  em blocos com `reshape`/`sum`, eliminando os loops Python (`vision.py:481-495`).
- **Meia resolução:** reduzir o frame para deteção (ex.: 50%) e escalar as
  coordenadas no fim; as operações OpenCV tornam-se ~4× mais rápidas.
- **`cv2.setNumThreads`:** garantir que o OpenCV usa vários núcleos.

### 3.4. Deteção incremental (otimização 7)

Após um toque, apenas a célula tocada e o corredor de saída mudam. Em vez de
recalcular `build_occupancy`/`detect_arrowheads` de todo o tabuleiro
(`bot.py:98-101`), reavaliar só a região afetada. Isto é especialmente valioso em
tabuleiros grandes, onde o full-board domina o custo de CPU.

### 3.5. Stitching mais rápido (otimizações 8, 9, 11)

- **Passo maior:** usar pans de 60–80% do *viewport* (em vez de 400 px fixos,
  `stitch.py:111`) mantendo sobreposição suficiente → menos frames.
- **Captura em paralelo:** *thread* produtora de frames + *queue*, enquanto a
  thread principal processa o frame anterior (esconde o custo da visão).
- **Reutilizar deteções:** `save_frame_grids` (`stitch.py:168`) e `snap_offsets`
  (`stitch.py:210`) detetam a mesma coisa duas vezes; calcular grid/fase/heads
  uma só vez por frame e reutilizar no `merge`.
- **`align` eficiente:** substituir a busca brute-force por correlação via FFT
  sobre os símbolos, ou reduzir a janela usando os offsets gravados (já feito em
  `grid_stitch.py:364-365`); tornar `refine_positions` condicional (só frames
  ambíguos) e reduzir iterações.

---

## 4. Quick wins vs mudanças estruturais

**Quick wins (baixo risco, bom retorno, sem alterar arquitetura):**

1. Raw `screencap` (#1).
2. Cache de `foreground_mask` por frame (#5).
3. Esperas adaptativas / redução de sleeps (#4).
4. Reutilizar deteções no *stitching* (#9).
5. Instrumentar tempos por etapa (ver secção 5).

**Mudanças estruturais (maior esforço, maior ganho):**

6. Stream de vídeo robusto / scrcpy-minicap (#3).
7. `build_occupancy` vetorizada e meia resolução (#6, #10).
8. Deteção incremental pós-tap (#7).
9. Stitching com passo maior + captura em paralelo (#8, #11).

---

## 5. Plano de implementação por fases

**Fase 0 — Medir.**
Adicionar instrumentação com `time.perf_counter` a: captura, `foreground_mask`,
`build_occupancy`, `detect_arrowheads`, `tap`, confirmação e (no stitching) cada
pan. Registar médias por etapa. Sem isto, não há como provar o ganho.

**Fase 1 — Captura + sleeps (quick wins #1, #4).**
Raw `screencap` com fallback; reduzir/substituir sleeps fixos por polling.
Meta: **< 1 s por jogada** (de ~2.8 s).

**Fase 2 — Cache e reutilização (#5, #9).**
Máscara única por frame; uma deteção por frame no stitching.

**Fase 3 — Stitching (#8).**
Passo maior e captura em paralelo.
Meta: **reduzir a criação da grelha em 50%+**.

**Fase 4 — Estrutural (#3, #6, #7, #10, #11).**
Stream de vídeo, ocupação vetorizada, deteção incremental, meia resolução e
`align` por FFT.

---

## 6. Riscos e mitigação

| Risco | Mitigação |
| :--- | :--- |
| Formato do raw `screencap` varia por dispositivo | Detetar o cabeçalho e usar `screencap -p` como fallback |
| Stream de vídeo pode congelar (já observado) | *Watchdog* de frame repetido + `Stream.restart` (`capture.py:72`) |
| Polling agressivo aumenta carga de CPU | Backoff progressivo (30 → 50 → 100 ms) |
| Meia resolução pode perder cabeças pequenas | Validar limiar de `head_ratio` (`vision.py:369`) à escala reduzida |
| Passo maior pode falhar o alinhamento | Manter sobreposição mínima e validar `_shift` (`stitch.py:29`) |

---

## 7. Resumo

- O maior ganho isolado é a **captura**: passar de `screencap -p` (~2.4 s) para
  raw/stream pode reduzir o tempo por jogada em **~80%**.
- Logo a seguir, **eliminar sleeps fixos** e **vetorizar/cachear a visão**.
- Na criação da grelha, o segredo é **menos frames** (passo maior), **captura em
  paralelo** e **não detetar duas vezes o mesmo frame**.
