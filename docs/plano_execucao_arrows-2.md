# Plano de Execução — Automatização do Jogo Arrows

## Objetivo do projeto

Criar um programa que corre no PC e joga sozinho o jogo Android **Arrows** num telemóvel ligado. O programa observa o ecrã do telemóvel, percebe o estado do tabuleiro, decide a jogada e envia o toque — sem intervenção humana, exceto para arrancar e supervisionar.

## O jogo Arrows — como se joga

O tabuleiro é uma grelha onde cada peça é uma seta que aponta para cima, baixo, esquerda ou direita. O objetivo é remover todas as setas.

**Importante:** as setas não ocupam necessariamente apenas uma célula. Podem ter tamanho variável e forma tipo "cobra" — ou seja, uma única seta pode estender-se por várias células em sequência, possivelmente com curvas, e não apenas numa linha reta. Isto significa que uma peça não é um simples par (posição, direção): é antes um conjunto de células ligadas entre si, com uma ponta que indica a direção de saída.

A regra geral é: uma seta só pode ser removida se o caminho entre a sua ponta (a extremidade que aponta para fora) e a borda do tabuleiro, na direção indicada, estiver completamente livre de outras setas. Se houver outra seta nesse caminho, ela está bloqueada e tem de esperar que a que está à frente seja removida primeiro. Cada remoção pode desbloquear novas setas, até o tabuleiro ficar vazio.

Esta caraterística (setas com corpo estendido/tipo cobra) tem impacto direto em várias fases seguintes — nomeadamente no reconhecimento visual (Fase 6) e na representação lógica do tabuleiro (Fase 7), que precisam de tratar cada seta como um grupo de células ligadas, e não como uma célula isolada. Isto deve ser confirmado e observado com atenção assim que o jogo for testado manualmente (Fase 2).

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
25. Definir uma forma simples de distinguir "célula vazia" de "célula ocupada por parte de uma seta".
26. Definir uma forma de identificar, em cada célula ocupada, se é o corpo da seta ou a ponta (a extremidade que indica a direção de saída).
27. Definir uma forma de agrupar células vizinhas que pertencem à mesma seta, já que uma seta pode ocupar várias células ligadas, incluindo com curvas.
28. Testar esse reconhecimento em várias setas diferentes (retas e com curvas) até funcionar de forma consistente.
29. Repetir o reconhecimento para todo o tabuleiro, construindo uma representação completa com todas as setas e as células que cada uma ocupa.

### Fase 7 — Construir a lógica de decisão

30. Representar cada seta como um grupo de células ligadas, com a posição da ponta e a direção de saída.
31. Definir a regra que verifica se a ponta de uma seta tem caminho livre até à borda do tabuleiro.
32. Testar essa regra em tabuleiros inventados por ti, à mão, incluindo casos com setas curvas, para garantir que o resultado está correto.
33. Definir a lógica que percorre todas as setas do tabuleiro e lista as que estão jogáveis num dado momento.
34. Testar essa lógica também em tabuleiros inventados, incluindo casos com várias setas bloqueadas e setas com corpo estendido.

### Fase 8 — Juntar reconhecimento e decisão

35. Capturar uma screenshot real do jogo.
36. Passar essa screenshot pelo reconhecimento de grelha (Fase 6) para obter todas as setas e as células que cada uma ocupa.
37. Passar essa representação pela lógica de decisão (Fase 7) para obter as setas jogáveis.
38. Confirmar, manualmente, que a jogada sugerida está de facto correta olhando para o jogo real.

### Fase 9 — Executar uma única jogada automática

39. Escolher a primeira seta jogável da lista.
40. Calcular a coordenada de ecrã correspondente ao ponto onde se deve tocar nessa seta (normalmente a ponta ou o corpo, conforme o que o jogo exigir).
41. Enviar o toque para essa coordenada via ADB.
42. Aguardar o tempo da animação do jogo.
43. Capturar uma nova screenshot e confirmar visualmente que a seta desapareceu.

### Fase 10 — Repetir automaticamente

44. Colocar os passos 35 a 43 dentro de um ciclo que se repete sozinho.
45. Adicionar uma verificação, a cada volta do ciclo, que confirma se o nível já terminou.
46. Adicionar uma paragem automática caso não exista nenhuma jogada disponível.
47. Deixar o ciclo correr sozinho num nível simples e observar se completa o jogo sem ajuda.

### Fase 11 — Tornar o processo mais robusto

48. Adicionar um limite máximo de jogadas, para evitar ciclos infinitos em caso de erro.
49. Adicionar registo (log) de cada jogada feita, para conseguires analisar o comportamento depois.
50. Adicionar um "modo de simulação" que decide as jogadas mas não toca de verdade no telemóvel, útil para testar em segurança.
51. Adicionar uma forma simples de interromper o programa manualmente a qualquer momento.

---

A ideia geral é: cada fase só avança depois da anterior estar a funcionar de forma fiável e confirmada visualmente, começando sempre pela parte mais simples de comprovar (a ligação) e só complicando quando o passo anterior já é sólido.
