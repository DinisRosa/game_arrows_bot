# Auto-ARROWS — Plano Consolidado do Bot

## 1. Objetivo

Criar um bot em Python que jogue automaticamente o jogo *Arrows* via ADB, capaz de
reconhecer o tabuleiro completo (mesmo partes fora do ecrã), calcular jogadas válidas
e executá-las sem causar penalizações.

## 2. Arquitetura e Stack

- **Linguagem**: Python 3 (com `venv`).
- **Bibliotecas**: `OpenCV` (processamento de imagem), `Pillow` (manipulação de ficheiros), `NumPy`.
- **Comunicação**: `ADB` (captura de ecrã, toques e swipes).
- **Módulos**:
  - `adb_client.py`: Interface de baixo nível com o Android.
  - `vision.py`: Processamento de imagem, deteção de pontas/corpos e **stitching** de múltiplas capturas.
  - `model.py`: Representação lógica das setas e do tabuleiro virtual.
  - `solver.py`: Algoritmo de decisão (caminho livre da ponta até à borda).
  - `bot.py`: Loop principal de execução.
  - `calibrate.py`: Ferramenta de configuração inicial; deteta pontos expostos em qualquer posição, usa a moldura preta para os limites e extrapola a grelha (não assume cantos).

## 3. Modelo de Mecânicas (Confirmado por imagem)

- **Setas**: Caminhos de células ligadas (90°) com uma "cabeça" triangular.
- **Setas iniciais**: as que podem sair logo no início, por não estarem bloqueadas por nenhuma outra; podem estar **em qualquer posição (laterais, meio)**, não só nos cantos.
- **Movimento**: Tipo "Snake" — a cabeça lidera e o corpo segue o trilho.
- **Condição de Saída**: Linha reta da ponta até à borda do tabuleiro (não do ecrã) deve estar livre de outras setas.
- **Bloqueio**: Uma seta curva ocupa várias células, podendo bloquear trajetórias horizontais e verticais ao mesmo tempo.
- **Fundo**: Branco com pontos cinzentos a marcar a grelha (úteis para alinhamento); estes pontos aparecem após uma ou mais setas saírem.
- **Cores**: Maioria das setas é azul muito escuro/preto; por vezes existem setas com outras cores.
- **Penalização**: Tocar numa seta bloqueada tem penalização (perda de uma vida, no máximo existem 3) → o bot nunca deve arriscar.

## 4. Fases de Execução

| Fase | Descrição | Verificação |
| :--- | :--- | :--- |
| **I. Ambiente** | Criar `venv`, instalar dependências e implementar `adb_client.py`. | Captura de ecrã e toques via código. |
| **II. Calibração** | Criar script para medir tamanho da célula e margens. **Bootstrap mínimo**: o utilizador retira apenas **2–3 setas iniciais** (as que podem sair logo, desbloqueadas), expondo alguns pontos da grelha — em qualquer posição, **não só nos cantos**. Os limites do tabuleiro vêm da **moldura preta**, e a grelha é extrapolada a partir de qualquer ponto exposto (espaçamento + fase). Sem este bootstrap, usa-se a **periodicidade da grelha / estrutura das setas**. | Coordenadas dos centros das células OK. |
| **III. Visão (Base)** | Deteção de pontas (triângulos) e agrupamento de pixéis por conectividade. Os pontos cinzentos são um sinal secundário de "célula vazia". | Tabuleiro visível mapeado corretamente. |
| **IV. Visão (Stitching)** | Lógica de swipe + alinhamento para construir o tabuleiro completo (Plano B). Como no início não há pontos, o alinhamento usa a **sobreposição do padrão de setas** e, à medida que o tabuleiro esvazia, também os pontos. | "Mega-tabuleiro" virtual sem erros de alinhamento. |
| **V. Solver** | Implementar regra de caminho livre e testes unitários com casos (L, S, curvas). | Jogadas sugeridas são válidas e seguras. |
| **VI. Integração** | Loop de jogo: reconhecer → decidir → (scroll se necessário) → tocar → verificar. | Bot completa um nível "Nightmare" sozinho. |
| **VII. Robustez** | Deteção de fim de nível (botão Next), leitura das **vidas (corações)**, tratamento de anúncios e logs. | Operação contínua e segura. |

## 5. Estratégia de Segurança (Anti-Penalização)

- **Orçamento de 3 vidas**: só há 3 penalizações possíveis por nível; o bot é conservador por defeito.
- **Leitura das vidas**: o bot lê os corações no topo em cada ciclo; se perder uma vida inesperadamente, **para imediatamente** (o modelo ou o reconhecimento falharam).
- **Verificação de Estabilidade**: O bot tira duas fotos antes de agir para garantir que não há animações a decorrer.
- **Modelo Completo**: Nenhuma seta é clicada se o seu caminho de saída levar a uma zona "desconhecida" (fora do stitching).
- **Paragem Automática**: Se um toque não resultar no desaparecimento da seta, o bot assume erro e para.
- **Recuperação (a investigar)**: existe botão de *Undo* na UI — verificar se desfaz uma jogada após penalização, para eventual recuperação automática.

## 6. Lógica de Stitching (Plano B)

Os pontos cinzentos só aparecem nas células já vazias, por isso o alinhamento tem de
funcionar em três regimes:

**A. Bootstrap mínimo (recomendado na 1ª vez por cada tamanho de grelha):**
- O utilizador retira apenas **2–3 setas iniciais** (as desbloqueadas), expondo alguns
  pontos da grelha; estes podem estar em qualquer posição, **não só nos cantos**.
- Os limites do tabuleiro vêm da **moldura preta**; o script usa os pontos expostos para
  calcular **espaçamento + fase** e **extrapola** a grelha inteira, guardando a config do nível.

**B. Tabuleiro cheio (sem bootstrap, sem pontos visíveis):**
1. Tiramos foto 1.
2. Fazemos swipe (pan) para a direção pretendida.
3. Tiramos foto 2.
4. Alinhamos as fotos pela **sobreposição do padrão de setas** (correlação/template matching)
   e pela **periodicidade da célula** já conhecida da calibração, para calcular o deslocamento exato.

**C. Tabuleiro parcialmente vazio (pontos visíveis):**
- Usamos os **pontos cinzentos** como grelha de referência direta para calcular o deslocamento,
  o que é mais rápido e robusto.

Em todos os casos:
5. Repetimos até cobrir todo o nível.
6. Construímos o "mega-tabuleiro" virtual com as células alinhadas.

Isso permite que o bot "saiba" o que está fora do ecrã e perceba se uma seta visível
está a ser bloqueada por uma que está escondida.

## 7. Notas de Comportamento

- **Vidas**: 3 corações no topo do ecrã; cada toque em seta bloqueada remove um.
- **Fim de nível**: Aparece botão de próximo nível, mas por vezes surge publicidade →
  o bot para no fim do nível e espera que o utilizador inicie o próximo; depois retoma.
- **Scroll**: Assumido livre (pan), a confirmar durante a implementação.
