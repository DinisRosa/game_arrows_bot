# Plano de Otimização de Velocidade do Stitching — Auto-ARROWS

> Documento baseado em leitura direta do código na branch `improvements`
> (`stitch.py`, `grid_stitch.py`, `capture.py`, `adb_client.py`, `vision.py`)
> e no histórico de commits. Complementa `docs/otimizacao_velocidade.md`,
> focando-se especificamente na fase de **stitching** (reconstrução de
> tabuleiros grandes por múltiplos frames).

---

## 1. Onde o tempo do stitching está a ser gasto hoje

Cada "passo" do varrimento (`_scan` em `stitch.py`, ou um braço do
`capture_grid_centered`) faz sempre a mesma sequência:

```
_pan()  →  time.sleep(settle)  →  capture.get_frame()  →  _shift()  →  [deteção opcional de heads]
```

Custo aproximado por passo, com os valores por omissão do `capture_grid_centered`
(`step=500`, `settle=0.4`, `duration=280ms` no swipe):

| Etapa | Custo típico | Fonte |
|---|---|---|
| `_pan` (swipe) | ~280 ms (duração do gesto) | `stitch._pan`, duration=280 |
| `time.sleep(settle)` | 400–500 ms | `capture_grid_centered`/`capture_grid` |
| `capture.get_frame()` | **~2.400 ms** | docstring de `capture.py`: *"screencap... ~2400 ms"* |
| `_shift` (template match) | alguns ms | `cv2.matchTemplate` numa patch pequena |
| Deteção de heads p/ smart-stop | dezenas de ms | `vision.detect_grid` + `detect_arrowheads` |

**Total por passo: ≈ 3,1–3,2 segundos, dos quais ~75–80% é só a captura do frame.**

Com o *Center-Out* (`max_arm_steps=2`, até 4 braços retos + 4 diagonais),
um tabuleiro grande típico pode facilmente exigir 6–16 passos até "smart
stop" disparar → **20 a 50 segundos só na fase de stitching**, antes mesmo
de resolver ou jogar o nível.

Conclusão: **todas as otimizações já feitas (batch tapping, cascata local,
Center-Out, `stop_heads_count`) atacam o tempo de swipe/toques/varrimento —
mas o maior custo individual, a captura do frame, ainda usa o caminho lento.**
É aqui que está a maior margem de ganho.

---

## 2. Causa raiz: `USE_STREAM = False` em `capture.py`

`capture.py` já tem implementado um caminho rápido — leitura contínua de
`adb exec-out screenrecord --output-format=h264` via FIFO + `cv2.VideoCapture`
— que segundo o próprio código reduziria o tempo de frame de **~2.400 ms para
~15–30 ms** (>80x mais rápido). Está desligado:

```python
# Set to False so screencap is used for 100% fresh, unbuffered frames.
USE_STREAM = False
```

O histórico de commits mostra que isto **já foi ligado e desligado várias
vezes**:

```
- # The screenrecord->FIFO->OpenCV stream proved unreliable
- # (it can return a frozen frame), so it is disabled by default
- USE_STREAM = False
...
+ USE_STREAM = True
...
- USE_STREAM = False   ← estado atual
```

Ou seja: o stream rápido existe mas foi abandonado por um **bug de
correção** (frames "congelados"/desatualizados), não por ser lento. A causa
mais provável do "frozen frame":

- `screenrecord` escreve continuamente para a FIFO. Se ninguém a ler ao
  ritmo de produção (o bot só chama `.frame()` uma vez por passo, a cada
  ~1 s), o buffer do pipe enche, `screenrecord`/o encoder bloqueia ou
  atrasa, e o próximo `.frame()` devolve um frame antigo.
- `Stream.frame(max_drain=30)` tenta mitigar isto lendo até 30 frames em
  cada chamada para "chegar ao mais recente" — mas se o produtor já ficou
  para trás (stall), isso só recupera o atraso acumulado, não elimina o
  frame estar desatualizado no momento da leitura.

Isto é um problema **de arquitetura de consumo**, não do H.264/FFMPEG em
si — e tem uma solução conhecida (usada por ferramentas como `minicap`/
`scrcpy`): **drenar continuamente em segundo plano**, nunca deixar o pipe
acumular.

### 2.1. Proposta: thread de captura contínua ("always draining")

Ideia: uma thread dedicada faz `cap.read()` em loop apertado, e guarda
sempre **só o frame mais recente** (com timestamp) numa variável protegida
por lock. `get_frame()` deixa de "puxar" do stream — passa a **ler o último
frame já decodificado**, instantaneamente.

```python
class Stream:
    def __init__(self, fifo=FIFO):
        ...
        self._latest = None
        self._latest_ts = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._thread.start()

    def _drain_loop(self):
        while not self._stop.is_set():
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self._lock:
                self._latest = frame
                self._latest_ts = time.monotonic()

    def frame(self, max_age: float = 0.2):
        with self._lock:
            if self._latest is None:
                return None
            age = time.monotonic() - self._latest_ts
            if age > max_age:
                return None  # frame demasiado velho -> fallback ao chamador
            return self._latest.copy()
```

Vantagens sobre o `max_drain` atual:
- A FIFO nunca acumula (é lida ao ritmo do encoder, não ao ritmo do bot).
- `get_frame()` deixa de custar "N leituras bloqueantes" — passa a ser
  leitura de uma variável em memória (sub-milissegundo).
- O parâmetro `max_age` dá uma **garantia explícita de frescura**: se o
  frame mais recente tiver mais de `max_age` segundos, `frame()` devolve
  `None` e `capture.get_frame()` cai automaticamente no fallback
  `adb_client.screenshot()` — mantém a segurança de "nunca usar dado
  obsoleto" sem eliminar o caminho rápido.

### 2.2. Onde ativar o stream primeiro (faseado, para não repetir o erro anterior)

Em vez de reativar `USE_STREAM=True` globalmente de imediato (foi o que
aconteceu nas tentativas anteriores e voltou a ser revertido), sugiro
introduzir **dois níveis de frescura**, porque o stitching e o loop de
jogo têm requisitos diferentes:

| Contexto | Sensibilidade a staleness | Frame source recomendada |
|---|---|---|
| `stitch._scan` / `capture_grid_centered` (entre swipes) | Baixa — já há um `settle` a seguir ao swipe; um frame com poucas dezenas de ms de atraso não muda o resultado do template-match | Stream rápido (com `max_age` curto, ex. 150–200 ms) |
| `bot.py play_level` / `play_grid.py` (decidir toques) | **Alta** — um frame desatualizado pode levar a tocar numa seta que já não é jogável → penalização | Manter `screencap` (ou stream só depois de validado extensivamente) |

Isto isola o risco: o stitching passa a beneficiar do ganho de velocidade
imediatamente, sem tocar no caminho crítico de correção (o loop de jogo,
onde um erro custa uma penalização real no jogo).

**Sinal de aceitação para depois expandir ao loop de jogo:** correr o
stitching com o stream em, digamos, 20 tabuleiros seguidos sem nenhum
`ValueError("No cells captured")`, sem heads fantasma, e sem regressão nos
testes/execuções manuais.

---

## 3. Otimizações secundárias (menor impacto, mas "quick wins")

### 3.1. Deteção de grelha/heads repetida 2–3x por frame

Hoje, o mesmo frame passa por `vision.detect_grid` + `vision.detect_arrowheads`
em até três sítios diferentes:

1. Dentro do próprio scan, para o `stop_heads_count` (em `_scan` e em
   `scan_arm`).
2. Em `save_frame_grids`, só para gerar as imagens de debug em `imgs/grids/`.
3. Indiretamente em `merge()`, via `detect_head_pixels` (para as heads
   globais).

O resultado do passo 1 (heads locais + grid) **podia ser guardado junto do
frame** (`frames.append((current, offset, grid, heads))`) e reaproveitado
nos passos 2 e 3, evitando recalcular deteção de grelha e correlação de
template para o mesmo frame múltiplas vezes. Ganho: dezenas de ms por
frame — pequeno frente à captura, mas gratuito e sem risco.

### 3.2. `save_frame_grids` a correr sempre, mesmo fora de debug

`stitch()` chama sempre `save_frame_grids(frames)`, que grava PNGs
anotados em `imgs/grids/` — I/O de disco + a deteção duplicada do ponto
3.1 — mesmo quando não é preciso depurar visualmente. Sugiro um flag
(`debug: bool = False`, ou reaproveitar o padrão já usado noutros sítios
do projeto de desativar escrita de imagens em execução rápida) para saltar
isto por omissão em `--delay 0` / execução "a sério", tal como já é feito
noutras partes do bot.

### 3.3. `detect_board_box` recalculado a cada `_shift`

`_shift()` chama `vision.detect_board_box(a)` em cada passo para recortar
a patch de template-matching. Medido o custo (é só `threshold` + `np.where`
sobre a imagem em tons de cinzento), **não é caro** — não é prioridade,
mas como o box do tabuleiro no ecrã não muda entre passos consecutivos do
mesmo braço (é o conteúdo que se move, não o viewport), pode ser calculado
uma vez por braço e reaproveitado. Incluído aqui só por completude; não
vale a pena gastar tempo nisto antes do ponto 2.

### 3.4. `settle` fixo vs. adaptativo

Atualmente `settle` é uma constante (0.4–0.5 s) que assume o pior caso de
animação do swipe. **Só depois de o stream contínuo (secção 2) estar
validado**, isto abre a porta a um `settle` adaptativo: comparar frames
sucessivos do stream e avançar assim que dois frames consecutivos forem
quase idênticos (animação estabilizou), em vez de esperar sempre o tempo
fixo. Não faz sentido implementar isto antes do ponto 2 — com `screencap`
cada frame extra de verificação custaria outros 2,4 s.

---

## 4. Plano faseado

| Fase | O quê | Esforço | Risco | Ganho esperado |
|---|---|---|---|---|
| **F1** | Reescrever `Stream` com thread de drenagem contínua + `max_age` | Médio (1 ficheiro, `capture.py`) | Baixo — fallback para `screenshot()` mantido | Base para tudo o resto |
| **F2** | Ligar o stream **só** no caminho de stitching (`stitch.py`), manter `bot.py`/`play_grid.py` em `screencap` | Baixo (1 parâmetro/flag a passar) | Baixo | Stitching passa de ~3,1 s/passo para ~0,7–0,9 s/passo (swipe+settle continuam) → **redução de ~70% no tempo total de stitching** |
| **F3** | Reaproveitar grid/heads já calculados durante o scan em `save_frame_grids`/`merge`, tornar a escrita de PNGs de debug opcional | Baixo | Muito baixo | Pequeno, mas grátis |
| **F4** | Validar extensivamente (≥20 tabuleiros, incluindo os "Super Hard" com muitas setas) e só depois considerar `settle` adaptativo e extensão do stream ao loop de jogo | Médio (validação manual) | — | Abre caminho a otimizações futuras no `bot.py`/`play_grid.py` |

**F1+F2 são o que realmente resolve o problema pedido** (velocidade do
stitching); F3 é opcional/paralelo; F4 é para depois, não faz parte deste
pedido mas fica registado para não se perder de vista.

---

## 5. Como validar sem arriscar regressões

1. Adicionar um `print`/log de tempo por passo (`time.perf_counter()` antes
   e depois de `capture.get_frame()`) — já há bastantes `print(..., flush=True)`
   no código, é só juntar timestamps para confirmar o ganho real medido,
   não só o teórico.
2. Comparar, para o mesmo tabuleiro (idealmente repetível, ex. o nível
   "Super Hard" já usado como referência), o tempo total de
   `capture_grid_centered`/`stitch()` antes e depois da F1+F2.
3. Confirmar que o número de heads detetadas no board final (`len(heads)`
   em `stitch()`) e a contagem de células `OCCUPIED` são **idênticos**
   entre a versão antiga (screencap) e a nova (stream) para o mesmo
   tabuleiro — é o teste de regressão mais direto para apanhar frames
   "congelados" a voltar a acontecer.
4. Manter o `max_age` como rede de segurança explícita: se o frame do
   stream nunca estiver "fresco o suficiente" em testes reais, o sistema
   já cai sozinho para `screenshot()` em vez de arriscar dados errados —
   por isso o parâmetro deve ficar configurável e testado nos dois
   extremos (muito apertado vs. muito permissivo).

---

## 6. Resumo

O maior travão à velocidade do stitching não está no algoritmo de
varrimento (já bem otimizado com Center-Out e paragem antecipada) nem nos
swipes — está na captura de imagem, que continua a usar o caminho lento
(`screencap`, ~2,4 s/frame) porque o caminho rápido (stream H.264) foi
desativado por um bug de frescura já identificado no histórico do projeto.
Corrigir esse bug com uma thread de drenagem contínua e ativá-lo
**primeiro só no stitching** (mantendo o loop de jogo no caminho seguro
atual) é a mudança com melhor relação ganho/risco: potencial de **~70%
menos tempo de stitching** sem tocar na lógica que decide toques no jogo.
