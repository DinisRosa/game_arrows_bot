# Análise Completa do Projeto Auto-ARROWS

## 1. Visão Geral do Projeto

O **Auto-ARROWS** é uma solução automatizada em Python concebida para jogar autonomamente o jogo mobile **Arrows** (Android) através da interface **ADB** (Android Debug Bridge).

O sistema combina técnicas de visão computacional (OpenCV/NumPy), reconstrução e alinhamento de tabuleiros de grande dimensão (*stitching*), um algoritmo determinístico de resolução de jogadas (*solver*) e mecanismos de automação física de toques/gestos (*ADB touch & swipe*), incluindo salvaguardas rigorosas para evitar penalizações no jogo.

---

## 2. Estrutura de Ficheiros e Componentes

| Ficheiro / Diretoria | Responsabilidade Principal |
| :--- | :--- |
| [adb_client.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/adb_client.py) | Camada de abstração de baixo nível para ADB. Utiliza **processo ADB Shell persistente (`_get_shell()`)** para envio de gestos instantâneos (0.0 ms). |
| [capture.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/capture.py) | Captura otimizada de imagens em resolução nativa sem desfocagem (`screencap` com fallback para stream). |
| [vision.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/vision.py) | Motor de visão computacional: deteção de grelhas, cálculo de fase/espaçamento, máscaras de cor, deteção de cabeças com **filtragem de margens de UI (`top_margin=400`, `bottom_margin=300`)** e matriz de ocupação. |
| [solver.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/solver.py) | Algoritmo determinístico que valida se uma seta pode sair do tabuleiro sem colidir com outras setas ou áreas desconhecidas. |
| [stitch.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/stitch.py) | *Stitching* contínuo de píxeis, função `pan_to_top_left` para regresso ao canto superior esquerdo e `clear_dir` para limpeza total da cache de memória. |
| [grid_stitch.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/grid_stitch.py) | *Stitching* discreto ao nível de símbolos (`FrameGrid`), fusão por votação, suporte a tabuleiros com poucas setas (`min_support=2`) e refinamento de posição. |
| [bot.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/bot.py) | Script principal de execução do bot para níveis standard (suporta `--all`, `--delay 0` e simulação). |
| [play_grid.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/play_grid.py) | Motor avançado para jogar tabuleiros gigantes com modelo de grelha discreta, suporte a relocalização em zonas limpas (`min_support=2`), toques imediatos sem scroll desnecessário e gravação de **imagens de debug anotadas**. |
| [calibrate.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/calibrate.py) | Ferramenta interativa com interface gráfica OpenCV para calibração manual/automática da grelha do tabuleiro. |
| [test_tap.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/test_tap.py) | Utilitário interativo para teste e verificação do mapeamento de coordenadas de toques ADB entre o PC e o telemóvel. |
| [requirements.txt](file:///home/dinisrosa22/Projects/Auto-ARROWS/requirements.txt) | Dependências externas do projeto (`numpy`, `opencv-python`, `pillow`). |
| [tests/test_solver.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/tests/test_solver.py) | Conjunto de testes unitários para validação das regras do `solver.py`. |
| [docs/](file:///home/dinisrosa22/Projects/Auto-ARROWS/docs) | Documentação de arquitetura, raciocínio de loop principal, análise do projeto e otimizações de velocidade. |

---

## 3. Arquitetura Técnica e Módulos em Detalhe

### 3.1. Comunicação e Captura (`adb_client.py` & `capture.py`)
- **`adb_client.py`**: Interage com o Android ADB.
  - `_get_shell()`: Mantém uma sessão de `adb shell` persistente em segundo plano. Os comandos `tap(x, y)` e `swipe(x1, y1, x2, y2)` escrevem diretamente na stream de input do processo aberto, executando gestos de forma **instantânea (0.0 ms)**.
  - `ensure_device()`: Garante que existe exatamente um dispositivo Android ligado e autorizado.
  - `screenshot()`: Captura a imagem BGR nativa do dispositivo.
- **`capture.py`**:
  - `get_frame()`: Fornece imagens límpidas em alta definição nativa (1220x2712), garantindo máxima fiabilidade no cálculo de posições.

### 3.2. Visão Computacional (`vision.py`)
O módulo `vision.py` é o núcleo de perceção do bot:
1. **Estrutura `Grid`**: Define o mapeamento entre células lógicas `(row, col)` e coordenadas de píxeis no dispositivo `(x, y)`.
2. **Deteção de Grelha**:
   - **Pontos Cinzentos (`dots_mask`, `detect_dots`)**: Deteta os pontos de interseção da grelha expostos no fundo branco.
   - **Periodicidade das Setas (`estimate_cell_size`, `build_grid_arrows`)**: Quando o tabuleiro está cheio de setas e sem pontos cinzentos visíveis, o tamanho das células é estimado calculando a autocorrelação das projeções horizontais e verticais das próprias setas.
3. **Máscara de Primeiro Plano (`foreground_mask`)**:
   - Analisa a luminosidade e a saturação de cor para detetar setas pretas e coloridas.
4. **Deteção de Cabeças (`detect_head_pixels`, `detect_arrowheads`)**:
   - Aplica a Transformada de Distância L2 e correlação de modelos (`cv2.matchTemplate`) com triângulos nas 4 direções (`U`, `D`, `L`, `R`).
   - **Filtragem de Margens de UI**: Descartam-se obrigatoriamente picos localizados na margem superior (`top_margin = 400`) e inferior (`bottom_margin = 300`), evitando falsas deteções na barra de interface (corações, pausa, botão de reiniciar).
5. **Classificação de Células (`build_occupancy`)**:
   - Classifica cada célula em `EMPTY` (0), `OCCUPIED` (1) ou `UNKNOWN` (2).

### 3.3. Algoritmo de Resolução (`solver.py`)
- **Princípio da Jogabilidade (`is_playable`)**: Uma seta é jogável se a linha reta a partir da sua cabeça até à borda do tabuleiro estiver inteiramente livre de outras setas ou células desconhecidas.
- **Regra de Segurança**: Se o raio de varredura encontrar `OCCUPIED` ou `UNKNOWN`, a seta é considerada não jogável (evitando perda de vidas).

### 3.4. Reconstrução de Tabuleiros Gigantes (`stitch.py` & `grid_stitch.py`)
1. **`stitch.py` (Stitching de Imagem/Píxeis)**:
   - Executa uma varredura 2D (raster scan).
   - Inclui a função `pan_to_top_left` para regressar ao topo esquerdo e `clear_dir` para eliminar a 100% o cache de imagens/modelos antigos antes de uma nova reconstrução.
2. **`grid_stitch.py` (Stitching Discreto de Símbolos)**:
   - Converte cada imagem numa matriz discreta de símbolos (`?`, `.`, `#`, `^`, `v`, `<`, `>`).
   - Alinha grelhas com suporte adaptativo (`min_support = 2`), permitindo unir frames mesmo quando restam poucas setas no tabuleiro.

### 3.5. Automação e Loops de Jogo (`bot.py` & `play_grid.py`)
- **`bot.py`**:
  - Executa níveis normais ou contínuos (`--all`), suportando parâmetro de velocidade máxima `--delay 0`.
- **`play_grid.py`**:
  - Motor para tabuleiros gigantes.
  - Se a seta a jogar já estiver visível dentro dos limites seguros do ecrã (`0 <= local_c < frame_grid.cols`), executa o toque **imediatamente sem efetuar scroll desnecessário**.
  - Grava imagens anotadas de debug antes e depois de cada movimento em `imgs/moves/play_move_XXX_before.png` e `after.png`.

---

## 4. Testes e Validação

O projeto inclui um conjunto de testes unitários localizados na pasta `tests/`:
- **`test_solver.py`**: Valida exaustivamente os cenários do solver.

*Resultado da verificação*: Todos os 6 testes unitários são executados com sucesso (status `OK`).

---

## 5. Resumo dos Comandos Úteis

| Ação | Comando |
| :--- | :--- |
| **Jogar nível gigante do zero** | `./venv/bin/python play_grid.py --recapture` |
| **Jogar nível gigante na velocidade máxima** | `./venv/bin/python play_grid.py --recapture --delay 0` |
| **Jogar nível standard de ecrã único** | `./venv/bin/python bot.py --delay 0` |
| **Jogar níveis em sequência automática** | `./venv/bin/python bot.py --all --delay 0` |
| **Simular jogadas (sem tocar)** | `./venv/bin/python bot.py --simulate` |
