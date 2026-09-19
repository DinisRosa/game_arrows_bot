# Análise Completa do Projeto Auto-ARROWS

## 1. Visão Geral do Projeto

O **Auto-ARROWS** é uma solução automatizada em Python concebida para jogar autonomamente o jogo mobile **Arrows** (Android) através da interface **ADB** (Android Debug Bridge).

O sistema combina técnicas de visão computacional (OpenCV/NumPy), reconstrução e alinhamento de tabuleiros de grande dimensão (*stitching*), um algoritmo determinístico de resolução de jogadas (*solver*) e mecanismos de automação física de toques em lote (*ADB batch touch & swipe*), incluindo salvaguardas para evitar penalizações ou toques acidentais no jogo.

---

## 2. Estrutura de Ficheiros e Componentes

| Ficheiro / Diretoria | Responsabilidade Principal |
| :--- | :--- |
| [adb_client.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/adb_client.py) | Camada de abstração de baixo nível para ADB. Utiliza **processo ADB Shell persistente (`_get_shell()`)** e suporte a **toques em lote (`tap_batch`)** com re-tentativas automáticas contra buffers PNG incompletos. |
| [capture.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/capture.py) | Captura otimizada de imagens. Inclui leitura contínua H.264 em segundo plano (*daemon thread drain loop*) a ~20–30 ms com fallback automático para `screencap` nativo (~2400 ms). |
| [vision.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/vision.py) | Motor de visão computacional: deteção de grelhas, cálculo de fase/espaçamento, máscaras de cor, deteção de cabeças validada por ocupação (`inside_board` + `OCCUPIED`), **filtragem de margens de UI (`top_margin=400`, `bottom_margin=300`)** e matriz de ocupação. |
| [solver.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/solver.py) | Algoritmo determinístico que valida se uma seta pode sair do tabuleiro sem colidir com outras setas ou áreas desconhecidas. |
| [stitch.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/stitch.py) | *Stitching* contínuo com paragem imediata nas bordas físicas, escaneamento inteligente com paragem antecipada (`stop_heads_count`), função `pan_to_top_left` e desativação de gravação de imagens PNG por omissão (`save_images=False`). |
| [grid_stitch.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/grid_stitch.py) | *Stitching* discreto ao nível de símbolos (`FrameGrid`), fusão por votação, suporte a tabuleiros com poucas setas (`min_support=2`) e refinamento de posição. |
| [bot.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/bot.py) | Script principal de execução para níveis standard de ecrã único, suportando **rajadas de toques ultra-rápidas em lote (`burst_taps`)**, simulação local em cascata e opções de depuração (`--save-debug`). |
| [play_grid.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/play_grid.py) | Motor avançado para jogar tabuleiros gigantes com modelo de grelha discreta, relocalização em zonas limpas (`min_support=2`), toques imediatos e tratamento seguro de nulos (`alignment is None`). |
| [calibrate.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/calibrate.py) | Ferramenta interativa com interface gráfica OpenCV para calibração manual/automática da grelha do tabuleiro. |
| [test_tap.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/test_tap.py) | Utilitário interativo para teste e verificação do mapeamento de coordenadas de toques ADB entre o PC e o telemóvel. |
| [requirements.txt](file:///home/dinisrosa22/Projects/Auto-ARROWS/requirements.txt) | Dependências externas do projeto (`numpy`, `opencv-python`, `pillow`). |
| [tests/test_solver.py](file:///home/dinisrosa22/Projects/Auto-ARROWS/tests/test_solver.py) | Conjunto de testes unitários para validação das regras do `solver.py`. |
| [docs/optimization/](file:///home/dinisrosa22/Projects/Auto-ARROWS/docs/optimization) | Documentação técnica detalhada das otimizações de velocidade e captura de vídeo H.264 stream. |

---

## 3. Arquitetura Técnica e Módulos em Detalhe

### 3.1. Comunicação e Captura (`adb_client.py` & `capture.py`)
- **`adb_client.py`**: Interage com o Android ADB.
  - `_get_shell()`: Mantém uma sessão de `adb shell` persistente em segundo plano.
  - `tap_batch(coords)`: Envia dezenas de toques de uma só vez numa única stream ADB, reduzindo o tempo de envio para menos de 1 ms.
  - `screenshot(retries=3)`: Captura a imagem BGR nativa do dispositivo com sistema de re-tentativas automáticas contra buffers PNG incompletos.
- **`capture.py`**:
  - `Stream` com *Continuous Drain*: Thread em segundo plano drena continuamente a FIFO H.264 de modo a garantir frames atualizados (20–30 ms) sem travamentos/frames congelados.
  - `get_frame(prefer_stream=False)`: Garante compatibilidade total. Caminho por defeito usa `screencap` seguro para decisões do jogo (`bot.py`, `play_grid.py`); passa `prefer_stream=True` no *stitching* (`stitch.py`), reduzindo a latência do stitching em ~70%.

### 3.2. Visão Computacional (`vision.py`)
O módulo `vision.py` é o núcleo de perceção do bot:
1. **Estrutura `Grid`**: Define o mapeamento entre células lógicas `(row, col)` e coordenadas de píxeis no dispositivo `(x, y)`.
2. **Deteção de Grelha**:
   - **Pontos Cinzentos (`dots_mask`, `detect_dots`)**: Deteta os pontos de interseção da grelha expostos no fundo branco.
   - **Periodicidade das Setas (`estimate_cell_size`, `build_grid_arrows`)**: Estima a dimensão das células via autocorrelação de projeções.
3. **Deteção de Cabeças de Setas Validada (`detect_arrowheads`)**:
   - Aplica a Transformada de Distância L2 e correlação de modelos (`cv2.matchTemplate`).
   - **Validação de Ocupação**: Exige rigorosamente `grid.inside_board(row, col)` e `occupancy[row, col] == OCCUPIED`, eliminando falsas deteções em áreas vazias ou fora da grelha.
   - **Filtragem de Margens de UI**: Descartam-se picos na margem superior (`top_margin = 400`) e inferior (`bottom_margin = 300`).

### 3.3. Algoritmo de Resolução (`solver.py`)
- **Princípio da Jogabilidade (`is_playable`)**: Uma seta é jogável se a linha reta a partir da sua cabeça até à borda do tabuleiro estiver inteiramente livre de outras setas ou células desconhecidas.
- **Segurança**: Células fora do tabuleiro ou com estado `UNKNOWN` bloqueiam o movimento.

### 3.4. Reconstrução e Escaneamento Inteligente (`stitch.py` & `grid_stitch.py`)
1. **`stitch.py` (Stitching de Imagem/Píxeis)**:
   - **Varrimento Center-Out (`capture_grid_centered`)**: Começa a partir da origem `(0, 0)` com pequenos deslizes em cruz/estrela. Reduz o número de deslizes para apenas 4-8.
   - **Smart Stitching (`stop_heads_count`)**: Interrompe antecipadamente o escaneamento assim que todas as setas restantes no tabuleiro forem identificadas nos primeiros frames.
   - **Otimização de I/O**: `save_frame_grids` por omissão não escreve ficheiros PNG no disco (`save_images=False`), evitando custos desnecessários de I/O em produção.
2. **`grid_stitch.py` (Stitching Discreto de Símbolos)**:
   - Converte cada imagem numa matriz discreta de símbolos (`?`, `.`, `#`, `^`, `v`, `<`, `>`).
   - Alinha grelhas com suporte adaptativo (`min_support = 2`).

### 3.5. Automação e Execução Ultra-Rápida (`bot.py` & `play_grid.py`)
- **`bot.py`**:
  - Executa níveis normais de ecrã único.
  - **Burst & Local Cascading**: Simula localmente a desobstrução de setas em cascata e dispara toques em lote (`tap_batch`), completando níveis em 1-2 segundos.
  - **Depuração Visual (`--save-debug`)**: Guarda capturas de ecrã anotadas com o mapa da grelha, deteção de cabeças (círculos azuis) e toques efetuados (círculos vermelhos) na pasta `moves/`.
- **`play_grid.py`**:
  - Motor para tabuleiros gigantes que utiliza alinhamento célula-a-célula e suporta re-stitching rápido com captura otimizada.

---

## 4. Testes e Validação

O projeto inclui testes unitários localizados na pasta `tests/`:
- **`test_solver.py`**: Valida os cenários do solver.

*Resultado da verificação*: Todos os 6 testes unitários são executados com sucesso (status `OK`).

---

## 5. Resumo dos Comandos Úteis

| Ação | Comando |
| :--- | :--- |
| **Jogar nível gigante do zero (rápido)** | `./venv/bin/python play_grid.py --recapture --delay 0` |
| **Jogar nível standard em rajada ultra-rápida** | `./venv/bin/python bot.py --delay 0` |
| **Jogar com registo de depuração visual** | `./venv/bin/python bot.py --delay 0.1 --save-debug` |
| **Jogar níveis em sequência automática** | `./venv/bin/python bot.py --all --delay 0` |
| **Executar testes unitários** | `./venv/bin/python -m unittest discover tests` |
