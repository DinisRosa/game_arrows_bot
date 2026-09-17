# Plano de Execução — Automatização do Jogo Arrows

## Objetivo do projeto

Criar um programa que corre no PC e joga sozinho o jogo Android **Arrows** num telemóvel ligado. O programa observa o ecrã do telemóvel, percebe o estado do tabuleiro, decide a jogada e envia o toque — sem intervenção humana, exceto para arrancar e supervisionar.

## O jogo Arrows — como se joga

O tabuleiro é uma grelha onde cada célula pode estar vazia ou conter uma seta apontada para cima, baixo, esquerda ou direita. O objetivo é remover todas as setas.

A regra é simples: uma seta só pode ser tocada e removida se o caminho entre ela e a borda do tabuleiro, na direção para onde aponta, estiver completamente livre de outras setas. Se houver outra seta nesse caminho, ela está bloqueada e tem de esperar que a que está à frente seja removida primeiro. Cada remoção pode desbloquear novas setas, até o tabuleiro ficar vazio.

## Passo a passo de execução

### Fase 0 — Preparar o ambiente

1. Instalar o Android Platform Tools (ferramenta que inclui o ADB) no PC.
2. Ativar as "Opções de programador" no telemóvel.
3. Ativar a "Depuração USB" dentro dessas opções.
4. Ligar o telemóvel ao PC por cabo USB (com transmissão de dados, não só carregamento).
5. Autorizar o computador quando o telemóvel pedir confirmação no ecrã.

### Fase 1 — Confirmar que a ligação funciona

6. Verificar, através do ADB, se o telemóvel aparece na lista de dispositivos ligados.
7. Confirmar que o estado do dispositivo aparece como "autorizado" e não como "não autorizado" ou "offline".
8. Se não aparecer nada, rever o cabo, o driver USB, e se a depuração está mesmo ativa.

### Fase 2 — Controlo visual manual

9. Instalar o scrcpy para espelhar o ecrã do telemóvel numa janela do PC.
10. Abrir o scrcpy e confirmar que consegues ver o ecrã do telemóvel em tempo real.
11. Testar clicar na janela do scrcpy e confirmar que o clique corresponde a um toque real no telemóvel.
12. Abrir o jogo Arrows manualmente e observar o aspeto do tabuleiro através do scrcpy.

### Fase 3 — Enviar toques por comando (sem scrcpy)

13. Descobrir a resolução do ecrã do telemóvel.
14. Enviar, via ADB, um toque de teste numa coordenada conhecida do ecrã.
15. Confirmar visualmente (com scrcpy ligado ao mesmo tempo) que o toque aconteceu no sítio certo.
16. Repetir o teste em diferentes zonas do ecrã até teres confiança nas coordenadas.

### Fase 4 — Capturar o ecrã por comando

17. Pedir ao ADB uma captura do ecrã do telemóvel e guardá-la como imagem no PC.
18. Abrir essa imagem e confirmar que mostra corretamente o jogo.
19. Repetir a captura várias vezes seguidas para confirmar que o processo é fiável e rápido o suficiente.

### Fase 5 — Mapear a grelha do jogo

20. Com o jogo aberto, anotar manualmente as coordenadas dos cantos da área do tabuleiro na imagem capturada.
21. Definir quantas linhas e colunas a grelha tem.
22. Calcular, a partir dos cantos, onde fica o centro de cada célula da grelha.
23. Confirmar visualmente que essas coordenadas caem mesmo no meio de cada célula.

### Fase 6 — Reconhecer o conteúdo de cada célula

24. Recortar da imagem apenas a zona correspondente a uma célula.
25. Definir uma forma simples de distinguir "célula vazia" de "célula com seta".
26. Definir uma forma de identificar, quando há seta, para que direção aponta.
27. Testar esse reconhecimento em várias células diferentes até funcionar de forma consistente.
28. Repetir o reconhecimento para todas as células da grelha, construindo uma representação completa do tabuleiro.

### Fase 7 — Construir a lógica de decisão

29. Representar o tabuleiro reconhecido de forma organizada (linha, coluna, direção de cada seta).
30. Definir a regra que verifica se uma seta tem caminho livre até à borda.
31. Testar essa regra em tabuleiros inventados por ti, à mão, para garantir que o resultado está correto.
32. Definir a lógica que percorre todo o tabuleiro e lista todas as setas jogáveis num dado momento.
33. Testar essa lógica também em tabuleiros inventados, incluindo casos com várias setas bloqueadas.

### Fase 8 — Juntar reconhecimento e decisão

34. Capturar uma screenshot real do jogo.
35. Passar essa screenshot pelo reconhecimento de grelha (Fase 6) para obter o tabuleiro.
36. Passar esse tabuleiro pela lógica de decisão (Fase 7) para obter as setas jogáveis.
37. Confirmar, manualmente, que a jogada sugerida está de facto correta olhando para o jogo real.

### Fase 9 — Executar uma única jogada automática

38. Escolher a primeira seta jogável da lista.
39. Calcular a coordenada de ecrã correspondente ao centro dessa célula.
40. Enviar o toque para essa coordenada via ADB.
41. Aguardar o tempo da animação do jogo.
42. Capturar uma nova screenshot e confirmar visualmente que a seta desapareceu.

### Fase 10 — Repetir automaticamente

43. Colocar os passos 34 a 42 dentro de um ciclo que se repete sozinho.
44. Adicionar uma verificação, a cada volta do ciclo, que confirma se o nível já terminou.
45. Adicionar uma paragem automática caso não exista nenhuma jogada disponível.
46. Deixar o ciclo correr sozinho num nível simples e observar se completa o jogo sem ajuda.

### Fase 11 — Tornar o processo mais robusto

47. Adicionar um limite máximo de jogadas, para evitar ciclos infinitos em caso de erro.
48. Adicionar registo (log) de cada jogada feita, para conseguires analisar o comportamento depois.
49. Adicionar um "modo de simulação" que decide as jogadas mas não toca de verdade no telemóvel, útil para testar em segurança.
50. Adicionar uma forma simples de interromper o programa manualmente a qualquer momento.

---

A ideia geral é: cada fase só avança depois da anterior estar a funcionar de forma fiável e confirmada visualmente, começando sempre pela parte mais simples de comprovar (a ligação) e só complicando quando o passo anterior já é sólido.
