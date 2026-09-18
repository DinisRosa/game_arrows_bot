# Otimização de Velocidade do Auto-ARROWS

Este documento detalha o plano de otimização de velocidade do bot e as funcionalidades implementadas para atingir performance máxima.

---

## 1. Estado de Implementação

### 1.1. Concluído com Sucesso

- [x] **Toques em Lote Stream ADB (`adb_client.tap_batch`)**:
  - Implementada a função `tap_batch` que envia dezenas de toques numa única stream contínua para o `adb shell`, reduzindo o tempo de envio de múltiplos gestos para < 1 ms.
- [x] **Rajadas de Toques e Cascata Local (`bot.py`)**:
  - O bot calcula todas as setas livres num único frame e simula localmente a desobstrução de setas em cascata.
  - Dispara todas as jogadas calculadas numa rajada instantânea sem esperar pelas animações do jogo. Níveis normais são limpos em **1 a 2 segundos**.
- [x] **Smart Stitching & Paragem Antecipada (`stitch.py`)**:
  - Deteção imediata dos limites físicos da grelha (`abs(dx)<5` e `abs(dy)<5`), eliminando deslizes desnecessários contra as paredes do ecrã.
  - Paragem antecipada do escaneamento (`stop_heads_count`) quando restam poucas setas no tabuleiro: interrompe a varredura assim que as setas restantes são detetadas nos primeiros frames.
- [x] **Eliminação de Gravamento de Imagens em Execução Rápida**:
  - Escrita de imagens PNG no disco desativada por omissão na execução de alta velocidade (`--delay 0`), evitando perda de tempo com I/O de disco.
- [x] **Resiliência ADB e Re-tentativas de Captura (`adb_client.py`)**:
  - Sistema de *retries* automático contra buffers de imagem PNG truncados (`libpng error`).
- [x] **Filtragem Estrita de Cabeças Falsas (`vision.py`)**:
  - Validação rigorosa `grid.inside_board` + `occupancy == OCCUPIED`, impedindo toques acidentais em áreas limpas ou fora da grelha.

---

## 2. Comandos de Velocidade Máxima

| Ação | Comando |
| :--- | :--- |
| **Jogar nível standard em rajada ultra-rápida** | `./venv/bin/python bot.py --delay 0` |
| **Jogar nível gigante com smart-stitching** | `./venv/bin/python play_grid.py --recapture --delay 0` |
| **Jogar níveis em sequência automática** | `./venv/bin/python bot.py --all --delay 0` |

