# Análise Completa do Projeto Auto-ARROWS

## 1. Visão Geral do Projeto

O **Auto-ARROWS** é uma solução automatizada em Python concebida para jogar autonomamente o jogo mobile **Arrows** (Android) através da interface **ADB** (Android Debug Bridge).

O sistema combina técnicas de visão computacional (OpenCV/NumPy), reconstrução e alinhamento de tabuleiros de grande dimensão (*stitching*), um algoritmo determinístico de resolução de jogadas (*solver*) e mecanismos de automação física de toques/gestos (*ADB touch & swipe*), incluindo salvaguardas rigorosas para evitar penalizações no jogo.

---

## 2. Estrutura de Ficheiros e Componentes

| Ficheiro / Diretoria | Responsabilidade Principal |
| :--- | :--- |
| [adb_client.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/adb_client.py) | Camada de abstração de baixo nível para execução de comandos ADB (`tap`, `swipe`, `screencap`, `wm size`). |
| [capture.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/capture.py) | Captura otimizada de imagens do ecrã do dispositivo (suporta `screencap` e stream H.264 experimental via FIFO). |
| [vision.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/vision.py) | Motor de visão computacional: deteção de grelhas, cálculo de fase/espaçamento, máscaras de cor, deteção de cabeças de setas e cálculo de matriz de ocupação. |
| [solver.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/solver.py) | Algoritmo determinístico que valida se uma seta pode sair do tabuleiro sem colidir com outras setas ou áreas desconhecidas. |
| [stitch.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/stitch.py) | *Stitching* contínuo ao nível de píxeis via varredura 2D (raster scan *boustrophedon*), alinhamento por *template matching* e ajuste por fase de reticulado. |
| [grid_stitch.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/grid_stitch.py) | *Stitching* discreto ao nível de símbolos (`FrameGrid`): alinhamento de matrizes simbólicas, fusão por votação e refinamento de posição (`refine_positions`). |
| [bot.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/bot.py) | Script principal de execução do bot para níveis standard e níveis maiores do que o ecrã. |
| [play_grid.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/play_grid.py) | Motor avançado para jogar tabuleiros gigantes com base no modelo de grelha discreta (`grid_stitch`), navegando e sincronizando a vista atual em tempo real. |
| [calibrate.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/calibrate.py) | Ferramenta interativa com interface gráfica OpenCV para calibração manual/automática da grelha do tabuleiro. |
| [test_tap.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/test_tap.py) | Utilitário interativo para teste e verificação do mapeamento de coordenadas de toques ADB entre o PC e o telemóvel. |
| [requirements.txt](file:///home/dinisrosa22/Projects/Auto-ARROWS/requirements.txt) | Dependências externas do projeto (`numpy`, `opencv-python`, `pillow`). |
| [tests/test_solver.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/tests/test_solver.py) | Conjunto de testes unitários para validação das regras do `solver.py`. |
| [docs/](file:///home/dinisrosa22/Projects/Auto-ARROWS/docs) | Documentação de planeamento e arquitetura do projeto. |

---

## 3. Arquitetura Técnica e Módulos em Detalhe

### 3.1. Comunicação e Captura (`adb_client.py` & `capture.py`)
- **`adb_client.py`**: Interage com o comando do sistema `adb`.
  - `ensure_device()`: Garante que existe exatamente um dispositivo Android ligado e autorizado.
  - `screenshot()`: Captura a imagem BGR do dispositivo usando `screencap -p`.
  - `tap(x, y)` & `swipe(x1, y1, x2, y2, duration)`: Executa toques e deslizes no ecrã.
- **`capture.py`**:
  - Encapsula a obtenção de capturas de ecrã.
  - Inclui suporte a streaming H.264 via `screenrecord` para reduzir a latência de captura (~5-15 ms vs ~2400 ms de `screencap`), embora desativado por defeito (`USE_STREAM = False`) para máxima fiabilidade.

### 3.2. Visão Computacional (`vision.py`)
O módulo `vision.py` é o núcleo de perceção do bot:
1. **Estrutura `Grid`**: Define o mapeamento entre células lógicas `(row, col)` e coordenadas de píxeis no dispositivo `(x, y)`.
2. **Deteção de Grelha**:
   - **Pontos Cinzentos (`dots_mask`, `detect_dots`)**: Deteta os pontos de interseção da grelha expostos no fundo branco.
   - **Periodicidade das Setas (`estimate_cell_size`, `build_grid_arrows`)**: Quando o tabuleiro está cheio de setas e sem pontos cinzentos visíveis, o tamanho das células é estimado calculando a autocorrelação das projeções horizontais e verticais das próprias setas.
3. **Máscara de Primeiro Plano (`foreground_mask`)**:
   - Analisa tanto a luminosidade (para setas pretas/escuras) como a saturação de cor (para setas coloridas), isolando o corpo/cabeça das setas do fundo.
4. **Deteção de Cabeças (`detect_head_pixels`, `detect_arrowheads`)**:
   - Aplica a Transformada de Distância L2 à máscara de setas (a cabeça é mais espessa do que o corpo).
   - Localiza picos locais e aplica correlação de modelos (`cv2.matchTemplate`) com triângulos nas 4 direções (`U`, `D`, `L`, `R`) para identificar a orientação exata.
5. **Classificação de Células (`build_occupancy`)**:
   - Classifica cada célula da grelha em um de três estados:
     - `EMPTY` (0): Célula vazia.
     - `OCCUPIED` (1): Célula contendo corpo ou cabeça de seta (calculado pela densidade no interior ou no centro da célula).
     - `UNKNOWN` (2): Célula fora da área visível do ecrã.

### 3.3. Algoritmo de Resolução (`solver.py`)
- **Princípio da Jogabilidade (`is_playable`)**: Uma seta é jogável se a linha reta a partir da sua cabeça até à borda do tabuleiro (na direção da cabeça) estiver inteiramente livre.
- **Regra de Segurança**: Se o raio de varredura encontrar uma célula com valor `OCCUPIED` ou `UNKNOWN`, a seta é considerada não jogável. Isso evita qualquer toque em setas bloqueadas (prevenindo a perda de vidas no jogo).

### 3.4. Reconstrução de Tabuleiros Gigantes (`stitch.py` & `grid_stitch.py`)
Quando o tabuleiro excede a área visível do ecrã, o bot utiliza técnicas de *stitching*:

1. **`stitch.py` (Stitching de Imagem/Píxeis)**:
   - Executa uma varredura 2D (raster scan) com deslizes ADB.
   - Utiliza *template matching* na zona de sobreposição para estimar o deslocamento contínuo em píxeis.
   - Aplica *snap* dos deslocamentos à fase da grelha para evitar desvios acumulados.

2. **`grid_stitch.py` (Stitching Discreto de Símbolos)**:
   - Abordagem de alto nível que converte cada imagem numa matriz discreta de símbolos (`?`, `.`, `#`, `^`, `v`, `<`, `>`).
   - Alinha duas grelhas através da procura do translação discreta `(dr, dc)` que maximiza o rácio de concordância em células informativas (`score = agree / support`).
   - Permite o merge de múltiplos frames com votação por célula e um processo de refinamento iterativo (`refine_positions`) para alinhar cada frame contra o modelo global.

### 3.5. Automação e Loops de Jogo (`bot.py` & `play_grid.py`)
- **`bot.py`**:
  - Controla a execução automática nível a nível.
  - Avalia se o tabuleiro cabe no ecrã ou se exige *stitching*.
  - Aplica validação de estabilidade de imagem (`wait_for_stable`) antes de decidir jogadas.
  - Carrega e respeita a máscara de segurança de toques (`imgs/mask/mask.png`), garantindo que o bot nunca toca na barra superior da UI ou botões de dicas.
- **`play_grid.py`**:
  - Implementa a navegação ativa no modelo simbólico global.
  - Localiza em tempo real em que parte do modelo global se encontra a vista visível do telemóvel, faz *pan* até à seta jogável, executa o toque na cabeça detetada e confirma o desaparecimento da seta.

---

## 4. Testes e Validação

O projeto inclui um conjunto de testes unitários localizados na pasta `tests/`:
- **`test_solver.py`**: Valida exaustivamente os cenários do solver:
  - Caminho desimpedido até à borda.
  - Bloqueio por outra seta.
  - Bloqueio por células desconhecidas (`UNKNOWN`).
  - Não bloqueio por células de padding/margem.
  - Setas na extremidade do tabuleiro.
  - Ordenação correta das jogadas jogáveis.

*Resultado da verificação*: Todos os 6 testes unitários são executados com sucesso (status `OK`).

---

## 5. Resumo das Capacidades do Bot

1. **Automação Completa**: Capaz de jogar níveis simples e níveis gigantes de forma totalmente autónoma.
2. **Resiliência a Cores**: Reconhece setas pretas e setas coloridas graças à análise conjunta de saturação e luminância.
3. **Tolerância a Tabuleiros Sem Pontos**: Consegue estimar o reticulado através da periodicidade do padrão das setas quando o tabuleiro está 100% cheio.
4. **Proteção Anti-Penalização**: Garante 0% de toques acidentais em setas bloqueadas e evita toques fora do tabuleiro através da `tap_mask`.
