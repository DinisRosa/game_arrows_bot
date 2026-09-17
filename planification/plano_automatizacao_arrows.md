# Plano de automatização do jogo Arrows no Android

## 1. Objetivo

Criar um programa executado no PC que jogue automaticamente o jogo móvel **Arrows** num telemóvel Android.

O programa deverá:

1. Capturar o estado atual do jogo através do ecrã do telemóvel.
2. Identificar a grelha, as setas e a direção de cada seta.
3. Determinar quais setas podem ser removidas.
4. Calcular uma ordem válida de jogadas.
5. Enviar toques automaticamente para o telemóvel.
6. Repetir o processo até completar o nível.

O objetivo é criar um agente de visão computacional e automação capaz de resolver o jogo sozinho, e não apenas controlar manualmente o telemóvel pelo PC.

## 2. Comunicação com o telemóvel

A ligação prevista é feita por **USB**, utilizando **ADB — Android Debug Bridge**.

### Requisitos

- Telemóvel Android.
- Cabo USB com transmissão de dados.
- Android Platform Tools instalado no PC.
- Opções de programador ativadas.
- Depuração USB ativada.
- Autorização do computador no telemóvel.

Testar a ligação com:

```bash
adb devices
```

O dispositivo deverá aparecer com o estado `device`.

## 3. Controlo do telemóvel

### scrcpy

O `scrcpy` pode espelhar o ecrã do Android numa janela do PC e permite controlar o telemóvel com rato e teclado, normalmente sem root. É útil para observar e testar manualmente o jogo.

```bash
scrcpy
```

### ADB diretamente

Para a automação final, é preferível enviar os toques diretamente por ADB:

```bash
adb shell input tap X Y
```

Exemplo:

```bash
adb shell input tap 540 1200
```

Implementação Python:

```python
import subprocess
import time


def tocar(x, y):
    subprocess.run(
        ["adb", "shell", "input", "tap", str(x), str(y)],
        check=True,
    )
    time.sleep(0.15)
```

O scrcpy pode continuar a ser utilizado para depuração visual.

## 4. Captura do ecrã

Capturar uma screenshot através do ADB:

```bash
adb exec-out screencap -p > screen.png
```

Implementação Python:

```python
import subprocess


def capturar_ecra(path="screen.png"):
    with open(path, "wb") as ficheiro:
        subprocess.run(
            ["adb", "exec-out", "screencap", "-p"],
            stdout=ficheiro,
            check=True,
        )
```

A imagem poderá ser processada com Python e OpenCV.

## 5. Funcionamento presumido do jogo

O jogo contém várias setas numa grelha. Cada seta aponta para uma direção:

- Cima.
- Baixo.
- Esquerda.
- Direita.

Uma seta pode ser removida quando o caminho desde a sua posição até à borda do tabuleiro está livre. Se existir outra seta no caminho, a seta atual está bloqueada.

A regra principal é:

> Uma seta está disponível se não existir outra seta entre ela e a borda na direção para que aponta.

Depois de remover uma seta, outras setas podem ficar desbloqueadas.

Esta regra deve ser confirmada na versão concreta do jogo, pois podem existir obstáculos, power-ups, vidas ou outras mecânicas.

## 6. Representação lógica do tabuleiro

O tabuleiro pode ser representado como uma matriz Python:

```python
board = [
    [None, "R", None, "D"],
    ["U", "L", "R", None],
    [None, "D", "U", "L"],
]
```

Convenção das direções:

- `"U"` — cima.
- `"D"` — baixo.
- `"L"` — esquerda.
- `"R"` — direita.
- `None` — célula vazia.

Cada seta deverá ter, no mínimo:

- Linha.
- Coluna.
- Direção.
- Coordenadas do centro da célula no ecrã.

## 7. Verificação de caminho livre

```python
def caminho_livre(board, row, col, direction):
    drdc = {
        "U": (-1, 0),
        "D": (1, 0),
        "L": (0, -1),
        "R": (0, 1),
    }

    dr, dc = drdc[direction]
    row += dr
    col += dc

    while 0 <= row < len(board) and 0 <= col < len(board[0]):
        if board[row][col] is not None:
            return False
        row += dr
        col += dc

    return True
```

Depois de remover uma seta, a célula correspondente passa a `None`.

## 8. Algoritmo de resolução

O algoritmo básico deverá:

1. Capturar o ecrã.
2. Reconhecer o tabuleiro.
3. Verificar se o nível terminou.
4. Encontrar setas com caminho livre.
5. Escolher uma seta.
6. Calcular o centro da respetiva célula.
7. Enviar um toque por ADB.
8. Aguardar a animação.
9. Repetir.

Pseudocódigo:

```python
while True:
    screenshot = capturar_ecra()
    board = reconhecer_tabuleiro(screenshot)

    if tabuleiro_concluido(board):
        break

    jogadas = encontrar_setas_livres(board)

    if not jogadas:
        print("Nenhuma jogada encontrada")
        break

    row, col = jogadas[0]
    x, y = centro_da_celula(row, col)
    tocar(x, y)
```

Função inicial para encontrar jogadas:

```python
def encontrar_setas_livres(board):
    jogadas = []

    for row in range(len(board)):
        for col in range(len(board[row])):
            direction = board[row][col]

            if direction is not None:
                if caminho_livre(board, row, col, direction):
                    jogadas.append((row, col))

    return jogadas
```

Uma primeira versão pode escolher a primeira seta disponível. Mais tarde, pode ser implementada uma estratégia de seleção mais robusta.

## 9. Reconhecimento visual

Esta é provavelmente a parte mais difícil do projeto: transformar uma screenshot numa representação lógica da grelha.

### 9.1 Grelha fixa

Abordagem recomendada para o primeiro protótipo:

1. Determinar manualmente os limites da grelha.
2. Definir o número de linhas e colunas.
3. Calcular o centro de cada célula.
4. Cortar cada célula da imagem.
5. Determinar se está vazia.
6. Se contiver uma seta, determinar a sua orientação.

### 9.2 Deteção automática

Numa versão mais avançada, usar OpenCV para identificar:

- Limites do tabuleiro.
- Contornos.
- Cores das setas.
- Forma das setas.
- Orientação.
- Células ocupadas.

A deteção automática será mais robusta para diferentes resoluções, escalas e posições, mas também mais complexa.

## 10. Estratégia de desenvolvimento

### Fase 1 — Ambiente

- Instalar ADB.
- Ativar a depuração USB.
- Confirmar `adb devices`.
- Testar `adb shell input tap`.
- Testar a captura de screenshots.
- Instalar e testar scrcpy.

### Fase 2 — Controlo manual

- Abrir o jogo no telemóvel.
- Executar scrcpy.
- Confirmar que os cliques correspondem a toques.
- Determinar a resolução do dispositivo:

```bash
adb shell wm size
```

- Registar as coordenadas da área do tabuleiro.

### Fase 3 — Reconhecimento básico

- Tirar uma screenshot.
- Recortar a área do tabuleiro.
- Dividir a área em células.
- Guardar células individuais para análise.
- Implementar o reconhecimento de células vazias e setas.

### Fase 4 — Solver

- Representar o tabuleiro numa matriz.
- Implementar `caminho_livre`.
- Implementar `encontrar_setas_livres`.
- Testar o solver com tabuleiros simulados.
- Verificar se encontra sequências válidas.

### Fase 5 — Integração com ADB

- Converter `(linha, coluna)` em coordenadas `(x, y)`.
- Enviar `adb shell input tap`.
- Aguardar a animação.
- Capturar uma nova imagem.
- Comparar o estado antes e depois da jogada.

### Fase 6 — Robustez

Adicionar:

- Deteção de fim de nível.
- Deteção de falha ou jogada inválida.
- Repetição de screenshots quando a imagem estiver instável.
- Ajuste a diferentes resoluções.
- Limite máximo de jogadas.
- Registo detalhado das decisões.
- Modo de simulação sem tocar no telemóvel.
- Interrupção manual segura.

## 11. Ciclo final esperado

```text
Iniciar programa
    ↓
Verificar ligação ADB
    ↓
Capturar screenshot
    ↓
Detetar área e grelha
    ↓
Reconhecer setas e direções
    ↓
Verificar se o nível terminou
    ↓
Encontrar setas desbloqueadas
    ↓
Escolher uma seta
    ↓
Calcular centro da célula
    ↓
Enviar toque por ADB
    ↓
Aguardar animação
    ↓
Voltar a capturar screenshot
```

## 12. Questões a confirmar

Antes da implementação final, confirmar:

- Qual é exatamente a aplicação instalada.
- Se a grelha tem sempre o mesmo número de linhas e colunas.
- Se as setas ocupam uma célula por completo.
- Se existem setas sobrepostas, obstáculos ou elementos especiais.
- Se o tabuleiro é sempre retangular.
- Se a seta desaparece imediatamente ou tem animação.
- Se uma seta bloqueada pode ser tocada sem penalização.
- Se existe publicidade ou pop-ups.
- Qual é a resolução e orientação do telemóvel.

## 13. Resultado pretendido

O resultado final deverá ser um programa Python capaz de:

- Ligar-se ao Android por ADB.
- Capturar o ecrã.
- Reconhecer automaticamente o tabuleiro.
- Identificar a orientação das setas.
- Resolver a sequência de remoção.
- Clicar nas coordenadas adequadas.
- Confirmar visualmente o progresso.
- Completar níveis com intervenção humana mínima.

A implementação deve começar com um protótipo controlado e observável, utilizando screenshots guardadas, uma grelha fixa e reconhecimento simples. Só depois de o solver funcionar corretamente em tabuleiros simulados deverá ser integrada a automação real no jogo.
