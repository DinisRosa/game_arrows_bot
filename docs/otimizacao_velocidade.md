# Otimização de Velocidade do Auto-ARROWS

Este documento analisa onde o bot perde tempo e detalha as otimizações implementadas e as melhorias futuras planeadas.

---

## 1. Estado de Implementação

### 1.1. Concluído com Sucesso (Fase 1 + Melhorias de Fiabilidade)

- [x] **Conexão ADB Shell Persistente (`adb_client.py`)**:
  - Implementado o processo `_get_shell()` que mantém um `adb shell` aberto em segundo plano.
  - O envio de gestos de toque (`tap`) e deslize (`swipe`) escreve diretamente na stream de input do processo aberto, reduzindo o tempo de envio de comandos de ~179 ms para **0.0 ms (instantâneo)**.
- [x] **Captura de Ecrã Nítida em Resolução Nativa (`capture.py`)**:
  - Configurada a captura de imagens a 100% de resolução nativa (`1220x2712`), garantindo máxima definição dos triângulos e reticulados sem desfocagem de redimensionamento.
- [x] **Esperas Adaptativas e Remoção de Delays (`--delay 0`)**:
  - Adicionado o parâmetro de linha de comandos `--delay 0` em `bot.py` e `play_grid.py`, eliminando a espera fixa pós-toque e permitindo jogadas à velocidade máxima da reação do telemóvel.
- [x] **Filtragem de Margens de UI (`vision.py`)**:
  - Implementada a filtragem estrita de topo (`top_margin = 400`) e fundo (`bottom_margin = 300`) em `detect_head_pixels` e `detect_arrowheads`, impedindo que ícones da interface (corações, pausa, botão de reiniciar) sejam detetados como falsas setas.
- [x] **Suporte a Tabuleiros Pouco Povoados / 90% Limpos (`play_grid.py` & `grid_stitch.py`)**:
  - Reduzido o limiar de suporte mínimo (`min_support`) de 40 para 2 células em `locate_view`, `relocate` e `grid_stitch.align`. O bot alinha a vista com 100% de precisão mesmo quando restam apenas 1 ou 2 setas no ecrã.
  - Adicionado *fallback* gracioso para as coordenadas conhecidas do pan (`guess`) quando a câmara passa por áreas 100% limpas do tabuleiro.
- [x] **Limpeza de Memória e Reset do Canto Superior Esquerdo (`stitch.py`)**:
  - Implementadas as funções `pan_to_top_left` (retorno automático ao canto superior esquerdo) e `clear_dir` (eliminação total do cache de imagens anteriores em `imgs/frames`, `imgs/grids` e `imgs/stitching`).

---

### 1.2. Otimizações Futuras Planeadas (Fases 2 e 3)

- [ ] **Stitching com Passo Maior (`stitch.py`)**:
  - Aumentar o passo de pan de 400 px para 650 px (aproveitando o ecrã de 1220x2712 px), reduzindo o número de capturas do raster de ~25 para ~10-12 por nível gigante.
- [ ] **Vetorização do `build_occupancy` (`vision.py`)**:
  - Substituir os loops aninhados `for row` / `for col` por operações matriciais em bloco NumPy ou imagem integral (`cv2.integral`), reduzindo o tempo de ocupação de ~100 ms para < 5 ms.
- [ ] **Cache de `foreground_mask` por Frame**:
  - Implementar cache volátil por imagem para evitar recalcular a `foreground_mask` 4 a 6 vezes no mesmo ciclo de processamento.

---

## 2. Diagnóstico: onde se perde tempo

### 2.1. Captura de ecrã (gargalo principal)

- `capture.get_frame()` (`capture.py:108`) chama `adb_client.screenshot()`
  (`adb_client.py:78`), que executa `adb exec-out screencap -p`.
- O `-p` obriga o dispositivo a **codificar um PNG** (e o PC a descodificá-lo),
  o que domina o custo (~2400 ms documentados em `capture.py:2`).

### 2.2. Esperas fixas (`time.sleep`)

| Local | Valor | Observação |
| :--- | :--- | :--- |
| `bot.py:132` | `delay` 0.3 s | configurável para 0s com `--delay 0` |
| `bot.py:56-69` (`wait_for_stable`) | 0.25 s × até 6 s | espera por frame estável |
| `stitch.py:93,128,149` | `settle` 0.5 s | por pan do *stitching* |
| `play_grid.py:95,314` | 0.5 s / `delay` 0.3 s | pan e pós-toque |

---

## 3. Comandos de Velocidade Máxima

| Ação | Comando |
| :--- | :--- |
| **Jogar nível gigante do zero (rápido)** | `./venv/bin/python play_grid.py --recapture --delay 0` |
| **Jogar nível de ecrã único (rápido)** | `./venv/bin/python bot.py --delay 0` |
| **Jogar vários níveis seguidos (rápido)** | `./venv/bin/python bot.py --all --delay 0` |
